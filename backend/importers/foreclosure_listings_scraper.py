"""ForeclosureListingsUSA scraper — FREE foreclosure listings.

Scrapes foreclosurelistingsusa.com for:
- Pre-foreclosures
- Short sales
- Home auctions
- Sheriff sales
- Government foreclosures (Fannie Mae, Freddie Mac, HUD, VA)
- Bank owned homes

Source: foreclosurelistingsusa.com (FREE, no subscription)
"""

from __future__ import annotations

import re
import logging
from typing import Any, Dict, List

import httpx
from bs4 import BeautifulSoup

from database import PostgresDatabase

logger = logging.getLogger("tarrantrei.foreclosure_listings")

FORECLOSURE_BASE = "https://www.foreclosurelistingsusa.com"


async def fetch_foreclosure_listings(
    city: str = "fort-worth",
    state: str = "tx",
    page: int = 1,
) -> List[Dict[str, Any]]:
    """Fetch foreclosure listings from ForeclosureListingsUSA."""
    url = f"{FORECLOSURE_BASE}/{state}/{city}/"
    params = {"page": page} if page > 1 else {}
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, params=params, headers=headers)
            response.raise_for_status()
        
        soup = BeautifulSoup(response.text, "html.parser")
        properties = []
        
        # Find property cards
        # The site uses a consistent structure for listings
        for card in soup.find_all("div", class_=re.compile(r"listing|property|card", re.I)):
            try:
                prop = _parse_listing_card(card)
                if prop and prop.get("address"):
                    properties.append(prop)
            except Exception as e:
                logger.debug("Failed to parse card: %s", e)
                continue
        
        # If no cards found with class, try to parse the raw HTML structure
        if not properties:
            properties = _parse_raw_html(response.text)
        
        logger.info("Fetched %d foreclosure listings from %s, page %d", 
                    len(properties), city, page)
        return properties
        
    except Exception as e:
        logger.warning("Failed to fetch foreclosure listings: %s", e)
        return []


def _parse_listing_card(card) -> Dict[str, Any]:
    """Parse a listing card element."""
    # Try to find price
    price_elem = card.find(string=re.compile(r"\$[\d,]+"))
    price = None
    if price_elem:
        price_match = re.search(r"\$([\d,]+)", price_elem)
        if price_match:
            price = int(price_match.group(1).replace(",", ""))
    
    # Try to find address
    address_elem = card.find("a", href=re.compile(r"/property/"))
    address = None
    city_state_zip = None
    
    if address_elem:
        address_text = address_elem.get_text(strip=True)
        # Parse address lines
        lines = [l.strip() for l in address_text.split("\n") if l.strip()]
        if len(lines) >= 2:
            address = lines[0]
            city_state_zip = lines[1]
        elif lines:
            address = lines[0]
    
    # Try to find property details
    sqft = None
    beds = None
    baths = None
    
    detail_text = card.get_text()
    
    sqft_match = re.search(r"([\d,]+)\s*sqft", detail_text, re.I)
    if sqft_match:
        sqft = int(sqft_match.group(1).replace(",", ""))
    
    beds_match = re.search(r"(\d+)\s*beds?", detail_text, re.I)
    if beds_match:
        beds = int(beds_match.group(1))
    
    baths_match = re.search(r"(\d+)\s*baths?", detail_text, re.I)
    if baths_match:
        baths = int(baths_match.group(1))
    
    # Try to find property type
    prop_type = "Single Family"
    if "Duplex" in detail_text:
        prop_type = "Duplex"
    elif "Triplex" in detail_text:
        prop_type = "Triplex"
    elif "Manufactured" in detail_text:
        prop_type = "Manufactured Housing"
    elif "Condo" in detail_text:
        prop_type = "Condo"
    elif "Commercial" in detail_text:
        prop_type = "Commercial"
    
    if not address:
        return None
    
    # Parse city and state from city_state_zip
    city = "Fort Worth"
    state = "TX"
    zip_code = ""
    
    if city_state_zip:
        csz_match = re.match(r"(.+?),\s*(\w{2})\s+(\d{5})", city_state_zip)
        if csz_match:
            city = csz_match.group(1).strip()
            state = csz_match.group(2).strip()
            zip_code = csz_match.group(3).strip()
    
    return {
        "address": address.upper(),
        "city": city.upper(),
        "state": state.upper(),
        "zip": zip_code,
        "price": price,
        "sqft": sqft,
        "beds": beds,
        "baths": baths,
        "property_type": prop_type,
    }


