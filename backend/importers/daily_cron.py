"""Daily cron — runs all FREE data imports with logging.

Runs via Railway cron every morning.
Reports success/failure for each source independently.

Usage:
    python importers/daily_cron.py [--limit 2000]
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
logger = logging.getLogger("daily_cron")


async def run_all(limit: int = 2000) -> dict:
    """Run all free imports and return structured results."""
    from database import PostgresDatabase

    db = PostgresDatabase()
    results = {"started_at": datetime.now(timezone.utc).isoformat(), "sources": {}}

    try:
        await db.connect()
        logger.info("Database connected.")
    except Exception as e:
        logger.error("Database connection failed: %s", e)
        results["error"] = f"DB_CONNECT: {e}"
        return results

    # ── Source definitions ──
    sources = [
        ("fort_worth_violations", "importers.fort_worth_violations", "import_fort_worth_violations", (db, limit)),
        ("foreclosures", "importers.foreclosure_finder", "import_foreclosures", (db,)),
        ("tad", "importers.tad_scraper", "import_tad_properties", (db, 300)),  # 300 = more reliable
    ]

    for name, module_path, func_name, args in sources:
        logger.info("Importing %s...", name)
        try:
            mod = __import__(module_path, fromlist=[func_name])
            func = getattr(mod, func_name)
            out = await func(*args)
            results["sources"][name] = {
                "ok": "error" not in out,
                **out,
            }
            logger.info("%s → %s", name, out.get("inserted", "error"))
        except Exception as e:
            logger.error("%s failed: %s", name, traceback.format_exc())
            results["sources"][name] = {"ok": False, "error": f"{type(e).__name__}: {e}"}

    # ── Summary ──
    results["finished_at"] = datetime.now(timezone.utc).isoformat()
    ok_count = sum(1 for s in results["sources"].values() if s.get("ok"))
    total = len(results["sources"])
    results["summary"] = f"{ok_count}/{total} sources OK"
    logger.info("Daily cron complete: %s", results["summary"])

    try:
        await db.close()
    except Exception:
        pass

    return results


def main():
    parser = argparse.ArgumentParser(description="Daily cron — all FREE imports")
    parser.add_argument("--limit", type=int, default=2000)
    args = parser.parse_args()

    results = asyncio.run(run_all(args.limit))
    print(json.dumps(results, indent=2, default=str))

    # Exit error if any source failed
    if any(not s.get("ok", False) for s in results.get("sources", {}).values()):
        sys.exit(1)


if __name__ == "__main__":
    main()
