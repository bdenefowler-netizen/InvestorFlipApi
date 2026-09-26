"""
Bulk imports plus the public high-capacity County workbook intake used by ADD.
"""

from __future__ import annotations

import csv
import io
import re
import uuid
import zipfile
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Mapping

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from database import PostgresDatabase

router = APIRouter(prefix="/import/bulk", tags=["buli-import"])

# The mobile ADD workflow must comfortably accept the county files the user
# works with. 300 MiB deliberately exceeds the requested 250 MiB minimum.
MAX_PUBLIC_UPLOAD_BYTES = 300 * 1024 * 1024
MAX_ZIP_EXPANDED_BYTES = 750 * 1024 * 1024


class BulkImportResult(BaseModel):
    batch_id: str
    total: int
    succeeded: int
    failed: int
    errors: List[Dict[str, str]]
    results: List[Dict[str, Any]]


@router.post("/mortgage-lookup", response_model=BulkImportResult)
async def bulk_mortgage_lookup(file: UploadFile = File(...)):
    """Upload a CSV with an address column and pull mortgage estimates."""
    content = await file.read()
    text = content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames or "address" not in reader.fieldnames:
        raise HTTPException(400, "CSV must have 'address' column")

    batch_id = str(uuid.uuid4())
    results: List[Dict[str, Any]] = []
    errors: List[Dict[str, str]] = []
    succeeded = failed = 0

    for row_num, row in enumerate(reader, start=2):
        address = row.get("address", "").strip()
        if not address:
            continue
        try:
            from mortgage_lookup import full_mortgage_report

            report = await full_mortgage_report(address)
            report["row"] = row_num
            report["notes"] = row.get("notes", "")
            results.append(report)
            succeeded += 1
        except Exception as exc:
            errors.append({"row": str(row_num), "address": address, "error": str(exc)})
            failed += 1

    return BulkImportResult(
        batch_id=batch_id,
        total=succeeded + failed,
        succeeded=succeed,
        failed=failed,
        errors=errors,
        results=results,
    )


@router.post("/properties")
async def bulk_import_properties(
    file: UploadFile = File(...),
    source: str = Form("csv_upload"),
):
    """Legacy simple CSV property importer."""
    content = await file.read()
    text = content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames or "address" not in reader.fieldnames:
        raise HTTPException(400, "CSV must have 'address' column")

    db = PostgresDatabase()
    try:
        await db.connect()
        imported = 0
        errors: List[Dict[str, str]] = []
        for row_num, row in enumerate(reader, start=2):
            address = row.get("address", "").strip()
            if not address:
                continue
            try:
                doc = {
                    "id": str(uuid.uuid4()),
                    "address": address,
                    "city": row.get("city", ""),
                    "state": row.get("state", "TX"),
                    "zip": row.get("zip", ""),
                    "price": float(row["price"]) if row.get("price") else None,
                    "beds": int(float(row["beds"])) if row.get("beds") else None,
                    "baths": float(row["baths"]) if row.get("baths") else None,
                    "sqft": float(row["sqft"]) if row.get("sqft") else None,
                    "notes": row.get("notes", ""),
                    "source": source,
                    "imported_at": datetime.now(timezone.utc).isoformat(),
                    "is_synthetic": False,
                }
                await db.properties.insert_one(doc)
                imported += 1
            except Exception as exc:
                errors.append({"row": str(row_num), "error": str(exc)})
        return {"imported": imported, "errors": errors, "source": source}
    finally:
        await db.close()


def _key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").strip().lower()).strip()


def _row_lookup(row: Mapping[str, Any], *names: str) -> Any:
    wanted = {_key(name) for name in names}
    for column, value in row.items():
        if _key(column) in wanted and value not in (None, ""):
            try:
                if value != value:  # pandas NaN
                    continue
            except Exception:
                pass
            return value
    return None


