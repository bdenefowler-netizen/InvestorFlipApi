"""OffMarketDeck scraper — FREE off-market & wholesale deals.

Scrapes offmarketdeck.com for:
- Off-market properties
- Wholesale, fix & flip, buy & hold deals
- Value-add opportunities

Data extracted from Next.js RSC payload (self.__next_f.push).

Source: offmarketdeck.com (FREE, no login)
"""

from __future__ import annotations

import json
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
    """Fetch off-market deals from OffMarketDeck.

    Extracts deal data from Next.js RSC payload embedded in the page HTML.
    """
    url = f"{OFFMARKET_BASE}/{state}/{city}"

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

        # Extract deal objects from Next.js RSC payload
        # Pattern: self.__next_f.push([1,"...{\\"deal\\":{...}}..."])
        rsc_matches = re.findall(r'self\.__next_f\.push\(\[1,\s*"(.*?)"\]\)', html)

        for match in rsc_matches:
            try:
                # Unescape the RSC-encoded string
                decoded = match.encode().decode("unicode_escape")

                # Find "deal" objects in the decoded text
                # The format is: {"deal":{"id":"...","price_min":...,"beds":...,"title":"..."}}
                deal_pattern = r'\{\s*"deal"\s*:\s*\{[^}]+(?:\{[^}]*\}[^}]*)*\}\}'
                deal_matches = re.findall(deal_pattern, decoded)
                for dm in deal_matches:
                    try:
                        deal_obj = json.loads(dm)
                        deal = deal_obj.get("deal", {})
                        if deal and deal.get("title"):
                            prop = _parse_deal_to_property(deal)
                            if prop:
                                properties.append(prop)
                    except json.JSONDecodeError:
                        continue

            except Exception:
                continue

        # Deduplicate by address
        seen = set()
        unique = []
        for p in properties:
            addr = p.get("address", "").upper()
            if addr and addr not in seen:
                seen.add(addr)
                unique.append(p)

        logger.info("Fetched %d off-market deals from OffMarketDeck", len(unique))
        return unique

    except Exception as e:
        logger.warning("Failed to fetch OffMarketDeck deals: %s", e)
        return []


def _parse_deal_to_property(deal: Dict[str, Any]) -> Dict[str, Any] | None:
    """Parse an OffMarketDeck deal object into a property dict."""
    title = deal.get("title", "")
    if not title:
        return None

    # Parse address from title: "4713 TRUELAND DR, FORT WORTH, TX 76119"
    addr_match = re.match(
        r"(.+?),\s*(FORT WORTH|NORTH RICHLAND HILLS|ARLINGTON|BEDFORD|EULESS),\s*(TX)\s*(\d{5})",
        title, re.I,
    )
    if not addr_match:
        # Try "4224 Shagbark Court, Fort Worth, TX 76137, USA"
        addr_match = re.match(
            r"(.+?),\s*(.+?),\s*(TX)\s*(\d{5})",
            title, re.I,
        )
    if not addr_match:
        return None

    address = addr_match.group(1).strip().upper()
    city = addr_match.group(2).strip().upper()
    state = addr_match.group(3).strip().upper()
    zip_code = addr_match.group(4).strip()
    full_address = f"{address}, {city}, {state} {zip_code}"

    # Extract ARV from description HTML
    description = deal.get("description", "") or ""
    arv = None
    arv_match = re.search(r"ARV[:\s]*[A-Za-z]*\s*\$?([\d,]+)", description, re.I)
    if arv_match:
        arv = int(arv_match.group(1).replace(",", ""))

    # Extract repairs from description
    repairs = None
    repair_match = re.search(r"Repairs[:\s]*\$?([\d,]+)", description, re.I)
    if repair_match:
        repairs = int(repair_match.group(1).replace(",", ""))

    price = deal.get("price_min") or deal.get("price_max")
    beds = deal.get("beds")
    baths = deal.get("baths")
    sqft = deal.get("sqft")
    lot_sqft = deal.get("lot_sqft")
    year_built = deal.get("year_built")
    prop_type_raw = deal.get("property_type", "")
    property_type = "Single Family"
    if prop_type_raw == "house":
        property_type = "Single Family"
    elif prop_type_raw == "duplex":
        property_type = "Duplex"
    elif prop_type_raw == "multifamily":
        property_type = "Multifamily"
    elif prop_type_raw == "condo":
        property_type = "Condo"
    elif prop_type_raw == "land":
        property_type = "Vacant Lot"

    county = "Tarrant"
    if "TARRANT" in full_address or "FORT WORTH" in full_address:
        county = "Tarrant"
    elif "DALLAS" in full_address:
        county = "Dallas"

    return {
        "id": f"omd-{deal.get('id', f'{hash(full_address) & 0xFFFFFFFF:08x}')}",
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
        "lot_size_sqft": lot_sqft,
        "property_type": property_type,
        "price": price,
        "assessed_value": None,
        "market_value": arv,
        "repair_estimate": repairs,
        "owner_name": "",
        "owner_type": "Unknown",
        "owner_mailing_address": "",
        "out_of_state_owner": False,
        "absentee_owner": False,
        "owner_occupied": False,
        "data_source": "OffMarketDeck",
        "source_platform": "OffMarketDeck",
        "listing_type": "Wholesale",
        "listing_status": "Active",
        "is_synthetic": False,
        "wholesale": True,
        "off_market": True,
        "fixer_upper": True,
        "omd_data": {
            "arv": arv,
            "repair_estimate": repairs,
            "deal_id": deal.get("id"),
            "strategy_id": deal.get("strategy_id"),
            "description": description[:500] if description else "",
        },
    }


async def import_offmarket_deals(
    db: PostgresDatabase,
    city: str = "fort-worth",
    pages: int = 3,
) -> Dict[str, Any]:
    """Import OffMarketDeck deals into the database."""
    all_properties = await fetch_offmarket_deals(city=city)

    if not all_properties:
        return {"fetched": 0, "inserted": 0, "matched": 0, "skipped": 0}

    inserted = 0
    matched = 0
    skipped = 0

    for prop in all_properties:
        address = prop.get("situs_address", "")
        if not address:
            skipped += 1
            continue

        try:
            existing = await db.properties.find_one({"situs_address": address})
            if existing:
                update_fields = {}
                if not existing.get("price") and prop.get("price"):
                    update_fields["price"] = prop["price"]
                if not existing.get("market_value") and prop.get("market_value"):
                    update_fields["market_value"] = prop["market_value"]
                update_fields["data_source"] = existing.get("data_source", "") + " + OffMarketDeck"
                update_fields["listing_type"] = "Wholesale"
                update_fields["wholesale"] = True
                update_fields["off_market"] = True
                update_fields["omd_data"] = prop.get("omd_data")
                await db.properties.update_one({"id": existing["id"]}, {"$set": update_fields})
                matched += 1
            else:
                await db.properties.insert_one(prop)
                inserted += 1
        except Exception as e:
            logger.warning("Failed to process deal %s: %s", address, e)
            skipped += 1

    return {"fetched": len(all_properties), "inserted": inserted, "matched": matched, "skipped": skipped}


if __name__ == "__main__":
    import asyncio, json

    async def main():
        from database import PostgresDatabase
        db = PostgresDatabase()
        try:
            await db.connect()
            result = await import_offmarket_deals(db)
            print(json.dumps(result, indent=2))
        finally:
            await db.close()

    asyncio.run(main())
