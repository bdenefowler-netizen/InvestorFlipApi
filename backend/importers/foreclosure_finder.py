"""Optional importer for a user-supplied, verified foreclosure CSV.

Sources:
- Tarrant County tax lien sales (monthly auctions)
- Foreclosure filings from public records
- Distressed property indicators
"""

from __future__ import annotations

import csv
import io
import logging
import os
from pathlib import Path
from typing import Any, Dict, List

from database import PostgresDatabase

logger = logging.getLogger("tarrantrei.foreclosure_finder")

# Bundled fixture retained for development reference only. Production must point
# TARRANT_FORECLOSURE_CSV at a separately downloaded, verified county document.
FORECLOSURE_CSV = Path(__file__).resolve().parent.parent / "data" / "tx_foreclosures.csv"


def load_foreclosures_from_csv() -> List[Dict[str, Any]]:
    """Load only an explicitly configured foreclosure CSV."""
    configured = os.environ.get("TARRANT_FORECLOSURE_CSV", "").strip()
    if not configured:
        logger.info("TARRANT_FORECLOSURE_CSV is not configured; bundled sample is disabled")
        return []
    csv_path = Path(configured).expanduser().resolve()
    if csv_path == FORECLOSURE_CSV.resolve():
        logger.warning("Refusing bundled sample foreclosure CSV: %s", csv_path)
        return []
    if not csv_path.exists():
        logger.warning("Configured foreclosure CSV not found: %s", csv_path)
        return []
    
    records = []
    with csv_path.open("r", encoding="utf-8") as f:
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
        return {
            "fetched": 0,
            "inserted": 0,
            "matched": 0,
            "skipped": True,
            "reason": "Upload a current verified foreclosure CSV or configure TARRANT_FORECLOSURE_CSV",
        }
    
    inserted = 0
    matched = 0
    skipped = 0
    
    # ─── Filter: only auctions 30+ days in the future ───
    from datetime import datetime, timedelta, timezone
    cutoff = datetime.now(timezone.utc) + timedelta(days=30)

    def _parse_sale_date(record):
        """Parse sale_date from record, return None if unparseable."""
        raw = record.get("sale_date", "")
        if not raw:
            return None
        raw = str(raw).strip()
        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%d-%b-%Y", "%b %d %Y"):
            try:
                return datetime.strptime(raw.replace("\n", " ").strip(), fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                pass
        return None

    filtered_records = []
    skipped_past = 0
    skipped_undated = 0
    for record in records:
        sale_dt = _parse_sale_date(record)
        if sale_dt is None:
            # Keep records without a date (can't verify)
            filtered_records.append(record)
            skipped_undated += 1
        elif sale_dt >= cutoff:
            filtered_records.append(record)
        else:
            skipped_past += 1

    logger.info(
        "Foreclosure filter: %d future auctions (30+ days), %d skipped (past), %d kept (no date)",
        len(filtered_records), skipped_past, skipped_undated,
    )
    records = filtered_records

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
        "filter": "auctions 30+ days in the future only",
    }
