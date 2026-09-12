"""Read-only county-record table API plus protected sync controls."""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
from typing import Any, AsyncIterator, Dict, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from database import PostgresDatabase
from export_safety import spreadsheet_safe
from importers.county_records import completeness, sync_tad_county_records


router = APIRouter(prefix="/api")


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

        # Uploaded spreadsheets are user research inputs, so surface them in the
        # County workspace immediately even before a TAD/tax match exists.
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
        "has_code_violations", "code_violation_count", "open_code_violation_count",
        "code_latest_complaint", "code_latest_status", "code_oldest_open_date",
        "code_latest_update", "code_next_activity_due", "code_geocode_score",
        "code_case_ids", "code_violation_ids", "code_complaint_types",
        "code_substandard_building", "code_property_maintenance", "code_high_grass_weeds",
        "code_health_hazard", "code_solid_waste", "code_zoning", "code_vehicle", "code_multifamily",
        "has_tad", "has_tax_roll", "tad_updated_at", "tax_roll_updated_at", "code_violation_updated_at",
        "tad_raw_json", "tax_roll_raw_json",
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
            "Content-Disposition": f'attachment; filename="investorflip-{source}-county-records.csv"',
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
