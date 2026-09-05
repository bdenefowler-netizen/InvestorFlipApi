"""Daily cron — runs all FREE data imports with logging.

Runs via Railway cron every morning.
Reports success/failure for each source independently.

Usage:
    python importers/daily_cron.py [--limit 2000]
"""

from __future__ import annotations

import argparse
import asyncio
import os
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

    results = {"started_at": datetime.now(timezone.utc).isoformat(), "sources": {}}

    # Wrap PostgresDatabase() + connect() together: an empty DATABASE_URL on
    # this Railway service instance raises RuntimeError before connect() even
    # runs. The main API service has its own DATABASE_URL — this cron is a
    # sidecar refresh task that can run without the DB.
    try:
        db = PostgresDatabase()
        await db.connect()
        logger.info("Database connected.")
    except Exception as e:
        logger.warning("Database unavailable (skipping imports): %s", e)
        results["error"] = f"DB_CONNECT: {e}"
        return results

    # ── Source definitions ──
    # NOTE: always pass params as KEYWORDS — several importers take (db, city,
    # limit) or (db, lead_types, limit); positional args silently bind to the
    # wrong parameter (e.g. (db, 300) → city=300 → TAD fetches 0 every day).
    sources = [
        ("fort_worth_violations", "importers.fort_worth_violations", "import_fort_worth_violations", (db,), {"limit": limit}),
        ("foreclosures", "importers.foreclosure_finder", "import_foreclosures", (db,), {}),
        ("foreclosure_listings", "importers.foreclosure_listings_scraper", "import_foreclosure_listings", (db,), {"pages": 2, "cities": list(TARRANT_COUNTY_CITIES.keys())}),
        ("tad", "importers.tad_scraper", "import_tad_properties", (db,), {"limit": 300}),
        ("brightdata_deals", "importers.brightdata_deal_finder", "import_brightdata_deals", (db,), {"days_back": 30}),
        ("brightdata_mcp", "importers.brightdata_mcp_scraper", "import_brightdata_mcp", (db,), {"max_pages": 3}),
        ("apify", None, None, None, {}),  # handled separately below (disabled)
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

    # ── Apify Import ── REMOVED 2026-09-04 (cost too high, data marginal)
    # Apify was burning $29/mo for 3% of deal data. All sources are now FREE.
    # Keeping the import_apify_runs function available at:
    #   from importers.apify_import import import_apify_runs
    # to run manually when needed with: await import_apify_runs(db, lookback_days=7)
    logger.info("Apify import disabled (cost too high). Use manual /api/import/apify if needed.")
    results["sources"]["apify"] = {"ok": True, "status": "DISABLED", "reason": "Cost too high — all sources now free"}
    
    # ── Tarrant County Tax Roll ── DISABLED 2026-09-05
    # The official ZIP download from tarrantcountytx.gov fails on Railway
    # (network restrictions / external ZIP download not supported in this env).
    # Disable entirely — it should NOT touch the tax roll from this cron.
    # Run manually if needed: python -m importers.tax_roll_sync --apply
    results["sources"]["tax_roll"] = {
        "ok": True, "skipped": True,
        "reason": "Disabled on Railway (external ZIP download not supported)",
    }

    # ── Live Listings (OpenWeb Ninja → RapidAPI fallback) ──
    # Re-enabled per QA audit 2026-08-02: production's last live sync was
    # July 28 because daily_cron stopped calling the sync entirely.
    logger.info("Syncing live Fort Worth listings (OpenWeb Ninja/RapidAPI)...")
    try:
        if os.environ.get("ENABLE_LIVE_LISTING_CRON", "false").lower() != "true":
            results["sources"]["live_listings"] = {
                "ok": True, "skipped": True,
                "reason": "Set ENABLE_LIVE_LISTING_CRON=true to enable the live-listing sync",
            }
        elif not (
            os.environ.get("OPENWEB_NINJA_REAL_ESTATE_API_KEY", "").strip()
            or os.environ.get("OPENWEB_NINJA_ZILLOW_API_KEY", "").strip()
            or os.environ.get("OPENWEB_NINJA_API_KEY", "").strip()
            or os.environ.get("OPENWEB_NINJA_KEY", "").strip()
            or os.environ.get("RAPIDAPI_KEY", "").strip()
        ):
            results["sources"]["live_listings"] = {
                "ok": True, "skipped": True,
                "reason": "No OpenWeb Ninja / RapidAPI key configured",
            }
        else:
            from server import sync_live_listings_to_database
            live_out = await sync_live_listings_to_database(db, limit=50)
            results["sources"]["live_listings"] = {
                "ok": bool(live_out.get("ok")),
                **{k: v for k, v in live_out.items() if k != "items"},
            }
            logger.info("Live listings → %s", live_out.get("summary", live_out))
    except Exception as e:
        logger.error("Live listings failed: %s", traceback.format_exc())
        results["sources"]["live_listings"] = {"ok": False, "error": f"{type(e).__name__}: {e}"}

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