def _upload_categories(filename: str, rows: List[Dict[str, Any]]) -> List[str]:
    name = _key(filename)
    headers = {_key(k) for row in rows[:20] for k in row.keys()}

    if "probate" in name or (
        any("decedent" in h for h in headers) and any("applicant" in h for h in headers)
    ):
        return ["probate"]

    if (
        "preforeclosure" in name
        or "pre foreclosure" in name
        or "county cause number" in headers
        or "scheduled auction" in headers
        or "foreclosure status" in headers
    ):
        return ["pre_foreclosure"]

    if (
        ("code" in name and "violation" in name)
        or "violation address" in headers
        or "complaint type description" in headers
    ):
        return ["code_violations"]

    if "tad" in name and "taxroll" not in name and "tax roll" not in name:
        return ["tad"]

    tax_signals = {
        "tax account number",
        "current year amount due",
        "prior year amount due",
        "delinquency date",
        "adjusted levy",
        "appraised value",
    }
    if "taxroll" in name or "tax roll" in name or len(headers & tax_signals) >= 2:
        categories = ["tax_roll"]
        if headers & {"current year amount due", "prior year amount due", "amount due", "delinquency date"}:
            categories.append("tax_due")
        return categories

    return ["uploaded"]


def _prepare_row(row: Mapping[str, Any], categories: List[str]) -> Dict[str, Any]:
    """Adapt source-specific headers to the canonical intake shape without losing originals."""
    out = dict(row)
    category = categories[0] if categories else "uploaded"

    if category == "probate":
        # Required project rule: Probate always pulls TAD by DECEDENT property address.
        address = _row_lookup(
            row,
            "Decedent Property Address",
            "Decedent Address",
        )
        if address:
            out["address"] = address
        decedent = _row_lookup(row, "Decedent")
        if decedent and not _row_lookup(row, "owner", "owner name", "current owner"):
            out["owner"] = decedent
        out["listing type"] = "Probate"

    elif category == "code_violations":
        address = _row_lookup(row, "Violation Address", "Property Address", "Address")
        if address:
            out["address"] = address
        out["listing type"] = "Code Violation"

    elif category == "pre_foreclosure":
        address = _row_lookup(
            row,
            "Property Address",
            "Matched Address",
            "Situs Address",
            "Address",
        )
        if address:
            out["address"] = address
        out["listing type"] = "Pre-Foreclosure"

    elif category in {"tax_roll", "tax_due"}:
        address = _row_lookup(
            row,
            "Street Name (Property Location)",
            "Property Location",
            "Property Address",
            "Situs Address",
            "Address",
        )
        if address:
            out["address"] = address

    elif category == "tad":
        address = _row_lookup(
            row,
            "Property Address",
            "Situs Address",
            "Address",
            "Location Address",
        )
        if address:
            out["address"] = address

    # Preserve separate matching identifiers even though legacy intake still has
    # its own canonical fields.
    tad = _row_lookup(
        row,
        "TAD Account #",
        "TAD Account Number",
        "TAD Account",
        "Appraisal District Number",
    )
    apn = _row_lookup(
        row,
        "Tax ID/APN",
        "Tax Account/APN",
        "Tax Account APN",
        "APN",
        "Parcel ID",
        "PIN",
    )
    legal = _row_lookup(
        row,
        "Legal Description",
        "Legal Description 1",
        "Property/Legal Description",
    )
    cause = _row_lookup(
        row,
        "County Cause Number",
        "Cause Number",
        "Cause No",
        "Cause #",
    )
    if tad is not None:
        out["tad account #"] = tad
        out.setdefault("account id", tad)
    if apn is not None:
        out["apn"] = apn
    if legal is not None:
        out["legal description"] = legal
    if cause is not None:
        out["cause number"] = cause
    out["upload categories"] = " | ".join(categories)
    return out


async def _tag_imported_properties(db, ids: List[str], categories: List[str]) -> None:
    if not ids:
        return
    primary = categories[0] if categories else "uploaded"
    fields: Dict[str, Any] = {
        "has_uploaded": True,
        "upload_category": primary,
        "upload_categories": categories,
    }
    if primary == "pre_foreclosure":
        fields.update(pre_foreclosure=True, has_pre_foreclosure=True)
    elif primary == "probate":
        fields["has_probate"] = True
    elif primary == "code_violations":
        fields["has_uploaded_code_violations"] = True
    elif primary == "tad":
        fields["has_uploaded_tad"] = True
    if "tax_roll" in categories:
        fields["has_uploaded_tax_roll"] = True
    if "tax_due" in categories:
        fields["has_uploaded_tax_due"] = True

    for property_id in ids:
        await db.properties.update_one({"id": property_id}, {"$set": fields})


