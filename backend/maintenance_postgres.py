"""Report and safely reclaim InvestorFlip PostgreSQL storage.

The default invocation is read-only.  Destructive maintenance requires both
``--apply`` and the specific operation flag so a routine deploy cannot silently
remove source payloads or indexes.

Examples (from ``backend``):

    python maintenance_postgres.py
    python maintenance_postgres.py --apply --drop-county-gin
    python maintenance_postgres.py --apply --strip-county-raw --batch-size 250
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from typing import Any, Dict, List


SIZE_SQL = """
SELECT
    c.relname AS name,
    c.relkind AS kind,
    pg_total_relation_size(c.oid) AS total_bytes,
    pg_relation_size(c.oid) AS table_bytes,
    pg_indexes_size(c.oid) AS index_bytes
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public'
  AND c.relkind IN ('r', 'm')
ORDER BY pg_total_relation_size(c.oid) DESC
"""


async def storage_report(connection: Any) -> List[Dict[str, Any]]:
    rows = await connection.fetch(SIZE_SQL)
    return [dict(row) for row in rows]


async def strip_county_raw(connection: Any, batch_size: int) -> int:
    removed = 0
    while True:
        keys = await connection.fetch(
            """
            SELECT document_key
            FROM county_records
            WHERE data ?| ARRAY['tad_raw', 'tax_roll_raw']
            LIMIT $1
            """,
            batch_size,
        )
        if not keys:
            break
        values = [row["document_key"] for row in keys]
        status = await connection.execute(
            """
            UPDATE county_records
            SET data = data - 'tad_raw' - 'tax_roll_raw', updated_at = now()
            WHERE document_key = ANY($1::text[])
            """,
            values,
        )
        removed += int(status.rsplit(" ", 1)[-1])
    return removed


async def run(args: argparse.Namespace) -> Dict[str, Any]:
    try:
        import asyncpg
    except ImportError as exc:
        raise RuntimeError(
            "asyncpg is required; install backend/requirements.txt or run in Railway"
        ) from exc

    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("DATABASE_URL is required")
    connection = await asyncpg.connect(database_url)
    try:
        before = await storage_report(connection)
        result: Dict[str, Any] = {"ok": True, "applied": False, "before": before}
        requested = args.drop_county_gin or args.strip_county_raw
        if requested and not args.apply:
            result["warning"] = "Operations requested but not applied; add --apply after reviewing the report"
            return result
        if not requested:
            return result

        result["applied"] = True
        if args.drop_county_gin:
            await connection.execute('DROP INDEX IF EXISTS "county_records_data_gin"')
            result["county_gin_dropped"] = True
        if args.strip_county_raw:
            result["county_rows_stripped"] = await strip_county_raw(
                connection, max(1, args.batch_size)
            )
        await connection.execute("VACUUM (ANALYZE) county_records")
        result["after"] = await storage_report(connection)
        return result
    finally:
        await connection.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Report/reclaim InvestorFlip PostgreSQL storage")
    parser.add_argument("--apply", action="store_true", help="Actually run requested maintenance")
    parser.add_argument("--drop-county-gin", action="store_true", help="Drop the oversized full-document county GIN index")
    parser.add_argument("--strip-county-raw", action="store_true", help="Remove legacy embedded raw county payloads")
    parser.add_argument("--batch-size", type=int, default=250)
    return parser


if __name__ == "__main__":
    print(json.dumps(asyncio.run(run(build_parser().parse_args())), indent=2, default=str))
