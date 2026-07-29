"""Railway cron entry point — TAD tax roll only.

Runs daily to enrich properties with Tarrant Appraisal District data:
ownership, assessed values, property characteristics.

Usage:
    python importers/tax_roll_cron.py [--limit 300]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import traceback
from datetime import datetime, timezone

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("tax_roll_cron")


async def run(limit: int = 300) -> dict:
    """Run the TAD tax roll import with detailed logging."""
    from database import PostgresDatabase
    from importers.tad_scraper import import_tad_properties

    db = PostgresDatabase()
    result = {}

    try:
        logger.info("Connecting to database...")
        await db.connect()
        logger.info("Connected. Fetching TAD properties (limit=%d)...", limit)

        result = await import_tad_properties(db, limit=limit)

        inserted = result.get("inserted", 0)
        matched = result.get("matched", 0)
        skipped = result.get("skipped", 0)
        logger.info(
            "TAD import complete: %d inserted, %d matched, %d skipped",
            inserted, matched, skipped,
        )
    except Exception as e:
        error_msg = f"{type(e).__name__}: {e}"
        logger.error("TAD import failed: %s", error_msg)
        logger.error(traceback.format_exc())
        result = {"error": error_msg, "inserted": 0, "matched": 0}
    finally:
        try:
            await db.close()
            logger.info("Database connection closed.")
        except Exception:
            pass

    return result


def main():
    parser = argparse.ArgumentParser(description="TAD tax roll cron job")
    parser.add_argument("--limit", type=int, default=300, help="Max records")
    args = parser.parse_args()

    result = asyncio.run(run(args.limit))

    print(json.dumps(result, indent=2))

    if "error" in result:
        sys.exit(1)


if __name__ == "__main__":
    main()
