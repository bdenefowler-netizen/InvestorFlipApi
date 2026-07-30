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

import json
import os
from fastapi import APIRouter, HTTPException, Body
from typing import Dict, Any, List, Optional

import httpx

router = APIRouter(prefix="/api")

from saved_searches_routes import router as saved_searches_router
from bulk_import import router as bulk_import_router
router.include_router(saved_searches_router, prefix="/saved-searches")
router.include_router(bulk_import_router)

# ========== Mortgage & Deed Lookup (FREE) ==========

@router.get("/mortgage-lookup")
async def mortgage_lookup(address: str):
    """Free mortgage/equity estimate using TAD deed records + amortization."""
    from mortgage_lookup import full_mortgage_report
    return await full_mortgage_report(address)


@router.post("/mortgage-lookup")
async def mortgage_lookup_post(address: str = ""):
    """Free mortgage/equity estimate (POST version)."""
    from mortgage_lookup import full_mortgage_report
    return await full_mortgage_report(address)



# ========== Apify Scraper Integration ==========

@router.post("/import/apify")
async def import_from_apify(
    actor_id: str = Body(..., description="Apify Actor ID (e.g. 'nF7qJ5wQdQx9bY3uL' for Zillow)"),
    run_input: Dict[str, Any] = Body(default={}, description="Input payload for the Apify actor"),
    wait_for_finish: int = Body(default=120, description="Max seconds to wait for run to complete"),
):
    """Run an Apify scraper and pipe results into InvestorFlip properties."""
    api_key = os.environ.get("APIFY_API_KEY", "").strip()
    if not api_key:
        raise HTTPException(400, "APIFY_API_KEY not configured — add it to Railway env vars")
    
    # Start the Apify actor run
    async with httpx.AsyncClient(timeout=30.0) as client:
        start_resp = await client.post(
            f"https://api.apify.com/v2/acts/{actor_id}/runs",
            params={"token": api_key},
            json={"runInput": run_input},
        )
        if start_resp.status_code not in (200, 201):
            raise HTTPException(502, f"Apify start failed: {start_resp.status_code} {start_resp.text[:300]}")
        
        run_data = start_resp.json().get("data", {})
        run_id = run_data.get("id")
        if not run_id:
            raise HTTPException(502, "Apify returned no run ID")
    
    # Poll until finished or timeout
    import asyncio
    poll_interval = 5
    elapsed = 0
    while elapsed < wait_for_finish:
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval
        
        async with httpx.AsyncClient(timeout=15.0) as client:
            status_resp = await client.get(
                f"https://api.apify.com/v2/actor-runs/{run_id}",
                params={"token": api_key},
            )
            if status_resp.status_code != 200:
                continue
            
            status_data = status_resp.json().get("data", {})
            run_status = status_data.get("status")
            
            if run_status == "SUCCEEDED":
                # Fetch results from default dataset
                dataset_id = status_data.get("defaultDatasetId")
                if not dataset_id:
                    return {"status": "finished", "records": 0, "error": "No dataset ID"}
                
                dataset_resp = await client.get(
                    f"https://api.apify.com/v2/datasets/{dataset_id}/items",
                    params={"token": api_key, "format": "json", "limit": 5000},
                )
                if dataset_resp.status_code != 200:
                    return {"status": "finished", "records": 0, "error": "Failed to fetch dataset"}
                
                records = dataset_resp.json()
                
                # Import records into properties database
                from backend.database import get_database
                db = await get_database()
                
                imported = 0
                for record in records:
                    try:
                        # Normalize address
                        addr_parts = []
                        for addr_key in ["address", "streetAddress", "street", "fullAddress", "formattedAddress"]:
                            val = record.get(addr_key, "")
                            if val:
                                addr_parts.append(str(val).strip())
                                break
                        
                        city = record.get("city", record.get("addressCity", "Fort Worth"))
                        state = record.get("state", record.get("addressState", "TX"))
                        zip_code = str(record.get("zip", record.get("addressZip", record.get("zipCode", ""))))
                        
                        full_address = f"{', '.join(addr_parts)}, {city}, {state} {zip_code}"
                        if not any(addr_parts):
                            full_address = record.get("situs_address", "")
                        if not full_address:
                            continue
                        
                        # Build property object
                        prop = {
                            "situs_address": full_address,
                            "city": city,
                            "state": state,
                            "zip": zip_code[:5],
                            "county": "Tarrant",
                            "price": record.get("price", record.get("listingPrice", 0)),
                            "beds": record.get("beds", record.get("bedrooms", record.get("bathrooms", None))),
                            "baths": record.get("baths", record.get("bathrooms", None)),
                            "sqft": record.get("sqft", record.get("squareFootage", record.get("livingArea", None))),
                            "year_built": record.get("yearBuilt", record.get("year_built", None)),
                            "lot_size_sqft": record.get("lotSize", record.get("lotSizeSqFt", None)),
                            "property_type": record.get("propertyType", record.get("homeType", "")),
                            "latitude": record.get("latitude", record.get("lat", None)),
                            "longitude": record.get("longitude", record.get("lng", None)),
                            "owner_name": record.get("ownerName", record.get("owner_name", "")),
                            "listing_status": record.get("status", record.get("listingStatus", "Active")),
                            "listing_type": record.get("listingType", ""),
                            "data_source": f"Apify/{actor_id}",
                            "source_platform": "Apify",
                            "apify_actor": actor_id,
                            "apify_run_id": run_id,
                            "created_at": None,
                            "updated_at": None,
                        }
                        
                        # Try case-insensitive match first
                        import re
                        existing = await db.properties.find_one({
                            "situs_address": {"$regex": f"^{re.escape(full_address)}$", "$options": "i"}
                        })
                        
                        if existing:
                            update_fields = {k: v for k, v in prop.items() if v and v != 0 and k not in ("situs_address", "created_at")}
                            update_fields["data_source"] = existing.get("data_source", "") + f" + Apify/{actor_id}"
                            await db.properties.update_one({"id": existing["id"]}, {"$set": update_fields})
                        else:
                            from datetime import datetime, timezone
                            from uuid import uuid4
                            prop["id"] = f"apify-{uuid4().hex[:12]}"
                            prop["created_at"] = datetime.now(timezone.utc).isoformat()
                            prop["updated_at"] = prop["created_at"]
                            await db.properties.insert_one(prop)
                        
                        imported += 1
                    except Exception:
                        continue
                
                return {
                    "status": "succeeded",
                    "actor_id": actor_id,
                    "run_id": run_id,
                    "records_fetched": len(records),
                    "records_imported": imported,
                }
            
            elif run_status in ("FAILED", "TIMED-OUT", "ABORTED"):
                return {"status": run_status.lower(), "error": status_data.get("statusMessage", "Unknown")}
    
    return {"status": "timeout", "message": f"Run still pending after {wait_for_finish}s — check Apify console"}


