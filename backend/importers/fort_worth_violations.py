"""Fort Worth Code Violations importer — pulls distressed properties from ArcGIS API.

These properties have active code violations (vacant structures, junk vehicles,
overgrown vegetation, nuisance abatement) which are strong indicators of
motivated sellers and distressed conditions.

Source: City of Fort Worth ArcGIS Feature Service
API: https://mapit.fortworthtexas.gov/ags/rest/services/CIVIC/Code_Violations_Experience_Builder/MapServer/4
"""

from __future__ import annotations

import os
import re
import uuid
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

from database import PostgresDatabase
from investor_logic import classify_owner, compute_scores, derive_owner_signals

logger = logging.getLogger("tarrantrei.fw_violations")

# Fort Worth Code Violations ArcGIS Feature Service
VIOLATIONS_URL = (
    "https://mapit.fortworthtexas.gov/ags/rest/services/"
    "CIVIC/Code_Violations_Experience_Builder/MapServer/4/query"
)

# Violation types that signal distressed/motivated seller
DISTRESSED_VIOLATIONS = {
    "BUILDING VACANT STRUCTURE": "vacant_structure",
    "JUNK VEHICLES": "junk_vehicles",
    "OVERGROWN VEGETATION": "overgrown_vegetation",
    "SWIMMING POOL W/O WATER": "pool_no_water",
    "NUISANCE ABATEMENT": "nuisance_abatement",
    "NUISANCE BOARDING HOUSE": "boarding_house",
    "TRASH AND DEBRIS": "trash_debris",
    "SUBSTANDARD STRUCTURE": "substandard_structure",
    "HIGH GRASS/WEEDS": "high_grass",
    "HIGH GRASS AND WEEDS": "high_grass",
}

PROPERTY_IMAGES = [
    "https://images.pexels.com/photos/18280830/pexels-photo-18280830.jpeg?auto=compress&cs=tinysrgb&dpr=2&h=650&w=940",
    "https://images.unsplash.com/photo-1649692560786-27c52dd9ac1d?crop=entropy&cs=srgb&fm=jpg&q=80&w=940",
    "https://images.pexels.com/photos/33404981/pexels-photo-33404981.jpeg?auto=compress&cs=tinysrgb&dpr=2&h=650&w=940",
    "https://images.pexels.com/photos/2102587/pexels-photo-2102587.jpeg?auto=compress&cs=tinysrgb&dpr=2&h=650&w=940",
    "https://images.pexels.com/photos/1396122/pexels-photo-1396122.jpeg?auto=compress&cs=tinysrgb&dpr=2&h=650&w=940",
]


def _epoch_to_date(ms: Optional[int]) -> Optional[str]:
    """Convert epoch milliseconds to ISO date string."""
    if ms and ms > 0:
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    return None


def _classify_violation(complaint_type: str) -> str:
    """Classify violation into investor-relevant categories."""
    upper = (complaint_type or "").upper()
    for keyword, category in DISTRESSED_VIOLATIONS.items():
        if keyword in upper:
            return category
    return "other_violation"


def _distress_score(violation_count: int, open_count: int, categories: set) -> int:
    """Calculate distress score (1-100) based on violations."""
    score = 0
    
    # Base score from violation count
    if violation_count >= 10:
        score += 40
    elif violation_count >= 5:
        score += 30
    elif violation_count >= 3:
        score += 20
    elif violation_count >= 1:
        score += 10
    
    # Open violations add urgency
    if open_count >= 5:
        score += 30
    elif open_count >= 3:
        score += 20
    elif open_count >= 1:
        score += 10
    
    # High-value violation types
    high_value = {"vacant_structure", "nuisance_abatement", "boarding_house", "substandard_structure"}
    if categories & high_value:
        score += 20
    
    medium_value = {"junk_vehicles", "pool_no_water", "trash_debris"}
    if categories & medium_value:
        score += 10
    
    return min(100, score)


