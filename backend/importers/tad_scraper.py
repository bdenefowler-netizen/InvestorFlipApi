"""Tarrant Appraisal District (TAD) scraper — FREE public data, no subscription.

TAD provides ArcGIS REST APIs with:
- Property ownership info
- Tax assessed values
- Parcel boundaries
- Owner mailing addresses
- Property characteristics

Source: gis-tad.opendata.arcgis.com (ArcGIS Feature Services)
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

from database import PostgresDatabase

logger = logging.getLogger("tarrantrei.tad")

# TAD ArcGIS Feature Services (FREE, no API key)
TAD_PROPERTY_URL = (
    "https://services8.arcgis.com/5S5T6XdxjqI5BK2Y/arcgis/rest/services/"
    "TAD_Parcels_1/FeatureServer/0/query"
)

TAD_IMPROVEMENT_URL = (
    "https://services8.arcgis.com/5S5T6XdxjqI5BK2Y/arcgis/rest/services/"
    "TAD_Improvements/FeatureServer/0/query"
)


def _query_tad(
    url: str,
    where: str = "1=1",
    out_fields: str = "*",
    result_offset: int = 0,
    result_count: int = 1000,
    extra_params: Optional[Dict[str, str]] = None,
) -> List[Dict[str, Any]]:
    """Query a TAD ArcGIS Feature Service."""
    params = {
        "where": where,
        "outFields": out_fields,
        "resultOffset": str(result_offset),
        "resultRecordCount": str(result_count),
        "f": "json",
        "returnGeometry": "false",
    }
    if extra_params:
        params.update(extra_params)
    
    with httpx.Client(timeout=60.0) as client:
        response = await_or_sync(client.get(url, params=params))
        response.raise_for_status()
        data = response.json()
    
    features = data.get("features", [])
    return [f.get("attributes", {}) for f in features]


# Make it sync for simplicity
import httpx

def query_tad_sync(
    url: str,
    where: str = "1=1",
    out_fields: str = "*",
    result_offset: int = 0,
    result_count: int = 1000,
) -> List[Dict[str, Any]]:
    """Query a TAD ArcGIS Feature Service (sync version)."""
    params = {
        "where": where,
        "outFields": out_fields,
        "resultOffset": str(result_offset),
        "resultRecordCount": str(result_count),
        "f": "json",
        "returnGeometry": "false",
    }
    
    with httpx.Client(timeout=60.0) as client:
        response = client.get(url, params=params)
        response.raise_for_status()
        data = response.json()
    
    features = data.get("features", [])
    return [f.get("attributes", {}) for f in features]


async def search_tad_by_address(address: str) -> List[Dict[str, Any]]:
    """Search TAD for properties by address."""
    # Clean address for search
    clean_addr = address.upper().strip()
    
    # Try multiple search patterns
    where_clauses = [
        f"SITE_ADDR_1 LIKE '%{clean_addr}%'",
        f"OWNER_NAME LIKE '%{clean_addr}%'",
        f"MAIL_ADDR LIKE '%{clean_addr}%'",
    ]
    
    results = []
    for where in where_clauses:
        try:
            features = query_tad_sync(
                TAD_PROPERTY_URL,
                where=where,
                out_fields="*",
                result_count=50,
            )
            results.extend(features)
        except Exception as e:
            logger.warning("TAD query failed: %s", e)
    
    # Deduplicate by TAXPIN
    seen = set()
    unique = []
    for r in results:
        pin = r.get("TAXPIN")
        if pin and pin not in seen:
            seen.add(pin)
            unique.append(r)
    
    return unique


async def search_tad_by_owner(owner_name: str) -> List[Dict[str, Any]]:
    """Search TAD for properties by owner name."""
    clean_name = owner_name.upper().strip()
    
    where = f"OWNER_NAME LIKE '%{clean_name}%'"
    
    return query_tad_sync(
        TAD_PROPERTY_URL,
        where=where,
        out_fields="*",
        result_count=100,
    )


async def fetch_tad_properties(
    city: str = "FORT WORTH",
    limit: int = 1000,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    """Fetch properties from TAD by city."""
    where = f"CITY LIKE '%{city.upper()}%'"
    
    return query_tad_sync(
        TAD_PROPERTY_URL,
        where=where,
        out_fields=(
            "TAXPIN,SITE_ADDR_1,CITY,STATE,ZIP,OWNER_NAME,MAIL_ADDR,"
            "MAIL_CITY,MAIL_STATE,MAIL_ZIP,OWNER_OCC,ABSENTEE_OWNER,"
            "TOTALASSESSED,PRIMLAND,PRIMIMPR,PRIMYEAR,PRIMSTYLE,"
            "PRIMBEDS,PRIMBATHS,PRIMSF,LANDSQFT,YEARBUILT,"
            "LATITUDE,LONGITUDE"
        ),
        result_offset=offset,
        result_count=min(limit, 1000),
    )


def _parse_tad_property(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Convert a TAD record to InvestorFlip format."""
    # Build full address
    site_addr = (raw.get("SITE_ADDR_1") or "").strip()
    city = (raw.get("CITY") or "Fort Worth").strip()
    state = (raw.get("STATE") or "TX").strip()
    zip_code = (raw.get("ZIP") or "").strip()[:5]
    
    full_address = f"{site_addr}, {city}, {state} {zip_code}".strip(", ")
    
    # Owner info
    owner_name = (raw.get("OWNER_NAME") or "").strip()
    mail_addr = (raw.get("MAIL_ADDR") or "").strip()
    mail_city = (raw.get("MAIL_CITY") or "").strip()
    mail_state = (raw.get("MAIL_STATE") or "").strip()
    mail_zip = (raw.get("MAIL_ZIP") or "").strip()[:5]
    
    mailing_address = f"{mail_addr}, {mail_city}, {mail_state} {mail_zip}".strip(", ")
    
    # Property details
    assessed_value = raw.get("TOTALASSESSED")
    land_value = raw.get("PRIMLAND")
    improvement_value = raw.get("PRIMIMPR")
    sqft = raw.get("PRIMSF")
    year_built = raw.get("PRIMYEAR") or raw.get("YEARBUILT")
    beds = raw.get("PRIMBEDS")
    baths = raw.get("PRIMBATHS")
    lot_size = raw.get("LANDSQFT")
    
    # Coordinates
    lat = raw.get("LATITUDE")
    lon = raw.get("LONGITUDE")
    
    # Ownership flags
    owner_occupied = raw.get("OWNER_OCC") == "Y"
    absentee_owner = raw.get("ABSENTEE_OWNER") == "Y"
    
    # Check if out of state
    mail_state_upper = (mail_state or "").upper()
    out_of_state = mail_state_upper and mail_state_upper != "TX"
    
    return {
        "id": f"tad-{raw.get('TAXPIN', uuid.uuid4().hex[:12])}",
        "situs_address": full_address,
        "city": city,
        "state": state,
        "zip": zip_code,
        "county": "Tarrant",
        "latitude": lat,
        "longitude": lon,
        
        # Property facts
        "beds": beds,
        "baths": baths,
        "sqft": sqft,
        "year_built": year_built,
        "lot_size_sqft": lot_size,
        "property_type": raw.get("PRIMSTYLE") or "Single Family Residential",
        
        # Financial
        "price": 0,  # Not available from TAD
        "assessed_value": assessed_value,
        "market_value": land_value + improvement_value if land_value and improvement_value else None,
        
        # Owner info
        "owner_name": owner_name,
        "owner_type": "Unknown",  # Will be classified by investor_logic
        "owner_mailing_address": mailing_address,
        "out_of_state_owner": out_of_state,
        "absentee_owner": absentee_owner,
        "owner_occupied": owner_occupied,
        
        # Source
        "data_source": "Tarrant Appraisal District (TAD)",
        "source_platform": "TAD Open Data",
        "parcel_id": raw.get("TAXPIN"),
        
        # Flags
        "is_synthetic": False,
        
        # TAD-specific
        "tad_data": {
            "total_assessed": assessed_value,
            "land_value": land_value,
            "improvement_value": improvement_value,
            "primary_style": raw.get("PRIMSTYLE"),
            "owner_occupied": owner_occupied,
            "absentee_owner": absentee_owner,
        },
    }