@router.post("/upload-workbook")
async def public_county_workbook_upload(file: UploadFile = File(...)):
    """Public ADD upload: parse -> deterministic match/store -> return.

    No admin key is required. Live County/TAD/API enrichment is deliberately
    excluded from this request path so large uploads cannot sit open for
    minutes waiting on network work.
    """
    from county_records_routes import _read_upload_rows
    from intake_v3 import upsert_import_records

    filename = Path(file.filename or "upload.xlsx").name
    suffix = Path(filename).suffix.lower()
    if suffix not in {".csv", ".xls", ".xlsx", ".zip"}:
        raise HTTPException(400, "Upload .csv, .xls, .xlsx, or .zip")

    raw = await file.read(MAX_PUBLIC_UPLOAD_BYTES + 1)
    if not raw:
        raise HTTPException(400, "The uploaded file is empty")
    if len(raw) > MAX_PUBLIC_UPLOAD_BYTES:
        raise HTTPException(413, "The upload is larger than 300 MiB")

    db = PostgresDatabase()
    await db.connect()
    try:
        ids: List[str] = []
        reports: List[Dict[str, Any]] = []
        seen: List[str] = []
        totals = {"accepted": 0, "rejected": 0, "inserted": 0, "updated": 0}

        async def import_one(name: str, payload: bytes, ext: str) -> None:
            rows, sheets = _read_upload_rows(payload, ext, name)
            categories = _upload_categories(name, rows)
            prepared = [_prepare_row(row, categories) for row in rows]
            rep = await upsert_import_records(
                db,
                prepared,
                f"User upload [{'+'.join(categories)}]: {name}",
            )
            property_ids = list(dict.fromkeys(rep["property_ids"]))
            ids.extend(property_ids)
            await _tag_imported_properties(db, property_ids, categories)
            for category in categories:
                if category not in seen:
                    seen.append(category)
            for key in totals:
                totals[key] += int(rep.get(key, 0) or 0)
            reports.append(
                {
                    "file": name,
                    "status": "ok",
                    "categories": categories,
                    "rows": rep["rows_read"],
                    "accepted": rep["accepted"],
                    "rejected": rep["rejected"],
                    "inserted": rep["inserted"],
                    "updated": rep["updated"],
                    "sheets": sheets,
                }
            )

        if suffix == ".zip":
            try:
                with zipfile.ZipFile(BytesIO(raw)) as zf:
                    members = [
                        member
                        for member in zf.infolist()
                        if not member.is_dir()
                        and Path(member.filename).suffix.lower() in {".csv", ".xls", ".xlsx"}
                    ]
                    if not members:
                        raise HTTPException(400, "ZIP contains no CSV or Excel files")
                    if sum(max(0, member.file_size) for member in members) > MAX_ZIP_EXPANDED_BYTES:
                        raise HTTPException(413, "ZIP expands beyond the 750 MB safety limit")
                    for member in members:
                        try:
                            await import_one(
                                member.filename,
                                zf.read(member),
                                Path(member.filename).suffix.lower(),
                            )
                        except Exception as exc:
                            reports.append({"file": member.filename, "status": "error", "reason": str(exc)[:300]})
            except HTTPException:
                raise
            except Exception as exc:
                raise HTTPException(400, f"Could not read ZIP: {str(exc)[:220]}") from exc
        else:
            try:
                await import_one(filename, raw, suffix)
            except Exception as exc:
                raise HTTPException(
                    400,
                    f"Could not import workbook: {str(exc)[:220]}",
                ) from exc

        unique = list(dict.fromkeys(ids))
        return {
            "ok": bool(totals["accepted"]),
            "filename": filename,
            "categories": seen,
            "files": reports,
            "rows_read": totals["accepted"] + totals["rejected"],
            **totals,
            "property_ids": unique,
            "enrichment": {
                "county": {
                    "live_checked": 0,
                    "enriched": 0,
                    "tad_lookups": 0,
                    "missing": 0,
                    "deferred": True,
                }
            },
        }
    finally:
        await db.close()
