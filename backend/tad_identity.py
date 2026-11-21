"""Resolve and reuse Tarrant Appraisal District identity without paid providers.

Uploaded leads may arrive with only a legal description or a street address.
The free TAD ArcGIS service is used once to recover the account/parcel identity;
after that the saved county record is the authoritative source for owner,
values, legal description, and property facts.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Mapping, Optional, Tuple

from address_utils import canonical_street_key
from importers.county_records import (
    county_record_from_tad,
    normalize_account_id,
    upsert_county_records,
)
from importers.tad_scraper import search_tad_by_address, search_tad_by_legal

logger = logging.getLogger("investorflip.tad_identity")

UPLOAD_LEAD_CATEGORIES = {
    "pre_foreclosure",
    "probate",
    "code_violations",
    "tax_lien",
    "tax_auction",
    "repair_notice",
}

_IDENTITY_FIELDS = (
    "account_id", "normalized_account_id", "parcel_id", "apn", "address_key",
    "tad_verified", "tad_resolved_by", "county_record_id", "county_match_method",
    "legal_description", "legal_key", "situs_address", "city", "state", "zip",
    "owner_name", "owner_mailing_address", "owner_type", "beds", "baths", "sqft",
    "year_built", "lot_size_sqft", "lot_size_acres", "market_value",
    "appraised_value", "tax_roll_market_value", "land_value", "improvement_value",
    "annual_taxes", "current_tax_amount_due", "prior_tax_amount_due",
    "tax_delinquent", "subdivision", "absentee_owner", "out_of_state_owner",
)

_COUNTY_FIELDS = (
    "account_id", "parcel_id", "legal_description", "situs_address", "city",
    "state", "zip", "owner_name", "owner_mailing_address", "beds", "baths",
    "sqft", "year_built", "lot_size_sqft", "lot_size_acres", "market_value",
    "appraised_value", "tax_roll_market_value", "land_value", "improvement_value",
    "annual_taxes", "current_tax_amount_due", "prior_tax_amount_due",
    "tax_delinquent", "subdivision", "absentee_owner", "out_of_state_owner",
    "owner_type",
)


def has_tad_identity(record: Mapping[str, Any]) -> bool:
    return bool(
        normalize_account_id(record.get("account_id"))
        or str(record.get("parcel_id") or record.get("apn") or "").strip()
    )


def _best_match(rows: list[Dict[str, Any]], record: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    wanted = canonical_street_key(
        str(record.get("situs_address") or record.get("address") or "")
    )
    wanted_zip = str(record.get("zip") or "").strip()[:5]
    if not rows:
        return None
    if wanted:
        exact = [
            row for row in rows
            if canonical_street_key(str(row.get("SITUS_ADDR") or "")) == wanted
        ]
        if exact:
            return exact[0]
    if wanted_zip:
        zipped = [
            row for row in rows
            if str(row.get("ZIPCODE") or row.get("SITUS_ZIP") or "").strip()[:5] == wanted_zip
        ]
        if zipped:
            return zipped[0]
    return rows[0]


async def resolve_tad_identity(
    db, record: Dict[str, Any], *, max_results: int = 5,
) -> bool:
    """Fill missing TAD identity from a free address/legal lookup.

    Returns True when the record was changed. Failures are non-fatal: uploads
    remain usable with the identity the caller supplied.
    """
    if has_tad_identity(record):
        return False

    address = str(record.get("situs_address") or record.get("address") or "").strip()
    legal = str(record.get("legal_description") or "").strip()
    if not address and not legal:
        return False

    try:
        if address:
            rows = await search_tad_by_address(address)
            method = "tad_address_lookup"
        else:
            rows = await search_tad_by_legal(legal)
            method = "tad_legal_lookup"
        best = _best_match(rows[:max_results], record)
        if not best:
            return False
        county = county_record_from_tad(best)
        if not county:
            return False
        await upsert_county_records(db, [county])
        from import_matching import fill_county_identity

        await fill_county_identity(db, record)
        if county.get("account_id") and not record.get("account_id"):
            record["account_id"] = county["account_id"]
            record["normalized_account_id"] = county.get("normalized_account_id")
        if county.get("parcel_id") and not record.get("parcel_id"):
            record["parcel_id"] = county["parcel_id"]
        record["tad_verified"] = True
        record["tad_resolved_by"] = method
        record["county_record_id"] = county["id"]
        record["county_match_method"] = method
        return True
    except Exception as exc:
        logger.warning("Free TAD identity lookup failed: %s", exc)
        return False


async def find_county_record(
    db, record: Mapping[str, Any],
) -> Optional[Tuple[Dict[str, Any], str]]:
    """Match a stored property to the county index by TAD id, parcel, then address."""
    account = normalize_account_id(record.get("account_id"))
    if account:
        rows = await db.county_records.find(
            {"$or": [
                {"normalized_account_id": account},
                {"account_id": record.get("account_id")},
            ]},
            {"_id": 0},
        ).limit(2).to_list(length=2)
        if len(rows) == 1:
            return dict(rows[0]), "tad_account_exact"
        if len(rows) > 1:
            return None

    parcel = str(record.get("parcel_id") or record.get("apn") or "").strip()
    if parcel:
        rows = await db.county_records.find(
            {"$or": [{"parcel_id": parcel}, {"account_id": parcel}]},
            {"_id": 0},
        ).limit(2).to_list(length=2)
        if len(rows) == 1:
            return dict(rows[0]), "parcel_exact"
        if len(rows) > 1:
            return None

    key = canonical_street_key(
        str(record.get("situs_address") or record.get("address") or "")
    )
    if not key:
        return None
    query: Dict[str, Any] = {"address_key": key}
    zip_code = str(record.get("zip") or "").strip()[:5]
    if zip_code:
        query["zip"] = zip_code
    rows = await db.county_records.find(query, {"_id": 0}).limit(2).to_list(length=2)
    if len(rows) == 1:
        return dict(rows[0]), "address_exact"
    return None


def apply_county_identity(record: Dict[str, Any], county: Mapping[str, Any], method: str) -> bool:
    """Copy authoritative county fields onto a property record."""
    changed = False
    for key in _COUNTY_FIELDS:
        value = county.get(key)
        if value not in (None, "", [], {}):
            record[key] = value
            changed = True
    record["county_record_id"] = county.get("id")
    record["county_match_method"] = method
    if method == "tad_account_exact":
        record["tad_verified"] = True
        record["has_uploaded_tad"] = True
    return changed


async def persist_identity(db, property_id: str, record: Mapping[str, Any]) -> int:
    update = {key: record[key] for key in _IDENTITY_FIELDS if key in record}
    if not update:
        return 0
    await db.properties.update_one({"id": property_id}, {"$set": update})
    return len(update)


async def enrich_from_county_identity(db, record: Dict[str, Any]) -> Tuple[bool, str]:
    """Hydrate a property from saved county/TAD data. Returns (changed, method)."""
    found = await find_county_record(db, record)
    if not found:
        return False, "unmatched"
    county, method = found
    return apply_county_identity(record, county, method), method
