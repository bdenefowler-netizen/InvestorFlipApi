"""Background enrichment queue for uploaded properties.

Uploads stay fast: they mark accepted Property Master rows as pending and return.
This worker then fills county identity plus provider details (owner, photos,
Zillow/Zestimate, beds/baths/sqft) after the browser is no longer waiting.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping

from database import PostgresDatabase


logger = logging.getLogger("investorflip.enrichment_worker")

_ADVISORY_LOCK_ID = 602_134_848
_POLL_SECONDS = 5.0
_BATCH_SIZE = 5
_CONCURRENCY = 3


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def enqueue_properties(
    db: PostgresDatabase, property_ids: Iterable[str],
) -> int:
    """Mark accepted upload rows for background enrichment without blocking."""
    ids = [property_id for property_id in dict.fromkeys(property_ids) if property_id]
    if not ids:
        return 0
    rows = await db.properties.find(
        {"id": {"$in": ids}},
        {"_id": 0, "id": 1, "enrichment_status": 1},
    ).to_list(length=len(ids))
    known = {str(item.get("id")): item.get("enrichment_status") for item in rows if item.get("id")}
    queued = 0
    for property_id in ids:
        if property_id not in known:
            continue
        if known[property_id] == "processing":
            continue
        await db.properties.update_one(
            {"id": property_id},
            {
                "$set": {
                    "enrichment_status": "pending",
                    "enrichment_requested_at": _now(),
                    "enrichment_error": None,
                }
            },
        )
        queued += 1
    return queued


async def _enrich_one(db: PostgresDatabase, property_id: str) -> Dict[str, Any]:
    from server import enrich_property_record

    try:
        prop = await db.properties.find_one({"id": property_id}, {"_id": 0}) or {}
        if not prop.get("tad_resolved_by"):
            from tad_identity import has_tad_identity, persist_identity, resolve_tad_identity

            if not has_tad_identity(prop):
                candidate = dict(prop)
                if await resolve_tad_identity(db, candidate):
                    await persist_identity(db, property_id, candidate)
        result = await enrich_property_record(db, property_id, require_visible=False)
        return {
            "property_id": property_id,
            "found": bool(result.get("found")),
            "error": None,
        }
    except Exception as exc:  # provider outages must not kill the worker
        logger.warning("Enrichment failed for %s: %s", property_id, exc)
        return {"property_id": property_id, "found": False, "error": str(exc)[:240]}


async def _pending_batch(db: PostgresDatabase) -> List[Mapping[str, Any]]:
    return await db.properties.find(
        {"enrichment_status": "pending"},
        {"_id": 0, "id": 1, "enrichment_attempts": 1},
    ).sort("enrichment_requested_at", 1).limit(_BATCH_SIZE).to_list(length=_BATCH_SIZE)


async def _run_worker() -> None:
    db = PostgresDatabase()
    pool = await db.connect()
    async with pool.acquire() as connection:
        locked = await connection.fetchval("SELECT pg_try_advisory_lock($1)", _ADVISORY_LOCK_ID)
        if not locked:
            logger.info("Another enrichment worker owns the advisory lock; waiting for handoff")
            while not locked:
                await asyncio.sleep(_POLL_SECONDS)
                locked = await connection.fetchval("SELECT pg_try_advisory_lock($1)", _ADVISORY_LOCK_ID)
            logger.info("Enrichment worker acquired the advisory lock after handoff")
        try:
            await connection.execute(
                """
                UPDATE properties
                SET data = data || jsonb_build_object('enrichment_status', 'pending')
                WHERE data ->> 'enrichment_status' = 'processing'
                """
            )
            semaphore = asyncio.Semaphore(_CONCURRENCY)
            while True:
                batch = await _pending_batch(db)
                if not batch:
                    await asyncio.sleep(_POLL_SECONDS)
                    continue

                async def run(item: Mapping[str, Any]) -> Dict[str, Any]:
                    property_id = str(item.get("id") or "")
                    attempts = int(item.get("enrichment_attempts") or 0)
                    await db.properties.update_one(
                        {"id": property_id},
                        {"$set": {
                            "enrichment_status": "processing",
                            "enrichment_started_at": _now(),
                            "enrichment_attempts": attempts + 1,
                        }},
                    )
                    async with semaphore:
                        result = await _enrich_one(db, property_id)
                    await db.properties.update_one(
                        {"id": property_id},
                        {"$set": {
                            "enrichment_status": "error" if result.get("error") else "done",
                            "enrichment_completed_at": _now(),
                            "enrichment_error": result.get("error"),
                        }},
                    )
                    return result

                results = await asyncio.gather(*(run(item) for item in batch))
                logger.info(
                    "Enriched %d uploaded properties (%d with provider details)",
                    len(results), sum(bool(item.get("found")) for item in results),
                )
        finally:
            with suppress(Exception):
                await connection.execute("SELECT pg_advisory_unlock($1)", _ADVISORY_LOCK_ID)
            await db.close()


def start_enrichment_worker(app) -> None:
    """Register one lifecycle-managed enrichment worker on the FastAPI app."""
    if getattr(app.state, "enrichment_worker_registered", False):
        return
    app.state.enrichment_worker_registered = True

    @app.on_event("startup")
    async def _start() -> None:
        app.state.enrichment_worker_task = asyncio.create_task(_run_worker())

    @app.on_event("shutdown")
    async def _stop() -> None:
        task = getattr(app.state, "enrichment_worker_task", None)
        if task:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
