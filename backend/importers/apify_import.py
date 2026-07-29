"""Import property data from Apify schedule runs.

Pulls datasets from recent successful Apify actor runs
and imports them into InvestorFlip properties.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional
from uuid import uuid4

import httpx

logger = logging.getLogger("apify_import")

APIFY_BASE = "https://api.apify.com/v2"
DEFAULT_LOOKBACK_DAYS = 7  # how far back to check for runs


def get_api_key() -> str:
    key = os.environ.get("APIFY_API_KEY", "").strip()
    return key


async def fetch_recent_runs(
    client: httpx.AsyncClient,
    token: str,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> List[Dict[str, Any]]:
    """Fetch all APIFY actor runs from the last N days with datasets."""
    since = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat()
    
    url = f"{APIFY_BASE}/actor-runs"
    params = {
        "token": token,
        "limit": 100,
        "status": "SUCCEEDED",
    }
    
    resp = await client.get(url, params=params)
    resp.raise_for_status()
    data = resp.json()
    runs = data.get("data", {}).get("items", [])
    
    # Filter to runs with datasets and within lookback
    valid = []
    for r in runs:
        if r.get("defaultDatasetId") and r.get("startedAt", "") >= since:
            valid.append(r)
    
    return valid


async def fetch_dataset(
    client: httpx.AsyncClient,
    token: str,
    dataset_id: str,
) -> List[Dict[str, Any]]:
    """Fetch all items from an Apify dataset."""
    url = f"{APIFY_BASE}/datasets/{dataset_id}/items"
    resp = await client.get(url, params={"token": token, "format": "json", "limit": 5000})
    resp.raise_for_status()
    return resp.json()


def normalize_record(record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Normalize an Apify record into InvestorFlip property schema."""
    
    # Extract address from various formats
    address = record.get("address") or record.get("streetAddress") or record.get("street") or ""
    city = record.get("city") or record.get("addressCity") or ""
    state = record.get("state") or record.get("addressState") or ""
    zip_code = str(record.get("zip") or record.get("postalCode") or record.get("addressZip") or record.get("zipCode") or "")[:5]
    
    # If no separate address fields, check for full address
    if not address and record.get("fullAddress"):
        parts = str(record["fullAddress"]).split(",")
        address = parts[0].strip() if parts else ""
    
    full_address = f"{address}, {city}, {state} {zip_code}".strip(", ")
    if not address or not city:
        return None
    
    # Map owner info
    owner_name = (
        record.get("ownerName")
        or record.get("owner_name")
        or f"{record.get('ownerFirstName', '')} {record.get('ownerLastName', '')}".strip()
        or ""
    )
    
    # Map price
    price = record.get("price") or record.get("listPrice") or record.get("listingPrice") or 0
    
    # Map beds/baths/sqft
    beds = record.get("beds") or record.get("bedrooms") or None
    baths = record.get("baths") or record.get("bathrooms") or record.get("bathsFull") or None
    sqft = record.get("sqft") or record.get("squareFootage") or record.get("livingArea") or record.get("pricePerSqft") or None
    
    # HAR.com specific fields
    if not beds and record.get("bedsFull") is not None:
        beds = record["bedsFull"]
    
    return {
        "situs_address": full_address.strip(),
        "city": city,
        "state": state,
        "zip": zip_code,
        "county": record.get("county") or record.get("county_name") or "Tarrant",
        "price": price or 0,
        "beds": beds,
        "baths": baths,
        "sqft": sqft,
        "year_built": record.get("yearBuilt") or record.get("year_built") or None,
        "lot_size_sqft": record.get("lotSize") or record.get("lotSizeSqFt") or record.get("landSqft") or None,
        "property_type": record.get("propertyType") or record.get("homeType") or record.get("listingType") or "",
        "latitude": record.get("latitude") or record.get("lat") or None,
        "longitude": record.get("longitude") or record.get("lng") or record.get("lon") or None,
        "owner_name": owner_name,
        "owner_mailing_address": "",
        "owner_type": "",
        "listing_status": record.get("status") or record.get("listingStatus") or "Active",
        "listing_type": record.get("listingType") or record.get("listing_type") or "For Sale",
        "mls_number": record.get("mlsNumber") or record.get("mls") or "",
        "listing_url": record.get("listingUrl") or record.get("url") or "",
        "data_source": "Apify",
    }


async def import_apify_runs(
    db,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> Dict[str, Any]:
    """Pull all recent Apify runs and import into the database."""
    token = get_api_key()
    if not token:
        return {"error": "APIFY_API_KEY not configured", "imported": 0}
    
    results = {"runs_found": 0, "runs_imported": 0, "records_fetched": 0, "records_imported": 0}
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        runs = await fetch_recent_runs(client, token, lookback_days)
        results["runs_found"] = len(runs)
        
        for run in runs:
            dataset_id = run["defaultDatasetId"]
            run_id = run["id"]
            started = run.get("startedAt", "")[:19]
            
            try:
                records = await fetch_dataset(client, token, dataset_id)
                results["records_fetched"] += len(records)
                
                imported = 0
                for record in records:
                    try:
                        prop = normalize_record(record)
                        if not prop:
                            continue
                        
                        # Case-insensitive match
                        existing = await db.properties.find_one({
                            "situs_address": {
                                "$regex": f"^{re.escape(prop['situs_address'])}$",
                                "$options": "i"
                            }
                        })
                        
                        if existing:
                            update_fields = {k: v for k, v in prop.items() 
                                           if v and v != 0 and k not in ("situs_address",)}
                            update_fields["data_source"] = existing.get("data_source", "") + " + Apify"
                            update_fields["apify_run_id"] = run_id
                            await db.properties.update_one(
                                {"id": existing["id"]},
                                {"$set": update_fields},
                            )
                        else:
                            prop["id"] = f"apify-{uuid4().hex[:12]}"
                            prop["apify_run_id"] = run_id
                            prop["data_source"] = "Apify"
                            prop["created_at"] = datetime.now(timezone.utc).isoformat()
                            prop["updated_at"] = prop["created_at"]
                            await db.properties.insert_one(prop)
                        
                        imported += 1
                    except Exception:
                        continue
                
                results["records_imported"] += imported
                results["runs_imported"] += 1
                
            except Exception as e:
                logger.warning("Failed to import run %s: %s", run_id, e)
    
    return results
