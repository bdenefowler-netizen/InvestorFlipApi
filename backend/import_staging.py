"""Fast staging persistence for public county uploads.

The HTTP upload path stores normalized rows here and returns immediately.
Property matching, owner classification, and enrichment happen later.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List


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
    records = []
    for row_number, row in enumerate(rows, start=1):
        records.append(
            (
                str(uuid.uuid4()),
                batch_id,
                source_file,
                primary,
                list(categories),
                row_number,
                dict(row),
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
