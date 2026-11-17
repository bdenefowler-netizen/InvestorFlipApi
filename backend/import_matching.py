"""Deterministic local parcel resolution. Never call paid providers during upload."""
import re
from address_utils import canonical_street_key
from importers.county_records import normalize_account_id


def legal_query(value):
    tokens = re.findall(r"[A-Z0-9]+", str(value or "").upper())
    if not tokens:
        return None
    return {"legal_description": {"$regex": r"^[^A-Z0-9]*" + r"[^A-Z0-9]+".join(map(re.escape, tokens)) + r"[^A-Z0-9]*$", "$options": "i"}}


async def candidates(collection, query):
    return await collection.find(query, {"_id": 0}).limit(2).to_list(length=2)


async def resolve_county(db, record):
    account = normalize_account_id(record.get("account_id"))
    if account:
        query = {"$or": [{"normalized_account_id": account}, {"account_id": record["account_id"]}]}
        method = "tad_account_exact"
    else:
        query = legal_query(record.get("legal_description"))
        method = "legal_exact"
        if not query:
            key = canonical_street_key(record.get("situs_address"))
            if not key or not (record.get("zip") or record.get("city")):
                return None, "unmatched"
            query = {"address_key": key}
            query["zip" if record.get("zip") else "city"] = record.get("zip") or record.get("city")
            method = "address_exact"
    rows = await candidates(db.county_records, query)
    if len(rows) > 1:
        return None, "ambiguous"
    return (rows[0], method) if rows else (None, "unmatched")


async def fill_county_identity(db, record):
    county, method = await resolve_county(db, record)
    if county:
        # County identity remains authoritative; the submitted values stay in raw_import_row.
        for key in ("account_id", "parcel_id", "legal_description", "situs_address", "city", "state", "zip", "owner_name", "owner_mailing_address", "sqft", "beds", "baths", "year_built", "market_value", "appraised_value"):
            if county.get(key) not in (None, ""):
                record[key] = county[key]
        record["address_key"] = canonical_street_key(record.get("situs_address"))
        record["legal_key"] = " ".join(re.findall(r"[A-Z0-9]+", str(record.get("legal_description") or "").upper()))
        record["county_record_id"] = county.get("id")
        record["county_match_method"] = method
    elif method == "ambiguous":
        record.update(import_review_required=True, import_validation_status="REVIEW")
        record["import_review_reasons"] = ["Multiple county parcels match; no parcel selected"]
    return method
