"""One-time, non-destructive MongoDB to PostgreSQL data migration."""

from __future__ import annotations

import argparse
import asyncio
import os
from typing import Any, Dict, Iterable

from database import COLLECTION_KEYS, PostgresDatabase


async def migrate_collection(
    mongo_db: Any,
    postgres: PostgresDatabase,
    name: str,
    batch_size: int,
) -> Dict[str, int]:
    source = mongo_db[name]
    target = getattr(postgres, name)
    key_field = COLLECTION_KEYS[name]
    read = written = skipped = 0
    batch = []

    async for document in source.find({}).batch_size(batch_size):
        read += 1
        mongo_id = document.pop("_id", None)
        key = document.get(key_field)
        if key in (None, "") and mongo_id is not None:
            key = str(mongo_id)
        if key in (None, ""):
            skipped += 1
            continue
        document[key_field] = str(key)
        batch.append(document)
        if len(batch) >= batch_size:
            result = await target.upsert_many(batch)
            written += result.modified_count
            batch = []
            print(f"{name}: read {read:,}, written {written:,}, skipped {skipped:,}")

    if batch:
        result = await target.upsert_many(batch)
        written += result.modified_count

    return {"read": read, "written": written, "skipped": skipped}


async def run(collections: Iterable[str], batch_size: int) -> None:
    try:
        from motor.motor_asyncio import AsyncIOMotorClient
    except ImportError as exc:
        raise RuntimeError(
            "Mongo migration dependencies are missing; install requirements-migration.txt"
        ) from exc

    mongo_url = os.environ.get("MONGO_URL", "").strip()
    mongo_db_name = os.environ.get("DB_NAME", "tarrantrei").strip()
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not mongo_url:
        raise RuntimeError("MONGO_URL is required for the one-time source read")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required for the PostgreSQL destination")

    mongo_client = AsyncIOMotorClient(mongo_url)
    postgres = PostgresDatabase(database_url)
    try:
        mongo_db = mongo_client[mongo_db_name]
        available = set(await mongo_db.list_collection_names())
        requested = list(collections)
        unknown = set(requested) - set(COLLECTION_KEYS)
        if unknown:
            raise ValueError(f"Unsupported collections: {', '.join(sorted(unknown))}")

        await postgres.connect()
        print(f"Migrating MongoDB database {mongo_db_name!r} to PostgreSQL")
        totals: Dict[str, Dict[str, int]] = {}
        for name in requested:
            if name not in available:
                print(f"{name}: source collection not present; skipped")
                continue
            totals[name] = await migrate_collection(mongo_db, postgres, name, batch_size)

        print("Migration complete. MongoDB source data was not modified.")
        for name, result in totals.items():
            print(
                f"{name}: read {result['read']:,}, written {result['written']:,}, "
                f"skipped {result['skipped']:,}"
            )
    finally:
        mongo_client.close()
        await postgres.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Copy InvestorFlip MongoDB documents to PostgreSQL")
    parser.add_argument(
        "--collections",
        nargs="+",
        default=list(COLLECTION_KEYS),
        choices=list(COLLECTION_KEYS),
    )
    parser.add_argument("--batch-size", type=int, default=1000)
    return parser


if __name__ == "__main__":
    args = build_parser().parse_args()
    asyncio.run(run(args.collections, args.batch_size))
