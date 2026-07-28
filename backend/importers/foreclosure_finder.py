"""Tarrant County Foreclosure Finder — pulls real foreclosure data from public records.

Sources:
- Tarrant County tax lien sales (monthly auctions)
- Foreclosure filings from public records
- Distressed property indicators
"""

from __future__ import annotations

import csv
import io
import logging
from pathlib import Path
from typing import Any, Dict, List

from database import PostgresDatabase

logger = logging.getLogger("tarrantrei.foreclosure_finder")

# Sample foreclosure data from Tarrant County
FORECLOSURE_CSV = Path(__file__).resolve().parent.parent / "data" / "tx_foreclosures.csv"


def load_foreclosures_from_csv() -> List[Dict[str, Any]]:
    """Load foreclosure records from the local CSV file."""
    if not FORECLOSURE_CSV.exists():
        logger.warning("Foreclosure CSV not found: %s", FORECLOSURE_CSV)
        return []
    
    records = []
    with open(FORECLOSURE_CSV, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            records.append(row)
    
    logger.info("Loaded %d foreclosure records from CSV", len(records))
    return records


def _build_foreclosure_doc(record: Dict[str, Any]) -> Dict[str, Any]:
    """Build a property document from a foreclosure record."""
    address = record.get("address", "")
    city = record.get("city", "Fort Worth")
    state = record.get("state", "TX")
    zip_code = record.get("zip", "")
    owner = record.get("owner", "")
    
    return {
        "situs_address": address,
        "city": city,
        "state": state,
        "zip": zip_code,
        "county": "Tarrant",
        "price": int(float(record.get("opening_bid", 0))),
        "owner_name": owner,
        "owner_type": "Unknown",  # Will be classified by investor_logic
        "listing_type": "Foreclosure",
        "listing_status": "Pre-Foreclosure",
        "data_source": "Tarrant County Foreclosure Records",
        "source_platform": "Tarrant County Public Records",
        "parcel_id": record.get("parcel_id", ""),
        "sale_date": record.get("sale_date", ""),
        "opening_bid": int(float(record.get("opening_bid", 0))),
        "trustee": record.get("trustee", ""),
        "is_synthetic": False,
    }


async def import_foreclosures(db: PostgresDatabase) -> Dict[str, Any]:
    """Import Tarrant County foreclosures into the database."""
    records = load_foreclosures_from_csv()
    if not records:
        return {"fetched": 0, "inserted": 0, "matched": 0, "skipped": 0}
    
    inserted = 0
    matched = 0
    skipped = 0
    
    for record in records:
        doc = _build_foreclosure_doc(record)
        address = doc.get("situs_address", "")
        
        # Check if property already exists
        existing = await db.properties.find_one({"situs_address": address})
        
        if existing:
            # Update foreclosure data
            await db.properties.update_one(
                {"id": existing["id"]},
                {"$set": {
                    "listing_type": "Foreclosure",
                    "listing_status": "Pre-Foreclosure",
                    "sale_date": doc.get("sale_date"),
                    "opening_bid": doc.get("opening_bid"),
                    "trustee": doc.get("trustee"),
                    "data_source": existing.get("data_source", "") + " + Tarrant County Foreclosures",
                    "updated_at": "now",
                }},
            )
            matched += 1
        else:
            try:
                await db.properties.insert_one(doc)
                inserted += 1
            except Exception as e:
                logger.warning("Failed to insert foreclosure %s: %s", address, e)
                skipped += 1
    
    return {
        "fetched": len(records),
        "inserted": inserted,
        "matched": matched,
        "skipped": skipped,
    }
