"""
Auto-Sync Scheduler - Automatically refreshes TAD/tax roll and other FREE data sources.
No CSV uploads needed - pulls directly from public APIs on a schedule.
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from database import PostgresDatabase

logger = logging.getLogger("auto_sync")

# How often to refresh each source
REFRESH_INTERVALS = {
    "tad": timedelta(days=7),           # TAD data changes slowly
    "fort_worth_violations": timedelta(days=3),  # Violations updated frequently
    "foreclosures": timedelta(days=1),  # Foreclosure sales change daily
    "foreclosure_listings": timedelta(days=3),
    "offmarketdeck": timedelta(days=3),
    "smartpropleads": timedelta(days=7),
}


async def get_last_sync(name: str) -> Optional[datetime]:
    """Get the last sync time for a data source."""
    db = PostgresDatabase()
    try:
        await db.connect()
        conn = await db._pool()
        row = await conn.fetchrow(
            "SELECT data FROM sync_log WHERE name = $1", name
        )
        if row:
            last = row["data"].get("last_sync_at")
            if last:
                return datetime.fromisoformat(last)
        return None
    except Exception:
        return None
    finally:
        await db.close()


async def set_last_sync(name: str):
    """Record when a sync completed."""
    db = PostgresDatabase()
    try:
        await db.connect()
        conn = await db._pool()
        await conn.execute("""
            INSERT INTO sync_log (name, data) VALUES ($1, $2::jsonb)
            ON CONFLICT (name) DO UPDATE SET data = $2::jsonb, updated_at = now()
        """, name, {
            "name": name,
            "last_sync_at": datetime.now(timezone.utc).isoformat(),
            "status": "success"
        })
    finally:
        await db.close()


async def sync_source(name: str, import_func, **kwargs):
    """Run a single source sync if it's due."""
    last = await get_last_sync(name)
    interval = REFRESH_INTERVALS.get(name, timedelta(days=7))
    
    if last and (datetime.now(timezone.utc) - last) < interval:
        logger.info(f"⏭️  {name} - synced recently, skipping")
        return {"source": name, "skipped": True, "last_sync": last.isoformat()}
    
    logger.info(f"🔄 {name} - starting sync")
    try:
        result = await import_func(**kwargs)
        await set_last_sync(name)
        logger.info(f"✅ {name} - sync complete")
        return {"source": name, "status": "success", "result": result}
    except Exception as e:
        logger.error(f"❌ {name} - sync failed: {e}")
        return {"source": name, "status": "failed", "error": str(e)}


async def sync_all_sources():
    """Sync all data sources that are due for refresh."""
    from importers.tad_scraper import import_tad_properties
    from importers.fort_worth_violations import import_fort_worth_violations
    from importers.foreclosure_listings_scraper import import_foreclosure_listings
    from importers.offmarketdeck_scraper import import_offmarket_deals
    from importers.smartpropleads_scraper import import_smartpropleads
    
    results = []
    
    # TAD tax roll - auto pulls from ArcGIS API
    results.append(await sync_source("tad", import_tad_properties, city="FORT WORTH", limit=500))
    
    # Fort Worth Code Violations
    results.append(await sync_source("fort_worth_violations", import_fort_worth_violations, limit=500))
    
    # Foreclosure listings
    results.append(await sync_source("foreclosure_listings", import_foreclosure_listings, city="fort-worth", pages=3))
    
    # Off-market deals
    results.append(await sync_source("offmarketdeck", import_offmarketdeck, city="fort-worth", pages=2))
    
    return results


async def background_sync_loop(interval_hours: int = 12):
    """
    Background loop that runs on server startup.
    Checks every `interval_hours` if sources need refreshing.
    """
    logger.info(f"🔁 Auto-sync started (checking every {interval_hours}h)")
    while True:
        try:
            await sync_all_sources()
        except Exception as e:
            logger.error(f"Auto-sync error: {e}")
        await asyncio.sleep(interval_hours * 3600)


def start_background_sync(app, interval_hours: int = 12):
    """Call this on server startup to begin auto-syncing."""
    @app.on_event("startup")
    async def startup_sync():
        # Initial sync on startup
        asyncio.create_task(background_sync_loop(interval_hours))
        logger.info("📡 Background auto-sync started")
