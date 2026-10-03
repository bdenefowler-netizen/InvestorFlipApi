"""Background processor for rows accepted by the public workbook uploader.

The upload endpoint intentionally returns after durable staging.  This worker
finishes the contract: staged rows are normalized into Property Master, while
code-violation rows are also aggregated into County Records for the dedicated
workspace.
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import suppress
from typing import Any, Dict, Iterable, List, Mapping, Optional

from database import PostgresDatabase


logger = logging.getLogger("investorflip.import_worker")
_ADVISORY_LOCK_ID = 602_134_847
_POLL_SECONDS = 2.0
_PROPERTY_BATCH_SIZE = 500
_COUNTY_BATCH_SIZE = 1_000


def _decode(value: Any) -> Dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        decoded = json.loads(value)
        if isinstance(decoded, Mapping):
            return dict(decoded)
    raise ValueError("Staged import payload is not an object")


def _decode_categories(value: Any, fallback: str) -> List[str]:
    """Decode categories stored by either native JSONB or older text staging."""
    decoded = value
    if isinstance(decoded, str):
        try:
            decoded = json.loads(decoded)
        except (TypeError, ValueError, json.JSONDecodeError):
            decoded = [part.strip() for part in decoded.split("|") if part.strip()]
    if not isinstance(decoded, (list, tuple, set)):
        decoded = []
    categories = [str(item).strip() for item in decoded if str(item).strip()]
    return list(dict.fromkeys(categories)) or [fallback]


async def _next_batch(connection) -> Optional[Dict[str, Any]]:
    row = await connection.fetchrow(
        """
        SELECT batch_id, category, source_file, categories
        FROM import_staging
        WHERE status = 'staged'
        ORDER BY
            CASE category
                WHEN 'pre_foreclosure' THEN 0
                WHEN 'code_violations' THEN 1
                WHEN 'probate' THEN 2
                WHEN 'owner' THEN 3
                ELSE 4
            END,
            created_at,
            row_number
        LIMIT 1
        """
    )
    if not row:
        return None

    batch_id = str(row["batch_id"])
    records = await connection.fetch(
        """
        UPDATE import_staging
        SET status = 'processing'
        WHERE batch_id = $1 AND status = 'staged'
        RETURNING id, row_number, payload
        """,
        batch_id,
    )
    if not records:
        return None
    return {
        "batch_id": batch_id,
        "category": str(row["category"] or "uploaded"),
        "source_file": str(row["source_file"] or "upload"),
        "categories": _decode_categories(row["categories"], str(row["category"] or "uploaded")),
        "rows": [_decode(item["payload"]) for item in sorted(records, key=lambda item: item["row_number"])],
    }


def _code_records(rows: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    from importers.fort_worth_code_violations import SOURCE_NAME, _aggregate
    from importers.county_records import county_record_id

    records = _aggregate(rows)
    for record in records:
        record["id"] = county_record_id(None, record.get("situs_address"))
        record["has_uploaded"] = True
        record["upload_category"] = "code_violations"
        record["source_names"] = list(dict.fromkeys([*(record.get("source_names") or []), SOURCE_NAME, "User upload"]))

        keys: List[str] = []
        signals: List[str] = []
        if record.get("open_code_violation_count", 0) > 0:
            keys.append("code_violation")
            signals.append("Open code violation")
        if keys:
            record["opportunity_signal_keys"] = keys
            record["opportunity_signals"] = signals
            record["signal_sources"] = {key: [SOURCE_NAME] for key in keys}
    return [record for record in records if record.get("id")]


async def _process_batch(db: PostgresDatabase, batch: Mapping[str, Any]) -> Dict[str, Any]:
    from bulk_import import _tag_imported_properties
    from importers.county_records import upsert_county_records
    from intake_v3 import upsert_import_records

    category = str(batch.get("category") or "uploaded")
    categories = list(batch.get("categories") or [category])
    source_file = str(batch.get("source_file") or "upload")
    rows = list(batch.get("rows") or [])
    county_written = 0

    property_rows: List[Mapping[str, Any]] = rows
    if category == "code_violations":
        county_rows = _code_records(rows)
        for start in range(0, len(county_rows), _COUNTY_BATCH_SIZE):
            county_written += await upsert_county_records(
                db,
                county_rows[start:start + _COUNTY_BATCH_SIZE],
            )
        # One Property Master candidate per address, not one per violation.
        property_rows = [
            {
                **record,
                "address": record.get("situs_address"),
                "listing type": "Code Violation",
            }
            for record in county_rows
        ]

    report: Dict[str, Any] = {
        "rows_read": 0,
        "accepted": 0,
        "rejected": 0,
        "duplicates_merged": 0,
        "inserted": 0,
        "updated": 0,
        "matched_by": {},
        "unmatched": 0,
        "property_ids": [],
        "rejections": [],
    }
    for start in range(0, len(property_rows), _PROPERTY_BATCH_SIZE):
        partial = await upsert_import_records(
            db,
            property_rows[start:start + _PROPERTY_BATCH_SIZE],
            f"User upload [{category}]: {source_file}",
        )
        for key in ("rows_read", "accepted", "rejected", "duplicates_merged", "inserted", "updated", "unmatched"):
            report[key] += int(partial.get(key) or 0)
        for method, count in dict(partial.get("matched_by") or {}).items():
            report["matched_by"][method] = report["matched_by"].get(method, 0) + int(count or 0)
        report["rejections"].extend(list(partial.get("rejections") or []))
        property_ids = list(dict.fromkeys(partial.get("property_ids") or []))
        report["property_ids"].extend(property_ids)
        await _tag_imported_properties(db, property_ids, categories)
    report["property_ids"] = list(dict.fromkeys(report["property_ids"]))
    report["rejections"] = report["rejections"][:25]
    return {
        **report,
        "county_records_written": county_written,
        "category": category,
    }


async def _run_worker() -> None:
    from import_staging import ensure_import_staging

    db = PostgresDatabase()
    pool = await db.connect()
    await ensure_import_staging(db)
    async with pool.acquire() as connection:
        locked = await connection.fetchval("SELECT pg_try_advisory_lock($1)", _ADVISORY_LOCK_ID)
        if not locked:
            logger.info("Another import worker owns the advisory lock; waiting for handoff")
            while not locked:
                await asyncio.sleep(_POLL_SECONDS)
                locked = await connection.fetchval("SELECT pg_try_advisory_lock($1)", _ADVISORY_LOCK_ID)
            logger.info("Import worker acquired the advisory lock after handoff")
        try:
            # A restart may have interrupted a batch after it was claimed. All
            # writes are deterministic/upserts, so safely resume it.
            await connection.execute(
                "UPDATE import_staging SET status = 'staged' WHERE status = 'processing' AND processed_at IS NULL"
            )
            # Repair the brief deploy where a JSON text category was treated as
            # an iterable and tagged records with '[', '"', 'p', ... .
            await connection.execute(
                """
                UPDATE properties
                SET data = data || jsonb_build_object(
                    'upload_category', 'pre_foreclosure',
                    'upload_categories', jsonb_build_array('pre_foreclosure')
                ), updated_at = now()
                WHERE data ->> 'upload_category' = '['
                  AND data ->> 'source_category' = 'pre_foreclosure'
                """
            )
            while True:
                batch = await _next_batch(connection)
                if not batch:
                    await asyncio.sleep(_POLL_SECONDS)
                    continue
                try:
                    report = await _process_batch(db, batch)
                    await connection.execute(
                        """
                        UPDATE import_staging
                        SET status = 'processed', processed_at = now()
                        WHERE batch_id = $1 AND status = 'processing'
                        """,
                        batch["batch_id"],
                    )
                    summary = {key: value for key, value in report.items() if key != "property_ids"}
                    summary["property_count"] = len(report.get("property_ids") or [])
                    logger.info("Processed import batch %s: %s", batch["batch_id"], summary)
                except Exception:
                    logger.exception("Import batch %s failed", batch["batch_id"])
                    await connection.execute(
                        """
                        UPDATE import_staging
                        SET status = 'error', processed_at = now()
                        WHERE batch_id = $1 AND status = 'processing'
                        """,
                        batch["batch_id"],
                    )
        finally:
            with suppress(Exception):
                await connection.execute("SELECT pg_advisory_unlock($1)", _ADVISORY_LOCK_ID)
            await db.close()


def start_import_worker(app) -> None:
    """Register one lifecycle-managed worker on the FastAPI application."""

    @app.on_event("startup")
    async def _start() -> None:
        app.state.import_worker_task = asyncio.create_task(_run_worker())

    @app.on_event("shutdown")
    async def _stop() -> None:
        task = getattr(app.state, "import_worker_task", None)
        if task:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
