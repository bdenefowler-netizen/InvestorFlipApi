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
from uuid import NAMESPACE_URL, uuid5

import httpx
from listing_normalization import extract_listing_fields, photo_url

logger = logging.getLogger("apify_import")

APIFY_BASE = "https://api.apify.com/v2"
DEFAULT_LOOKBACK_DAYS = 7  # how far back to check for runs

# Fort Worth / Tarrant County allowlist — the ONLY cities we ingest.
# This is the enforcement layer: even if an Apify actor ignores its input
# config and scrapes nationwide, nothing outside this list reaches the DB.
TARRANT_CITIES = {
    "fort worth", "arlington", "north richland hills", "haltom city",
    "keller", "southlake", "colleyville", "grapevine", "bedford",
    "euless", "hurst", "benbrook", "white settlement", "saginaw",
    "watauga", "river oaks", "forest hill", "crowley", "burleson",
    "mansfield", "azle", "lake worth", "sansom park", "westworth village",
    "haslet", "eagle mountain", "blue mound", "pelican bay",
    "kennedale", "everman", "dalworthington gardens", "pantego",
}

def is_fort_worth_area(city: str) -> bool:
    """True if the city is Fort Worth or a Tarrant County city."""
    c = (city or "").strip().lower()
    if not c:
        return False
    if c in TARRANT_CITIES:
        return True
    # tolerate "Fort Worth, TX" style values
    return c.startswith("fort worth") or "fort worth" in c


def get_api_key() -> str:
    key = os.environ.get("APIFY_API_KEY", "").strip()
    return key


def _configured_ids(name: str) -> set[str]:
    return {
        value.strip()
        for value in os.environ.get(name, "").split(",")
        if value.strip()
    }


def is_allowed_actor_id(actor_id: str, built_in_ids: Optional[set[str]] = None) -> bool:
    """Authorize an actor explicitly through config or the reviewed built-in set."""
    return actor_id in (_configured_ids("APIFY_ALLOWED_ACTOR_IDS") | (built_in_ids or set()))


