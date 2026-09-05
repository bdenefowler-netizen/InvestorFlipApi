"""
Zillow Enrichment API Routes — InvestorFlip V1

Endpoints:
  POST /api/enrich/property        — Enrich one property by address
  POST /api/enrich/preforeclosures  — Batch enrich preforeclosures missing estimates
  GET  /api/enrich/status          — Enrichment progress / stats
  GET  /api/enrich/test            — Test endpoint (no auth required)

Authentication: Admin key required (same as other /api/import/* routes)
"""

from fastapi import APIRouter, HTTPException, Query, Header
from typing import Optional
import asyncio
import logging
import os
from datetime import datetime, timezone
import hmac

logger = logging.getLogger("zillow_enrich")

router = APIRouter(prefix="/api/enrich", tags=["zillow-enrich"])


# ─── Auth ─────────────────────────────────────────────────────────────────────

def require_admin_key(x_admin_key: Optional[str] = Header(None)):
    """Check admin key header. Used as a dependency."""
    expected = os.environ.get("INVESTORFLIP_ADMIN_KEY", "").strip()
    if not expected:
        raise HTTPException(status_code=503, detail="Admin key not configured on server")
    provided = x_admin_key or ""
    if not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="Invalid or missing X-Admin-Key header")
    return True


# ─── Database singleton ──────────────────────────────────────────────────────

_db = None
def get_db():
    global _db
    if _db is None:
        from database import PostgresDatabase
        _db = PostgresDatabase()
    return _db


# ─── Routes ──────────────────────────────────────────────────────────────────

@router.get("/test")
async def test_endpoint():
    """Test endpoint - verify the enricher module is importable."""
    try:
        from importers.zillow_enricher import search_zillow_for_address
        return {
            "ok": True,
            "module": "zillow_enricher",
            "message": "Enricher module is loaded and importable",
            "brightdata_token_set": bool(os.environ.get("BRIGHTDATA_TOKEN", "").strip()),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.post("/property")
async def enrich_property(
    address: str = Query(..., description="Property address, e.g. '3915 Meadowbrook Dr'"),
    city: str = Query("Fort Worth"),
    state: str = Query("TX"),
    auth: bool = require_admin_key(0),  # requires header
):
    """
    Enrich a single property with Zillow data (Zestimate, sold price, tax).
    Searches Google via Bright Data MCP.

    Headers: X-Admin-Key: <your admin key>
    """
    from importers.zillow_enricher import enrich_property as do_enrich

    db = get_db()
    try:
        await db.connect()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"DB connect failed: {e}")

    try:
        result = await do_enrich(db, address, city, state)
        return result
    except Exception as e:
        logger.error("Enrich failed for %s: %s", address, e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/preforeclosures")
async def enrich_preforeclosures(
    limit: int = Query(50, ge=1, le=500),
    auth: bool = require_admin_key(0),
):
    """
    Batch enrich all preforeclosure / foreclosure records missing Zestimate.

    Headers: X-Admin-Key: <your admin key>
    """
    from importers.zillow_enricher import enrich_all_preforeclosures

    db = get_db()
    try:
        await db.connect()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"DB connect failed: {e}")

    logger.info("Starting batch enrichment (limit=%d)", limit)
    result = await enrich_all_preforeclosures(db, limit=limit)
    return result


@router.get("/status")
async def enrichment_status(
    auth: bool = require_admin_key(0),
):
    """
    Show how many preforeclosures have been enriched vs not.

    Headers: X-Admin-Key: <your admin key>
    """
    db = get_db()
    try:
        await db.connect()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"DB connect failed: {e}")

    total_preforeclosures = await db.properties.count_documents({"pre_foreclosure": True})
    enriched = await db.properties.count_documents({
        "pre_foreclosure": True,
        "estimated_value": {"$exists": True, "$ne": None},
    })
    with_zillow = await db.properties.count_documents({
        "pre_foreclosure": True,
        "zillow_enriched_at": {"$exists": True},
    })
    needs_enrichment = total_preforeclosures - enriched

    return {
        "total_preforeclosures": total_preforeclosures,
        "with_estimated_value": enriched,
        "enriched_via_zillow": with_zillow,
        "needs_enrichment": needs_enrichment,
        "enrichment_rate_pct": round((enriched / total_preforeclosures * 100) if total_preforeclosures else 0, 1),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
