"""County-record workspace, upload intake bridge, and protected sync controls."""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any, AsyncIterator, Dict, Iterable, List, Mapping, Optional

import pandas as pd
from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse

from address_utils import canonical_street_key
from database import PostgresDatabase
from export_safety import spreadsheet_safe
from intake import upsert_import_records
from importers.county_records import (
    completeness,
    enrich_live_properties_from_county_records,
    sync_tad_county_records,
)


router = APIRouter(prefix="/api")


_ACCOUNT_HEADERS = {
    "account", "account id", "account number", "account num", "account_num",
    "acct num", "acct_num", "tax account", "tax account apn", "tax account/apn",
    "apn", "pin", "pin number", "tad account", "tad #", "property id", "prop_id",
}
_ADDRESS_HEADERS = {
    "address", "property address", "situs address", "street address", "site address",
    "full address", "matched address",
}


def _header(value: Any) -> str:
    return re.sub(r"[_\s]+", " ", str(value or "").strip().lower())


def _clean_cell(value: Any) -> Any:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(value, "item"):
        try:
            value = value.item()
        except (TypeError, ValueError):
            pass
    if hasattr(value, "isoformat") and not isinstance(value, (str, bytes)):
        try:
            return value.isoformat()
        except (TypeError, ValueError):
            return str(value)
    text = str(value).strip()
    return None if not text or text.lower() in {"nan", "none", "null", "n/a"} else value


def _first_value(row: Mapping[str, Any], headers: Iterable[str]) -> Any:
    wanted = {_header(name) for name in headers}
    for key, value in row.items():
        if _header(key) in wanted:
            cleaned = _clean_cell(value)
            if cleaned is not None:
                return cleaned
    return None


def _normalized_account(value: Any) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper()).lstrip("0")


def _row_identity(row: Mapping[str, Any]) -> str:
    account = _normalized_account(_first_value(row, _ACCOUNT_HEADERS))
    if account:
        return f"account:{account}"
    address = str(_first_value(row, _ADDRESS_HEADERS) or "").strip()
    street_key = canonical_street_key(address)
    return f"address:{street_key}" if street_key else ""


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    return text in {"1", "true", "yes", "y", "x", "active", "open"}


def _canonicalize_research_row(row: Mapping[str, Any], sheet_name: str) -> Dict[str, Any]:
    """Keep every original field while adding canonical aliases used by intake.py."""
    out = {str(key): _clean_cell(value) for key, value in row.items()}
    out["__sheet_name"] = sheet_name

    aliases = {
        "address": ("property address", "situs address", "matched address", "street address"),
        "owner": ("grantor/owner", "grantor", "current owner", "owner name"),
        "account id": ("tax account/apn", "tax account apn", "account number", "account_num", "pin", "apn"),
        "legal description": ("legal description", "property/legal description"),
        "subdivision": ("subdivision",),
        "land use code": ("land use code",),
        "sqft": ("total structure area", "living area", "building sqft", "structure area"),
        "year built": ("year built",),
        "effective year": ("year updated", "effective year", "effective year built"),
        "beds": ("bedrooms",),
        "baths": ("bathrooms",),
        "garage": ("parking", "garage spaces", "garage"),
        "pool": ("pool",),
        "quality": ("structure quality", "quality", "building quality"),
        "condition": ("structure condition", "condition", "building condition"),
        "improvement type": ("improvements", "improvement type"),
        "land value": ("land value",),
        "improvement value": ("improvement value",),
        "assessed value": ("total assessed value", "assessed value"),
        "market value": ("market value",),
        "sale date": ("sale date", "auction date"),
        "purchaser": ("buyer information", "buyer", "purchaser"),
        "sale amount": ("latest sale price", "sale price", "sale amount"),
        "sale status": ("latest sale type", "sale type", "sale status"),
    }
    for canonical, names in aliases.items():
        if _clean_cell(out.get(canonical)) is not None:
            continue
        value = _first_value(row, names)
        if value is not None:
            out[canonical] = value

    heating = _first_value(row, ("heating",))
    cooling = _first_value(row, ("air conditioning", "cooling",))
    if heating or cooling:
        out.setdefault("hvac", " / ".join(str(v) for v in (heating, cooling) if v))

    distress_parts: List[str] = []
    pre_value = _first_value(row, ("pre-foreclosure", "pre foreclosure", "preforeclosure"))
    foreclosure_value = _first_value(row, ("foreclosure", "foreclosed", "foreclosure status"))
    status_value = _first_value(row, ("status", "property status", "listing status", "sale status"))
    status_text = str(status_value or "").lower()
    if _truthy(pre_value) or "pre-foreclos" in status_text or "preforeclos" in status_text:
        distress_parts.append("Pre-Foreclosure")
    if _truthy(foreclosure_value) or ("foreclos" in status_text and "pre" not in status_text):
        distress_parts.append("Foreclosure")
    if distress_parts:
        out["status"] = " ".join(distress_parts)

    # Preserve research/audit columns even when intake.py does not yet have a
    # first-class field for them. raw_import_row stores this complete mapping.
    out.setdefault("Research Source Tab", sheet_name)
    return out


