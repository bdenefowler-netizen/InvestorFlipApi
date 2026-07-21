"""Railway cron entry point for Fort Worth live-listing refreshes."""

from __future__ import annotations

import argparse
import asyncio
import json
import os

from database import PostgresDatabase
from server import sync_live_listings_to_database


async def run(limit: int) -> None:
    if os.environ.get("ENABLE_LIVE_LISTING_CRON", "false").lower() != "true":
        print(json.dumps({
            "ok": False,
            "skipped": True,
            "reason": "Set ENABLE_LIVE_LISTING_CRON=true after confirming the provider quota",
        }))
        return
    if not os.environ.get("RAPIDAPI_KEY", "").strip():
        print(json.dumps({
            "ok": False,
            "skipped": True,
            "reason": "RAPIDAPI_KEY is not configured for this Railway service",
        }))
        return

    database = PostgresDatabase()
    try:
        await database.connect()
        result = await sync_live_listings_to_database(database, limit=limit)
        summary = {key: value for key, value in result.items() if key != "items"}
        print(json.dumps(summary, indent=2, default=str))
    finally:
        await database.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Refresh current Fort Worth residential listings")
    parser.add_argument("--limit", type=int, default=50, choices=range(1, 101), metavar="1-100")
    return parser


if __name__ == "__main__":
    args = build_parser().parse_args()
    asyncio.run(run(args.limit))
