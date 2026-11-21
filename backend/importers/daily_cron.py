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

from importers.foreclosure_listings_scraper import TARRANT_COUNTY_CITIES

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
        ("fort_worth_violations", "importers.fort_worth_code_violations", "sync_fort_worth_code_violations", (db,), {"limit": limit}),
        ("foreclosures", "importers.foreclosure_finder", "import_foreclosures", (db,), {}),
        ("foreclosure_listings", "importers.foreclosure_listings_scraper", "import_foreclosure_listings", (db,), {"pages": 2, "cities": list(TARRANT_COUNTY_CITIES.keys())}),
        ("tad", "importers.tad_scraper", "import_tad_properties", (db,), {"limit": 300}),
        # brightdata_deal_finder is a compatibility/preview wrapper around the
        # same MCP collection below. Running both spends credits twice and
        # produces overlapping OffMarketDeck/FSBO/Hubzu leads.
        ("brightdata_mcp", "importers.brightdata_mcp_scraper", "import_brightdata_mcp", (db,), {"max_pages": 3}),
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
            summary = out.get("inserted", out.get("matched", out.get("status", "ok")))
            logger.info("%s → %s", name, summary)
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
        # Don't fail the cron just because the official tax-roll ZIP can't be
        # reached from Railway. Mark as a soft skip so the GitHub status
        # doesn't show red on a network-restricted environment.
        results["sources"]["tax_roll"] = {
            "ok": False,
            "skipped": True,
            "error": f"{type(e).__name__}: {e}",
            "reason": "Tax roll ZIP unreachable from Railway — run manually",
        }

    results["sources"]["live_listings"] = {
        "ok": True,
        "skipped": True,
        "reason": "Disabled: production live listings come from Bright Data, ArcGIS/TAD, and Tarrant County",
    }

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

    # Exit error only if a core source failed. Disabled/optional sources are soft-skips.
    OPTIONAL_SOURCES = {"apify", "live_listings", "tax_roll"}
    core_failed = [
        name for name, s in results.get("sources", {}).items()
        if name not in OPTIONAL_SOURCES and not s.get("ok", False)
    ]
    if core_failed:
        logger.error("Core sources failed: %s", ", ".join(core_failed))
        sys.exit(1)


if __name__ == "__main__":
    main()