async def import_tad_properties(
    db: PostgresDatabase,
    city: str = "FORT WORTH",
    limit: int = 1000,
) -> Dict[str, Any]:
    """Import TAD properties into the database."""
    try:
        raw_properties = await fetch_tad_properties(city=city, limit=limit)
    except Exception as e:
        logger.error("Failed to fetch TAD data: %s", e)
        return {"fetched": 0, "inserted": 0, "matched": 0, "skipped": 0, "error": str(e)}
    
    if not raw_properties:
        return {"fetched": 0, "inserted": 0, "matched": 0, "skipped": 0}
    
    inserted = 0
    matched = 0
    skipped = 0
    
    for raw in raw_properties:
        try:
            prop = _parse_tad_property(raw)
            
            address = prop.get("situs_address", "")
            if not address or address == ", Fort Worth, TX":
                skipped += 1
                continue
            
            # Check if exists
            existing = await db.properties.find_one({"situs_address": address})
            
            if existing:
                # Update with TAD data
                update_fields = {}
                for key in ["assessed_value", "owner_name", "owner_mailing_address",
                           "beds", "baths", "sqft", "year_built", "latitude", "longitude"]:
                    if prop.get(key) and not existing.get(key):
                        update_fields[key] = prop[key]
                
                update_fields["data_source"] = existing.get("data_source", "") + " + TAD"
                update_fields["tad_data"] = prop.get("tad_data")
                
                await db.properties.update_one(
                    {"id": existing["id"]},
                    {"$set": update_fields},
                )
                matched += 1
            else:
                await db.properties.insert_one(prop)
                inserted += 1
        except Exception as e:
            logger.warning("Failed to process TAD record: %s", e)
            skipped += 1
    
    return {
        "fetched": len(raw_properties),
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
            result = await import_tad_properties(db, limit=100)
            import json
            print(json.dumps(result, indent=2))
        finally:
            await db.close()
    
    asyncio.run(main())
