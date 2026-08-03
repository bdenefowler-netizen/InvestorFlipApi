"""Tarrant Appraisal District (TAD) scraper — FREE public data.

Pulls property data from Tarrant County ArcGIS REST API:
- Ownership info (owner name, mailing address)
- Property characteristics (beds, baths, sqft, year built)
- Assessed values (land, improvement, total)
- Parcel boundaries and IDs

Source: mapit.tarrantcounty.com (FREE ArcGIS REST API)
"""

from __future__ import annotations

import logging
import uuid
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx

from address_utils import canonical_street_key, street_prefix_regex
from database import PostgresDatabase

logger = logging.getLogger("tarrantrei.tad")

# Correct TAD Parcels Feature Service URL
TAD_PARCELS_URL = (
    "https://mapit.tarrantcounty.com/arcgis/rest/services/"
    "Dynamic/TADParcels/FeatureServer/0/query"
)

TAD_MAX_RECORDS = 1000


_REQUIRED_FIELDS = ",".join([
    "TAXPIN", "ACCOUNT", "OWNER_NAME", "OWNER_ADDR", "OWNER_CITY",
    "OWNER_ZIP", "SITUS_ADDR", "CITY", "STATE", "ZIPCODE",
    "BEDROOMS", "BATHROOMS", "YEAR_BUILT", "LIVING_ARE",
    "LAND_ACRES", "LAND_SQFT", "APPRAISEDV",
    "LAND_VALUE", "IMPR_VALUE", "TOTAL_VALU",
    "DEED_DATE", "SCHOOL", "GARAGE_CAP",
])


def _query_tad(
    where: str = "1=1",
    out_fields: str = _REQUIRED_FIELDS,
    result_offset: int = 0,
    result_count: int = TAD_MAX_RECORDS,
    order_by: str = "",
) -> List[Dict[str, Any]]:
    """Query the TAD Parcels Feature Service (sync)."""
    params = {
        "where": where,
        "outFields": out_fields,
        "resultOffset": str(result_offset),
        "resultRecordCount": str(min(result_count, TAD_MAX_RECORDS)),
        "f": "json",
        "returnGeometry": "false",
    }
    if order_by:
        params["orderByFields"] = order_by
    with httpx.Client(timeout=60.0) as client:
        response = client.get(TAD_PARCELS_URL, params=params)
        response.raise_for_status()
        data = response.json()
    features = data.get("features", [])
    return [f.get("attributes", {}) for f in features]


def _epoch_to_date(ms: Optional[int]) -> Optional[str]:
    if ms and ms > 0:
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    return None


def _scrape_number(value: Any) -> Optional[float]:
    try:
        v = float(value)
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


