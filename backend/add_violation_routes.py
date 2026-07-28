"""Add Fort Worth violation, foreclosure, and wholesale routes to the API.

This module provides route definitions for:
- Fort Worth Code Violations (ArcGIS)
- Tarrant County Foreclosures
- New Western Marketplace wholesale deals
- BatchLeads API integration
"""

from fastapi import APIRouter, HTTPException
from typing import Dict, Any, Optional

router = APIRouter(prefix="/api")


# ========== Fort Worth Code Violations ==========

@router.post("/import/fort-worth-violations")
async def import_fort_worth_violations_endpoint(limit: int = 2000):
    """Import distressed properties from Fort Worth Code Violations.
    
    Pulls properties with active code violations (vacant structures,
    junk vehicles, overgrown vegetation, nuisance abatement).
    """
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


@router.get("/fort-worth-violations/status")
async def fort_worth_violations_status():
    """Check Fort Worth Code Violations API availability."""
    import httpx
    
    url = (
        "https://mapit.fortworthtexas.gov/ags/rest/services/"
        "CIVIC/Code_Violations_Experience_Builder/MapServer/4/query"
    )
    params = {"where": "1=1", "outFields": "Address", "resultRecordCount": "1", "f": "json"}
    
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            count = len(data.get("features", []))
            return {
                "available": True,
                "source": "Fort Worth ArcGIS",
                "sample_records": count,
                "url": url,
            }
    except Exception as e:
        return {"available": False, "error": str(e)}


# ========== Foreclosures ==========

@router.post("/import/foreclosures")
async def import_foreclosures_endpoint():
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


# ========== New Western Marketplace ==========

@router.post("/import/new-western")
async def import_new_western_endpoint(city: str = "Fort Worth", limit: int = 50):
    """Import wholesale deals from New Western Marketplace."""
    from database import PostgresDatabase
    from importers.new_western_scraper import fetch_new_western_deals, build_new_western_property
    
    db = PostgresDatabase()
    try:
        await db.connect()
        
        deals = await fetch_new_western_deals(city=city, limit=limit)
        
        inserted = 0
        matched = 0
        skipped = 0
        
        for deal in deals:
            doc = build_new_western_property(deal)
            address = doc.get("situs_address", "")
            
            existing = await db.properties.find_one({"situs_address": address})
            
            if existing:
                # Update with new Western data
                await db.properties.update_one(
                    {"id": existing["id"]},
                    {"$set": {
                        "listing_type": "Wholesale",
                        "data_source": existing.get("data_source", "") + " + New Western",
                        "updated_at": "now",
                    }},
                )
                matched += 1
            else:
                try:
                    await db.properties.insert_one(doc)
                    inserted += 1
                except Exception as e:
                    skipped += 1
        
        return {
            "fetched": len(deals),
            "inserted": inserted,
            "matched": matched,
            "skipped": skipped,
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        await db.close()


@router.get("/new-western/status")
async def new_western_status():
    """Check New Western Marketplace availability."""
    import httpx
    
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                "https://marketplace.newwestern.com",
                headers={"User-Agent": "Mozilla/5.0"},
                follow_redirects=True,
            )
            return {
                "available": response.status_code == 200,
                "source": "New Western Marketplace",
                "url": "https://marketplace.newwestern.com",
            }
    except Exception as e:
        return {"available": False, "error": str(e)}


# ========== BatchLeads API ==========

@router.get("/batchleads/status")
async def batchleads_status_endpoint():
    """Check BatchLeads API configuration."""
    from importers.batchleads_importer import batchleads_status
    return batchleads_status()


@router.post("/import/batchleads")
async def import_batchleads_endpoint(limit: int = 50):
    """Import properties from BatchLeads API.
    
    Requires BATCHLEADS_API_KEY environment variable.
    """
    from importers.batchleads_importer import search_properties, build_batchleads_property
    from database import PostgresDatabase
    
    db = PostgresDatabase()
    try:
        await db.connect()
        
        properties = await search_properties(limit=limit)
        
        inserted = 0
        matched = 0
        skipped = 0
        
        for raw in properties:
            doc = build_batchleads_property(raw)
            address = doc.get("situs_address", "")
            
            existing = await db.properties.find_one({"situs_address": address})
            
            if existing:
                await db.properties.update_one(
                    {"id": existing["id"]},
                    {"$set": {
                        "data_source": existing.get("data_source", "") + " + BatchLeads",
                        "updated_at": "now",
                    }},
                )
                matched += 1
            else:
                try:
                    await db.properties.insert_one(doc)
                    inserted += 1
                except Exception as e:
                    skipped += 1
        
        return {
            "fetched": len(properties),
            "inserted": inserted,
            "matched": matched,
            "skipped": skipped,
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        await db.close()


# ========== Distressed Properties ==========

@router.get("/distressed-properties")
async def get_distressed_properties(
    filter_type: str = "all",
    limit: int = 100,
):
    """Get distressed properties with code violations and foreclosure data.
    
    filter_type: all, violations, foreclosure, vacant, nuisance, wholesale
    """
    from database import PostgresDatabase
    
    db = PostgresDatabase()
    try:
        await db.connect()
        
        query: Dict[str, Any] = {}
        
        if filter_type == "violations":
            query["violation_count"] = {"$gt": 0}
        elif filter_type == "foreclosure":
            query["listing_type"] = "Foreclosure"
        elif filter_type == "vacant":
            query["vacant"] = True
        elif filter_type == "nuisance":
            query["$or"] = [
                {"violation_types": "nuisance_abatement"},
                {"violation_types": "boarding_house"},
                {"violation_types": "substandard_structure"},
            ]
        elif filter_type == "wholesale":
            query["listing_type"] = "Wholesale"
        
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