# ========== Generic Scraper Import Endpoint ==========

@router.post("/import/scraper")
async def import_from_scraper(
    properties: List[Dict[str, Any]] = Body(..., description="Array of property objects to import"),
    source_name: str = Body(default="custom_scraper", description="Name of the data source"),
):
    """Generic endpoint to import scraped property data from any source."""
    from backend.database import get_database
    from datetime import datetime, timezone
    from uuid import uuid4
    import re
    
    db = await get_database()
    imported = 0
    updated = 0
    errors = 0
    
    for prop in properties:
        try:
            # Normalize address
            address = prop.get("situs_address", prop.get("address", prop.get("full_address", "")))
            if not address:
                # Try building from components
                parts = []
                for k in ["street", "streetAddress", "addressLine1"]:
                    if prop.get(k):
                        parts.append(str(prop[k]))
                city = prop.get("city", "Fort Worth")
                state = prop.get("state", "TX")
                zip_str = str(prop.get("zip", prop.get("zip_code", prop.get("zipCode", ""))))
                address = f"{', '.join(parts)}, {city}, {state} {zip_str}"
            
            if not address:
                errors += 1
                continue
            
            # Normalize into standard schema
            normalized = {
                "situs_address": address,
                "city": prop.get("city", "Fort Worth"),
                "state": prop.get("state", "TX"),
                "zip": str(prop.get("zip", prop.get("zip_code", prop.get("zipCode", ""))))[:5],
                "county": prop.get("county", prop.get("county_name", "Tarrant")),
                "price": prop.get("price", prop.get("list_price", prop.get("listingPrice", 0))),
                "beds": prop.get("beds", prop.get("bedrooms", None)),
                "baths": prop.get("baths", prop.get("bathrooms", None)),
                "sqft": prop.get("sqft", prop.get("square_footage", prop.get("livingArea", None))),
                "year_built": prop.get("year_built", prop.get("yearBuilt", None)),
                "lot_size_sqft": prop.get("lot_size_sqft", prop.get("lotSize", prop.get("lotSizeSqFt", None))),
                "property_type": prop.get("property_type", prop.get("homeType", prop.get("type", ""))),
                "latitude": prop.get("latitude", prop.get("lat", None)),
                "longitude": prop.get("longitude", prop.get("lng", prop.get("lon", None))),
                "owner_name": prop.get("owner_name", prop.get("ownerName", "")),
                "owner_mailing_address": prop.get("owner_mailing_address", prop.get("mailingAddress", "")),
                "owner_type": prop.get("owner_type", ""),
                "out_of_state_owner": prop.get("out_of_state_owner", False),
                "absentee_owner": prop.get("absentee_owner", False),
                "assessed_value": prop.get("assessed_value", prop.get("assessedValue", None)),
                "market_value": prop.get("market_value", prop.get("marketValue", None)),
                "listing_status": prop.get("listing_status", prop.get("status", prop.get("listingStatus", "Active"))),
                "listing_type": prop.get("listing_type", prop.get("listingType", "For Sale")),
                "data_source": f"Custom/{source_name}",
                "source_platform": prop.get("source_platform", source_name),
                "vacant": prop.get("vacant", prop.get("is_vacant", False)),
                "tax_delinquent": prop.get("tax_delinquent", False),
                "high_equity": prop.get("high_equity", False),
                "investor_owned": prop.get("investor_owned", False),
                "distress_score": prop.get("distress_score", 0),
                "violation_count": prop.get("violation_count", 0),
                "open_violation_count": prop.get("open_violation_count", 0),
                "extra_data": prop.get("extra_data", prop.get("extraData", {})),
            }
            
            # Try case-insensitive match
            existing = await db.properties.find_one({
                "situs_address": {"$regex": f"^{re.escape(address)}$", "$options": "i"}
            })
            
            if existing:
                update_fields = {}
                for k, v in normalized.items():
                    if v and v != 0 and k not in ("situs_address",):
                        update_fields[k] = v
                update_fields["data_source"] = existing.get("data_source", "") + f" + {source_name}"
                await db.properties.update_one({"id": existing["id"]}, {"$set": update_fields})
                updated += 1
            else:
                normalized["id"] = f"scraper-{uuid4().hex[:12]}"
                normalized["created_at"] = datetime.now(timezone.utc).isoformat()
                normalized["updated_at"] = normalized["created_at"]
                await db.properties.insert_one(normalized)
                imported += 1
                
        except Exception:
            errors += 1
    
    return {
        "status": "ok",
        "source": source_name,
        "imported": imported,
        "updated": updated,
        "errors": errors,
        "total": len(properties),
    }

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
        
        properties = await db.properties.find(query).sort("distress_score", -1).limit(limit).to_list()
        
        return {
            "count": len(properties),
            "filter": filter_type,
            "items": properties,
        }
    except Exception as e:
        return {
            "count": 0,
            "filter": filter_type,
            "items": [],
            "error": str(e),
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
        ("tad", "https://mapit.tarrantcounty.com/arcgis/rest/services/Dynamic/TADParcels/FeatureServer/0/query?where=1%3D1https://services8.arcgis.com/5S5T6XdxjqI5BK2Y/arcgis/rest/services/TAD_Parcels_1/FeatureServer/0/query?where=1=1&outFields=TAXPIN&resultRecordCount=1&f=jsonoutFields=TAXPINhttps://services8.arcgis.com/5S5T6XdxjqI5BK2Y/arcgis/rest/services/TAD_Parcels_1/FeatureServer/0/query?where=1=1&outFields=TAXPIN&resultRecordCount=1&f=jsonresultRecordCount=1https://services8.arcgis.com/5S5T6XdxjqI5BK2Y/arcgis/rest/services/TAD_Parcels_1/FeatureServer/0/query?where=1=1&outFields=TAXPIN&resultRecordCount=1&f=jsonf=json"),
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


# ========== Quill AI Analysis ==========

@router.post("/quill/analyze")
async def quill_analyze(
    address: str,
    listing_price: Optional[float] = None,
    beds: Optional[int] = None,
    baths: Optional[float] = None,
    sqft: Optional[int] = None,
    arv_estimate: Optional[float] = None,
    repair_estimate: Optional[float] = None,
    rent_estimate: Optional[float] = None,
):
    """Analyze a property with Quill AI. Returns BUY/PASS/NEGOTIATE.

    Provide as much data as you have. Quill makes the best analysis possible.
    """
    from ai.models import QuillAnalyzeRequest
    from ai.quill import analyze_property_with_quill

    request = QuillAnalyzeRequest(
        address=address,
        listing_price=listing_price,
        beds=beds,
        baths=baths,
        sqft=sqft,
        arv_estimate=arv_estimate,
        repair_estimate=repair_estimate,
        rent_estimate=rent_estimate,
    )
    return analyze_property_with_quill(request)


@router.post("/quill/analyze-property/{property_id}")
async def quill_analyze_property_id(property_id: str):
    """Analyze a property from the database by its ID.

    Uses Serenity to enrich the property data first, then Quill analyzes it.
    """
    from database import PostgresDatabase
    from ai.serenity import build_quill_request_from_property
    from ai.quill import analyze_property_with_quill

    db = PostgresDatabase()
    try:
        await db.connect()
        doc = await db.properties.find_one({"id": property_id})
        if not doc:
            raise HTTPException(status_code=404, detail="Property not found")

        request = build_quill_request_from_property(doc)
        return analyze_property_with_quill(request)
    finally:
        await db.close()


@router.get("/quill/status")
async def quill_status():
    """Get Quill AI status and version information."""
    from ai.serenity import serenity_status

    return {
        "name": "Quill AI",
        "version": "1.0",
        "role": "Senior Real Estate Investment Analyst",
        "serenity": serenity_status(),
        "description": (
            "Quill analyzes properties and returns BUY / PASS / NEGOTIATE "
            "decisions with max offer calculations, risk flags, and offer letters."
        ),
    }


# ========== Quick Analysis (minimal input) ==========

@router.post("/analyze/quick")
async def quick_analyze(
    address: str,
    price: float,
    arv: float,
    repairs: float = 0,
    rent: float = 0,
):
    """Quick one-shot deal analyzer — just the numbers."""
    from ai.calculations import calculate_max_offer, decide_buy_pass_negotiate

    max_offer = calculate_max_offer(arv, repairs)
    decision = decide_buy_pass_negotiate(price, max_offer)
    profit = arv - price - repairs

    return {
        "address": address,
        "decision": decision,
        "listing_price": price,
        "arv": arv,
        "repairs": repairs,
        "max_offer": max_offer,
        "estimated_profit": profit,
        "roi_pct": round((profit / (price + repairs)) * 100, 1) if (price + repairs) > 0 else 0,
    }


# ========== Apify-Powered Imports (replaces broken scrapers) ==========

@router.post("/import/apify/investorlift")
async def import_from_investorlift(limit: int = 500, city: str = ""):
    """Import wholesale deals from InvestorLift via Apify.
    Replaces: OffMarketDeck scraper (broken)
    Data includes: price, ARV, beds/baths/sqft, wholesaler contacts
    """
    from database import PostgresDatabase
    from importers.apify_sources import import_investorlift

    db = PostgresDatabase()
    try:
        await db.connect()
        result = await import_investorlift(db, limit=limit, city=city or None)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        await db.close()


@router.post("/import/apify/motivated-sellers")
async def import_from_motivated_sellers(limit: int = 500, min_score: int = 0):
    """Import motivated seller leads from Apify.
    Replaces: SmartPropLeads scraper (broken)
    Data includes: owner name, phone, email, motivation score, FSBO listings
    """
    from database import PostgresDatabase
    from importers.apify_sources import import_motivated_sellers

    db = PostgresDatabase()
    try:
        await db.connect()
        result = await import_motivated_sellers(db, limit=limit, min_score=min_score)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        await db.close()


@router.post("/import/apify/skip-trace")
async def import_from_skip_trace(limit: int = 100):
    """Import skip trace data from Apify.
    Replaces: free_skip_trace.py (limited)
    Data includes: phone numbers, emails, current addresses
    """
    from database import PostgresDatabase
    from importers.apify_sources import import_skip_trace_apify

    db = PostgresDatabase()
    try:
        await db.connect()
        result = await import_skip_trace_apify(db, limit=limit)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        await db.close()


@router.post("/import/apify/run-aggregator")
async def run_real_estate_aggregator(location: str = "Fort Worth, TX"):
    """Run the Real Estate Aggregator actor. Scrapes Zillow + Realtor for Fort Worth.
    Replaces: All sites that were blocked (Zillow 403, Realtor 429, Redfin 403)
    """
    from importers.apify_sources import run_real_estate_aggregator

    result = await run_real_estate_aggregator(location=location)
    return result


@router.get("/apify/status")
async def get_apify_status():
    """Check status of all Apify actors and data sources."""
    from importers.apify_sources import apify_status

    return await apify_status()


# ========== Import All (Apify replacement version) ==========

@router.post("/import/all-with-apify")
async def import_all_with_apify():
    """Import from all sources, using Apify for broken scrapers."""
    from database import PostgresDatabase
    from importers.fort_worth_violations import import_fort_worth_violations
    from importers.foreclosure_finder import import_foreclosures
    from importers.apify_sources import import_investorlift, import_motivated_sellers, import_skip_trace_apify

    db = PostgresDatabase()
    results = {}

    try:
        await db.connect()

        # Working sources
        for name, fn in [
            ("fort_worth_violations", import_fort_worth_violations),
            ("foreclosures", import_foreclosures),
        ]:
            try:
                results[name] = await fn(db)
            except Exception as e:
                results[name] = {"error": str(e)}

        # Apify sources (replaces broken scrapers)
        for name, fn in [
            ("investorlift_wholesale", import_investorlift),
            ("motivated_seller_leads", import_motivated_sellers),
        ]:
            try:
                results[name] = await fn(db)
            except Exception as e:
                results[name] = {"error": str(e)}

        return {"status": "complete", "results": results}
    finally:
        await db.close()
