"""
Zillow + Redfin Enrichment Routes — InvestorFlip V1

Endpoints:
  POST /api/enrich/property         Enrich one property by address
  POST /api/enrich/preforeclosures  Batch enrich preforeclosures
  GET  /api/enrich/status          Enrichment stats
  GET  /api/enrich/test            Module health check (no auth)

Auth: X-Admin-Key header required on POST/GET /status
"""

from fastapi import APIRouter, HTTPException, Query, Header, Depends
from typing import Optional
import hmac, logging, os
from datetime import datetime, timezone

logger = logging.getLogger("zillow_enrich")
router = APIRouter(prefix="/api/enrich", tags=["enrichment"])


# ─── Auth ─────────────────────────────────────────────────────────────────────

ADMIN_KEY: str = ""

def _get_admin_key() -> str:
    global ADMIN_KEY
    if not ADMIN_KEY:
        ADMIN_KEY = os.environ.get("INVESTORFLIP_ADMIN_KEY", "").strip()
    return ADMIN_KEY


async def require_admin_key(x_admin_key: Optional[str] = Header(None)):
    """FastAPI dependency — validates X-Admin-Key."""
    expected = _get_admin_key()
    if not expected:
        raise HTTPException(503, "INVESTORFLIP_ADMIN_KEY not configured")
    if not hmac.compare_digest(x_admin_key or "", expected):
        raise HTTPException(401, "Invalid or missing X-Admin-Key header")


def get_db():
    """Lazy DB singleton."""
    from database import PostgresDatabase
    return PostgresDatabase()


# ─── Health check ─────────────────────────────────────────────────────────────

@router.get("/test")
async def test():
    """Module health check — no auth required."""
    try:
        from importers.zillow_enricher import ZillowRedfinEnricher
        return {
            "ok": True,
            "module": "zillow_enricher",
            "classes": ["ZillowRedfinEnricher"],
            "brightdata_token_set": bool(os.environ.get("BRIGHTDATA_TOKEN", "").strip()),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ─── Single property enrich ────────────────────────────────────────────────────

@router.post("/property")
async def enrich_property_ep(
    address: str = Query(..., description="Street address"),
    city: str = Query("Fort Worth"),
    state: str = Query("TX"),
    _auth: None = Depends(require_admin_key),
):
    """Enrich one property with Zillow + Redfin data via Bright Data MCP."""
    from importers.zillow_enricher import ZillowRedfinEnricher
    db = get_db()
    await db.connect()
    try:
        enricher = ZillowRedfinEnricher()
        result = await enricher.enrich(db, address, city, state)
        return result
    finally:
        await db.close()


# ─── Batch enrich ─────────────────────────────────────────────────────────────

@router.post("/preforeclosures")
async def enrich_batch_ep(
    limit: int = Query(50, ge=1, le=500),
    _auth: None = Depends(require_admin_key),
):
    """Batch enrich all preforeclosures missing estimated_value."""
    from importers.zillow_enricher import ZillowRedfinEnricher
    db = get_db()
    await db.connect()
    try:
        enricher = ZillowRedfinEnricher()
        result = await enricher.enrich_all_preforeclosures(db, limit=limit)
        return result
    finally:
        await db.close()


# ─── Status ───────────────────────────────────────────────────────────────────

@router.get("/status")
async def enrichment_status(_auth: None = Depends(require_admin_key)):
    """Show enrichment coverage across preforeclosures."""
    db = get_db()
    await db.connect()
    try:
        total = await db.properties.count_documents({"pre_foreclosure": True})
        with_est = await db.properties.count_documents({
            "pre_foreclosure": True,
            "estimated_value": {"$exists": True, "$ne": None},
        })
        enriched = await db.properties.count_documents({
            "pre_foreclosure": True,
            "enrichment_source": {"$exists": True},
        })
        return {
            "total_preforeclosures": total,
            "with_estimated_value": with_est,
            "enriched_via_scraper": enriched,
            "needs_enrichment": max(0, total - with_est),
            "enrichment_rate_pct": round((with_est / total * 100) if total else 0, 1),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    finally:
        await db.close()
