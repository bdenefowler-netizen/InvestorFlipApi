"""Preview-first PostgreSQL storage reporting and conservative housekeeping."""

from __future__ import annotations

import logging
from typing import Any, Dict, Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from database import PostgresDatabase

logger = logging.getLogger("storage_housekeeping")
router = APIRouter(prefix="/api/admin/storage", tags=["admin-storage"])

CONFIRMATION_PHRASE = "DELETE SAFE DATABASE JUNK"


class CleanupRequest(BaseModel):
    action: Literal[
        "safe_housekeeping", "processed_staging", "old_sync_logs", "staging_batch"
    ] = "safe_housekeeping"
    retention_days: int = Field(default=7, ge=1, le=365)
    keep_log_rows: int = Field(default=100, ge=10, le=5000)
    batch_id: Optional[str] = Field(default=None, min_length=36, max_length=36)
    dry_run: bool = True
    confirmation: str = ""


def require_cleanup_confirmation(request: CleanupRequest) -> None:
    phrase = (
        f"DELETE STAGING BATCH {request.batch_id}"
        if request.action == "staging_batch"
        else CONFIRMATION_PHRASE
    )
    if request.action == "staging_batch" and not request.batch_id:
        raise HTTPException(status_code=422, detail="batch_id is required for staging_batch")
    if not request.dry_run and request.confirmation != phrase:
        raise HTTPException(
            status_code=409,
            detail=f"Set confirmation exactly to: {phrase}",
        )


async def _table_exists(connection, table: str) -> bool:
    return bool(await connection.fetchval("SELECT to_regclass($1)", f"public.{table}"))


async def build_storage_report(db: PostgresDatabase) -> Dict[str, Any]:
    pool = await db.connect()
    async with pool.acquire() as connection:
        database_bytes = int(await connection.fetchval("SELECT pg_database_size(current_database())") or 0)
        table_rows = await connection.fetch(
            """
            SELECT
                relname AS table_name,
                pg_total_relation_size(relid)::bigint AS total_bytes,
                pg_relation_size(relid)::bigint AS table_bytes,
                pg_indexes_size(relid)::bigint AS index_bytes,
                n_live_tup::bigint AS estimated_rows,
                n_dead_tup::bigint AS dead_rows
            FROM pg_stat_user_tables
            ORDER BY pg_total_relation_size(relid) DESC
            """
        )

        property_stats = await connection.fetchrow(
            """
            SELECT
                count(*)::bigint AS rows,
                COALESCE(sum(pg_column_size(data)), 0)::bigint AS json_bytes,
                count(*) FILTER (WHERE data ? 'raw_import_row')::bigint AS raw_import_rows,
                COALESCE(sum(pg_column_size(data -> 'raw_import_row'))
                    FILTER (WHERE data ? 'raw_import_row'), 0)::bigint AS raw_import_bytes,
                count(*) FILTER (WHERE data ? 'raw_source_excerpt')::bigint AS raw_source_rows,
                COALESCE(sum(pg_column_size(data -> 'raw_source_excerpt'))
                    FILTER (WHERE data ? 'raw_source_excerpt'), 0)::bigint AS raw_source_bytes,
                count(*) FILTER (
                    WHERE data @> '{"is_live_listing": true}'::jsonb
                    AND COALESCE(data ->> 'data_source', '') ~* 'user upload \\[tad\\]'
                    AND COALESCE(data ->> 'data_source', '') !~* '(rapidapi|openweb ninja|lgbs tax sales|foreclosure|fsbo|hubzu|offmarket)'
                )::bigint AS legacy_untrusted_live_flags
            FROM properties
            """
        )

        staging = []
        staging_sources = []
        staging_batches = []
        if await _table_exists(connection, "import_staging"):
            staging = await connection.fetch(
                """
                SELECT
                    status,
                    count(*)::bigint AS rows,
                    COALESCE(sum(pg_column_size(payload)), 0)::bigint AS payload_bytes,
                    min(created_at) AS oldest,
                    max(created_at) AS newest
                FROM import_staging
                GROUP BY status
                ORDER BY payload_bytes DESC
                """
            )
            staging_sources = await connection.fetch(
                """
                SELECT
                    source_file,
                    category,
                    status,
                    count(*)::bigint AS rows,
                    count(DISTINCT batch_id)::bigint AS batches,
                    COALESCE(sum(pg_column_size(payload)), 0)::bigint AS payload_bytes,
                    min(created_at) AS oldest,
                    max(created_at) AS newest
                FROM import_staging
                GROUP BY source_file, category, status
                ORDER BY payload_bytes DESC
                LIMIT 50
                """
            )
            staging_batches = await connection.fetch(
                """
                SELECT
                    batch_id,
                    source_file,
                    category,
                    status,
                    count(*)::bigint AS rows,
                    COALESCE(sum(pg_column_size(payload)), 0)::bigint AS payload_bytes,
                    min(created_at) AS created_at
                FROM import_staging
                GROUP BY batch_id, source_file, category, status
                ORDER BY payload_bytes DESC
                LIMIT 50
                """
            )

        return {
            "database_bytes": database_bytes,
            "tables": [dict(row) for row in table_rows],
            "properties": dict(property_stats or {}),
            "import_staging": [dict(row) for row in staging],
            "staging_sources": [dict(row) for row in staging_sources],
            "staging_batches": [dict(row) for row in staging_batches],
            "notes": [
                "Deleting rows makes space reusable inside PostgreSQL; it does not immediately shrink the Railway volume.",
                "VACUUM FULL can shrink files but requires an approved maintenance window because it locks and rewrites tables.",
            ],
        }