def _merge_workbook_rows(sheet_rows: Iterable[tuple[str, Mapping[str, Any]]]) -> List[Dict[str, Any]]:
    merged: List[Dict[str, Any]] = []
    identity_to_index: Dict[str, int] = {}
    for sheet_name, raw in sheet_rows:
        row = _canonicalize_research_row(raw, sheet_name)
        identity = _row_identity(row)
        if identity and identity in identity_to_index:
            current = merged[identity_to_index[identity]]
            sheets = [part.strip() for part in str(current.get("Research Source Tab") or "").split("|") if part.strip()]
            if sheet_name not in sheets:
                sheets.append(sheet_name)
            current["Research Source Tab"] = " | ".join(sheets)
            for key, value in row.items():
                if _clean_cell(value) is None:
                    continue
                if _clean_cell(current.get(key)) is None:
                    current[key] = value
                elif current.get(key) != value and not key.startswith("__"):
                    # Keep the first value in the canonical column and retain a
                    # conflicting source value under its worksheet-qualified name.
                    current.setdefault(f"{sheet_name} :: {key}", value)
            continue
        if identity:
            identity_to_index[identity] = len(merged)
        merged.append(row)
    return merged


def _read_upload_rows(raw: bytes, suffix: str, filename: str) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Read CSV or every sheet in an Excel workbook and return merged rows + tab report."""
    reports: List[Dict[str, Any]] = []
    if suffix == ".csv":
        try:
            frame = pd.read_csv(BytesIO(raw), encoding="utf-8-sig")
        except UnicodeDecodeError:
            frame = pd.read_csv(BytesIO(raw), encoding="latin1")
        rows = [_canonicalize_research_row(row, "CSV") for row in frame.to_dict(orient="records")]
        reports.append({"sheet": "CSV", "rows": len(rows)})
        return rows, reports
    if suffix not in {".xls", ".xlsx"}:
        raise ValueError(f"Unsupported file type: {suffix}")

    workbook = pd.read_excel(BytesIO(raw), sheet_name=None)
    all_rows: List[tuple[str, Mapping[str, Any]]] = []
    for sheet_name, frame in workbook.items():
        sheet_records = frame.to_dict(orient="records")
        reports.append({"sheet": str(sheet_name), "rows": len(sheet_records)})
        all_rows.extend((str(sheet_name), row) for row in sheet_records)
    return _merge_workbook_rows(all_rows), reports


@router.post("/intake/upload")
async def county_workbook_upload(file: UploadFile = File(...)):
    """Smart upload path: read every workbook tab, merge by PIN/account/address, then enrich."""
    filename = Path(file.filename or "upload.xlsx").name
    suffix = Path(filename).suffix.lower()
    if suffix not in {".csv", ".xls", ".xlsx", ".zip"}:
        raise HTTPException(400, "Upload .csv, .xls, .xlsx, or .zip")
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "The uploaded file is empty")
    if len(raw) > 200 * 1024 * 1024:
        raise HTTPException(413, "The upload is larger than 200 MB")

    db = PostgresDatabase()
    await db.connect()
    try:
        source_name = f"User upload: {filename}"
        property_ids: List[str] = []
        total_accepted = total_rejected = total_inserted = total_updated = 0
        file_reports: List[Dict[str, Any]] = []

        async def import_one(name: str, payload: bytes, ext: str) -> None:
            nonlocal total_accepted, total_rejected, total_inserted, total_updated
            rows, sheet_reports = _read_upload_rows(payload, ext, name)
            if len(rows) > 250:
                raise ValueError(f"{len(rows)} merged properties exceeds the 250-property test limit")
            report = await upsert_import_records(db, rows, f"{source_name} / {name}")
            property_ids.extend(report["property_ids"])
            total_accepted += report["accepted"]
            total_rejected += report["rejected"]
            total_inserted += report["inserted"]
            total_updated += report["updated"]
            file_reports.append({
                "file": name,
                "status": "ok",
                "rows": report["rows_read"],
                "accepted": report["accepted"],
                "inserted": report["inserted"],
                "updated": report["updated"],
                "sheets": sheet_reports,
            })

        if suffix == ".zip":
            try:
                with zipfile.ZipFile(BytesIO(raw)) as zf:
                    members = [
                        member for member in zf.namelist()
                        if not member.endswith("/") and Path(member).suffix.lower() in {".csv", ".xls", ".xlsx"}
                    ]
                    if not members:
                        raise HTTPException(400, "ZIP contains no CSV or Excel files")
                    for member in members:
                        try:
                            await import_one(member, zf.read(member), Path(member).suffix.lower())
                        except Exception as exc:
                            file_reports.append({"file": member, "status": "error", "reason": str(exc)[:240]})
            except HTTPException:
                raise
            except Exception as exc:
                raise HTTPException(400, f"Could not read ZIP: {str(exc)[:180]}") from exc
        else:
            try:
                await import_one(filename, raw, suffix)
            except Exception as exc:
                raise HTTPException(400, f"Could not import workbook: {str(exc)[:180]}") from exc

        unique_ids = list(dict.fromkeys(property_ids))
        county = await enrich_live_properties_from_county_records(
            db,
            property_ids=unique_ids,
            lookup_missing_tad=True,
        ) if unique_ids else {"live_checked": 0, "enriched": 0, "tad_lookups": 0, "missing": 0}

        return {
            "ok": bool(total_accepted),
            "filename": filename,
            "files": file_reports,
            "rows_read": total_accepted + total_rejected,
            "accepted": total_accepted,
            "rejected": total_rejected,
            "inserted": total_inserted,
            "updated": total_updated,
            "total_accepted": total_accepted,
            "total_rejected": total_rejected,
            "total_inserted": total_inserted,
            "total_updated": total_updated,
            "property_ids": unique_ids,
            "enrichment": {
                "county": county,
                "details": {"attempted": 0, "found": 0, "not_found": 0, "errors": []},
            },
        }
    finally:
        await db.close()


def _display_record(record: Dict[str, Any]) -> Dict[str, Any]:
    item = dict(record)
    item["sources"] = [
        label
        for enabled, label in (
            (item.get("has_uploaded"), "Uploaded"),
            (item.get("has_tad"), "TAD"),
            (item.get("has_tax_roll"), "Tax Roll"),
            (item.get("has_code_violations"), "Fort Worth Code Violations"),
        )
        if enabled
    ]
    item["market_value"] = item.get("market_value") or item.get("tax_roll_market_value")
    item.update(completeness(item))
    return item


def _display_uploaded(record: Dict[str, Any]) -> Dict[str, Any]:
    item = dict(record)
    item["has_uploaded"] = True
    item["record_kind"] = "uploaded"
    item["sources"] = ["Uploaded"]
    item["appraised_value"] = item.get("appraised_value") or item.get("assessed_value")
    item["market_value"] = item.get("market_value") or item.get("assessed_value")
    item.update(completeness(item))
    return item


def _source_query(source: str) -> Dict[str, Any]:
    if source == "tad":
        return {"has_tad": True}
    if source == "tax_roll":
        return {"has_tax_roll": True}
    if source == "tax_delinquent":
        return {"tax_delinquent": True}
    if source == "code_violations":
        return {"has_code_violations": True}
    return {}


def _search_query(search: Optional[str]) -> Dict[str, Any]:
    if not search or not search.strip():
        return {}
    regex = {"$regex": re.escape(search.strip()), "$options": "i"}
    return {"$or": [
        {"situs_address": regex},
        {"owner_name": regex},
        {"owner_mailing_address": regex},
        {"account_id": regex},
        {"parcel_id": regex},
        {"zip": regex},
        {"code_latest_complaint": regex},
        {"code_latest_status": regex},
        {"code_case_ids": regex},
        {"code_violation_ids": regex},
    ]}


@router.get("/county-records/stats")
async def county_record_stats():
    db = PostgresDatabase()
    try:
        await db.connect()
        latest = await db.county_sync_log.find({}, {"_id": 0}).sort("created_at", -1).limit(8).to_list(length=8)
        cursor = await db.sync_log.find_one({"name": "county_tad_cursor"}) or {}
        return {
            "total": await db.county_records.count_documents({}),
            "uploaded": await db.properties.count_documents({"raw_import_row": {"$exists": True}}),
            "with_tad": await db.county_records.count_documents({"has_tad": True}),
            "with_tax_roll": await db.county_records.count_documents({"has_tax_roll": True}),
            "tax_delinquent": await db.county_records.count_documents({"tax_delinquent": True}),
            "with_code_violations": await db.county_records.count_documents({"has_code_violations": True}),
            "open_code_violations": await db.county_records.count_documents({"open_code_violation_count": {"$gt": 0}}),
            "tad_next_offset": cursor.get("next_offset", 0),
            "tad_snapshot_completed_at": cursor.get("last_completed_snapshot"),
            "recent_syncs": latest,
        }
    finally:
        await db.close()


@router.get("/county-records")
async def list_county_records(
    source: str = Query("all", pattern="^(all|uploaded|tad|tax_roll|tax_delinquent|code_violations)$"),
    search: Optional[str] = Query(None, max_length=160),
    page: int = Query(1, ge=1),
    limit: int = Query(75, ge=1, le=200),
):
    db = PostgresDatabase()
    try:
        await db.connect()
        search_query = _search_query(search)

        if source == "uploaded":
            query: Dict[str, Any] = {"raw_import_row": {"$exists": True}}
            if search_query:
                query = {"$and": [query, search_query]}
            total = await db.properties.count_documents(query)
            docs = await (
                db.properties.find(query, {"_id": 0})
                .sort("situs_address", 1)
                .skip((page - 1) * limit)
                .limit(limit)
                .to_list(length=limit)
            )
            return {
                "count": len(docs),
                "total": total,
                "page": page,
                "pages": max(1, (total + limit - 1) // limit),
                "items": [_display_uploaded(item) for item in docs],
            }

        query = _source_query(source)
        if search_query:
            query = {"$and": [query, search_query]} if query else search_query
        total = await db.county_records.count_documents(query)
        docs = await (
            db.county_records.find(query, {"_id": 0})
            .sort("situs_address", 1)
            .skip((page - 1) * limit)
            .limit(limit)
            .to_list(length=limit)
        )
        items = [_display_record(item) for item in docs]

        if source == "all" and page == 1:
            uploaded_query: Dict[str, Any] = {"raw_import_row": {"$exists": True}}
            if search_query:
                uploaded_query = {"$and": [uploaded_query, search_query]}
            uploaded_docs = await (
                db.properties.find(uploaded_query, {"_id": 0})
                .sort("situs_address", 1)
                .limit(limit)
                .to_list(length=limit)
            )
            existing_keys = {
                (str(item.get("account_id") or ""), str(item.get("address_key") or ""))
                for item in items
            }
            for uploaded in uploaded_docs:
                key = (str(uploaded.get("account_id") or ""), str(uploaded.get("address_key") or ""))
                if key not in existing_keys:
                    items.append(_display_uploaded(uploaded))
                    existing_keys.add(key)
            total += len(uploaded_docs)

        return {
            "count": len(items),
            "total": total,
            "page": page,
            "pages": max(1, (total + limit - 1) // limit),
            "items": items,
        }
    finally:
        await db.close()


@router.get("/county-records/export.csv")
async def export_county_records(source: str = Query("all", pattern="^(all|uploaded|tad|tax_roll|tax_delinquent|code_violations)$")):
    fields = [
        "account_id", "parcel_id", "situs_address", "city", "state", "zip",
        "owner_name", "owner_mailing_address", "mailing_city", "mailing_state", "mailing_zip",
        "beds", "baths", "sqft", "year_built", "lot_size_sqft", "appraised_value",
        "market_value", "tax_roll_market_value", "land_value", "improvement_value",
        "annual_taxes", "current_tax_amount_due", "prior_tax_amount_due", "tax_delinquent",
        "delinquency_date", "legal_description", "school_district", "deed_date",
        "absentee_owner", "out_of_state_owner", "trust_owned", "company_owned",
        "pre_foreclosure", "listing_type", "listing_status", "sale_status", "auction_date",
        "has_code_violations", "code_violation_count", "open_code_violation_count",
        "code_latest_complaint", "code_latest_status", "code_oldest_open_date",
        "code_latest_update", "code_next_activity_due", "code_geocode_score",
        "code_case_ids", "code_violation_ids", "code_complaint_types",
        "code_substandard_building", "code_property_maintenance", "code_high_grass_weeds",
        "code_health_hazard", "code_solid_waste", "code_zoning", "code_vehicle", "code_multifamily",
        "has_tad", "has_tax_roll", "tad_updated_at", "tax_roll_updated_at", "code_violation_updated_at",
        "raw_import_row", "tad_raw_json", "tax_roll_raw_json",
    ]

    async def rows() -> AsyncIterator[str]:
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        yield output.getvalue()
        output.seek(0)
        output.truncate(0)

        db = PostgresDatabase()
        page = 0
        batch_size = 5000
        projection = {field: 1 for field in fields}
        projection.update({
            "has_tad": 1,
            "has_tax_roll": 1,
            "has_code_violations": 1,
            "tad_raw": 1,
            "tax_roll_raw": 1,
        })
        try:
            await db.connect()
            collection = db.properties if source == "uploaded" else db.county_records
            query = {"raw_import_row": {"$exists": True}} if source == "uploaded" else _source_query(source)
            while True:
                docs = await (
                    collection.find(query, projection)
                    .sort("situs_address", 1)
                    .skip(page * batch_size)
                    .limit(batch_size)
                    .to_list(length=batch_size)
                )
                if not docs:
                    break
                for item in docs:
                    display = _display_uploaded(item) if source == "uploaded" else _display_record(item)
                    if display.get("raw_import_row"):
                        display["raw_import_row"] = json.dumps(display["raw_import_row"], ensure_ascii=False, default=str)
                    display["tad_raw_json"] = (
                        json.dumps(item.get("tad_raw"), ensure_ascii=False, default=str)
                        if item.get("tad_raw") else None
                    )
                    display["tax_roll_raw_json"] = (
                        json.dumps(item.get("tax_roll_raw"), ensure_ascii=False, default=str)
                        if item.get("tax_roll_raw") else None
                    )
                    writer.writerow({field: spreadsheet_safe(display.get(field)) for field in fields})
                yield output.getvalue()
                output.seek(0)
                output.truncate(0)
                if len(docs) < batch_size:
                    break
                page += 1
        finally:
            await db.close()

    return StreamingResponse(
        rows(),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'investorflip-{source}-county-records.csv',
            "X-Export-Complete": "true",
        },
    )


@router.get("/county-records/{record_id}")
async def get_county_record(record_id: str):
    db = PostgresDatabase()
    try:
        await db.connect()
        record = await db.county_records.find_one({"id": record_id}, {"_id": 0})
        if record:
            return _display_record(record)
        uploaded = await db.properties.find_one({"id": record_id, "raw_import_row": {"$exists": True}}, {"_id": 0})
        if uploaded:
            return _display_uploaded(uploaded)
        raise HTTPException(status_code=404, detail="County record not found")
    finally:
        await db.close()


@router.post("/admin/county-records/sync")
async def sync_county_records(
    source: str = Query("all", pattern="^(all|tad|tax_roll|code_violations)$"),
    tad_records: int = Query(20000, ge=100, le=50000),
    code_records: Optional[int] = Query(None, ge=1, le=200000),
):
    db = PostgresDatabase()
    results: Dict[str, Any] = {}
    try:
        await db.connect()
        if source in {"all", "tad"}:
            results["tad"] = await sync_tad_county_records(db, records_per_run=tad_records)
        if source in {"all", "code_violations"}:
            from importers.fort_worth_code_violations import sync_fort_worth_code_violations
            results["code_violations"] = await sync_fort_worth_code_violations(db, limit=code_records)
    finally:
        await db.close()
    if source in {"all", "tax_roll"}:
        from importers.tax_roll_sync import run as run_tax_roll

        results["tax_roll"] = await run_tax_roll(argparse.Namespace(
            url=None, layout=None, max_records=None, force=False,
            apply=True, dry_run=False,
        ))
    return {"ok": True, "results": results}
