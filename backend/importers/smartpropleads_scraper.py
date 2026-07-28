"""SmartPropLeads scraper — FREE DFW off-market seller leads.

SmartPropLeads provides:
- 3M+ parcels across 11 North Texas counties
- 14 motivated-seller lead types
- AI-scored leads (Hot/Warm)
- Data from public county appraisal records
- FREE to browse

Source: smartpropleads.com (FREE public browsing)
"""

from __future__ import annotations

import json
import re
import logging
from typing import Any, Dict, List, Optional

import httpx

from database import PostgresDatabase

logger = logging.getLogger("tarrantrei.smartpropleads")

SMARTPROP_BASE = "https://smartpropleads.com"
SMARTPROP_API = "https://api.smartpropleads.com"

# Lead types available on SmartPropLeads
LEAD_TYPES = {
    "absentee-owner": {"label": "Absentee Owners", "count": 543073, "hot": 11733, "warm": 59438},
    "out-of-state": {"label": "Out-of-State Owners", "count": 45811, "hot": 3813, "warm": 12612},
    "non-owner-occupied": {"label": "Non-Owner Occupied", "count": 1100000, "hot": 27162, "warm": 56964},
    "long-term-owner": {"label": "Long-Term Owners (15y+)", "count": 315488, "hot": 14177, "warm": 48543},
    "senior-owner": {"label": "Senior / OV65 Owners", "count": 233138, "hot": 5687, "warm": 45478},
    "vacant-lot": {"label": "Vacant Lots", "count": 187302, "hot": 2305, "warm": 6290},
    "new-construction": {"label": "New Construction", "count": 117842, "hot": 287, "warm": 436},
    "recent-transfer": {"label": "Recent Transfers", "count": 285134, "hot": 2570, "warm": 2490},
    "pre-foreclosure": {"label": "Pre-Foreclosure", "count": 846, "hot": 843, "warm": 0},
    "tax-delinquent": {"label": "Tax Delinquent", "count": 79498, "hot": 42994, "warm": 21167},
    "high-equity": {"label": "High Equity Owners", "count": 453132, "hot": 9628, "warm": 49316},
    "cash-buyer": {"label": "Cash / Investor Buyers", "count": 196158, "hot": 1580, "warm": 4578},
    "free-clear": {"label": "Free & Clear (No Mortgage)", "count": 45406, "hot": 190, "warm": 760},
    "commercial": {"label": "Commercial", "count": 133936, "hot": 2460, "warm": 8349},
}

# Counties covered
COUNTIES = [
    "Collin", "Dallas", "Denton", "Tarrant",
    "Rockwall", "Kaufman", "Ellis", "Johnson",
    "Parker", "Wise", "Hunt"
]


async def fetch_smartpropleads_page(lead_type: str, county: str = "Tarrant") -> List[Dict[str, Any]]:
    """Fetch leads from SmartPropLeads browse page.
    
    This scrapes the public browse page for a specific lead type and county.
    """
    url = f"{SMARTPROP_BASE}/leads/search"
    params = {
        "type": lead_type,
        "county": county,
    }
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, params=params, headers=headers)
            response.raise_for_status()
        
        html = response.text
        
        # Extract property data from Next.js React Server Components
        properties = []
        
        # Look for property data in the script tags
        # SmartPropLeads uses __next_f.push to inject data
        pattern = r'self\.__next_f\.push\(\[1,\s*"(.*?)"\]\)'
        matches = re.findall(pattern, html)
        
        for match in matches:
            try:
                unescaped = match.encode().decode('unicode_escape')
                
                # Look for property-like data structures
                if '"address"' in unescaped or '"ownerName"' in unescaped:
                    # Try to extract JSON objects
                    json_pattern = r'\{[^{}]*"address"[^{}]*\}'
                    json_matches = re.findall(json_pattern, unescaped)
                    
                    for jm in json_matches:
                        try:
                            obj = json.loads(jm)
                            if 'address' in obj:
                                properties.append(obj)
                        except json.JSONDecodeError:
                            continue
            except Exception:
                continue
        
        logger.info("Fetched %d properties from SmartPropLeads (%s, %s)", 
                    len(properties), lead_type, county)
        return properties
        
    except Exception as e:
        logger.warning("Failed to fetch SmartPropLeads: %s", e)
        return []