async def fetch_violations(limit: int = 2000) -> List[Dict[str, Any]]:
    """Fetch code violations from Fort Worth ArcGIS API."""
    params = {
        "where": "1=1",
        "outFields": "Address,Complaint_Type_Description,Case_Current_Status,"
                     "Violation_Current_Status,Case_Created_Date,"
                     "Violation_Created_Date,ZipCode,Latitude,Longitude,"
                     "Code_Officer,Case_ID,Violation_ID",
        "resultRecordCount": str(min(limit, 2000)),
        "f": "json",
    }
    
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.get(VIOLATIONS_URL, params=params)
        response.raise_for_status()
        data = response.json()
    
    features = data.get("features", [])
    logger.info("Fetched %d violation records from Fort Worth ArcGIS", len(features))
    return [f.get("attributes", {}) for f in features]


def _group_by_address(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Group violations by address and aggregate counts/types."""
    properties: Dict[str, Dict[str, Any]] = {}
    
    for rec in records:
        address = (rec.get("Address") or "").strip()
        if not address:
            continue
        
        key = address.upper()
        if key not in properties:
            properties[key] = {
                "address": address,
                "city": "Fort Worth",
                "state": "TX",
                "zip_code": rec.get("ZipCode", ""),
                "latitude": rec.get("Latitude"),
                "longitude": rec.get("Longitude"),
                "case_id": rec.get("Case_ID", ""),
                "case_status": rec.get("Case_Current_Status", ""),
                "code_officer": rec.get("Code_Officer", ""),
                "violation_count": 0,
                "open_count": 0,
                "closed_count": 0,
                "violation_types": set(),
                "open_violation_types": set(),
                "earliest_case_date": None,
                "latest_violation_date": None,
            }
        
        p = properties[key]
        p["violation_count"] += 1
        
        complaint = (rec.get("Complaint_Type_Description") or "").upper()
        v_status = (rec.get("Violation_Current_Status") or "").upper()
        category = _classify_violation(complaint)
        
        p["violation_types"].add(category)
        
        if v_status == "OPEN":
            p["open_count"] += 1
            p["open_violation_types"].add(category)
        else:
            p["closed_count"] += 1
        
        # Track dates
        case_date = _epoch_to_date(rec.get("Case_Created_Date"))
        v_date = _epoch_to_date(rec.get("Violation_Created_Date"))
        
        if case_date and (p["earliest_case_date"] is None or case_date < p["earliest_case_date"]):
            p["earliest_case_date"] = case_date
        if v_date and (p["latest_violation_date"] is None or v_date > p["latest_violation_date"]):
            p["latest_violation_date"] = v_date
    
    # Sort by violation count descending
    sorted_props = sorted(properties.values(), key=lambda x: x["violation_count"], reverse=True)
    
    # Convert sets to lists for JSON serialization
    for p in sorted_props:
        p["violation_types"] = sorted(p["violation_types"])
        p["open_violation_types"] = sorted(p["open_violation_types"])
    
    return sorted_props


def _build_property_doc(v: Dict[str, Any], idx: int) -> Dict[str, Any]:
    """Build a property document compatible with InvestorFlip schema."""
    address = v["address"]
    zip_code = v.get("zip_code", "")
    
    # Derive owner signals (unknown owner from violations data)
    owner_signals = derive_owner_signals(
        owner_name="",
        mailing_address="",
        situs_address=f"{address}, Fort Worth, TX {zip_code}",
        property_state="TX",
    )
    
    # Calculate distress score
    distress = _distress_score(
        v["violation_count"],
        v["open_count"],
        set(v["violation_types"]),
    )
    
    # Build listing type based on violations
    listing_type = "Distressed"
    if "vacant_structure" in v["violation_types"]:
        listing_type = "Vacant"
    elif "nuisance_abatement" in v["violation_types"] or "substandard_structure" in v["violation_types"]:
        listing_type = "Nuisance"
    elif v["open_count"] > 3:
        listing_type = "Code Violation"
    
    return {
        "id": f"fw-violation-{uuid.uuid5(uuid.NAMESPACE_DNS, address.upper()).hex[:12]}",
        "situs_address": address,
        "city": "Fort Worth",
        "state": "TX",
        "zip": zip_code,
        "county": "Tarrant",
        "latitude": v.get("latitude"),
        "longitude": v.get("longitude"),
        
        # Property facts (unknown from violations data)
        "beds": None,
        "baths": None,
        "sqft": None,
        "year_built": None,
        "lot_size_sqft": None,
        "property_type": "Single Family Residential",
        
        # Pricing (unknown from violations data)
        "price": 0,
        "market_value": None,
        "tax_roll_market_value": None,
        "assessed_value": None,
        "annual_taxes": None,
        "equity_estimate": None,
        "est_roi_pct": None,
        
        # Owner intelligence
        "owner_name": "",
        "owner_type": "Unknown",
        "owner_mailing_address": "",
        "out_of_state_owner": False,
        "tax_delinquent": False,
        "vacant": "vacant_structure" in v["violation_types"],
        "high_equity": False,
        "cash_buyer": False,
        "investor_owned": False,
        
        # Listing info
        "listing_type": listing_type,
        "listing_status": v["case_status"],
        "data_source": "Fort Worth Code Violations (ArcGIS)",
        "source_platform": "Fort Worth Open Data",
        
        # Code violation specifics
        "violation_count": v["violation_count"],
        "open_violation_count": v["open_count"],
        "closed_violation_count": v["closed_count"],
        "violation_types": v["violation_types"],
        "open_violation_types": v["open_violation_types"],
        "case_id": v["case_id"],
        "code_officer": v["code_officer"],
        "earliest_case_date": v["earliest_case_date"],
        "latest_violation_date": v["latest_violation_date"],
        "distress_score": distress,
        
        # Image
        "image_url": PROPERTY_IMAGES[idx % len(PROPERTY_IMAGES)],
        
        # Metadata
        "is_synthetic": False,
        "listing_description": (
            f"Fort Worth code violation property. "
            f"{v['violation_count']} total violations ({v['open_count']} open). "
            f"Types: {', '.join(v['violation_types'])}. "
            f"Case ID: {v['case_id']}."
        ),
    }


async def import_fort_worth_violations(
    db: PostgresDatabase,
    limit: int = 2000,
) -> Dict[str, Any]:
    """Import Fort Worth code violation properties into the database.
    
    Returns summary of import results.
    """
    records = await fetch_violations(limit)
    if not records:
        return {"fetched": 0, "inserted": 0, "matched": 0, "skipped": 0}
    
    grouped = _group_by_address(records)
    logger.info("Grouped into %d unique properties", len(grouped))
    
    inserted = 0
    matched = 0
    skipped = 0
    
    for idx, v in enumerate(grouped):
        doc = _build_property_doc(v, idx)
        
        # Check if property already exists
        existing = await db.properties.find_one({"situs_address": v["address"]})
        
        if existing:
            # Update violation data on existing property
            update_fields = {
                "violation_count": v["violation_count"],
                "open_violation_count": v["open_count"],
                "closed_violation_count": v["closed_count"],
                "violation_types": v["violation_types"],
                "open_violation_types": v["open_violation_types"],
                "case_id": v["case_id"],
                "code_officer": v["code_officer"],
                "earliest_case_date": v["earliest_case_date"],
                "latest_violation_date": v["latest_violation_date"],
                "distress_score": _distress_score(
                    v["violation_count"], v["open_count"], set(v["violation_types"])
                ),
                "data_source": existing.get("data_source", "") + " + Fort Worth Code Violations",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            
            # Update vacant flag if we found a vacant structure
            if "vacant_structure" in v["violation_types"]:
                update_fields["vacant"] = True
            
            await db.properties.update_one(
                {"id": existing["id"]},
                {"$set": update_fields},
            )
            matched += 1
        else:
            # Insert new property
            try:
                await db.properties.insert_one(doc)
                inserted += 1
            except Exception as e:
                logger.warning("Failed to insert %s: %s", v["address"], e)
                skipped += 1
    
    summary = {
        "fetched": len(records),
        "unique_properties": len(grouped),
        "inserted": inserted,
        "matched": matched,
        "skipped": skipped,
    }
    
    logger.info(
        "Fort Worth violations import complete: %s inserted, %s matched, %s skipped",
        inserted, matched, skipped,
    )
    
    return summary


# CLI entry point
if __name__ == "__main__":
    import asyncio
    
    async def main():
        database = PostgresDatabase()
        try:
            await database.connect()
            result = await import_fort_worth_violations(database)
            import json
            print(json.dumps(result, indent=2))
        finally:
            await database.close()
    
    asyncio.run(main())