def _parse_tad_property(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Convert a TAD record to InvestorFlip format."""
    # Parse situs address
    situs_addr = (raw.get("SITUS_ADDR") or "").strip()
    city = (raw.get("CITY") or "FORT WORTH").strip()
    state = (raw.get("STATE") or "TX").strip()
    zip_code = (raw.get("ZIPCODE") or "").strip()

    full_address = f"{situs_addr}, {city}, {state} {zip_code}".strip(", ")

    # Parse owner info
    owner_name = (raw.get("OWNER_NAME") or "").strip()
    owner_addr = (raw.get("OWNER_ADDR") or "").strip()
    owner_city = (raw.get("OWNER_CITY") or "").strip()
    owner_zip = (raw.get("OWNER_ZIP") or "").strip()
    mailing_address = f"{owner_addr}, {owner_city} {owner_zip}".strip(", ")

    # Check if out of state
    out_of_state = False
    if "," in owner_city:
        owner_state_part = owner_city.split(",")[-1].strip()
        out_of_state = owner_state_part.upper() != "TX"

    # Owner occupied check
    mailing_street = owner_addr.upper().strip() if owner_addr else ""
    situs_street = situs_addr.upper().strip() if situs_addr else ""
    absentee = bool(mailing_street and situs_street and mailing_street != situs_street)

    # Property details
    beds = _scrape_number(raw.get("BEDROOMS"))
    baths = _scrape_number(raw.get("BATHROOMS"))
    sqft = _scrape_number(raw.get("LIVING_ARE"))
    year_built = int(year) if (year := raw.get("YEAR_BUILT")) else None
    lot_sqft = _scrape_number(raw.get("LAND_SQFT"))
    land_acres = _scrape_number(raw.get("LAND_ACRES"))

    # Financial
    appraised_value = _scrape_number(raw.get("APPRAISEDV"))
    land_value = _scrape_number(raw.get("LAND_VALUE"))
    imp_value = _scrape_number(raw.get("IMPR_VALUE"))
    total_value = _scrape_number(raw.get("TOTAL_VALU"))

    # Deed date
    deed_date = _epoch_to_date(raw.get("DEED_DATE"))

    # Property type
    prop_type = "Single Family Residential"
    lot_sqft_int = int(lot_sqft) if lot_sqft else 0
    if lot_sqft_int == 0 and beds is None:
        prop_type = "Vacant Lot"
    elif not sqft or sqft < 1:
        prop_type = "Vacant Lot" if not beds else prop_type

    garage = _scrape_number(raw.get("GARAGE_CAP"))

    return {
        "id": f"tad-{raw.get('TAXPIN', uuid.uuid4().hex[:12])}",
        "situs_address": full_address or f"{situs_addr}, FORT WORTH, TX",
        "city": city.upper(),
        "state": state.upper(),
        "zip": zip_code,
        "county": "Tarrant",
        "latitude": None,
        "longitude": None,

        "beds": beds,
        "baths": baths,
        "sqft": sqft,
        "year_built": year_built,
        "lot_size_acres": land_acres,
        "lot_size_sqft": lot_sqft,
        "garage_capacity": garage,
        "property_type": prop_type,

        "price": None,
        "assessed_value": appraised_value,
        "market_value": total_value,
        "land_value": land_value,
        "improvement_value": imp_value,

        "owner_name": owner_name,
        "owner_type": "Unknown",
        "owner_mailing_address": mailing_address,
        "out_of_state_owner": out_of_state,
        "absentee_owner": absentee,
        "owner_occupied": not absentee,

        "data_source": "Tarrant Appraisal District (TAD)",
        "source_platform": "TAD Parcels ArcGIS",
        "parcel_id": raw.get("TAXPIN"),
        "account_id": raw.get("ACCOUNT"),
        "deed_date": deed_date,
        "school_district": raw.get("SCHOOL"),

        "is_synthetic": False,

        "tad_data": {
            "total_value": total_value,
            "appraised_value": appraised_value,
            "land_value": land_value,
            "improvement_value": imp_value,
            "land_acres": land_acres,
            "owner_occupied": not absentee,
            "absentee_owner": absentee,
            "parcel_type": raw.get("PARCELTYPE"),
        },
    }


async def search_tad_by_address(address: str) -> List[Dict[str, Any]]:
    """Search TAD for properties by address."""
    clean = address.upper().strip()
    # Try direct situs address match
    results = []
    for where in [
        f"UPPER(SITUS_ADDR) LIKE '%{clean}%'",
        f"UPPER(SITUS_ADDR) LIKE '%{clean.split(',')[0]}%'",
    ]:
        try:
            results.extend(_query_tad(where=where, result_count=50))
        except Exception as e:
            logger.warning("TAD search failed: %s", e)

    # Deduplicate
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
    clean = owner_name.upper().strip()
    where = f"UPPER(OWNER_NAME) LIKE '%{clean}%'"
    return _query_tad(where=where, result_count=100)


async def fetch_tad_properties(
    city: str = "FORT WORTH",
    limit: int = 2000,
) -> List[Dict[str, Any]]:
    """Fetch properties from TAD by city."""
    all_results = []
    offset = 0
    batch_size = min(limit, TAD_MAX_RECORDS)

    while offset < limit:
        where = f"UPPER(CITY) LIKE '%{city.upper()}%'"
        try:
            batch = _query_tad(
                where=where,
                result_offset=offset,
                result_count=batch_size,
            )
            if not batch:
                break
            all_results.extend(batch)
            offset += len(batch)
            if len(batch) < batch_size:
                break
        except Exception as e:
            logger.error("TAD fetch failed at offset %d: %s", offset, e)
            break

    logger.info("Fetched %d properties from TAD", len(all_results))
    return all_results



# --- Off-market distress gate -------------------------------------------------
# TAD is the tax roll (every parcel). We only INSERT unmatched parcels when they
# show investor-relevant distress signals; otherwise we skip them so the DB
# doesn't fill with random county parcels.

_PROBATE_MARKERS = ("ESTATE", "HEIRS", "HEIR", "DECEASED", "PROBATE",
                    "REVOCABLE", "LIVING TRUST", "TRUSTEE")


def _deed_years_owned(deed_date: Optional[str]) -> float:
    if not deed_date:
        return 0.0
    try:
        d = datetime.fromisoformat(str(deed_date).replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - d).days / 365.25
    except Exception:
        return 0.0


def _tad_distress_signals(prop: Dict[str, Any]) -> Dict[str, Any]:
    owner = str(prop.get("owner_name") or "").upper()
    probate = any(m in owner for m in _PROBATE_MARKERS)
    years = _deed_years_owned(prop.get("deed_date"))
    long_term = years >= 15
    market_value = prop.get("market_value") or 0
    high_equity = long_term and market_value >= 100000
    improvement = prop.get("improvement_value") or 0
    land = prop.get("land_value") or 0
    vacant_land = bool(improvement == 0 and land > 0)
    return {
        "probate_estate": probate,
        "long_term_owner": long_term,
        "high_equity": high_equity,
        "vacant_land": vacant_land,
    }


async def import_tad_properties(
    db: PostgresDatabase,
    city: str = "FORT WORTH",
    limit: int = 1000,
) -> Dict[str, Any]:
    """Compatibility wrapper that stores TAD only in county_records.

    Older jobs and admin routes still call this name. Redirecting them here is
    the final guard against public parcel rows becoming listing cards.
    """
    from importers.county_records import sync_tad_county_records

    result = await sync_tad_county_records(db, records_per_run=limit)
    return {
        **result,
        "inserted": 0,
        "matched": 0,
        "skipped": result.get("rejected_blank", 0),
        "deprecated_entrypoint": True,
        "note": "TAD rows were stored in county_records; no listing cards were created.",
    }


if __name__ == "__main__":
    import asyncio, json

    async def main():
        from database import PostgresDatabase
        db = PostgresDatabase()
        try:
            await db.connect()
            result = await import_tad_properties(db, limit=100)
            print(json.dumps(result, indent=2))
        finally:
            await db.close()

    asyncio.run(main())