async def build_cleanup_preview(db: PostgresDatabase, request: CleanupRequest) -> Dict[str, Any]:
    pool = await db.connect()
    async with pool.acquire() as connection:
        staging = {"rows": 0, "payload_bytes": 0}
        if request.action in {"safe_housekeeping", "processed_staging"} and await _table_exists(connection, "import_staging"):
            row = await connection.fetchrow(
                """
                SELECT count(*)::bigint AS rows,
                       COALESCE(sum(pg_column_size(payload)), 0)::bigint AS payload_bytes
                FROM import_staging
                WHERE status IN ('processed', 'error')
                  AND COALESCE(processed_at, created_at) < now() - make_interval(days => $1)
                """,
                request.retention_days,
            )
            staging = dict(row or staging)

        batch = None
        if request.action == "staging_batch":
            if not request.batch_id:
                raise HTTPException(status_code=422, detail="batch_id is required for staging_batch")
            if not await _table_exists(connection, "import_staging"):
                raise HTTPException(status_code=404, detail="import_staging does not exist")
            rows = await connection.fetch(
                """
                SELECT source_file, category, status, count(*)::bigint AS rows,
                       COALESCE(sum(pg_column_size(payload)), 0)::bigint AS payload_bytes,
                       min(created_at) AS created_at
                FROM import_staging
                WHERE batch_id = $1
                GROUP BY source_file, category, status
                """,
                request.batch_id,
            )
            if not rows:
                raise HTTPException(status_code=404, detail="Staging batch not found")
            batch = {
                "batch_id": request.batch_id,
                "groups": [dict(row) for row in rows],
                "contains_processing_rows": any(row["status"] == "processing" for row in rows),
                "rows": sum(int(row["rows"]) for row in rows),
                "payload_bytes": sum(int(row["payload_bytes"]) for row in rows),
            }

        logs: Dict[str, int] = {}
        if request.action in {"safe_housekeeping", "old_sync_logs"}:
            for table in ("live_sync_log", "county_sync_log"):
                logs[table] = int(await connection.fetchval(
                    f"SELECT GREATEST(count(*) - $1, 0)::bigint FROM {table}",
                    request.keep_log_rows,
                ) or 0)

        return {
            "action": request.action,
            "dry_run": request.dry_run,
            "retention_days": request.retention_days,
            "keep_log_rows": request.keep_log_rows,
            "processed_staging": staging,
            "staging_batch": batch,
            "old_sync_logs": logs,
            "confirmation_required": (
                f"DELETE STAGING BATCH {request.batch_id}"
                if request.action == "staging_batch"
                else CONFIRMATION_PHRASE
            ),
            "properties_deleted": 0,
        }


