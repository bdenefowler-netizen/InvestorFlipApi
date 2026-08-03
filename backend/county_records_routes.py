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
            (item.get("has_tad"), "TAD"),
            (item.get("has_tax_roll"), "Tax Roll"),
        )
        if enabled
    ]
    item["market_value"] = item.get("market_value") or item.get("tax_roll_market_value")
    item.update(completeness(item))
    return item


def _source_query(source: str) -> Dict[str, Any]:
    if source == "tad":
        return {"has_tad": True}
    if source == "tax_roll":
        return {"has_tax_roll": True}
    if source == "tax_delinquent":
        return {"tax_delinquent": True}
    return {}


@router.get("/county-records/stats")
async def county_record_stats():
    db = PostgresDatabase()
    try:
        await db.connect()
        latest = await db.county_sync_log.find({}, {"_id": 0}).sort("created_at", -1).limit(8).to_list(length=8)
        cursor = await db.sync_log.find_one({"name": "county_tad_cursor"}) or {}
        return {
            "total": await db.county_records.count_documents({}),
            "with_tad": await db.county_records.count_documents({"has_tad": True}),
            "with_tax_roll": await db.county_records.count_documents({"has_tax_roll": True}),
            "tax_delinquent": await db.county_records.count_documents({"tax_delinquent": True}),
            "tad_next_offset": cursor.get("next_offset", 0),
            "tad_snapshot_completed_at": cursor.get("last_completed_snapshot"),
            "recent_syncs": latest,
        }
    finally:
        await db.close()


@router.get("/county-records")
async def list_county_records(
    source: str = Query("all", pattern="^(all|tad|tax_roll|tax_delinquent)$"),
    search: Optional[str] = Query(None, max_length=160),
    page: int = Query(1, ge=1),
    limit: int = Query(75, ge=1, le=200),
):
    db = PostgresDatabase()
    try:
        await db.connect()
        query = _source_query(source)
        if search and search.strip():
            regex = {"$regex": re.escape(search.strip()), "$options": "i"}
            search_query = {"$or": [
                {"situs_address": regex},
                {"owner_name": regex},
                {"owner_mailing_address": regex},
                {"account_id": regex},
                {"parcel_id": regex},
                {"zip": regex},
            ]}
            query = {"$and": [query, search_query]} if query else search_query
        total = await db.county_records.count_documents(query)
        docs = await (
            db.county_records.find(query, {"_id": 0})
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
            "items": [_display_record(item) for item in docs],
        }
    finally:
        await db.close()


@router.get("/county-records/export.csv")
async def export_county_records(source: str = Query("all", pattern="^(all|tad|tax_roll|tax_delinquent)$")):
    fields = [
        "account_id", "parcel_id", "situs_address", "city", "state", "zip",
        "owner_name", "owner_mailing_address", "mailing_city", "mailing_state", "mailing_zip",
        "beds", "baths", "sqft", "year_built", "lot_size_sqft", "appraised_value",
        "market_value", "tax_roll_market_value", "land_value", "improvement_value",
        "annual_taxes", "current_tax_amount_due", "prior_tax_amount_due", "tax_delinquent",
        "delinquency_date", "legal_description", "school_district", "deed_date",
        "absentee_owner", "out_of_state_owner", "trust_owned", "company_owned",
        "has_tad", "has_tax_roll", "tad_updated_at", "tax_roll_updated_at",
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
            "tad_raw": 1,
            "tax_roll_raw": 1,
        })
        try:
            await db.connect()
            while True:
                docs = await (
                    db.county_records.find(_source_query(source), projection)
                    .sort("situs_address", 1)
                    .skip(page * batch_size)
                    .limit(batch_size)
                    .to_list(length=batch_size)
                )
                if not docs:
                    break
                for item in docs:
                    display = _display_record(item)
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
        if not record:
            raise HTTPException(status_code=404, detail="County record not found")
        return _display_record(record)
    finally:
        await db.close()


@router.post("/admin/county-records/sync")
async def sync_county_records(
    source: str = Query("all", pattern="^(all|tad|tax_roll)$"),
    tad_records: int = Query(20000, ge=100, le=50000),
):
    db = PostgresDatabase()
    results: Dict[str, Any] = {}
    try:
        await db.connect()
        if source in {"all", "tad"}:
            results["tad"] = await sync_tad_county_records(db, records_per_run=tad_records)
    finally:
        await db.close()
    if source in {"all", "tax_roll"}:
        from importers.tax_roll_sync import run as run_tax_roll

        results["tax_roll"] = await run_tax_roll(argparse.Namespace(
            url=None, layout=None, max_records=None, force=False,
            apply=True, dry_run=False,
        ))
    return {"ok": True, "results": results}
