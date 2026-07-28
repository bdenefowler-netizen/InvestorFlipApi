"""PropStream data scraper — imports data exported from PropStream.

PropStream doesn't have a public API, but they support CSV/Excel exports.
This module provides utilities to:
1. Import data exported from PropStream
2. Merge with existing database records

Usage:
1. Export your list from PropStream (CSV or Excel)
2. Run this importer to merge the data

Source: propstream.com (requires subscription)
"""

from __future__ import annotations

import csv
import io
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from database import PostgresDatabase

logger = logging.getLogger("tarrantrei.propstream")

# PropStream export file location (upload via API or place in data/)
PROPSTREAM_EXPORT_DIR = Path(__file__).resolve().parent.parent / "data"


def load_propstream_csv(file_path: Path) -> List[Dict[str, Any]]:
    """Load a PropStream CSV export file."""
    if not file_path.exists():
        logger.warning("PropStream file not found: %s", file_path)
        return []
    
    records = []
    with open(file_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            records.append(dict(row))
    
    logger.info("Loaded %d records from PropStream", len(records))
    return records


def _normalize_propstream_address(raw: str) -> str:
    """Normalize a PropStream address to standard format."""
    # Remove extra whitespace and normalize
    addr = " ".join(raw.split()).strip()
    return addr


def _parse_propstream_record(record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Parse a PropStream record into InvestorFlip format."""
    
    # PropStream field names vary by export configuration
    # Common field mappings:
    field_map = {
        "Address": "situs_address",
        "PropertyAddress": "situs_address",
        "Site Address": "situs_address",
        "Street Address": "situs_address",
        "Property Address": "situs_address",
        "City": "city",
        "PropertyCity": "city",
        "State": "state",
        "PropertyState": "state",
        "Zip": "zip",
        "ZipCode": "zip",
        "PostalCode": "zip",
        "Owner Name": "owner_name",
        "OwnerName": "owner_name",
        "Owner": "owner_name",
        "Mailing Address": "owner_mailing_address",
        "MailingAddress": "owner_mailing_address",
        "Mailing Street": "owner_mailing_address",
        "County": "county",
        "PropertyCounty": "county",
        "Assessed Value": "assessed_value",
        "AssessedValue": "assessed_value",
        "Tax Assessed Value": "assessed_value",
        "Market Value": "market_value",
        "MarketValue": "market_value",
        "Estimated Value": "market_value",
        "Bedrooms": "beds",
        "Beds": "beds",
        "Bathrooms": "baths",
        "Baths": "baths",
        "Sqft": "sqft",
        "Square Feet": "sqft",
        "Living Area": "sqft",
        "Year Built": "year_built",
        "YearBuilt": "year_built",
        "Lot Size": "lot_size_sqft",
        "LotSize": "lot_size_sqft",
        "Latitude": "latitude",
        "Longitude": "longitude",
        "APN": "parcel_id",
        "Parcel ID": "parcel_id",
        "ParcelID": "parcel_id",
        "Property Type": "property_type",
        "PropertyType": "property_type",
        "MLS": "mls_id",
        "MLS ID": "mls_id",
        "Owner Occupied": "owner_occupied",
        "Absente Owner": "absentee_owner",
        "Out of State": "out_of_state_owner",
        "Tax Delinquent": "tax_delinquent",
        "Pre-Foreclosure": "pre_foreclosure",
        "Auction Date": "auction_date",
    }
    
    parsed = {}
    
    for ps_key, if_key in field_map.items():
        value = record.get(ps_key)
        if value is not None and str(value).strip():
            parsed[if_key] = value
    
    # Ensure we have at least an address
    if not parsed.get("situs_address"):
        return None
    
    # Convert numeric fields
    for field in ["beds", "baths", "sqft", "year_built", "assessed_value", "market_value", "latitude", "longitude"]:
        if field in parsed:
            try:
                val = str(parsed[field]).replace(",", "").strip()
                if val:
                    parsed[field] = float(val) if "." in val else int(float(val))
            except (ValueError, TypeError):
                del parsed[field]
    
    # Determine listing type based on flags
    listing_type = "Wholesale"
    if parsed.get("tax_delinquent"):
        listing_type = "Tax Lien"
    elif parsed.get("pre_foreclosure"):
        listing_type = "Pre-Foreclosure"
    
    parsed["listing_type"] = listing_type
    parsed["listing_status"] = "Active"
    parsed["data_source"] = "PropStream"
    parsed["source_platform"] = "PropStream"
    parsed["is_synthetic"] = False
    
    return parsed


async def import_propstream_export(
    db: PostgresDatabase,
    file_path: Path,
) -> Dict[str, Any]:
    """Import a PropStream CSV/Excel export into the database."""
    records = load_propstream_csv(file_path)
    
    if not records:
        return {"fetched": 0, "inserted": 0, "matched": 0, "skipped": 0}
    
    inserted = 0
    matched = 0
    skipped = 0
    
    for record in records:
        prop = _parse_propstream_record(record)
        if not prop:
            skipped += 1
            continue
        
        address = prop.get("situs_address", "")
        
        # Check if property already exists
        existing = await db.properties.find_one({"situs_address": address})
        
        if existing:
            # Merge PropStream data into existing record
            update_fields = {}
            
            # Only update fields that are currently empty
            for key in ["beds", "baths", "sqft", "year_built", "assessed_value", "market_value",
                        "owner_name", "owner_mailing_address", "latitude", "longitude", "parcel_id"]:
                if key in prop and not existing.get(key):
                    update_fields[key] = prop[key]
            
            # Always update data source
            update_fields["data_source"] = existing.get("data_source", "") + " + PropStream"
            
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
        "fetched": len(records),
        "inserted": inserted,
        "matched": matched,
        "skipped": skipped,
    }


# CLI entry point
if __name__ == "__main__":
    import sys
    import asyncio
    
    async def main():
        if len(sys.argv) < 2:
            print("Usage: python propstream_scraper.py <path_to_export.csv>")
            sys.exit(1)
        
        file_path = Path(sys.argv[1])
        db = PostgresDatabase()
        try:
            await db.connect()
            result = await import_propstream_export(db, file_path)
            print(json.dumps(result, indent=2))
        finally:
            await db.close()
    
    asyncio.run(main())