def _parse_smartpropleads_property(raw: Dict[str, Any], lead_type: str) -> Dict[str, Any]:
    """Parse a SmartPropLeads property into InvestorFlip format."""
    
    # Extract address components
    address = raw.get("address", "")
    city = raw.get("city", "")
    state = raw.get("state", "TX")
    zip_code = raw.get("zip", "")
    
    # Build full address if not provided as single string
    if not address and city:
        street = raw.get("street", "")
        address = f"{street}, {city}, {state} {zip_code}".strip(", ")
    
    # Get owner info
    owner_name = raw.get("ownerName", "")
    mailing_address = raw.get("mailingAddress", "")
    
    # Get property details
    beds = raw.get("beds")
    baths = raw.get("baths")
    sqft = raw.get("sqft")
    year_built = raw.get("yearBuilt")
    assessed_value = raw.get("assessedValue")
    market_value = raw.get("marketValue")
    
    # Get coordinates
    lat = raw.get("lat") or raw.get("latitude")
    lon = raw.get("lng") or raw.get("longitude")
    
    # Get AI score
    ai_score = raw.get("score") or raw.get("aiScore")
    is_hot = raw.get("isHot", False)
    is_warm = raw.get("isWarm", False)
    
    # Determine listing type based on lead type
    listing_type_map = {
        "absentee-owner": "Absentee Owner",
        "out-of-state": "Out-of-State Owner",
        "non-owner-occupied": "Non-Owner Occupied",
        "long-term-owner": "Long-Term Owner",
        "senior-owner": "Senior Owner",
        "vacant-lot": "Vacant Lot",
        "new-construction": "New Construction",
        "recent-transfer": "Recent Transfer",
        "pre-foreclosure": "Pre-Foreclosure",
        "tax-delinquent": "Tax Delinquent",
        "high-equity": "High Equity",
        "cash-buyer": "Cash Buyer",
        "free-clear": "Free & Clear",
        "commercial": "Commercial",
    }
    
    listing_type = listing_type_map.get(lead_type, "Lead")
    
    # Check for distress indicators
    is_vacant = lead_type == "vacant-lot" or raw.get("vacant", False)
    is_distressed = lead_type in ["pre-foreclosure", "tax-delinquent"]
    
    return {
        "id": f"spl-{raw.get('id', raw.get('parcelId', ''))}",
        "situs_address": address,
        "city": city,
        "state": state,
        "zip": zip_code,
        "county": raw.get("county", "Tarrant"),
        "latitude": lat,
        "longitude": lon,
        
        # Property facts
        "beds": beds,
        "baths": baths,
        "sqft": sqft,
        "year_built": year_built,
        "lot_size_sqft": raw.get("lotSize"),
        "property_type": raw.get("propertyType", "Single Family Residential"),
        
        # Financial
        "price": 0,  # Not provided in browse
        "assessed_value": assessed_value,
        "market_value": market_value,
        
        # Owner info
        "owner_name": owner_name,
        "owner_type": raw.get("ownerType", "Unknown"),
        "owner_mailing_address": mailing_address,
        "out_of_state_owner": raw.get("outOfState", False),
        "absentee_owner": raw.get("absentee", False),
        "owner_occupied": raw.get("ownerOccupied", True),
        
        # Lead scoring
        "ai_score": ai_score,
        "is_hot": is_hot,
        "is_warm": is_warm,
        
        # Source
        "data_source": "SmartPropLeads",
        "source_platform": "SmartPropLeads",
        "listing_type": listing_type,
        "listing_status": "Active",
        
        # Flags
        "is_synthetic": False,
        "vacant": is_vacant,
        "tax_delinquent": lead_type == "tax-delinquent",
        "high_equity": lead_type == "high-equity",
        
        # SmartPropLeads specific
        "spl_data": {
            "lead_type": lead_type,
            "lead_label": LEAD_TYPES.get(lead_type, {}).get("label", lead_type),
            "ai_score": ai_score,
            "is_hot": is_hot,
            "is_warm": is_warm,
            "parcel_id": raw.get("parcelId"),
        },
    }


async def fetch_tarrant_leads(
    lead_types: Optional[List[str]] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """Fetch leads from SmartPropLeads for Tarrant County."""
    
    if not lead_types:
        # Default to the most useful lead types for investors
        lead_types = [
            "absentee-owner",
            "out-of-state",
            "tax-delinquent",
            "pre-foreclosure",
            "high-equity",
            "vacant-lot",
        ]
    
    all_properties = []
    
    for lead_type in lead_types:
        try:
            raw_properties = await fetch_smartpropleads_page(
                lead_type=lead_type,
                county="Tarrant"
            )
            
            for raw in raw_properties:
                prop = _parse_smartpropleads_property(raw, lead_type)
                if prop.get("situs_address"):
                    all_properties.append(prop)
                    
        except Exception as e:
            logger.warning("Failed to fetch %s: %s", lead_type, e)
    
    # Deduplicate by address
    seen = set()
    unique = []
    for prop in all_properties:
        addr = prop.get("situs_address", "").upper()
        if addr and addr not in seen:
            seen.add(addr)
            unique.append(prop)
    
    return unique[:limit]


async def import_smartpropleads(
    db: PostgresDatabase,
    lead_types: Optional[List[str]] = None,
    limit: int = 100,
) -> Dict[str, Any]:
    """Import SmartPropLeads into the database."""
    properties = await fetch_tarrant_leads(lead_types, limit)
    
    if not properties:
        return {"fetched": 0, "inserted": 0, "matched": 0, "skipped": 0}
    
    inserted = 0
    matched = 0
    skipped = 0
    
    for prop in properties:
        address = prop.get("situs_address", "")
        
        existing = await db.properties.find_one({"situs_address": address})
        
        if existing:
            # Update with SmartPropLeads data
            update_fields = {}
            
            # Add AI score if not present
            if not existing.get("ai_score") and prop.get("ai_score"):
                update_fields["ai_score"] = prop["ai_score"]
                update_fields["is_hot"] = prop.get("is_hot", False)
                update_fields["is_warm"] = prop.get("is_warm", False)
            
            # Update data source
            update_fields["data_source"] = existing.get("data_source", "") + " + SmartPropLeads"
            update_fields["spl_data"] = prop.get("spl_data")
            
            if update_fields:
                await db.properties.update_one(
                    {"id": existing["id"]},
                    {"$set": update_fields},
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
        "lead_types_queried": len(lead_types or []),
    }


# CLI entry point
if __name__ == "__main__":
    import asyncio
    
    async def main():
        from database import PostgresDatabase
        db = PostgresDatabase()
        try:
            await db.connect()
            result = await import_smartpropleads(db, limit=50)
            print(json.dumps(result, indent=2))
        finally:
            await db.close()
    
    asyncio.run(main())
