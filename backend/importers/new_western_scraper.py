"""New Western Marketplace scraper — pulls wholesale properties from the marketplace.

This module scrapes the public New Western marketplace for wholesale deals.
No API key required — uses the public agent marketplace pages.

Source: marketplace.newwestern.com
"""

from __future__ import annotations

import json
import re
import logging
from typing import Any, Dict, List, Optional

import httpx

from database import PostgresDatabase

logger = logging.getLogger("tarrantrei.new_western")

# New Western agent pages (Fort Worth market)
AGENT_PAGES = [
    "https://marketplace.newwestern.com/agent/brooke-cartwright",
    # Add more agents as needed
]

# Base URL for API calls
API_BASE = "https://marketplace.newwestern.com"


def _extract_json_from_html(html: str) -> List[Dict[str, Any]]:
    """Extract property data from Next.js JSON in the HTML."""
    # Look for the Next.js data payload
    # New Western uses React Server Components with serialized JSON
    
    # Try to find the main data structure
    properties = []
    
    # Look for deal data in the script tags
    # Pattern: self.__next_f.push([1, "...data..."])
    pattern = r'self\.__next_f\.push\(\[1,\s*"(.*?)"\]\)'
    matches = re.findall(pattern, html)
    
    for match in matches:
        try:
            # Unescape the string
            unescaped = match.encode().decode('unicode_escape')
            
            # Try to parse as JSON
            if '"listPrice"' in unescaped and '"bedroomsTotal"' in unescaped:
                # This looks like property data
                # Extract the JSON object
                json_pattern = r'\{"bathroomsFull".*?"status":\d+\}'
                json_matches = re.findall(json_pattern, unescaped)
                
                for jm in json_matches:
                    try:
                        obj = json.loads(jm)
                        if 'listPrice' in obj:
                            properties.append(obj)
                    except json.JSONDecodeError:
                        continue
        except Exception:
            continue
    
    return properties


def _parse_property(data: Dict[str, Any]) -> Dict[str, Any]:
    """Parse a New Western property into InvestorFlip format."""
    address_parts = []
    
    # Build address from available fields
    street = data.get("streetAddress") or data.get("street") or ""
    city = data.get("city", "")
    state = data.get("stateOrProvince", "TX")
    zip_code = data.get("postalCode", "")
    
    if street:
        address_parts.append(street)
    if city:
        address_parts.append(city)
    if state:
        address_parts.append(state)
    if zip_code:
        address_parts.append(zip_code)
    
    full_address = ", ".join(address_parts) if address_parts else ""
    
    # Get price
    list_price = data.get("listPrice")
    if isinstance(list_price, str):
        list_price = float(list_price.replace(",", ""))
    
    # Get photo
    photos = data.get("media", {}).get("primary", [])
    image_url = None
    if photos and len(photos) > 0:
        image_url = photos[0].get("mobileSrc") or photos[0].get("src")
    
    # Get market info
    market = data.get("market", {})
    market_name = market.get("name", "")
    
    return {
        "situs_address": full_address or f"{city}, {state} {zip_code}",
        "city": city,
        "state": state,
        "zip": zip_code,
        "county": "Tarrant",
        "beds": data.get("bedroomsTotal"),
        "baths": data.get("bathroomsFull"),
        "sqft": data.get("livingArea"),
        "price": list_price or 0,
        "listing_type": "Wholesale",
        "listing_status": "Active",
        "data_source": "New Western Marketplace",
        "source_platform": "New Western",
        "latitude": data.get("latitude"),
        "longitude": data.get("longitude"),
        "image_url": image_url,
        "is_synthetic": False,
        "market_name": market_name,
        "property_id_nw": data.get("salesforceId"),
        "description": f"New Western wholesale deal in {market_name}",
    }


async def fetch_new_western_properties(agent_url: str) -> List[Dict[str, Any]]:
    """Fetch properties from a New Western agent page."""
    async with httpx.AsyncClient(
        timeout=30.0,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
    ) as client:
        response = await client.get(agent_url)
        response.raise_for_status()
    
    html = response.text
    raw_properties = _extract_json_from_html(html)
    
    # Parse into InvestorFlip format
    properties = []
    for raw in raw_properties:
        try:
            parsed = _parse_property(raw)
            if parsed.get("situs_address"):
                properties.append(parsed)
        except Exception as e:
            logger.warning("Failed to parse property: %s", e)
    
    logger.info("Fetched %d properties from %s", len(properties), agent_url)
    return properties


async def fetch_all_new_western(limit: int = 100) -> List[Dict[str, Any]]:
    """Fetch properties from all configured New Western agent pages."""
    all_properties = []
    
    for agent_url in AGENT_PAGES:
        try:
            properties = await fetch_new_western_properties(agent_url)
            all_properties.extend(properties)
        except Exception as e:
            logger.warning("Failed to fetch from %s: %s", agent_url, e)
    
    # Deduplicate by address
    seen = set()
    unique = []
    for prop in all_properties:
        addr = prop.get("situs_address", "").upper()
        if addr and addr not in seen:
            seen.add(addr)
            unique.append(prop)
    
    return unique[:limit]


async def import_new_western(db: PostgresDatabase, limit: int = 100) -> Dict[str, Any]:
    """Import New Western wholesale properties into the database."""
    properties = await fetch_all_new_western(limit)
    
    if not properties:
        return {"fetched": 0, "inserted": 0, "matched": 0, "skipped": 0}
    
    inserted = 0
    matched = 0
    skipped = 0
    
    for prop in properties:
        address = prop.get("situs_address", "")
        
        # Check if property already exists
        existing = await db.properties.find_one({"situs_address": address})
        
        if existing:
            # Update with New Western data
            await db.properties.update_one(
                {"id": existing["id"]},
                {"$set": {
                    "listing_type": "Wholesale",
                    "listing_status": "Active",
                    "data_source": existing.get("data_source", "") + " + New Western Marketplace",
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
        from database import PostgresDatabase
        db = PostgresDatabase()
        try:
            await db.connect()
            result = await import_new_western(db)
            import json
            print(json.dumps(result, indent=2))
        finally:
            await db.close()
    
    asyncio.run(main())
