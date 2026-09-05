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
import re
from fastapi import APIRouter, HTTPException, Body
from pydantic import BaseModel
from typing import Dict, Any, List, Optional

import httpx

router = APIRouter(prefix="/api")

from bulk_import import router as bulk_import_router
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

# Fort Worth scoping defaults per known actor — applied when run_input omits
# location/caps, so a bare call can NEVER fire a nationwide run. This is the
# second layer of the Fort Worth lockdown (the first is the ingest filter in
# apify_import.py, which drops any non-Tarrant record before it hits the DB).
FORT_WORTH_DEFAULTS: Dict[str, Dict[str, Any]] = {
    "cMyVy1qjmV7jKZ4YW": {"locations": ["Fort Worth, Tx"], "maxItems": 500},          # crawlerbros/propwire
    "j0emD7OFNyWcl8ZMQ": {"locations": ["Fort Worth, Tx"], "maxItems": 500},          # jungle_synthesizer/propwire (dupe)
    "PM6eEFaxhMZCWpn1Y": {"location": "Fort Worth, TX", "listingType": "for_sale", "maxItems": 500},  # jp_ishac/us-listings
    "d4o0SCOyzzwUSxL3e": {"mode": "active", "propertyTypes": ["Single-Family"], "states": ["TX"]},    # investorlift (state-only; ingest filter bounds it)
    "qu04TKDjVwWvLWpQW": {"mode": "active", "propertyTypes": ["Single-Family"], "states": ["TX"]},    # corent1robert/investorlift (dupe)
    "GMyiJdAWTaVk9ElKN": {"cities": ["Fort Worth"], "states": ["TX"], "maxItems": 100},              # swerve/motivated-seller
}

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

    from importers.apify_import import is_allowed_actor_id

    if not is_allowed_actor_id(actor_id, set(FORT_WORTH_DEFAULTS)):
        raise HTTPException(
            403,
            "Actor is not approved. Add its ID to APIFY_ALLOWED_ACTOR_IDS before running it.",
        )

    # FORT WORTH ENFORCEMENT: reviewed defaults win over caller input so an
    # authenticated request cannot accidentally expand a known actor nationwide.
    defaults = FORT_WORTH_DEFAULTS.get(actor_id, {})
    if not defaults and not run_input:
        raise HTTPException(400, "Configured custom actors require an explicit scoped run_input")
    run_input = {**run_input, **defaults}

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
                
                # Import through the same normalized, Fort Worth-gated shape as
                # scheduled Apify runs. Unknown/malformed/out-of-area rows never
                # reach the listing table.
                from datetime import datetime, timezone
                from uuid import NAMESPACE_URL, uuid5
                from database import PostgresDatabase
                from importers.apify_import import is_fort_worth_area, normalize_record

                db = PostgresDatabase()
                imported = rejected = 0
                try:
                    await db.connect()
                    for record in records:
                        prop = normalize_record(record)
                        if not prop or not is_fort_worth_area(prop.get("city", "")):
                            rejected += 1
                            continue
                        now = datetime.now(timezone.utc).isoformat()
                        prop.update({
                            "data_source": f"Apify/{actor_id}",
                            "source_platform": "Apify",
                            "apify_actor": actor_id,
                            "apify_run_id": run_id,
                            "is_live_listing": True,
                            "listing_last_seen_at": now,
                            "missed_syncs": 0,
                            "updated_at": now,
                        })
                        existing = await db.properties.find_one({
                            "situs_address": {
                                "$regex": f"^{re.escape(prop['situs_address'])}$",
                                "$options": "i",
                            },
                        })
                        if existing:
                            updates = {
                                key: value for key, value in prop.items()
                                if value not in (None, "", 0) and key != "situs_address"
                            }
                            updates["data_source"] = (
                                str(existing.get("data_source") or "") + f" + Apify/{actor_id}"
                            ).strip(" +")
                            await db.properties.update_one({"id": existing["id"]}, {"$set": updates})
                        else:
                            prop["id"] = str(uuid5(
                                NAMESPACE_URL,
                                f"listing:{prop['situs_address'].upper()}",
                            ))
                            prop["created_at"] = now
                            await db.properties.insert_one(prop)
                        imported += 1
                finally:
                    await db.close()
                
                return {
                    "status": "succeeded",
                    "actor_id": actor_id,
                    "run_id": run_id,
                    "records_fetched": len(records),
                    "records_imported": imported,
                    "records_rejected": rejected,
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


@router.post("/import/tax-roll")
async def import_tax_roll(apply: bool = False, url: str = "", max_records: int = 0):
    """Download & match the official Tarrant County tax roll ZIP.

    apply=False → dry-run report only (safe default).
    apply=True  → writes matched tax facts + enriches properties.
    """
    import argparse
    from importers.tax_roll_sync import run as run_tax_roll

    tax_args = argparse.Namespace(
        url=url or None,
        layout=None,
        max_records=max_records or None,
        force=False,
        apply=apply,
        dry_run=not apply,
    )
    try:
        return await run_tax_roll(tax_args)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

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

        # Tarrant County Tax Roll (official, delinquent-tax data)
        try:
            import argparse
            from importers.tax_roll_sync import run as run_tax_roll
            tax_args = argparse.Namespace(url=None, layout=None, max_records=None, force=False, apply=True, dry_run=False)
            results["tax_roll"] = await run_tax_roll(tax_args)
        except Exception as e:
            results["tax_roll"] = {"error": str(e)}
        
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
        "tax_roll": {"available": False, "cost": "FREE", "note": "Official Tarrant County tax roll ZIP"},
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
        ("tad", "https://mapit.tarrantcounty.com/arcgis/rest/services/Dynamic/TADParcels/FeatureServer/0/query?where=1%3D1&outFields=TAXPIN&resultRecordCount=1&f=json"),
        ("foreclosure_listings", "https://www.foreclosurelistingsusa.com/fort-worth-tx/"),
        ("offmarketdeck", "https://offmarketdeck.com/texas/fort-worth"),
        ("new_western", "https://marketplace.newwestern.com/"),
        ("stessa", "https://www.stessa.com/investment-properties/"),
        ("smartpropleads", "https://smartpropleads.com/browse"),
        ("tax_roll", "https://www.tarrantcountytx.gov/content/main/en/tax/property-tax/tarrant-county-tax-roll.html"),
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

@router.post("/quill/analyze-basic")
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
    if price <= 0:
        # No purchase price → refuse to fabricate a profit/ROI.
        return {
            "address": address,
            "decision": "PASS",
            "listing_price": None,
            "arv": arv,
            "repairs": repairs,
            "max_offer": max_offer,
            "estimated_profit": None,
            "roi_pct": None,
            "warning": "Purchase price required — profit and ROI cannot be computed without it.",
        }
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

@router.post("/import/investorlift")
async def import_investorlift_free(limit: int = 50, state: str = "TX", city: str = ""):
    """Import wholesale deals from InvestorLift for FREE (no Apify).
    
    Scrapes server-side rendered deal data directly from investorlift.com.
    Data includes: price, ARV, beds/baths/sqft, gross margin, score.
    """
    from database import PostgresDatabase
    from importers.investorlift_scraper import import_investorlift

    db = PostgresDatabase()
    try:
        await db.connect()
        result = await import_investorlift(
            db,
            max_deals=limit,
            target_states=[state.upper()] if state else None,
            target_city=city or None,
        )
        return result
    except Exception as e:
        from fastapi import HTTPException
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        await db.close()
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


@router.post("/import/apify/us-listings")
async def import_from_us_listings(limit: int = 500, city: str = ""):
    """Import real MLS listings (Realtor.com) scraped by Apify's US listings actor."""
    from database import PostgresDatabase
    from importers.apify_sources import import_us_listings

    db = PostgresDatabase()
    try:
        await db.connect()
        result = await import_us_listings(db, limit=limit, city=city or None)
        return {"ok": True, **result}
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


# ========== Quill Analysis Routes ==========

@router.get("/quill/analyze/{property_id}")
async def quill_analyze(property_id: str, check_flood: bool = True):
    """Analyze a property with Quill — ARV, mortgage estimate, deal type, P&L.

    Returns the full deal breakdown + Quill's plain-English take.
    """
    from database import PostgresDatabase
    from importers.quill_analyzer import analyze_property

    db = PostgresDatabase()
    try:
        await db.connect()
        prop = await db.properties.find_one({"id": property_id})
        if not prop:
            prop = await db.properties.find_one({"situs_address": property_id})
        if not prop:
            raise HTTPException(status_code=404, detail=f"Property not found: {property_id}")
        
        # Merge nested fields
        if isinstance(prop.get("data"), dict):
            prop.update(prop["data"])
        
        result = await analyze_property(prop, check_flood=check_flood)
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        await db.close()


@router.post("/quill/analyze")
async def quill_analyze_custom(payload: dict):
    """Analyze a custom property payload with Quill (no DB lookup needed)."""
    from importers.quill_analyzer import analyze_property

    result = await analyze_property(payload, check_flood=bool(payload.get("latitude")))
    return result


@router.get("/quill/hello")
async def quill_hello():
    """Quill's greeting endpoint."""
    return {
        "greeting": "Hey bud, what adventure are we gonna get in today?",
        "tagline": "What kind of deal can we find today?",
        "status": "ready",
    }


@router.post("/quill/offer-letter")
async def quill_offer_letter(payload: dict):
    """Generate an offer letter for a property."""
    from importers.quill_analyzer import (
        analyze_property, generate_offer_letter,
    )

    analysis = await analyze_property(payload, check_flood=False)
    letter = generate_offer_letter(
        analysis,
        buyer_name=payload.get("buyer_name", "[Buyer Name]"),
        offer_price=payload.get("offer_price"),
        earnest_money=payload.get("earnest_money", 1000),
        closing_days=payload.get("closing_days", 30),
        financing=payload.get("financing", "Cash"),
    )
    return {"letter": letter, "analysis": analysis}


@router.post("/quill/negotiate")
async def quill_negotiate(payload: dict):
    """Get negotiation advice for a deal."""
    from importers.quill_analyzer import (
        analyze_property, negotiation_advice,
    )

    analysis = await analyze_property(payload, check_flood=False)
    return {
        "advice": negotiation_advice(analysis),
        "take": analysis["take"],
    }


# ========== Bright Data Cross-Check Routes ==========

@router.get("/brightdata/check/{property_id}")
async def brightdata_check(property_id: str):
    """Cross-check a property against live Zillow data via Bright Data MCP."""
    from database import PostgresDatabase
    from importers.brightdata_check import cross_check_property

    db = PostgresDatabase()
    try:
        await db.connect()
        prop = await db.properties.find_one({"id": property_id})
        if not prop:
            prop = await db.properties.find_one({"situs_address": property_id})
        if not prop:
            raise HTTPException(status_code=404, detail=f"Property not found: {property_id}")
        if isinstance(prop.get("data"), dict):
            prop.update(prop["data"])
        result = await cross_check_property(prop)
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        await db.close()


@router.post("/brightdata/check-batch")
async def brightdata_check_batch(payload: dict):
    """Cross-check multiple properties (payload: {"properties": [ids...]} or full objects)."""
    from database import PostgresDatabase
    from importers.brightdata_check import cross_check_batch

    db = PostgresDatabase()
    try:
        await db.connect()
        ids = payload.get("properties") or []
        props = []
        for pid in ids[:25]:
            prop = await db.properties.find_one({"id": pid})
            if prop:
                if isinstance(prop.get("data"), dict):
                    prop.update(prop["data"])
                props.append(prop)
        if not props:
            return {"results": [], "note": "No properties found"}
        results = await cross_check_batch(props, concurrency=3)
        return {"results": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        await db.close()


@router.post("/brightdata/save-check")
async def brightdata_save_check(payload: dict):
    """Save verified Bright Data cross-check results back to a property.

    payload: {"property_id": "...", "result": {...cross_check_property output}}
    """
    from datetime import datetime, timezone
    from database import PostgresDatabase

    prop_id = payload.get("property_id")
    result = payload.get("result") or {}
    if not prop_id:
        raise HTTPException(status_code=400, detail="property_id required")

    db = PostgresDatabase()
    try:
        await db.connect()
        prop = await db.properties.find_one({"id": prop_id})
        if not prop:
            raise HTTPException(status_code=404, detail="Property not found")

        updates = {
            "verified_zestimate": result.get("zestimate"),
            "verified_cotality": result.get("cotality"),
            "verified_redfin": result.get("redfin_value"),
            "verified_zillow_url": result.get("zillow_url"),
            "verified_realtor_url": result.get("realtor_url"),
            "verified_redfin_url": result.get("redfin_url"),
            "verified_comps": result.get("comps") or [],
            "verified_status": result.get("status"),
            "verified_at": datetime.now(timezone.utc).isoformat(),
        }
        await db.properties.update_one({"id": prop_id}, {"$set": updates})
        return {"saved": True, "property_id": prop_id, "updates": updates}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        await db.close()


# ========== Bright Data Deal Finder (county clerk + FSBO + Hubzu) ==========
@router.post("/import/brightdata-deals")
async def import_brightdata_deals_route(
    days_back: int = Body(default=30, embed=True, description="How many days back to search county clerk (7, 30, 90, 180, 365)"),
):
    """
    Fetch pre-foreclosure + FSBO + REO leads from:
    1. Tarrant County Clerk (Lis Pendens filings = pre-foreclosure BEFORE auction)
    2. FSBO.com (For Sale By Owner = motivated sellers, no agent commission)
    3. Hubzu (Zillow's REO auction inventory = bank-owned)
    
    Uses Bright Data Web Unlocker to bypass anti-bot protections.
    Cost: ~50-100 credits per run. Free tier: 5,000 credits/month.
    """
    from database import PostgresDatabase
    db = PostgresDatabase()
    try:
        await db.connect()
        result = await import_brightdata_deals(db, days_back=days_back)
        return result
    except Exception as e:
        logger.exception("brightdata_deals import failed")
        return {"imported": 0, "status": "error", "error": str(e)}
    finally:
        try:
            await db.close()
        except Exception:
            pass


@router.get("/brightdata-deals/preview")
async def preview_brightdata_deals(
    days_back: int = 30,
    limit: int = 50,
):
    """
    Preview leads without writing to DB. Returns the raw fetched leads
    so you can verify the data quality before committing.
    """
    try:
        leads = await fetch_all_pre_foreclosure_leads(days_back=days_back)
        return {
            "total": len(leads),
            "preview": leads[:limit],
            "by_source": _count_by_source(leads),
            "days_back": days_back,
        }
    except Exception as e:
        logger.exception("preview failed")
        return {"error": str(e), "total": 0, "preview": []}


def _count_by_source(leads: list) -> dict:
    counts = {}
    for l in leads:
        src = l.get("data_source", "Unknown")
        counts[src] = counts.get(src, 0) + 1
    return counts


@router.get("/brightdata-deals/status")
async def brightdata_deals_status():
    """
    Check Bright Data integration status + remaining credits.
    """
    import os
    has_token = bool(os.environ.get("BRIGHT_DATA_TOKEN", ""))
    has_zone = bool(os.environ.get("BRIGHT_DATA_ZONE", ""))
    return {
        "brightdata_configured": has_token,
        "web_unlocker_zone": has_zone,
        "sources": [
            {"name": "Tarrant County Clerk", "type": "Pre-Foreclosure (Lis Pendens)", "requires_brightdata": True},
            {"name": "FSBO.com", "type": "For Sale By Owner", "requires_brightdata": True},
            {"name": "Hubzu", "type": "REO Auction (Bank-Owned)", "requires_brightdata": True},
        ],
        "credit_cost_per_run": "~50-100 credits (5,000 free/month)",
    }


# ========== Bright Data MCP Scraper (no zone required) ==========

@router.post("/import/brightdata-mcp")
async def import_brightdata_mcp_route(
    include_offmarket: bool = Body(default=True),
    include_fsbo: bool = Body(default=True),
    include_hubzu: bool = Body(default=True),
    max_pages: int = Body(default=2),
):
    """
    Scrape off-market deals via Bright Data MCP (no zone required).

    Sources:
    1. OffMarketDeck — wholesale + flip + rental deals
    2. FSBO.com — for sale by owner (motivated sellers)
    3. Hubzu — bank-owned REO auctions

    Cost: ~5-20 MCP credits per run. Free tier: 5,000 credits/month.
    """
    from database import PostgresDatabase
    from importers.brightdata_mcp_scraper import (
        fetch_all_leads, import_leads_to_db,
    )

    db = PostgresDatabase()
    try:
        await db.connect()
        leads = await fetch_all_leads(
            include_offmarket=include_offmarket,
            include_fsbo=include_fsbo,
            include_hubzu=include_hubzu,
            max_pages=max_pages,
        )
        result = await import_leads_to_db(db, leads)
        result["status"] = "ok"
        result["source"] = "brightdata_mcp"
        result["by_source"] = {}
        for lead in leads:
            src = lead.get("data_source", "Unknown")
            result["by_source"][src] = result["by_source"].get(src, 0) + 1
        return result
    except Exception as e:
        logger.exception("brightdata_mcp import failed")
        return {"imported": 0, "status": "error", "error": str(e)}
    finally:
        try:
            await db.close()
        except Exception:
            pass


@router.get("/brightdata-mcp/preview")
async def preview_brightdata_mcp(
    include_offmarket: bool = True,
    include_fsbo: bool = True,
    include_hubzu: bool = True,
    max_pages: int = 1,
):
    """
    Preview MCP-scraped leads without writing to DB.
    Returns raw leads so you can verify data quality first.
    """
    from importers.brightdata_mcp_scraper import fetch_all_leads

    try:
        leads = await fetch_all_leads(
            include_offmarket=include_offmarket,
            include_fsbo=include_fsbo,
            include_hubzu=include_hubzu,
            max_pages=max_pages,
        )
        by_source = {}
        for lead in leads:
            src = lead.get("data_source", "Unknown")
            by_source[src] = by_source.get(src, 0) + 1
        return {
            "total": len(leads),
            "preview": leads[:30],
            "by_source": by_source,
        }
    except Exception as e:
        logger.exception("brightdata_mcp preview failed")
        return {"error": str(e), "total": 0, "preview": []}


@router.get("/brightdata-mcp/status")
async def brightdata_mcp_status():
    """
    Check Bright Data MCP configuration and available tools.
    """
    import os
    has_token = bool(os.environ.get("BRIGHTDATA_TOKEN", "") or os.environ.get("BRIGHTDATA_TOKEN", ""))
    return {
        "brightdata_mcp_configured": has_token,
        "tools": [
            "scrape_as_html",
            "scrape_as_markdown",
            "scrape_batch",
            "search_engine",
            "search_engine_batch",
        ],
        "sources": [
            {"name": "OffMarketDeck", "type": "Off-market / wholesale deals", "mcp_tool": "scrape_as_html"},
            {"name": "FSBO.com", "type": "For sale by owner", "mcp_tool": "scrape_as_html"},
            {"name": "Hubzu", "type": "Bank-owned auctions", "mcp_tool": "scrape_as_html"},
            {"name": "Zillow", "type": "Property lookup (per address)", "mcp_tool": "scrape_as_html"},
        ],
        "credit_cost_per_run": "~5-20 MCP credits (5,000 free/month)",
    }


# ========== URL File Import (downloads + imports) ==========

import io
import zipfile
import pandas as pd
import httpx

from pathlib import Path
from intake import upsert_import_records

class URLImportRequest(BaseModel):
    url: str
    source_label: str | None = None


@router.post("/import/url")
async def import_from_url(body: URLImportRequest):
    from database import PostgresDatabase
    db = PostgresDatabase()
    """
    Download a file from a URL, detect type (csv/xlsx/zip), then import.

    Use cases:
    - Hosted CSV (any public URL)
    - Hosted Excel (.xlsx)
    - Hosted ZIP containing multiple CSVs
    - Google Sheets exported as CSV
    - Dropbox / OneDrive share links
    """
    url = body.url.strip()
    if not url:
        raise HTTPException(400, "URL is required")

    # Normalize Google Sheets export links
    if "docs.google.com/spreadsheets" in url and "/export" not in url:
        # Convert view link to CSV export
        try:
            parts = url.split("/d/")[1].split("/")
            sheet_id = parts[0]
            gid = "0"
            for p in parts:
                if p.startswith("gid="):
                    gid = p.split("=")[1]
            url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={gid}"
        except Exception:
            pass

    # Download the file
    try:
        async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": "InvestorFlip/1.0"})
    except Exception as exc:
        raise HTTPException(400, f"Could not download URL: {str(exc)[:200]}")

    if resp.status_code != 200:
        raise HTTPException(400, f"URL returned status {resp.status_code}")

    raw = resp.content
    if not raw:
        raise HTTPException(400, "Downloaded file is empty")

    # Detect type from URL or content-type
    url_lower = url.lower().split("?")[0]
    content_type = resp.headers.get("content-type", "").lower()

    if "zip" in url_lower or "application/zip" in content_type:
        file_type = "zip"
    elif "xls" in url_lower and not "xls" in url_lower.replace("xlsx", ""):
        file_type = "xlsx"
    elif "csv" in url_lower or "text/csv" in content_type or "application/vnd.ms-excel" in content_type:
        file_type = "csv"
    else:
        raise HTTPException(400, f"Could not detect file type from URL: {url_lower}")

    source_name = body.source_label or f"URL import: {url[:80]}"

    # Process based on type
    property_ids: list[str] = []
    file_reports: list[dict] = []

    try:
        if file_type == "zip":
            with zipfile.ZipFile(io.BytesIO(raw)) as zf:
                members = [
                    m for m in zf.namelist()
                    if not m.endswith("/")
                    and Path(m).suffix.lower() in {".csv", ".xls", ".xlsx"}
                ]
                if not members:
                    raise HTTPException(400, "ZIP contains no CSV or Excel files")
                for member in members:
                    try:
                        member_raw = zf.read(member)
                        member_suffix = Path(member).suffix.lower()
                        if member_suffix == ".csv":
                            frame = pd.read_csv(io.BytesIO(member_raw), encoding="utf-8-sig")
                        else:
                            frame = pd.read_excel(io.BytesIO(member_raw))
                        rows = frame.to_dict(orient="records")
                        if len(rows) > 250:
                            file_reports.append({
                                "file": member, "status": "skipped",
                                "reason": f"{len(rows)} rows exceeds 250-row limit"})
                            continue
                        report = await upsert_import_records(
                            db, rows, f"{source_name} / {member}")
                        property_ids.extend(report["property_ids"])
                        file_reports.append({
                            "file": member, "status": "ok",
                            "rows": report["rows_read"], "accepted": report["accepted"],
                            "inserted": report["inserted"], "updated": report["updated"]})
                    except Exception as exc:
                        file_reports.append({"file": member, "status": "error", "reason": str(exc)[:160]})
        elif file_type == "xlsx":
            frame = pd.read_excel(io.BytesIO(raw))
            rows = frame.to_dict(orient="records")
            if len(rows) > 250:
                raise HTTPException(400, f"File has {len(rows)} rows, max 250 per import")
            report = await upsert_import_records(db, rows, source_name)
            property_ids = report["property_ids"]
            file_reports = [{
                "file": url.split("/")[-1] or "import.xlsx", "status": "ok",
                "rows": report["rows_read"], "accepted": report["accepted"],
                "inserted": report["inserted"], "updated": report["updated"]}]
        else:  # csv
            try:
                frame = pd.read_csv(io.BytesIO(raw), encoding="utf-8-sig")
            except UnicodeDecodeError:
                frame = pd.read_csv(io.BytesIO(raw), encoding="latin1")
            rows = frame.to_dict(orient="records")
            if len(rows) > 250:
                raise HTTPException(400, f"File has {len(rows)} rows, max 250 per import")
            report = await upsert_import_records(db, rows, source_name)
            property_ids = report["property_ids"]
            file_reports = [{
                "file": url.split("/")[-1] or "import.csv", "status": "ok",
                "rows": report["rows_read"], "accepted": report["accepted"],
                "inserted": report["inserted"], "updated": report["updated"]}]
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, f"Could not parse file: {str(exc)[:200]}")

    # Enrich imported properties
    enrichment = await _enrich_imported_properties(property_ids)

    total_accepted = sum(r.get("accepted", 0) for r in file_reports)
    total_inserted = sum(r.get("inserted", 0) for r in file_reports)
    total_updated = sum(r.get("updated", 0) for r in file_reports)

    return {
        "ok": bool(total_accepted),
        "source_url": url,
        "files": file_reports,
        "total_accepted": total_accepted,
        "total_inserted": total_inserted,
        "total_updated": total_updated,
        "property_ids": property_ids,
        "enrichment": enrichment,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Bright Data MCP Scraper (OffMarketDeck + FSBO + Hubzu)
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/import/brightdata-mcp")
async def import_brightdata_mcp_route(
    include_offmarket: bool = True,
    include_fsbo: bool = True,
    include_hubzu: bool = True,
    max_pages: int = 3,
):
    """
    Scrape off-market deals, FSBO listings, and Hubzu auctions using Bright Data MCP.
    Pulls from public marketplaces and saves to the database.
    """
    from database import PostgresDatabase
    from importers.brightdata_mcp_scraper import import_brightdata_mcp
    db = PostgresDatabase()
    return await import_brightdata_mcp(
        db,
        include_offmarket=include_offmarket,
        include_fsbo=include_fsbo,
        include_hubzu=include_hubzu,
        max_pages=max_pages,
    )
