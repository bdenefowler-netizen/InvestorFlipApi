"""Fast staging persistence for public county uploads.

The HTTP upload path stores normalized rows here and returns immediately.
Property matching, owner classification, and enrichment happen later.
"""

from __future__ import annotations

import json
import math
import uuid
from datetime import date, datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping


_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS import_staging (
    id text PRIMARY KEY,
    batch_id text NOT NULL,
    source_file text NOT NULL,
    category text,
    categories jsonb NOT NULL,
    row_number integer NOT NULL,
    payload jsonb NOT NULL,
    status text NOT NULL DEFAULT 'staged',
    created_at timestamptz NOT NULL DEFAULT now(),
    processed_at timestamptz
)
"""

_CREATE_BATCH_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS import_staging_batch_idx
ON import_staging (batch_id, row_number)
"""

_CREATE_STATUS_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS import_staging_status_idx
ON import_staging (status, created_at)
"""

_INSERT_SQL = """
INSERT INTO import_staging (
    id,
    batch_id,
    source_file,
    category,
    categories,
    row_number,
    payload,
    status,
    created_at
)
VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7::jsonb, 'staged', $8)
"""


def _json_safe(value: Any) -> Any:
    """Convert workbook/pandas values to strict JSON-safe primitives."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]

    # numpy/pandas scalar values usually expose item().
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return _json_safe(item())
        except Exception:
            pass

    # pandas-style NaN/NA values.
    try:
        if value != value:
            return None
    except Exception:
        pass

    return str(value)


def _json_text(value: Any) -> str:
    return json.dumps(_json_safe(value), ensure_ascii=False, allow_nan=False)


async def ensure_import_staging(db) -> None:
    pool = await db.connect()
    async with pool.acquire() as conn:
        await conn.execute(_CREATE_SQL)
        await conn.execute(_CREATE_BATCH_INDEX_SQL)
        await conn.execute(_CREATE_STATUS_INDEX_SQL)


async def stage_import_rows(
    db,
    *,
    batch_id: str,
    source_file: str,
    categories: List[str],
    rows: Iterable[Dict[str, Any]],
) -> int:
    """Persist one parsed file in one batched database operation."""
    await ensure_import_staging(db)

    now = datetime.now(timezone.utc)
    primary = categories[0] if categories else "uploaded"
    categories_json = _json_text(list(categories))
    records = []
    for row_number, row in enumerate(rows, start=1):
        records.append(
            (
                str(uuid.uuid4()),
                batch_id,
                source_file,
                primary,
                categories_json,
                row_number,
                _json_text(dict(row)),
                now,
            )
        )

    if not records:
        return 0

    pool = await db.connect()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.executemany(_INSERT_SQL, records)
    return len(records)


async def batch_status(db, batch_id: str) -> Dict[str, Any]:
    """Report how many staged rows from one upload have been processed."""
    await ensure_import_staging(db)
    pool = await db.connect()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT
                count(*) AS total,
                count(*) FILTER (WHERE status = 'staged') AS staged,
                count(*) FILTER (WHERE status = 'processing') AS processing,
                count(*) FILTER (WHERE status = 'processed') AS processed,
                count(*) FILTER (WHERE status = 'error') AS errors,
                min(created_at) AS created_at,
                max(processed_at) AS processed_at
            FROM import_staging
            WHERE batch_id = $1
            """,
            batch_id,
        )
    if not row:
        return {"batch_id": batch_id, "found": False}
    total = int(row["total"] or 0)
    processed = int(row["processed"] or 0)
    errors = int(row["errors"] or 0)
    status = "staged"
    if total and processed + errors >= total:
        status = "error" if errors else "processed"
    elif processed or int(row["processing"] or 0):
        status = "processing"
    return {
        "batch_id": batch_id,
        "found": bool(total),
        "status": status,
        "total": total,
        "staged": int(row["staged"] or 0),
        "processing": int(row["processing"] or 0),
        "processed": processed,
        "errors": errors,
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        "processed_at": row["processed_at"].isoformat() if row["processed_at"] else None,
    }