async def execute_cleanup(db: PostgresDatabase, request: CleanupRequest) -> Dict[str, Any]:
    require_cleanup_confirmation(request)
    preview = await build_cleanup_preview(db, request)
    if request.dry_run:
        return preview

    pool = await db.connect()
    deleted_staging = 0
    deleted_logs: Dict[str, int] = {}
    async with pool.acquire() as connection:
        async with connection.transaction():
            if request.action == "staging_batch":
                assert request.batch_id is not None
                processing = int(await connection.fetchval(
                    "SELECT count(*) FROM import_staging WHERE batch_id = $1 AND status = 'processing'",
                    request.batch_id,
                ) or 0)
                if processing:
                    raise HTTPException(
                        status_code=409,
                        detail="This batch is currently processing and cannot be deleted",
                    )
                status = await connection.execute(
                    "DELETE FROM import_staging WHERE batch_id = $1",
                    request.batch_id,
                )
                deleted_staging = int(status.rsplit(" ", 1)[-1])

            if request.action in {"safe_housekeeping", "processed_staging"} and await _table_exists(connection, "import_staging"):
                status = await connection.execute(
                    """
                    DELETE FROM import_staging
                    WHERE status IN ('processed', 'error')
                      AND COALESCE(processed_at, created_at) < now() - make_interval(days => $1)
                    """,
                    request.retention_days,
                )
                deleted_staging = int(status.rsplit(" ", 1)[-1])

            if request.action in {"safe_housekeeping", "old_sync_logs"}:
                for table in ("live_sync_log", "county_sync_log"):
                    status = await connection.execute(
                        f"""
                        DELETE FROM {table}
                        WHERE document_key IN (
                            SELECT document_key FROM {table}
                            ORDER BY created_at DESC
                            OFFSET $1
                        )
                        """,
                        request.keep_log_rows,
                    )
                    deleted_logs[table] = int(status.rsplit(" ", 1)[-1])

        # Regular VACUUM is non-blocking and makes deleted space reusable by
        # PostgreSQL. It intentionally does not attempt VACUUM FULL.
        if deleted_staging and await _table_exists(connection, "import_staging"):
            await connection.execute("VACUUM (ANALYZE) import_staging")
        for table, count in deleted_logs.items():
            if count:
                await connection.execute(f"VACUUM (ANALYZE) {table}")

    return {
        **preview,
        "dry_run": False,
        "deleted": {
            "processed_staging": deleted_staging,
            "sync_logs": deleted_logs,
            "properties": 0,
        },
    }


@router.get("/report")
async def storage_report() -> Dict[str, Any]:
    db = PostgresDatabase()
    try:
        return await build_storage_report(db)
    finally:
        await db.close()


@router.post("/cleanup")
async def cleanup_storage(request: CleanupRequest) -> Dict[str, Any]:
    db = PostgresDatabase()
    try:
        return await execute_cleanup(db, request)
    finally:
        await db.close()


async def log_storage_summary(db: PostgresDatabase) -> None:
    try:
        report = await build_storage_report(db)
        largest = report.get("tables", [])[:5]
        logger.info(
            "Postgres storage: database_bytes=%s largest_tables=%s properties=%s staging=%s",
            report.get("database_bytes"),
            largest,
            report.get("properties"),
            report.get("import_staging"),
        )
        logger.info(
            "Postgres staging sources: %s; largest batches: %s",
            report.get("staging_sources", [])[:20],
            report.get("staging_batches", [])[:20],
        )
    except Exception:
        logger.exception("Could not collect PostgreSQL storage report")
