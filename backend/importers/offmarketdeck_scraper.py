"""OffMarketDeck scraper — FREE off-market & wholesale deals.

Scrapes offmarketdeck.com for:
- Off-market properties
- Wholesale deals
- Fix & flip opportunities
- Buy & hold properties
- Value-add deals

Source: offmarketdeck.com (FREE, no login required)
"""

from __future__ import annotations

import re
import logging
from typing import Any, Dict, List

import httpx

from database import PostgresDatabase

logger = logging.getLogger("tarrantrei.offmarketdeck")

OFFMARKET_BASE = "https://offmarketdeck.com"


async def fetch_offmarket_deals(
    city: str = "fort-worth",
    state: str = "texas",
    page: int = 1,
) -> List[Dict[str, Any]]:
    """Fetch off-market deals from OffMarketDeck."""
    url = f"{OFFMARKET_BASE}/{state}/{city}" if city else f"{OFFMARKET_BASE}/{state}"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
        
        html = response.text
        properties = []
        
        # Extract deal cards from the page
        # Look for price and ARV patterns
        price_pattern = r'\$(\d{1,3}(?:,\d{3})*(?:,\d{3})*)\s*ARV\s*\$(\d{1,3}(?:,\d{3})*(?:,\d{3})*)'
        price_matches = re.findall(price_pattern, html)
        
        # Look for address patterns
        address_pattern = r'(\d+\s+[A-Z][A-Za-z\s]+(?:St|Ave|Dr|Ln|Ct|Blvd|Rd|Way|Pl|Cir))\s*,?\s*Fort Worth,\s*TX\s*(\d{5})'
        address_matches = re.findall(address_pattern, html)
        
        # Look for property details
        detail_pattern = r'(\d+)\s*Beds?\s*(\d+)\s*Baths?\s*([\d,]+)\s*sq\.?ft'
        detail_matches = re.findall(detail_pattern, html, re.I)
        
        # Combine matches
        for i, (price, arv) in enumerate(price_matches):
            prop = {
                "price": int(price.replace(",", "")),
                "arv": int(arv.replace(",", "")),
                "city": "FORT WORTH",
                "state": "TX",
            }
            
            if i < len(address_matches):
                prop["address"] = address_matches[i][0].upper()
                prop["zip"] = address_matches[i][1]
            
            if i < len(detail_matches):
                prop["beds"] = int(detail_matches[i][0])
                prop["baths"] = int(detail_matches[i][1])
                prop["sqft"] = int(detail_matches[i][2].replace(",", ""))
            
            properties.append(prop)
        
        logger.info("Fetched %d off-market deals from OffMarketDeck", len(properties))
        return properties
        
    except Exception as e:
        logger.warning("Failed to fetch OffMarketDeck deals: %s", e)
        return []


def _parse_offmarket_property(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Convert an OffMarketDeck listing to InvestorFlip format."""
    address = raw.get("address", "")
    city = raw.get("city", "FORT WORTH")
    state = raw.get("state", "TX")
    zip_code = raw.get("zip", "")
    
    full_address = f"{address}, {city}, {state} {zip_code}".strip(", ")
    
    # Calculate equity potential
    arv = raw.get("arv", 0)
    price = raw.get("price", 0)
    equity_potential = arv - price if arv and price else None
    
    return {
        "id": f"offmarket-{hash(full_address) & 0xFFFFFFFF:08x}",
        "situs_address": full_address,
        "city": city,
        "state": state,
        "zip": zip_code,
        "county": "Tarrant",
        "latitude": None,
        "longitude": None,
        
        # Property facts
        "beds": raw.get("beds"),
        "baths": raw.get("baths"),
        "sqft": raw.get("sqft"),
        "year_built": None,
        "lot_size_sqft": None,
        "property_type": "Single Family",
        
        # Financial
        "price": price,
        "assessed_value": None,
        "market_value": arv,
        
        # Owner info
        "owner_name": "",
        "owner_type": "Unknown",
        "owner_mailing_address": "",
        "out_of_state_owner": False,
        "absentee_owner": False,
        "owner_occupied": False,
        
        # Source
        "data_source": "OffMarketDeck",
        "source_platform": "OffMarketDeck",
        "listing_type": "Wholesale",
        "listing_status": "Active",
        
        # Flags
        "is_synthetic": False,
        "wholesale": True,
        "off_market": True,
        "fixer_upper": True,
        
        # OffMarketDeck specific
        "offmarket_data": {
            "arv": arv,
            "equity_potential": equity_potential,
            "strategy": "Wholesale",
            "grade": raw.get("grade", ""),
        },
    }


async def import_offmarket_deals(
    db: PostgresDatabase,
    city: str = "fort-worth",
    pages: int = 3,
) -> Dict[str, Any]:
    """Import OffMarketDeck deals into the database."""
    all_properties = []
    
    for page in range(1, pages + 1):
        try:
            raw_properties = await fetch_offmarket_deals(city=city, page=page)
            all_properties.extend(raw_properties)
        except Exception as e:
            logger.warning("Failed to fetch page %d: %s", page, e)
    
    if not all_properties:
        return {"fetched": 0, "inserted": 0, "matched": 0, "skipped": 0}
    
    inserted = 0
    matched = 0
    skipped = 0
    
    for raw in all_properties:
        try:
            prop = _parse_offmarket_property(raw)
            
            address = prop.get("situs_address", "")
            if not address:
                skipped += 1
                continue
            
            existing = await db.properties.find_one({"situs_address": address})
            
            if existing:
                # Update with off-market data
                update_fields = {}
                
                if not existing.get("price") and prop.get("price"):
                    update_fields["price"] = prop["price"]
                
                if not existing.get("market_value") and prop.get("market_value"):
                    update_fields["market_value"] = prop["market_value"]
                
                update_fields["data_source"] = existing.get("data_source", "") + " + OffMarketDeck"
                update_fields["listing_type"] = "Wholesale"
                update_fields["wholesale"] = True
                
                await db.properties.update_one(
                    {"id": existing["id"]},
                    {"$set": update_fields},
                )
                matched += 1
            else:
                await db.properties.insert_one(prop)
                inserted += 1
        except Exception as e:
            logger.warning("Failed to process deal: %s", e)
            skipped += 1
    
    return {
        "fetched": len(all_properties),
        "inserted": inserted,
        "matched": matched,
        "skipped": skipped,
    }


# CLI entry point
if __name__ == "__main__":
    import asyncio
    import json
    
    async def main():
        from database import PostgresDatabase
        db = PostgresDatabase()
        try:
            await db.connect()
            result = await import_offmarket_deals(db, pages=2)
            print(json.dumps(result, indent=2))
        finally:
            await db.close()
    
    asyncio.run(main())
