"""ForeclosureListingsUSA scraper — FREE foreclosure listings.

Scrapes foreclosurelistingsusa.com for:
- Pre-foreclosures, short sales, auctions, sheriff sales
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
    """Fetch foreclosure listings from ForeclosureListingsUSA.

    Uses URL format: /fort-worth-tx/ (not /tx/fort-worth/)
    """
    # Correct URL format: /{city}-{state}/
    url = f"{FORECLOSURE_BASE}/{city}-{state}/"
    params = {"page": page} if page > 1 else {}

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            response = await client.get(url, params=params, headers=headers)
            response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")
        properties = []

        # Find listing cards - each is wrapped in col-md-4
        for container in soup.find_all("div", class_=re.compile(r"col-md-4")):
            try:
                prop = _parse_listing_container(container)
                if prop and prop.get("address"):
                    properties.append(prop)
            except Exception as e:
                logger.debug("Failed to parse listing container: %s", e)

        # Fallback: find property-container divs directly
        if not properties:
            for container in soup.find_all("div", class_="property-container"):
                try:
                    prop = _parse_listing_container(container.find_parent("div", class_=re.compile(r"col-md-4")))
                    if prop and prop.get("address"):
                        properties.append(prop)
                except Exception as e:
                    logger.debug("Failed to parse property-container: %s", e)

        if not properties:
            properties = _parse_raw_html(response.text)

        logger.info(
            "Fetched %d foreclosure listings from %s, page %d",
            len(properties), city, page,
        )
        return properties

    except Exception as e:
        logger.warning("Failed to fetch foreclosure listings: %s", e)
        return []


def _parse_listing_container(container) -> Dict[str, Any] | None:
    """Parse a single listing card from the container div."""
    # --- Price ---
    price_div = container.find("div", class_="property-price")
    price = None
    if price_div:
        price_text = price_div.get_text(strip=True)
        price_match = re.search(r"\$([\d,]+)", price_text)
        if price_match:
            price = int(price_match.group(1).replace(",", ""))

    # --- Property type (status badge) ---
    status_div = container.find("div", class_="property-status")
    prop_type = "Single Family"
    if status_div:
        prop_type = status_div.get_text(strip=True) or "Single Family"

    # --- Features: sqft, beds, baths ---
    features_div = container.find("div", class_="property-features")
    sqft = None
    beds = None
    baths = None
    if features_div:
        features_text = features_div.get_text()
        sqft_match = re.search(r"([\d,]+)\s*sqft", features_text, re.I)
        if sqft_match:
            sqft = int(sqft_match.group(1).replace(",", ""))
        beds_match = re.search(r"(\d+)\s*bed", features_text, re.I)
        if beds_match:
            beds = int(beds_match.group(1))
        baths_match = re.search(r"(\d+)\s*bath", features_text, re.I)
        if baths_match:
            baths = int(baths_match.group(1))

    # --- Address ---
    content_div = container.find("div", class_="property-content")
    address = None
    city = "Fort Worth"
    state = "TX"
    zip_code = ""
    if content_div:
        addr_div = content_div.find("div", class_="address")
        if addr_div:
            address = addr_div.get_text(strip=True)
        small = content_div.find("small")
        if small:
            csz_text = small.get_text(strip=True)
            csz_match = re.match(r"(.+?),\s*(TX|TX)\s*(\d{5})", csz_text, re.I)
            if csz_match:
                city = csz_match.group(1).strip()
                state = csz_match.group(2).strip()
                zip_code = csz_match.group(3).strip()

    if not address:
        return None

    return {
        "address": address.upper(),
        "city": city.upper(),
        "state": state.upper(),
        "zip": zip_code,
        "price": price,
        "sqft": sqft,
        "beds": beds,
        "baths": baths,
        "property_type": prop_type or "Single Family",
    }


def _parse_raw_html(html: str) -> List[Dict[str, Any]]:
    """Parse properties from raw HTML when structured parsing fails."""
    properties = []
    # Match common street suffixes in Fort Worth
    addr_re = r"(\d+\s+[A-Z][A-Za-z\s]+(?:St|Ave|Dr|Ln|Ct|Blvd|Rd|Way|Pl|Cir))\s*,?\s*FORT WORTH,\s*TX\s*(\d{5})"
    matches = re.findall(addr_re, html, re.I)
    for addr, zip_code in matches:
        properties.append({
            "address": addr.strip().upper(),
            "city": "FORT WORTH",
            "state": "TX",
            "zip": zip_code,
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
        "id": f"flusa-{hash(full_address) & 0xFFFFFFFF:08x}",
        "situs_address": full_address,
        "city": city,
        "state": state,
        "zip": zip_code,
        "county": "Tarrant",
        "latitude": None,
        "longitude": None,
        "beds": raw.get("beds"),
        "baths": raw.get("baths"),
        "sqft": raw.get("sqft"),
        "year_built": None,
        "lot_size_sqft": None,
        "property_type": raw.get("property_type", "Single Family"),
        "price": raw.get("price"),
        "assessed_value": None,
        "market_value": None,
        "owner_name": "",
        "owner_type": "Unknown",
        "owner_mailing_address": "",
        "out_of_state_owner": False,
        "absentee_owner": False,
        "owner_occupied": False,
        "data_source": "ForeclosureListingsUSA",
        "source_platform": "ForeclosureListingsUSA",
        "listing_type": "Foreclosure",
        "listing_status": "Active",
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
                update_fields = {}
                if not existing.get("price") and prop.get("price"):
                    update_fields["price"] = prop["price"]
                update_fields["data_source"] = existing.get("data_source", "") + " + ForeclosureListingsUSA"
                update_fields["listing_type"] = "Foreclosure"
                update_fields["foreclosure"] = True
                await db.properties.update_one({"id": existing["id"]}, {"$set": update_fields})
                matched += 1
            else:
                await db.properties.insert_one(prop)
                inserted += 1
        except Exception as e:
            logger.warning("Failed to process listing: %s", e)
            skipped += 1

    return {"fetched": len(all_properties), "inserted": inserted, "matched": matched, "skipped": skipped}


if __name__ == "__main__":
    import asyncio, json

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
