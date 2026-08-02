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
    # NOTE: always pass params as KEYWORDS — several importers take (db, city,
    # limit) or (db, lead_types, limit); positional args silently bind to the
    # wrong parameter (e.g. (db, 300) → city=300 → TAD fetches 0 every day).
    sources = [
        ("fort_worth_violations", "importers.fort_worth_violations", "import_fort_worth_violations", (db,), {"limit": limit}),
        ("foreclosures", "importers.foreclosure_finder", "import_foreclosures", (db,), {}),
        ("foreclosure_listings", "importers.foreclosure_listings_scraper", "import_foreclosure_listings", (db,), {"pages": 3}),
        ("tad", "importers.tad_scraper", "import_tad_properties", (db,), {"limit": 300}),
        ("apify", None, None, None, {}),  # handled separately below
    ]

    for name, module_path, func_name, args, kwargs in sources:
        logger.info("Importing %s...", name)
        try:
            mod = __import__(module_path, fromlist=[func_name])
            func = getattr(mod, func_name)
            out = await func(*args, **kwargs)
            results["sources"][name] = {
                "ok": "error" not in out,
                **out,
            }
            logger.info("%s → %s", name, out.get("inserted", "error"))
        except Exception as e:
            logger.error("%s failed: %s", name, traceback.format_exc())
            results["sources"][name] = {"ok": False, "error": f"{type(e).__name__}: {e}"}

    # ── Apify Import ──
    logger.info("Importing Apify actor runs...")
    try:
        from importers.apify_import import import_apify_runs
        apify_result = await import_apify_runs(db, lookback_days=7)
        results["sources"]["apify"] = {
            "ok": "error" not in apify_result,
            **apify_result,
        }
        logger.info("Apify → %d records imported from %d runs",
                     apify_result.get("records_imported", 0),
                     apify_result.get("runs_imported", 0))
    except Exception as e:
        logger.error("Apify failed: %s", traceback.format_exc())
        results["sources"]["apify"] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
    
    # ── Tarrant County Tax Roll (official delinquent-tax data) ──
    logger.info("Importing Tarrant County tax roll (official ZIP)...")
    try:
        import argparse
        from importers.tax_roll_sync import run as run_tax_roll
        tax_args = argparse.Namespace(
            url=None, layout=None, max_records=None, force=False,
            apply=True, dry_run=False,
        )
        tax_result = await run_tax_roll(tax_args)
        results["sources"]["tax_roll"] = {
            "ok": bool(tax_result.get("ok", False)),
            **tax_result,
        }
        logger.info("Tax roll → %s", tax_result.get("matches", tax_result))
    except Exception as e:
        logger.error("Tax roll failed: %s", traceback.format_exc())
        results["sources"]["tax_roll"] = {"ok": False, "error": f"{type(e).__name__}: {e}"}

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

    # Exit error if a CORE source failed. Apify is optional (free-plan account is
    # hard-blocked), so it must not turn every cron execution red.
    core_failed = [
        name for name, s in results.get("sources", {}).items()
        if name != "apify" and not s.get("ok", False)
    ]
    if core_failed:
        logger.error("Core sources failed: %s", ", ".join(core_failed))
        sys.exit(1)


if __name__ == "__main__":
    main()
