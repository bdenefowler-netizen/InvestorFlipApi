"""Apify actor integrations for InvestorFlip.

Replaces broken scrapers with reliable Apify actors:
  - real-estate-aggregator  → Zillow, Realtor, Redfin (replaces blocked sites)
  - investorlift-scraper    → Wholesale deals with ARV (replaces OffMarketDeck)
  - investorlift-property-scraper → Detailed InvestorLift properties
  - motivated-seller-leads  → FSBO with motivation scores (replaces SmartPropLeads scrape)
  - propwire-leads-scraper  → Real estate leads
  - skip-trace              → Owner contact info (replaces free_skip_trace)
  - zillow-scraper-ppe      → Zillow property data
  - us-real-estate-listings-scraper → US listings

Set APIFY_API_KEY in Railway environment variables.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone

import httpx

from database import PostgresDatabase

logger = logging.getLogger("tarrantrei.apify")

APIFY_API_KEY = os.environ.get("APIFY_API_KEY", "")
APIFY_BASE = "https://api.apify.com/v2"

# Actor IDs from investorflip schedule
ACTORS = {
    "real_estate_aggregator": "7mcQAVcB4AKIoWrJR",
    "investorlift_scraper": "qu04TKDjVwWvLWpQW",
    "investorlift_property": "d4o0SCOyzzwUSxL3e",
    "motivated_seller_leads": "GMyiJdAWTaVk9ElKN",
    "propwire_leads": "cMyVy1qjmV7jKZ4YW",
    "skip_trace": "vmf6h5lxPAkB1W2gT",
    "skip_trace_2": "Ts951UmWt2NyLug84",
    "zillow_scraper": "7EG6vc4LOoouPfk3t",
    "us_listings": "PM6eEFaxhMZCWpn1Y",
}

# Datasets from previous runs
EXISTING_DATASETS = {
    "investorlift_scraper": "K00xbwfLYOFQ9fcmQ",
    "investorlift_property": "K00xbwfLYOFQ9fcmQ",
    "motivated_seller_leads": "BG4icdyQcGVJ7MjCJ",
    "us_listings": "HEo6OX3hLmXzaYMhw",
    "propwire": "G5kN67pFvxwLkAvCN",
    "skip_trace": "rmcgNCc7QPLwIQ7LT",
}


def _ready() -> bool:
    return bool(APIFY_API_KEY)


def _headers() -> Dict[str, str]:
    return {"Accept": "application/json"}


def _auth_url(path: str) -> str:
    sep = "&" if "?" in path else "?"
    return f"{APIFY_BASE}{path}{sep}token={APIFY_API_KEY}"


async def _get_dataset_items(dataset_id: str, limit: int = 1000) -> List[Dict[str, Any]]:
    """Fetch items from an Apify dataset."""
    url = _auth_url(f"/datasets/{dataset_id}/items?limit={limit}")
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.get(url, headers=_headers())
        resp.raise_for_status()
        data = resp.json()
    return data if isinstance(data, list) else []


async def _run_actor(
    actor_id: str,
    input_data: Dict[str, Any],
    wait_secs: int = 120,
) -> Optional[str]:
    """Run an Apify actor and return the dataset ID."""
    url = _auth_url(f"/acts/{actor_id}/runs?waitForFinish={wait_secs}")
    async with httpx.AsyncClient(timeout=wait_secs + 30) as client:
        resp = await client.post(url, json=input_data, headers=_headers())
        resp.raise_for_status()
        result = resp.json()
    data = result.get("data", {})
    dataset_id = data.get("defaultDatasetId")
    status = data.get("status")
    logger.info("Actor run %s: status=%s dataset=%s", actor_id, status, dataset_id)
    return dataset_id if status == "SUCCEEDED" else None


# ========== Import Functions ==========

async def import_investorlift(
    db: PostgresDatabase,
    limit: int = 500,
    city: Optional[str] = None,
) -> Dict[str, Any]:
    """Import wholesale deals from InvestorLift (replaces OffMarketDeck)."""
    dataset = EXISTING_DATASETS.get("investorlift_scraper")
    if not dataset:
        return {"fetched": 0, "inserted": 0, "matched": 0, "skipped": 0,
                "error": "No InvestorLift dataset found. Run the actor first via Apify Console."}

    items = await _get_dataset_items(dataset, limit)
    if not items:
        return {"fetched": 0, "inserted": 0, "matched": 0, "skipped": 0}

    # Filter to Texas / DFW
    if city:
        items = [i for i in items if i.get("city", "").upper() == city.upper()]
    else:
        items = [i for i in items if (i.get("state") or i.get("state_code") or "").upper() in ("TX",)]

    inserted = 0
    matched = 0
    skipped = 0

    for item in items:
        try:
            prop = _parse_investorlift(item)
            if not prop.get("situs_address"):
                skipped += 1
                continue

            existing = await db.properties.find_one({"situs_address": prop["situs_address"]})
            if existing:
                update_fields = {}
                if not existing.get("price") and prop.get("price"):
                    update_fields["price"] = prop["price"]
                if not existing.get("market_value") and prop.get("market_value"):
                    update_fields["market_value"] = prop["market_value"]
                update_fields["data_source"] = existing.get("data_source", "") + " + InvestorLift"
                update_fields["wholesale"] = True
                update_fields["investorlift_data"] = prop.get("investorlift_data")
                if update_fields:
                    await db.properties.update_one({"id": existing["id"]}, {"$set": update_fields})
                matched += 1
            else:
                await db.properties.insert_one(prop)
                inserted += 1
        except Exception as e:
            logger.warning("InvestorLift parse error: %s", e)
            skipped += 1

    return {"fetched": len(items), "inserted": inserted, "matched": matched, "skipped": skipped,
            "source": "InvestorLift (Apify)"}


def _parse_investorlift(item: Dict[str, Any]) -> Dict[str, Any]:
    """Parse an InvestorLift deal into InvestorFlip format."""
    title = item.get("title", "")
    city = (item.get("city") or "").strip().upper()
    state = (item.get("state") or item.get("state_code") or "TX").upper()
    zip_code = (item.get("zip") or item.get("postalCode") or "").strip()
    address = item.get("address", item.get("street_address", item.get("public_address", "")))

    # Parse address from title if missing
    if not address and title:
        parts = title.split("–")
        if len(parts) > 1:
            address = parts[0].strip().upper()
        elif "," in title:
            addr_part = title.split(",")[0].strip().upper()
            if any(c.isdigit() for c in addr_part):
                address = addr_part

    full_address = f"{address}, {city}, {state} {zip_code}".strip(", ")

    price_raw = str(item.get("price") or "0").replace(",", "").replace("$", "").strip()
    price = int(float(price_raw)) if price_raw.replace(".", "").isdigit() else 0

    arv = item.get("arv_estimate")
    if arv is None:
        arv_raw = str(item.get("arv") or "0").replace(",", "").replace("$", "").strip()
        arv = int(float(arv_raw)) if arv_raw.replace(".", "").isdigit() else None

    gross_margin = item.get("gross_margin")
    if gross_margin is None:
        gm_raw = str(item.get("estimated_profit") or item.get("profit") or "0").replace(",", "").replace("$", "").strip()
        gross_margin = int(float(gm_raw)) if gm_raw.replace(".", "").isdigit() else None

    beds = int(item["bedrooms"]) if item.get("bedrooms") else None
    baths = float(item["bathrooms"]) if item.get("bathrooms") else None
    sqft = int(item["sq_footage"]) if item.get("sq_footage") else None
    year_built = int(item["year_built"]) if item.get("year_built") else None
    lat = float(item["latitude"]) if item.get("latitude") else None
    lon = float(item["longitude"]) if item.get("longitude") else None
    prop_type = item.get("property_type", "Single-Family") or "Single-Family"
    status = item.get("status", "available")

    return {
        "id": f"il-{item.get('id', format(hash(full_address) & 0xFFFFFFFF, '08x'))}",
        "situs_address": full_address,
        "city": city,
        "state": state,
        "zip": zip_code,
        "county": (item.get("county") or "Tarrant").upper(),
        "latitude": lat,
        "longitude": lon,
        "beds": beds,
        "baths": baths,
        "sqft": sqft,
        "year_built": year_built,
        "property_type": prop_type,
        "price": price or None,
        "market_value": arv,
        "assessed_value": None,
        "repair_estimate": None,
        "owner_name": item.get("wholesaler_name", item.get("account_title", "")),
        "owner_type": "Wholesaler",
        "wholesale": True,
        "listing_type": "Wholesale",
        "listing_status": status.title(),
        "data_source": "InvestorLift (Apify)",
        "source_platform": "InvestorLift",
        "is_synthetic": False,
        "investorlift_data": {
            "arv": arv,
            "gross_margin": gross_margin,
            "arv_percentage": item.get("arv_percentage"),
            "wholesaler_company": item.get("wholesaler_company"),
            "wholesaler_name": item.get("wholesaler_name"),
            "days_on_market": item.get("days_on_il"),
            "url": item.get("property_page_url"),
        },
    }


async def import_motivated_sellers(
    db: PostgresDatabase,
    limit: int = 500,
    min_score: int = 0,
) -> Dict[str, Any]:
    """Import motivated seller leads from Apify (replaces SmartPropLeads scrape)."""
    dataset = EXISTING_DATASETS.get("motivated_seller_leads")
    if not dataset:
        return {"fetched": 0, "inserted": 0, "matched": 0, "skipped": 0,
                "error": "No Motivated Seller dataset found."}

    items = await _get_dataset_items(dataset, limit)
    if not items:
        return {"fetched": 0, "inserted": 0, "matched": 0, "skipped": 0}

    # Filter to DFW area and minimum motivation score
    dfw_counties = {"TARRANT", "DALLAS", "COLLIN", "DENTON", "ROCKWALL",
                    "KAUFMAN", "ELLIS", "JOHNSON", "PARKER", "WISE", "HUNT"}
    dfw_items = []
    for item in items:
        county = (item.get("county") or "").upper()
        state = (item.get("state") or "").upper()
        if state == "TX" and county in dfw_counties:
            score = item.get("motivationScore") or 0
            if score >= min_score:
                dfw_items.append(item)

    inserted = 0
    matched = 0
    skipped = 0

    for item in dfw_items:
        try:
            prop = _parse_motivated_seller(item)
            if not prop.get("situs_address"):
                skipped += 1
                continue

            existing = await db.properties.find_one({"situs_address": prop["situs_address"]})
            if existing:
                update_fields = {}
                if not existing.get("price") and prop.get("price"):
                    update_fields["price"] = prop["price"]
                if not existing.get("owner_name") and prop.get("owner_name"):
                    update_fields["owner_name"] = prop["owner_name"]
                update_fields["data_source"] = existing.get("data_source", "") + " + MotivatedSeller"
                update_fields["motivation_score"] = prop.get("motivation_score")
                update_fields["fsbo_data"] = prop.get("fsbo_data")
                if update_fields:
                    await db.properties.update_one({"id": existing["id"]}, {"$set": update_fields})
                matched += 1
            else:
                await db.properties.insert_one(prop)
                inserted += 1
        except Exception as e:
            logger.warning("Motivated seller parse error: %s", e)
            skipped += 1

    return {
        "fetched": len(items),
        "filtered_to_dfw": len(dfw_items),
        "inserted": inserted,
        "matched": matched,
        "skipped": skipped,
        "source": "Motivated Seller Leads (Apify)",
    }


def _parse_motivated_seller(item: Dict[str, Any]) -> Dict[str, Any]:
    """Parse a motivated seller lead into InvestorFlip format."""
    street = (item.get("streetAddress") or "").strip().upper()
    city = (item.get("city") or "").strip().upper()
    state = (item.get("state") or "TX").upper()
    zip_code = (item.get("postalCode") or "").strip()
    county = (item.get("county") or "").upper()

    full_address = f"{street}, {city}, {state} {zip_code}".strip(", ")

    price = item.get("price")
    beds = item.get("beds")
    baths = item.get("baths")
    sqft = item.get("sqft")
    lot_size = item.get("lotSize")
    year_built = item.get("yearBuilt")
    prop_type = item.get("propertyType", "House")
    motivation_score = item.get("motivationScore") or 0
    dom = item.get("daysOnMarket") or 0
    listing_date = item.get("listingDate")

    owner_name = (item.get("ownerName") or "").strip()
    phone = item.get("phone")
    email = item.get("email")

    return {
        "id": f"fsbo-{item.get('listingId', format(hash(full_address) & 0xFFFFFFFF, '08x'))}",
        "situs_address": full_address,
        "city": city,
        "state": state,
        "zip": zip_code,
        "county": county,
        "latitude": None,
        "longitude": None,
        "beds": beds,
        "baths": baths,
        "sqft": sqft,
        "year_built": year_built,
        "lot_size_sqft": lot_size,
        "property_type": prop_type or "Single Family Residential",
        "price": price,
        "market_value": None,
        "assessed_value": None,
        "owner_name": owner_name,
        "owner_type": "Individual",
        "owner_occupied": True,
        "owner_mailing_address": full_address,
        "data_source": "Motivated Seller Leads (Apify)",
        "source_platform": "FSBO",
        "listing_type": "For Sale By Owner",
        "listing_status": "Active",
        "is_synthetic": False,
        "listing_date": listing_date,
        "days_on_market": dom,
        "for_sale_by_owner": True,
        "fixer_upper": motivation_score >= 30,
        "motivation_score": motivation_score,
        "fsbo_data": {
            "motivation_score": motivation_score,
            "motivation_reasons": item.get("motivationReasons", []),
            "days_on_market": dom,
            "is_price_reduced": item.get("isPriceReduced", False),
            "owner_phone": phone,
            "owner_email": email,
            "source_url": item.get("listingUrl"),
            "will_work_with_agent": item.get("willWorkWithBuyersAgent", False),
        },
    }


async def import_skip_trace_apify(
    db: PostgresDatabase,
    limit: int = 100,
) -> Dict[str, Any]:
    """Import skip trace data from Apify."""
    dataset = EXISTING_DATASETS.get("skip_trace")
    if not dataset:
        return {"fetched": 0, "inserted": 0, "matched": 0, "skipped": 0,
                "error": "No Skip Trace dataset found."}

    items = await _get_dataset_items(dataset, limit)
    if not items:
        return {"fetched": 0, "inserted": 0, "matched": 0, "skipped": 0}

    return {
        "fetched": len(items),
        "source": "Skip Trace (Apify)",
        "data": items[:10],  # Sample
    }


def _parse_us_listing(item: Dict[str, Any]) -> Dict[str, Any]:
    """Parse a US real-estate listing (Realtor.com format) into InvestorFlip shape."""
    street = (item.get("street") or item.get("address") or "").strip()
    city = (item.get("city") or "Fort Worth").strip().upper()
    state = (item.get("state") or "TX").upper()
    zip_code = (item.get("zipCode") or item.get("zip") or "").strip()
    full_address = f"{street}, {city}, {state} {zip_code}".strip(", ")

    price = item.get("listPrice") or item.get("price") or 0
    beds = item.get("beds")
    baths = item.get("fullBaths") or item.get("baths")
    sqft = item.get("sqft")
    year_built = item.get("yearBuilt")
    lot_sqft = item.get("lotSqft")
    mls_id = item.get("mlsId")
    listing_url = item.get("propertyUrl") or item.get("url")
    status = (item.get("status") or "FOR_SALE").lower()
    county = (item.get("county") or "").upper()
    listing_date = item.get("listingDate") or item.get("listDate")
    description = item.get("listingDescription")
    agent_name = item.get("agentName") or item.get("listingAgentName")
    agent_phone = item.get("agentPhone") or item.get("listingAgentPhone")
    photos = item.get("photos") or item.get("imageUrls") or []

    prop = {
        "situs_address": full_address,
        "city": city,
        "state": state,
        "zip": zip_code,
        "county": county,
        "price": int(price) if price else None,
        "beds": beds,
        "baths": baths,
        "sqft": sqft,
        "year_built": year_built,
        "lot_size_sqft": lot_sqft,
        "latitude": item.get("latitude"),
        "longitude": item.get("longitude"),
        "mls_id": mls_id,
        "listing_type": "MLS LISTING",
        "data_source": "Apify US Listings (Realtor.com)",
        "source_platform": "Realtor.com",
        "detail_url": listing_url,
        "listing_status": status,
        "listing_description": description,
        "listing_agent_name": agent_name,
        "listing_agent_phone": agent_phone,
        "photos": photos if isinstance(photos, list) else [],
        "image_url": photos[0] if isinstance(photos, list) and photos else None,
        "listing_date": listing_date,
        "investment_score": None,
    }
    return prop


async def import_us_listings(
    db: PostgresDatabase,
    limit: int = 500,
    city: Optional[str] = None,
) -> Dict[str, Any]:
    """Import real MLS listings scraped by the US real-estate listings actor."""
    dataset = EXISTING_DATASETS.get("us_listings")
    if not dataset:
        return {"fetched": 0, "inserted": 0, "matched": 0, "skipped": 0,
                "error": "No US Listings dataset found. Run the actor first."}

    items = await _get_dataset_items(dataset, limit)
    if not items:
        return {"fetched": 0, "inserted": 0, "matched": 0, "skipped": 0}

    if city:
        items = [i for i in items if (i.get("city") or "").upper() == city.upper()]

    inserted = 0
    matched = 0
    skipped = 0

    for item in items:
        try:
            prop = _parse_us_listing(item)
            if not prop.get("situs_address") or len(prop["situs_address"].split(",")[0]) < 4:
                skipped += 1
                continue

            existing = await db.properties.find_one({"situs_address": prop["situs_address"]})
            if existing:
                update_fields = {}
                if not existing.get("price") and prop.get("price"):
                    update_fields["price"] = prop["price"]
                if not existing.get("mls_id") and prop.get("mls_id"):
                    update_fields["mls_id"] = prop["mls_id"]
                if not existing.get("detail_url") and prop.get("detail_url"):
                    update_fields["detail_url"] = prop["detail_url"]
                update_fields["data_source"] = existing.get("data_source", "") + " + USListings"
                update_fields["listing_status"] = prop.get("listing_status")
                if prop.get("photos"):
                    update_fields["photos"] = prop["photos"]
                if update_fields:
                    await db.properties.update_one({"id": existing["id"]}, {"$set": update_fields})
                matched += 1
            else:
                await db.properties.insert_one(prop)
                inserted += 1
        except Exception as e:
            logger.warning("US listing parse error: %s", e)
            skipped += 1

    return {"fetched": len(items), "inserted": inserted, "matched": matched,
            "skipped": skipped, "source": "Apify US Listings (Realtor.com)"}


async def run_real_estate_aggregator(
    location: str = "Fort Worth, TX",
    sources: List[str] = None,
) -> Dict[str, Any]:
    """Run the Real Estate Aggregator actor for Fort Worth properties."""
    if not _ready():
        return {"available": False, "error": "APIFY_API_KEY not set"}

    if sources is None:
        sources = ["realtor", "zillow"]

    input_data = {
        "search": location,
        "sources": sources,
        "maxResults": 100,
        "propertyType": "All",
    }

    dataset_id = await _run_actor(ACTORS["real_estate_aggregator"], input_data)
    if dataset_id:
        items = await _get_dataset_items(dataset_id)
        return {"available": True, "fetched": len(items), "dataset_id": dataset_id}
    return {"available": False, "error": "Actor run failed"}


async def apify_status() -> Dict[str, Any]:
    """Check status of all Apify actors."""
    return {
        "configured": _ready(),
        "actors": {
            "real_estate_aggregator": "Ready - scrapes Zillow, Realtor, Redfin",
            "investorlift_scraper": f"Dataset: {EXISTING_DATASETS.get('investorlift_scraper', 'None')}",
            "motivated_seller_leads": f"Dataset: {EXISTING_DATASETS.get('motivated_seller_leads', 'None')}",
            "skip_trace": f"Dataset: {EXISTING_DATASETS.get('skip_trace', 'None')}",
            "propwire_leads": "Not yet run",
            "zillow_scraper": "Not yet run",
            "us_listings": "Not yet run",
        },
    }


# Map of broken scrapers -> Apify replacements
APIFY_REPLACEMENTS = {
    "offmarketdeck": "investorlift_scraper",
    "foreclosure_listings": "real_estate_aggregator (Zillow/Realtor)",
    "tad": "real_estate_aggregator (county data)",
    "smartpropleads": "motivated_seller_leads",
    "free_skip_trace": "skip_trace",
}
