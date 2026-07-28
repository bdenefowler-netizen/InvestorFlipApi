"""Add all FREE data source routes to the API.

This module provides route definitions for:
- Fort Worth Code Violations (FREE)
- Tarrant County Foreclosures (FREE)
- ForeclosureListingsUSA (FREE)
- OffMarketDeck (FREE)
- TAD Property Data (FREE)
- New Western Marketplace (FREE scraping)
- Stessa Marketplace (FREE scraping)
- SmartPropLeads (FREE to browse)
- Free Skip Tracing (FREE)

NO SUBSCRIPTIONS REQUIRED - All data sources are free public APIs.

Include this in server.py:
    from add_all_routes import router as all_router
    app.include_router(all_router)
"""

from fastapi import APIRouter, HTTPException
from typing import Dict, Any, List, Optional

router = APIRouter(prefix="/api")


# ========== Fort Worth Violations (FREE) ==========

@router.post("/import/fort-worth-violations")
async def import_fort_worth_violations(limit: int = 2000):
    """Import distressed properties from Fort Worth Code Violations."""
    from database import PostgresDatabase
    from importers.fort_worth_violations import import_fort_worth_violations
    
    db = PostgresDatabase()
    try:
        await db.connect()
        result = await import_fort_worth_violations(db, limit=limit)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        await db.close()


# ========== Foreclosures (FREE) ==========

@router.post("/import/foreclosures")
async def import_foreclosures():
    """Import Tarrant County foreclosure records."""
    from database import PostgresDatabase
    from importers.foreclosure_finder import import_foreclosures
    
    db = PostgresDatabase()
    try:
        await db.connect()
        result = await import_foreclosures(db)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        await db.close()


# ========== ForeclosureListingsUSA (FREE) ==========

@router.post("/import/foreclosure-listings")
async def import_foreclosure_listings(city: str = "fort-worth", pages: int = 5):
    """Import foreclosure listings from ForeclosureListingsUSA."""
    from database import PostgresDatabase
    from importers.foreclosure_listings_scraper import import_foreclosure_listings
    
    db = PostgresDatabase()
    try:
        await db.connect()
        result = await import_foreclosure_listings(db, city=city, pages=pages)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        await db.close()


# ========== OffMarketDeck (FREE) ==========

@router.post("/import/offmarketdeck")
async def import_offmarketdeck(city: str = "fort-worth", pages: int = 3):
    """Import off-market deals from OffMarketDeck.
    
    Source: offmarketdeck.com (FREE, no login required)
    Includes: Wholesale, fix & flip, buy & hold deals
    """
    from database import PostgresDatabase
    from importers.offmarketdeck_scraper import import_offmarket_deals
    
    db = PostgresDatabase()
    try:
        await db.connect()
        result = await import_offmarket_deals(db, city=city, pages=pages)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        await db.close()


# ========== TAD Property Data (FREE) ==========

@router.post("/import/tad")
async def import_tad(city: str = "FORT WORTH", limit: int = 1000):
    """Import property data from Tarrant Appraisal District."""
    from database import PostgresDatabase
    from importers.tad_scraper import import_tad_properties
    
    db = PostgresDatabase()
    try:
        await db.connect()
        result = await import_tad_properties(db, city=city, limit=limit)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        await db.close()


