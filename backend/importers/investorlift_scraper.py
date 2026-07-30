"""InvestorLift FREE scraper — wholesale deals with ARV, margins, and scores.

No API key needed. No subscription. Bypasses CloudFront with TLS fingerprinting.
Extracts server-side rendered Nuxt payload directly from the HTML.

Data includes: price, ARV, beds/baths/sqft, year built, gross margin, 
motivation score, hotness, and location.

Usage:
    from importers.investorlift_scraper import scrape_investorlift
    
    deals = scrape_investorlift(max_deals=50)
    for d in deals:
        print(f"{d['city']}: ${d['price']} -> ${d['arv_estimate']} (margin: {d['gross_margin']}%)")
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger("investorlift")

# Try curl_cffi first (TLS fingerprint spoofing), fall back to requests
try:
    from curl_cffi import requests as http_client
    HAS_CURL_CFFI = True
except ImportError:
    import requests as http_client
    HAS_CURL_CFFI = False

BASE_URL = "https://investorlift.com"
MARKETPLACE_URL = f"{BASE_URL}/marketplace"


def _fetch_page(url: str, timeout: int = 20) -> Optional[str]:
    """Fetch a page with anti-bot measures."""
    kwargs = {"timeout": timeout}
    if HAS_CURL_CFFI:
        kwargs["impersonate"] = "chrome124"
    
    try:
        r = http_client.get(url, **kwargs)
        if r.status_code == 200:
            return r.text
        else:
            logger.warning(f"HTTP {r.status_code} for {url}")
            return None
    except Exception as e:
        logger.error(f"Failed to fetch {url}: {e}")
        return None


def _decode_nuxt_payload(html: str) -> Optional[List[Dict[str, Any]]]:
    """Decode Nuxt 3 indexed payload format into list of deal dicts."""
    match = re.search(
        r'<script[^>]*id="__NUXT_DATA__"[^>]*>(.*?)</script>',
        html, re.DOTALL
    )
    if not match:
        logger.warning("No __NUXT_DATA__ payload found in page")
        return None
    
    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError:
        logger.error("Failed to parse Nuxt payload JSON")
        return None
    
    if not isinstance(payload, list) or len(payload) < 50:
        logger.warning(f"Unexpected payload format (len={len(payload) if payload else 0})")
        return None
    
    # Find the meta object (usually around index 7)
    meta = None
    for item in payload:
        if isinstance(item, dict) and "columns" in item and "data" in item:
            meta = item
            break
    
    if not meta:
        logger.warning("Could not find data structure metadata in payload")
        return None
    
    try:
        col_idx = int(meta["columns"])  # e.g., 8
        row_idx = int(meta["data"])     # e.g., 45
    except (KeyError, ValueError, TypeError):
        logger.warning("Invalid column/data indices in metadata")
        return None
    
    # Get column names
    if col_idx >= len(payload):
        return None
    col_indices = payload[col_idx]
    columns = [payload[i] if i < len(payload) else str(i) for i in col_indices]
    
    # Get row start groups
    if row_idx >= len(payload):
        return None
    row_groups = payload[row_idx]
    
    # Decode each row
    deals = []
    for start in row_groups:
        start = int(start) if not isinstance(start, int) else start
        if start >= len(payload):
            continue
        
        value_indices = payload[start]
        row = {}
        for j, col in enumerate(columns):
            if j < len(value_indices):
                vi = value_indices[j]
                vi = int(vi) if not isinstance(vi, int) else vi
                if isinstance(vi, int) and 0 <= vi < len(payload):
                    row[col] = payload[vi]
                else:
                    row[col] = None
            else:
                row[col] = None
        
        # Only include if it has a valid ID
        if row.get("id") and isinstance(row["id"], int):
            deals.append(row)
    
    return deals


def scrape_marketplace() -> List[Dict[str, Any]]:
    """Scrape all deals from the InvestorLift marketplace."""
    html = _fetch_page(MARKETPLACE_URL)
    if not html:
        return []
    
    deals = _decode_nuxt_payload(html)
    if deals is None:
        return []
    
    # Clean and normalize
    for d in deals:
        # Normalize price/arv to int
        for field in ["price", "arv_estimate", "entry_fee"]:
            if field in d and d[field] is not None:
                try:
                    d[field] = int(d[field])
                except (ValueError, TypeError):
                    pass
        
        # Extract actual address from title
        title = d.get("title", "") or ""
        # Clean emoji and special chars
        clean_title = re.sub(r'[^\x00-\x7F]+', '', title).strip()
        d["clean_title"] = clean_title
        
        # Source marker
        d["_source"] = "investorlift"
    
    logger.info(f"Scraped {len(deals)} deals from InvestorLift")
    return deals


def scrape_deal_detail(deal_id: int) -> Optional[Dict[str, Any]]:
    """Scrape a single deal detail page for full financials.
    
    Returns deal data including repair estimates, occupancy, 
    wholesaler info, and property details.
    """
    url = f"{BASE_URL}/marketplace/deal/{deal_id}"
    html = _fetch_page(url)
    if not html:
        return None
    
    # The deal detail page has a more complex Nuxt payload
    # with pinia state containing the full deal object
    match = re.search(
        r'<script[^>]*id="__NUXT_DATA__"[^>]*>(.*?)</script>',
        html, re.DOTALL
    )
    if not match:
        logger.warning(f"No Nuxt payload in deal {deal_id}")
        return None
    
    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
    
    # Find the deal data in the pinia state
    results = {}
    
    def _find_deal_objects(obj, depth=0):
        if depth > 5 or not isinstance(obj, (dict, list)):
            return
        if isinstance(obj, dict):
            # Check if this looks like a deal
            if "id" in obj and "account_id" in obj and "price" in obj:
                if isinstance(obj["id"], int) and obj["id"] == deal_id:
                    results["deal"] = obj
            for v in obj.values():
                _find_deal_objects(v, depth + 1)
        elif isinstance(obj, list):
            for item in obj[:100]:  # Limit search
                _find_deal_objects(item, depth + 1)
    
    _find_deal_objects(payload)
    
    if "deal" in results:
        deal = results["deal"]
        # Add source marker
        deal["_source_detail"] = True
        return deal
    
    # Fallback: extract from raw HTML
    logger.info(f"Could not extract full deal object for {deal_id}, using marketplace data")
    return None


def filter_by_state(deals: List[Dict[str, Any]], states: List[str]) -> List[Dict[str, Any]]:
    """Filter deals by state code(s)."""
    states_upper = [s.upper() for s in states]
    return [d for d in deals if d.get("state_code", "").upper() in states_upper]


def filter_by_city(deals: List[Dict[str, Any]], city: str) -> List[Dict[str, Any]]:
    """Filter deals by city (case-insensitive partial match)."""
    city_lower = city.lower()
    return [d for d in deals if city_lower in d.get("city", "").lower()]


def to_property_record(deal: Dict[str, Any]) -> Dict[str, Any]:
    """Convert an InvestorLift deal to InvestorFlip property format."""
    address = deal.get("clean_title", "") or deal.get("title", "") or ""
    city = deal.get("city", "") or ""
    state = deal.get("state_code", "") or ""
    zip_code = str(deal.get("zip", "") or "")
    
    return {
        "situs_address": address,
        "city": city.upper(),
        "state": state.upper(),
        "zip": zip_code,
        "county": deal.get("county", "").upper(),
        "latitude": deal.get("latitude"),
        "longitude": deal.get("longitude"),
        "price": deal.get("price"),
        "market_value": deal.get("arv_estimate"),
        "beds": deal.get("bedrooms"),
        "baths": deal.get("bathrooms"),
        "sqft": deal.get("sq_footage"),
        "year_built": deal.get("year_built"),
        "property_type": "Single Family Residential",
        "data_source": "InvestorLift",
        "wholesale": True,
        "arv_estimate": deal.get("arv_estimate"),
        "gross_margin": deal.get("gross_margin"),
        "investorlift_score": deal.get("score"),
        "investorlift_hotness": deal.get("hotness"),
        "investorlift_deal_id": deal.get("id"),
        "investorlift_raw": deal,
    }


if __name__ == "__main__":
    # CLI test
    logging.basicConfig(level=logging.INFO)
    
    print("=" * 60)
    print("InvestorLift FREE Scraper — Test Run")
    print("=" * 60)
    
    deals = scrape_marketplace()
    print(f"\nTotal deals on marketplace: {len(deals)}")
    
    # Show Texas deals
    tx_deals = filter_by_state(deals, ["TX"])
    print(f"Texas deals: {len(tx_deals)}")
    
    for d in (tx_deals if tx_deals else deals[:5]):
        city = d.get("city", "?")
        state = d.get("state_code", "?")
        price = d.get("price", "?")
        arv = d.get("arv_estimate", "?")
        beds = d.get("bedrooms", "?")
        baths = d.get("bathrooms", "?")
        sqft = d.get("sq_footage", "?")
        margin = d.get("gross_margin", "?")
        score = d.get("score", "?")
        
        print(f"\n  📍 {city}, {state}")
        print(f"     Price: ${price:,}" if isinstance(price, int) else f"     Price: {price}")
        print(f"     ARV: ${arv:,}" if isinstance(arv, int) else f"     ARV: {arv}")
        print(f"     {beds}bd/{baths}ba · {sqft}sqft")
        print(f"     Margin: {margin}% · Score: {score}")
    
    # Try detail page for first deal
    if deals:
        d_id = deals[0].get("id")
        if d_id:
            print(f"\n--- Fetching deal {d_id} detail ---")
            detail = scrape_deal_detail(d_id)
            if detail:
                print(f"  Repair: ${detail.get('repair_estimate_min')} - ${detail.get('repair_estimate_max')}")
                print(f"  Condition: {detail.get('condition')}")
                print(f"  Occupancy: {detail.get('occupancy')}")
                print(f"  Wholesaler: {detail.get('account', {}).get('title', 'N/A')}")


# ========== API Routes Integration ==========

async def import_investorlift(
    db,
    max_deals: int = 100,
    min_score: float = 0.0,
    target_states: Optional[List[str]] = None,
    target_city: Optional[str] = None,
) -> Dict[str, Any]:
    """Scrape InvestorLift deals and import into the database.
    
    Args:
        db: PostgresDatabase instance
        max_deals: Maximum deals to process
        min_score: Minimum motivation score filter (0-1)
        target_states: List of state codes to filter (e.g., ['TX'])
        target_city: City name filter
    
    Returns:
        Dict with import stats
    """
    from datetime import datetime, timezone
    
    deals = scrape_marketplace()
    if not deals:
        return {"fetched": 0, "inserted": 0, "matched": 0, "error": "No deals scraped"}
    
    # Apply filters
    if target_states:
        deals = filter_by_state(deals, target_states)
    if target_city:
        deals = filter_by_city(deals, target_city)
    if min_score > 0:
        deals = [d for d in deals if (d.get("score") or 0) >= min_score]
    
    # Limit
    deals = deals[:max_deals]
    
    inserted = 0
    matched = 0
    skipped = 0
    
    for deal in deals:
        try:
            record = to_property_record(deal)
            address = record.get("situs_address", "")
            if not address:
                skipped += 1
                continue
            
            existing = await db.properties.find_one({"situs_address": address})
            if existing:
                # Update with enrichment fields
                updates = {}
                if not existing.get("price") and record.get("price"):
                    updates["price"] = record["price"]
                if not existing.get("market_value") and record.get("market_value"):
                    updates["market_value"] = record["market_value"]
                updates["wholesale"] = True
                updates["data_source"] = _merge_source(existing.get("data_source", ""), "InvestorLift")
                updates["investorlift_data"] = record.get("investorlift_raw")
                if updates:
                    await db.properties.update_one({"id": existing["id"]}, {"$set": updates})
                matched += 1
            else:
                await db.properties.insert_one(record)
                inserted += 1
        except Exception as e:
            logger.warning(f"Import error: {e}")
            skipped += 1
    
    return {
        "fetched": len(deals),
        "inserted": inserted,
        "matched": matched,
        "skipped": skipped,
        "source": "InvestorLift (FREE)",
    }


def _merge_source(current: str, new: str) -> str:
    """Merge data source strings without duplication."""
    sources = [s.strip() for s in current.replace("+", ",").split(",")]
    if new not in sources:
        sources.append(new)
    return " + ".join(s for s in sources if s)


# ========== Standalone CLI ==========

if __name__ == "__main__":
    import sys
    from pprint import pprint
    
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    
    deals = scrape_marketplace()
    print(f"\n📊 Total deals: {len(deals)}")
    
    # State filter
    if len(sys.argv) > 1:
        state_filter = sys.argv[1].upper()
        deals = filter_by_state(deals, [state_filter])
        print(f"   Filtered to {state_filter}: {len(deals)}")
    
    for d in deals[:20]:
        title = (d.get("clean_title") or d.get("title", ""))[:50]
        price = d.get("price", "?")
        arv = d.get("arv_estimate", "?")
        margin = d.get("gross_margin", "?")
        score = d.get("score", "?")
        beds = d.get("bedrooms", "?")
        baths = d.get("bathrooms", "?")
        
        if isinstance(price, int):
            spread = (arv - price) if isinstance(arv, int) else 0
            print(f"\n  💰 ${price:,} → ${arv:,} (${spread:,} spread)")
        else:
            print(f"\n  💰 ${price} → ${arv}")
        print(f"     {d.get('city')}, {d.get('state_code')} | {beds}bd/{baths}ba | Score: {score} | Margin: {margin}")
        print(f"     {title}")