def is_allowed_run(run: Dict[str, Any]) -> bool:
    """Only import explicitly approved property actors/tasks.

    An Apify account may contain unrelated actors whose output also happens to
    contain address/city fields. Importing every successful run can therefore
    turn unrelated datasets into InvestorFlip listings.
    """
    if os.environ.get("APIFY_IMPORT_ALL_RUNS", "false").strip().lower() == "true":
        return True
    actor_ids = _configured_ids("APIFY_ALLOWED_ACTOR_IDS")
    task_ids = _configured_ids("APIFY_ALLOWED_TASK_IDS")
    return bool(
        (actor_ids and str(run.get("actorId") or "") in actor_ids)
        or (task_ids and str(run.get("actorTaskId") or "") in task_ids)
    )


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
        if (
            r.get("defaultDatasetId")
            and r.get("startedAt", "") >= since
            and is_allowed_run(r)
        ):
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

    fields = extract_listing_fields(record)
    extracted_address = fields["address"]
    location = record.get("location") if isinstance(record.get("location"), dict) else {}
    nested_address = location.get("address") if isinstance(location.get("address"), dict) else {}
    nested_county = location.get("county") if isinstance(location.get("county"), dict) else {}
    description = record.get("description") if isinstance(record.get("description"), dict) else {}
    primary_photo = record.get("primary_photo") if isinstance(record.get("primary_photo"), dict) else {}
    coordinate = nested_address.get("coordinate") if isinstance(nested_address.get("coordinate"), dict) else {}
    source = record.get("source") if isinstance(record.get("source"), dict) else {}

    # Extract address from various formats
    address = (
        extracted_address.get("street")
        or record.get("address")
        or record.get("streetAddress")
        or record.get("street")
        or nested_address.get("line")
        or ""
    )
    full_address = extracted_address.get("full") or ""
    if isinstance(address, dict):
        address = (
            address.get("streetAddress")
            or address.get("street_address")
            or address.get("street")
            or address.get("line")
            or address.get("address1")
            or ""
        )
    if not address and full_address:
        address = full_address.split(",", 1)[0].strip()

    city = extracted_address.get("city") or record.get("city") or record.get("addressCity") or nested_address.get("city") or ""
    state = (
        extracted_address.get("state")
        or record.get("state")
        or record.get("addressState")
        or nested_address.get("state_code")
        or nested_address.get("state")
        or ""
    )
    zip_code = str(
        extracted_address.get("zip")
        or record.get("zip")
        or record.get("postalCode")
        or record.get("addressZip")
        or record.get("zipCode")
        or nested_address.get("postal_code")
        or ""
    )[:5]

    # If no separate address fields, check for full address
    if not address and record.get("fullAddress"):
        parts = str(record["fullAddress"]).split(",")
        address = parts[0].strip() if parts else ""

    full_address = full_address or f"{address}, {city}, {state} {zip_code}".strip(", ")
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
    price = fields["price"] or record.get("price") or record.get("listPrice") or record.get("listingPrice") or record.get("list_price") or 0

    # Map beds/baths/sqft
    beds = fields["beds"] or record.get("beds") or record.get("bedrooms") or description.get("beds") or None
    baths = (
        fields["baths"]
        or record.get("baths")
        or record.get("bathrooms")
        or record.get("bathsFull")
        or description.get("baths")
        or description.get("baths_full")
        or None
    )
    sqft = (
        fields["sqft"]
        or record.get("sqft")
        or record.get("squareFootage")
        or record.get("livingArea")
        or description.get("sqft")
        or None
    )

    # HAR.com specific fields
    if not beds and record.get("bedsFull") is not None:
        beds = record["bedsFull"]

    return {
        "situs_address": full_address.strip(),
        "city": city,
        "state": state,
        "zip": zip_code,
        "county": record.get("county") or record.get("county_name") or nested_county.get("name") or "Tarrant",
        "price": price or 0,
        "beds": beds,
        "baths": baths,
        "sqft": sqft,
        "year_built": fields["year_built"] or record.get("yearBuilt") or record.get("year_built") or description.get("year_built") or None,
        "lot_size_sqft": (
            fields["lot_size_sqft"]
            or record.get("lotSize")
            or record.get("lotSizeSqFt")
            or record.get("landSqft")
            or description.get("lot_sqft")
            or None
        ),
        "property_type": fields["property_type"] or record.get("propertyType") or record.get("homeType") or record.get("listingType") or description.get("type") or "",
        "latitude": fields["latitude"] or record.get("latitude") or record.get("lat") or coordinate.get("lat") or None,
        "longitude": fields["longitude"] or record.get("longitude") or record.get("lng") or record.get("lon") or coordinate.get("lon") or None,
        "owner_name": owner_name,
        "owner_mailing_address": "",
        "owner_type": "",
        "listing_status": record.get("status") or record.get("listingStatus") or "Active",
        "listing_type": record.get("listingType") or record.get("listing_type") or "For Sale",
        "mls_number": record.get("mlsNumber") or record.get("mls") or source.get("listing_id") or "",
        "listing_url": record.get("listingUrl") or record.get("url") or record.get("href") or "",
        "image_url": record.get("image_url") or primary_photo.get("href") or photo_url(record.get("image")) or "",
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
    if (
        os.environ.get("APIFY_IMPORT_ALL_RUNS", "false").strip().lower() != "true"
        and not _configured_ids("APIFY_ALLOWED_ACTOR_IDS")
        and not _configured_ids("APIFY_ALLOWED_TASK_IDS")
    ):
        return {
            "skipped": True,
            "reason": (
                "Configure APIFY_ALLOWED_ACTOR_IDS or APIFY_ALLOWED_TASK_IDS; "
                "unscoped account-wide imports are disabled"
            ),
            "records_imported": 0,
            "property_ids": [],
        }
    
    results = {
        "runs_found": 0, "runs_imported": 0, "records_fetched": 0,
        "records_imported": 0, "records_skipped_non_fortworth": 0,
        "records_skipped_missing_address": 0,
        "records_failed": 0, "property_ids": [],
    }
    
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
                            results["records_skipped_missing_address"] += 1
                            continue

                        # FORT WORTH ENFORCEMENT: drop anything outside Tarrant/Fort Worth
                        if not is_fort_worth_area(prop.get("city", "")):
                            results["records_skipped_non_fortworth"] += 1
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
                            update_fields["is_live_listing"] = True
                            update_fields["listing_last_seen_at"] = datetime.now(timezone.utc).isoformat()
                            update_fields["missed_syncs"] = 0
                            await db.properties.update_one(
                                {"id": existing["id"]},
                                {"$set": update_fields},
                            )
                            results["property_ids"].append(existing["id"])
                        else:
                            prop["id"] = str(uuid5(NAMESPACE_URL, f"listing:{prop['situs_address'].upper()}"))
                            prop["apify_run_id"] = run_id
                            prop["data_source"] = "Apify"
                            prop["created_at"] = datetime.now(timezone.utc).isoformat()
                            prop["updated_at"] = prop["created_at"]
                            prop["is_live_listing"] = True
                            prop["listing_last_seen_at"] = prop["created_at"]
                            prop["missed_syncs"] = 0
                            await db.properties.insert_one(prop)
                            results["property_ids"].append(prop["id"])
                        
                        imported += 1
                    except Exception as exc:
                        results["records_failed"] += 1
                        logger.debug("Apify record rejected from run %s: %s", run_id, exc)
                        continue
                
                results["records_imported"] += imported
                results["runs_imported"] += 1
                
            except Exception as e:
                logger.warning("Failed to import run %s: %s", run_id, e)
    
    return results