@router.get("/tad/search")
async def search_tad(query: str, search_type: str = "address"):
    """Search TAD for property information."""
    from importers.tad_scraper import search_tad_by_address, search_tad_by_owner
    
    try:
        if search_type == "owner":
            results = await search_tad_by_owner(query)
        else:
            results = await search_tad_by_address(query)
        
        return {
            "count": len(results),
            "query": query,
            "search_type": search_type,
            "items": results[:50],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ========== New Western Marketplace (FREE scraping) ==========

@router.post("/import/new-western")
async def import_new_western(limit: int = 100):
    """Import wholesale properties from New Western Marketplace."""
    from database import PostgresDatabase
    from importers.new_western_scraper import import_new_western
    
    db = PostgresDatabase()
    try:
        await db.connect()
        result = await import_new_western(db, limit=limit)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        await db.close()


# ========== Stessa Marketplace (FREE scraping) ==========

@router.post("/import/stessa")
async def import_stessa(limit: int = 100):
    """Import investment properties from Stessa Marketplace."""
    from database import PostgresDatabase
    from importers.stessa_scraper import import_stessa
    
    db = PostgresDatabase()
    try:
        await db.connect()
        result = await import_stessa(db, limit=limit)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        await db.close()


# ========== SmartPropLeads (FREE to browse) ==========

@router.post("/import/smartpropleads")
async def import_smartpropleads(
    lead_types: Optional[List[str]] = None,
    limit: int = 100,
):
    """Import leads from SmartPropLeads."""
    from database import PostgresDatabase
    from importers.smartpropleads_scraper import import_smartpropleads
    
    db = PostgresDatabase()
    try:
        await db.connect()
        result = await import_smartpropleads(db, lead_types=lead_types, limit=limit)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        await db.close()


@router.get("/smartpropleads/lead-types")
async def get_smartpropleads_lead_types():
    """Get available lead types from SmartPropLeads."""
    from importers.smartpropleads_scraper import LEAD_TYPES
    return {"count": len(LEAD_TYPES), "items": LEAD_TYPES}


@router.get("/smartpropleads/counties")
async def get_smartpropleads_counties():
    """Get counties covered by SmartPropLeads."""
    from importers.smartpropleads_scraper import COUNTIES
    return {"count": len(COUNTIES), "items": COUNTIES}


# ========== Free Skip Tracing ==========

@router.get("/skip-trace")
async def skip_trace(address: str, owner_name: str = ""):
    """Perform free skip tracing using public data sources."""
    from importers.free_skip_trace import skip_trace_property
    
    try:
        result = await skip_trace_property(address, owner_name)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ========== Distressed Properties Query ==========

@router.get("/distressed-properties")
async def get_distressed_properties(filter_type: str = "all", limit: int = 100):
    """Get distressed properties with violations, foreclosures, and wholesale deals."""
    from database import PostgresDatabase
    
    db = PostgresDatabase()
    try:
        await db.connect()
        
        query: Dict[str, Any] = {}
        
        if filter_type == "violations":
            query["violation_count"] = {"$gt": 0}
        elif filter_type == "foreclosure":
            query["$or"] = [
                {"listing_type": "Foreclosure"},
                {"foreclosure": True},
            ]
        elif filter_type == "vacant":
            query["vacant"] = True
        elif filter_type == "nuisance":
            query["$or"] = [
                {"violation_types": "nuisance_abatement"},
                {"violation_types": "boarding_house"},
                {"violation_types": "substandard_structure"},
            ]
        elif filter_type == "wholesale":
            query["$or"] = [
                {"listing_type": "Wholesale"},
                {"wholesale": True},
            ]
        elif filter_type == "tax-delinquent":
            query["tax_delinquent"] = True
        elif filter_type == "pre-foreclosure":
            query["$or"] = [
                {"pre_foreclosure": True},
                {"listing_type": "Pre-Foreclosure"},
            ]
        elif filter_type == "absentee":
            query["absentee_owner"] = True
        elif filter_type == "fixer-upper":
            query["$or"] = [
                {"fixer_upper": True},
                {"listing_type": "Fix & Flip"},
            ]
        elif filter_type == "off-market":
            query["$or"] = [
                {"off_market": True},
                {"wholesale": True},
            ]
        
        # Exclude demo/synthetic records
        query["is_synthetic"] = {"$ne": True}
        
        properties = await db.properties.find(query).sort("distress_score", -1).limit(limit)
        
        return {
            "count": len(properties),
            "filter": filter_type,
            "items": properties,
        }
    finally:
        await db.close()


# ========== Import All (FREE sources only) ==========

@router.post("/import/all")
async def import_all_sources():
    """Import from all FREE data sources at once."""
    from database import PostgresDatabase
    from importers.fort_worth_violations import import_fort_worth_violations
    from importers.foreclosure_finder import import_foreclosures
    from importers.foreclosure_listings_scraper import import_foreclosure_listings
    from importers.offmarketdeck_scraper import import_offmarket_deals
    from importers.tad_scraper import import_tad_properties
    from importers.new_western_scraper import import_new_western
    from importers.stessa_scraper import import_stessa
    from importers.smartpropleads_scraper import import_smartpropleads
    
    db = PostgresDatabase()
    results = {}
    
    try:
        await db.connect()
        
        # Fort Worth Violations (FREE)
        try:
            results["fort_worth_violations"] = await import_fort_worth_violations(db)
        except Exception as e:
            results["fort_worth_violations"] = {"error": str(e)}
        
        # Foreclosures (FREE)
        try:
            results["foreclosures"] = await import_foreclosures(db)
        except Exception as e:
            results["foreclosures"] = {"error": str(e)}
        
        # ForeclosureListingsUSA (FREE)
        try:
            results["foreclosure_listings"] = await import_foreclosure_listings(db, pages=3)
        except Exception as e:
            results["foreclosure_listings"] = {"error": str(e)}
        
        # OffMarketDeck (FREE)
        try:
            results["offmarketdeck"] = await import_offmarket_deals(db, pages=2)
        except Exception as e:
            results["offmarketdeck"] = {"error": str(e)}
        
        # TAD (FREE)
        try:
            results["tad"] = await import_tad_properties(db, limit=500)
        except Exception as e:
            results["tad"] = {"error": str(e)}
        
        # New Western (FREE scraping)
        try:
            results["new_western"] = await import_new_western(db, limit=100)
        except Exception as e:
            results["new_western"] = {"error": str(e)}
        
        # Stessa (FREE scraping)
        try:
            results["stessa"] = await import_stessa(db, limit=100)
        except Exception as e:
            results["stessa"] = {"error": str(e)}
        
        # SmartPropLeads (FREE to browse)
        try:
            results["smartpropleads"] = await import_smartpropleads(db, limit=100)
        except Exception as e:
            results["smartpropleads"] = {"error": str(e)}
        
        return results
    finally:
        await db.close()


# ========== Status Check ==========

@router.get("/data-sources/status")
async def data_sources_status():
    """Check status of all FREE data sources."""
    import httpx
    
    status = {
        "fort_worth_violations": {"available": False, "cost": "FREE"},
        "tad": {"available": False, "cost": "FREE"},
        "foreclosure_listings": {"available": False, "cost": "FREE"},
        "offmarketdeck": {"available": False, "cost": "FREE"},
        "new_western": {"available": False, "cost": "FREE"},
        "stessa": {"available": False, "cost": "FREE"},
        "smartpropleads": {"available": False, "cost": "FREE"},
        "foreclosures": {"available": True, "cost": "FREE", "note": "CSV file included"},
        "realtor": {"available": False, "cost": "BLOCKED", "note": "Anti-scraping protections (429)"},
        "zillow": {"available": False, "cost": "BLOCKED", "note": "Anti-scraping protections (403)"},
        "redfin": {"available": False, "cost": "BLOCKED", "note": "Anti-scraping protections (403)"},
        "mashvisor": {"available": False, "cost": "SUBSCRIPTION REQUIRED", "note": "API starts at $129/month"},
        "tdrealtytx": {"available": False, "cost": "SUBSCRIPTION REQUIRED", "note": "Uses Lofty CRM with anti-scraping"},
    }
    
    # Check each source
    checks = [
        ("fort_worth_violations", "https://mapit.fortworthtexas.gov/ags/rest/services/CIVIC/Code_Violations_Experience_Builder/MapServer/4/query?where=1=1&outFields=Address&resultRecordCount=1&f=json"),
        ("tad", "https://services8.arcgis.com/5S5T6XdxjqI5BK2Y/arcgis/rest/services/TAD_Parcels_1/FeatureServer/0/query?where=1=1&outFields=TAXPIN&resultRecordCount=1&f=json"),
        ("foreclosure_listings", "https://www.foreclosurelistingsusa.com/fort-worth-tx/"),
        ("offmarketdeck", "https://offmarketdeck.com/texas/fort-worth"),
        ("new_western", "https://marketplace.newwestern.com/"),
        ("stessa", "https://www.stessa.com/investment-properties/"),
        ("smartpropleads", "https://smartpropleads.com/browse"),
    ]
    
    for key, url in checks:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    status[key]["available"] = True
        except Exception:
            pass
    
    return status
