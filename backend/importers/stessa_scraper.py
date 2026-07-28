"""Stessa investment properties scraper — pulls data from Stessa marketplace.

Source: stessa.com/investment-properties
Uses public marketplace data (no login required for browsing).

Note: Stessa is primarily a property management tool, but they have a
public marketplace of investment properties that can be scraped.
"""

from __future__ import annotations

import json
import re
import logging
from typing import Any, Dict, List, Optional

import httpx

from database import PostgresDatabase

logger = logging.getLogger("tarrantrei.stessa")

STESSA_MARKETPLACE_URL = "https://www.stessa.com/investment-properties/"


def _extract_json_from_html(html: str) -> List[Dict[str, Any]]:
    """Extract property data from Stessa's HTML/JSON."""
    properties = []
    
    # Look for Next.js or React data
    # Stessa uses a combination of server-rendered HTML and client-side React
    
    # Try to find JSON-LD structured data
    ld_pattern = r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>'
    ld_matches = re.findall(ld_pattern, html, re.DOTALL)
    
    for match in ld_matches:
        try:
            data = json.loads(match)
            if isinstance(data, dict) and "@type" in data:
                if data.get("@type") == "RealEstateListing":
                    properties.append(data)
            elif isinstance(data, list):
                for item in data:
                    if isinstance(item, dict) and item.get("@type") == "RealEstateListing":
                        properties.append(item)
        except json.JSONDecodeError:
            continue
    
    # Also look for __NEXT_DATA__ or similar
    next_data_pattern = r'__NEXT_DATA__\s*=\s*({.*?})\s*;?\s*</script>'
    next_matches = re.findall(next_data_pattern, html, re.DOTALL)
    
    for match in next_matches:
        try:
            data = json.loads(match)
            # Navigate the data structure to find properties
            if "props" in data and "pageProps" in data["props"]:
                page_props = data["props"]["pageProps"]
                if "properties" in page_props:
                    properties.extend(page_props["properties"])
                elif "listings" in page_props:
                    properties.extend(page_props["listings"])
        except json.JSONDecodeError:
            continue
    
    # Fallback: parse HTML cards
    card_pattern = r'<div[^>]*class="[^"]*property[^"]*"[^>]*>(.*?)</div>'
    card_matches = re.findall(card_pattern, html, re.DOTALL)
    
    for card in card_matches:
        # Extract basic info from card HTML
        price_match = re.search(r'\$[\d,]+', card)
        address_match = re.search(r'<(?:h[23]|p)[^>]*>(.*?)</(?:h[23]|p)>', card)
        
        if price_match and address_match:
            properties.append({
                "price": price_match.group(0),
                "address": address_match.group(1).strip(),
            })
    
    return properties


def _parse_stessa_property(data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Parse a Stessa property listing into InvestorFlip format."""
    
    # Handle JSON-LD format
    if "@type" in data:
        address = data.get("address", {})
        if isinstance(address, dict):
            street = address.get("streetAddress", "")
            city = address.get("addressLocality", "")
            state = address.get("addressRegion", "TX")
            zip_code = address.get("postalCode", "")
        else:
            street = str(address)
            city = ""
            state = "TX"
            zip_code = ""
        
        price = data.get("offers", {})
        if isinstance(price, dict):
            price = price.get("price")
        if isinstance(price, str):
            price = float(price.replace(",", ""))
        
        return {
            "situs_address": f"{street}, {city}, {state} {zip_code}".strip(", "),
            "city": city,
            "state": state,
            "zip": zip_code,
            "price": price or 0,
            "listing_type": "Investment",
            "listing_status": "Active",
            "data_source": "Stessa Marketplace",
            "source_platform": "Stessa",
            "is_synthetic": False,
        }
    
    # Handle raw data format
    address = data.get("address") or data.get("street_address") or ""
    city = data.get("city", "")
    state = data.get("state", "TX")
    zip_code = data.get("zip") or data.get("postal_code") or ""
    
    price = data.get("price") or data.get("list_price")
    if isinstance(price, str):
        price = float(price.replace(",", "").replace("$", ""))
    
    return {
        "situs_address": f"{address}, {city}, {state} {zip_code}".strip(", ") if address else "",
        "city": city,
        "state": state,
        "zip": zip_code,
        "beds": data.get("bedrooms") or data.get("beds"),
        "baths": data.get("bathrooms") or data.get("baths"),
        "sqft": data.get("square_feet") or data.get("sqft"),
        "price": price or 0,
        "listing_type": "Investment",
        "listing_status": "Active",
        "data_source": "Stessa Marketplace",
        "source_platform": "Stessa",
        "is_synthetic": False,
    }


async def fetch_stessa_properties(limit: int = 100) -> List[Dict[str, Any]]:
    """Fetch properties from Stessa marketplace."""
    async with httpx.AsyncClient(
        timeout=30.0,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
    ) as client:
        response = await client.get(STESSA_MARKETPLACE_URL)
        response.raise_for_status()
    
    html = response.text
    raw_properties = _extract_json_from_html(html)
    
    properties = []
    for raw in raw_properties[:limit]:
        try:
            parsed = _parse_stessa_property(raw)
            if parsed and parsed.get("situs_address"):
                properties.append(parsed)
        except Exception as e:
            logger.warning("Failed to parse Stessa property: %s", e)
    
    logger.info("Fetched %d properties from Stessa", len(properties))
    return properties


async def import_stessa(db: PostgresDatabase, limit: int = 100) -> Dict[str, Any]:
    """Import Stessa marketplace properties into the database."""
    properties = await fetch_stessa_properties(limit)
    
    if not properties:
        return {"fetched": 0, "inserted": 0, "matched": 0, "skipped": 0}
    
    inserted = 0
    matched = 0
    skipped = 0
    
    for prop in properties:
        address = prop.get("situs_address", "")
        
        existing = await db.properties.find_one({"situs_address": address})
        
        if existing:
            await db.properties.update_one(
                {"id": existing["id"]},
                {"$set": {
                    "data_source": existing.get("data_source", "") + " + Stessa Marketplace",
                    "updated_at": "now",
                }},
            )
            matched += 1
        else:
            try:
                await db.properties.insert_one(prop)
                inserted += 1
            except Exception as e:
                logger.warning("Failed to insert %s: %s", address, e)
                skipped += 1
    
    return {
        "fetched": len(properties),
        "inserted": inserted,
        "matched": matched,
        "skipped": skipped,
    }


# CLI entry point
if __name__ == "__main__":
    import asyncio
    
    async def main():
        db = PostgresDatabase()
        try:
            await db.connect()
            result = await import_stessa(db)
            print(json.dumps(result, indent=2))
        finally:
            await db.close()
    
    asyncio.run(main())