def _parse_raw_html(html: str) -> List[Dict[str, Any]]:
    """Parse properties from raw HTML when card parsing fails."""
    properties = []
    
    # Find all address patterns
    address_pattern = r"(\d+\s+[A-Z\s]+(?:AVE|ST|DR|LN|CT|BLVD|RD|WAY|PL|CIR|CANYON|CREST|GREENE|PARKVIEW|CASINO|PANGOLIN|BIRDELL|CEDARCREST|CLOER|KELLY|OAKLAND|RYAN|TURNER|VALENCIA|RODEO|BULLHEAD|SAN FRANCISCO|SYLVANIA|FRAZIER|BARNES|CALMONT|HARBOUR|ALSTON|RICOCHET|CRESTLINE|LEDGESTONE|CLEAR LAKE|CANYON|TURNER MAY))"
    
    matches = re.findall(address_pattern, html)
    
    for match in matches:
        properties.append({
            "address": match.strip(),
            "city": "FORT WORTH",
            "state": "TX",
            "zip": "",
            "price": None,
            "sqft": None,
            "beds": None,
            "baths": None,
            "property_type": "Single Family",
        })
    
    return properties


def _parse_foreclosure_property(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Convert a foreclosure listing to InvestorFlip format."""
    address = raw.get("address", "")
    city = raw.get("city", "FORT WORTH")
    state = raw.get("state", "TX")
    zip_code = raw.get("zip", "")
    
    full_address = f"{address}, {city}, {state} {zip_code}".strip(", ")
    
    return {
        "id": f"foreclosure-{hash(full_address) & 0xFFFFFFFF:08x}",
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
        "property_type": raw.get("property_type", "Single Family"),
        
        # Financial
        "price": raw.get("price"),
        "assessed_value": None,
        "market_value": None,
        
        # Owner info
        "owner_name": "",
        "owner_type": "Unknown",
        "owner_mailing_address": "",
        "out_of_state_owner": False,
        "absentee_owner": False,
        "owner_occupied": False,
        
        # Source
        "data_source": "ForeclosureListingsUSA",
        "source_platform": "ForeclosureListingsUSA",
        "listing_type": "Foreclosure",
        "listing_status": "Active",
        
        # Flags
        "is_synthetic": False,
        "foreclosure": True,
        "pre_foreclosure": True,
    }


async def import_foreclosure_listings(
    db: PostgresDatabase,
    city: str = "fort-worth",
    pages: int = 5,
) -> Dict[str, Any]:
    """Import foreclosure listings into the database."""
    all_properties = []
    
    for page in range(1, pages + 1):
        try:
            raw_properties = await fetch_foreclosure_listings(city=city, page=page)
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
            prop = _parse_foreclosure_property(raw)
            
            address = prop.get("situs_address", "")
            if not address:
                skipped += 1
                continue
            
            existing = await db.properties.find_one({"situs_address": address})
            
            if existing:
                # Update with foreclosure data
                update_fields = {}
                
                if not existing.get("price") and prop.get("price"):
                    update_fields["price"] = prop["price"]
                
                update_fields["data_source"] = existing.get("data_source", "") + " + ForeclosureListingsUSA"
                update_fields["listing_type"] = "Foreclosure"
                update_fields["foreclosure"] = True
                
                await db.properties.update_one(
                    {"id": existing["id"]},
                    {"$set": update_fields},
                )
                matched += 1
            else:
                await db.properties.insert_one(prop)
                inserted += 1
        except Exception as e:
            logger.warning("Failed to process listing: %s", e)
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
            result = await import_foreclosure_listings(db, pages=3)
            print(json.dumps(result, indent=2))
        finally:
            await db.close()
    
    asyncio.run(main())
