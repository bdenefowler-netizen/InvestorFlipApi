"""User-facing spreadsheet and property-link intake helpers.

The intake path rejects addressless rows, uses stable address identities, and
stores the original row so imported data can be audited instead of silently
discarding columns we do not recognize yet.
"""

from __future__ import annotations

import math
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional
from urllib.parse import unquote, urlparse

from address_utils import canonical_street_key
from database import PostgresDatabase
from investor_logic import compute_scores, derive_owner_signals, merge_live_refresh


ALIASES = {
    "address": ("address", "property address", "situs address", "street address", "site address", "full address"),
    "city": ("city", "property city", "situs city"),
    "state": ("state", "property state", "situs state"),
    "zip": ("zip", "zipcode", "zip code", "postal code", "property zip"),
    "price": ("price", "list price", "listing price", "asking price", "opening bid"),
    "market_value": ("market value", "estimated value", "zestimate", "arv"),
    "beds": ("beds", "bedrooms", "bedroom count"),
    "baths": ("baths", "bathrooms", "bathroom count"),
    "sqft": ("sqft", "square feet", "living area", "living sqft"),
    "year_built": ("year built", "year_built"),
    "owner_name": ("owner", "owner name", "property owner", "seller"),
    "owner_mailing_address": ("mailing address", "owner mailing address", "owner address"),
    "account_id": ("account", "account id", "tax account", "parcel", "parcel id", "apn"),
    "listing_type": ("listing type", "deal type", "status", "property status", "tags"),
    "listing_description": ("description", "listing description", "remarks", "public remarks", "marketing remarks", "notes"),
    "detail_url": ("url", "link", "property url", "listing url"),
    "image_url": ("image", "image url", "photo", "photo url", "property image", "property_image"),
    "property_type": ("property type", "home type", "building type"),
    "phone": ("phone", "phone number", "phone_number", "owner phone", "seller phone"),
    "email": ("email", "owner email", "seller email"),
}


def _clean(value: Any) -> Any:
    if value is None:
        return None
    try:
        if isinstance(value, float) and math.isnan(value):
            return None
        if value != value:  # pandas/numpy missing scalar
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(value, "item"):
        try:
            value = value.item()
        except (TypeError, ValueError):
            pass
    if hasattr(value, "isoformat") and not isinstance(value, (str, bytes)):
        try:
            value = value.isoformat()
        except (TypeError, ValueError):
            value = str(value)
    text = str(value).strip()
    return None if not text or text.lower() in {"nan", "none", "null", "n/a"} else value


def _text(value: Any) -> str:
    cleaned = _clean(value)
    return re.sub(r"\s+", " ", str(cleaned).strip()) if cleaned is not None else ""


def _number(value: Any) -> Optional[float]:
    try:
        text = re.sub(r"[^0-9.-]", "", _text(value))
        return float(text) if text else None
    except (TypeError, ValueError):
        return None


def _row_value(row: Mapping[str, Any], field: str) -> Any:
    lowered = {re.sub(r"[_\s]+", " ", str(key).strip().lower()): value for key, value in row.items()}
    for alias in ALIASES[field]:
        value = _clean(lowered.get(alias))
        if value is not None:
            return value
    return None


def _address_parts(address: str, city: str, state: str, zip_code: str) -> Dict[str, str]:
    parts = [part.strip() for part in address.split(",")]
    street = parts[0] if parts else address
    if not city and len(parts) > 1:
        city = parts[1]
    tail = " ".join(parts[2:]) if len(parts) > 2 else address
    state_match = re.search(r"\b([A-Z]{2})\s+\d{5}(?:-\d{4})?\b", tail.upper())
    zip_match = re.search(r"\b(\d{5})(?:-\d{4})?\b", tail)
    state = state or (state_match.group(1) if state_match else "TX")
    zip_code = zip_code or (zip_match.group(1) if zip_match else "")
    city = city or "Fort Worth"
    full = f"{street}, {city}, {state} {zip_code}".strip()
    return {"street": street, "city": city, "state": state, "zip": zip_code[:5], "full": full}


def infer_listing_type(value: Any) -> Dict[str, Any]:
    text = _text(value).lower().replace("_", "-")
    if "for sale by owner" in text or "fsbo" in text:
        listing_type = "For Sale By Owner"
    elif "reo" in text or "bank owned" in text:
        listing_type = "REO"
    elif "foreclos" in text or "pre-foreclos" in text:
        listing_type = "Foreclosure"
    elif "as-is" in text or "as is" in text:
        listing_type = "As-Is"
    elif "cash offer" in text or "cash house" in text:
        listing_type = "Cash House"
    elif "investor" in text:
        listing_type = "Investor"
    else:
        listing_type = "For Sale"
    motivated = any(term in text for term in (
        "motivated", "distress", "foreclos", "reo", "tax lien", "tax lein",
        "cash offer", "investor special", "as-is", "as is", "wholesale",
        "for sale by owner", "fsbo", "needs tlc", "contractor", "fixer upper",
        "fixer-upper", "cash only", "priced to sell", "needs work", "rehab",
        "renovation", "handyman special",
    ))
    return {
        "listing_type": listing_type,
        "listing_status": _text(value) or listing_type,
        "pre_foreclosure": "pre-foreclos" in text or "preforeclos" in text,
        "fsbo_confirmed": listing_type == "For Sale By Owner",
        "wholesale": "wholesale" in text or "investor special" in text,
        "motivation_score": 70 if motivated else 0,
        "distress_score": 70 if motivated else 0,
    }


def normalize_import_row(row: Mapping[str, Any], source_name: str, row_number: int) -> Optional[Dict[str, Any]]:
    address = _text(_row_value(row, "address"))
    if not canonical_street_key(address):
        return None
    parts = _address_parts(
        address,
        _text(_row_value(row, "city")),
        _text(_row_value(row, "state")),
        _text(_row_value(row, "zip")),
    )
    identity = f"{canonical_street_key(parts['full'])}:{parts['zip']}"
    now = datetime.now(timezone.utc).isoformat()
    owner_name = _text(_row_value(row, "owner_name"))
    owner_mailing = _text(_row_value(row, "owner_mailing_address"))
    property_type = _text(_row_value(row, "property_type")) or "Single Family Residential"
    listing_description = _text(_row_value(row, "listing_description"))
    status = infer_listing_type(" ".join(filter(None, [_text(_row_value(row, "listing_type")), listing_description])))
    price = _number(_row_value(row, "price"))
    market_value = _number(_row_value(row, "market_value"))
    document: Dict[str, Any] = {
        "id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"listing:{identity}")),
        "address_key": canonical_street_key(parts["full"]),
        "situs_address": parts["full"],
        "city": parts["city"],
        "state": parts["state"],
        "zip": parts["zip"],
        "county": "Tarrant",
        "property_type": property_type,
        "home_type": property_type,
        "price": int(price) if price is not None else 0,
        "market_value": int(market_value) if market_value is not None else None,
        "market_value_source": "uploaded estimate" if market_value else None,
        "beds": _number(_row_value(row, "beds")),
        "baths": _number(_row_value(row, "baths")),
        "sqft": int(_number(_row_value(row, "sqft")) or 0) or None,
        "year_built": int(_number(_row_value(row, "year_built")) or 0) or None,
        "owner_name": owner_name,
        "owner_mailing_address": owner_mailing,
        "owner_phone": _text(_row_value(row, "phone")),
        "owner_email": _text(_row_value(row, "email")),
        "account_id": _text(_row_value(row, "account_id")),
        "detail_url": _text(_row_value(row, "detail_url")),
        "image_url": _text(_row_value(row, "image_url")),
        "listing_description": listing_description,
        "is_live_listing": True,
        "data_source": source_name,
        "listing_sources": [source_name],
        "import_row_number": row_number,
        "raw_import_row": {str(key): _clean(value) for key, value in row.items()},
        "created_at": now,
        "updated_at": now,
        "listing_last_seen_at": now,
        "missed_syncs": 0,
        **status,
    }
    document.update(derive_owner_signals(owner_name, owner_mailing, parts["full"], parts["state"]))
    document.update(compute_scores(document))
    return document


async def upsert_import_records(
    database: PostgresDatabase,
    rows: Iterable[Mapping[str, Any]],
    source_name: str,
) -> Dict[str, Any]:
    accepted: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    for row_number, row in enumerate(rows, start=2):
        normalized = normalize_import_row(row, source_name, row_number)
        if normalized:
            accepted.append(normalized)
        else:
            rejected.append({"row": row_number, "reason": "missing or invalid property address"})

    # One file can contain the same property more than once. Merge those rows
    # before touching the database so the result is deterministic.
    unique: Dict[str, Dict[str, Any]] = {}
    duplicates = 0
    for record in accepted:
        if record["id"] in unique:
            unique[record["id"]] = merge_live_refresh(unique[record["id"]], record)
            duplicates += 1
        else:
            unique[record["id"]] = record

    ids: List[str] = []
    inserted = updated = 0
    for record in unique.values():
        existing = await database.properties.find_one({"id": record["id"]}, {"_id": 0})
        if not existing:
            existing = await database.properties.find_one({
                "address_key": record["address_key"], "zip": record["zip"],
            }, {"_id": 0})
        if not existing:
            street = str(record.get("situs_address") or "").split(",", 1)[0].strip()
            address_query: Dict[str, Any] = {
                "situs_address": {"$regex": f"^{re.escape(street)}(?:,|\\s|$)", "$options": "i"},
            }
            if record.get("zip"):
                address_query["zip"] = record["zip"]
            existing = await database.properties.find_one(address_query, {"_id": 0})
        if existing:
            record = merge_live_refresh(existing, record)
            updated += 1
        else:
            inserted += 1
        await database.properties.update_one({"id": record["id"]}, {"$set": record}, upsert=True)
        ids.append(record["id"])
    return {
        "rows_read": len(accepted) + len(rejected),
        "accepted": len(unique),
        "rejected": len(rejected),
        "duplicates_merged": duplicates,
        "inserted": inserted,
        "updated": updated,
        "property_ids": ids,
        "rejections": rejected[:25],
    }


SUPPORTED_LINK_HOSTS = (
    "zillow.com", "realtor.com", "redfin.com", "auction.com",
    "xome.com", "trulia.com", "homes.com", "har.com",
)


def property_link_seed(url: str) -> Dict[str, Any]:
    parsed = urlparse(_text(url))
    host = (parsed.hostname or "").lower().removeprefix("www.")
    if parsed.scheme not in {"http", "https"} or not any(host == item or host.endswith(f".{item}") for item in SUPPORTED_LINK_HOSTS):
        raise ValueError("Use a Zillow, Realtor, Redfin, Auction.com, Xome, Trulia, Homes.com, or HAR property link.")

    decoded = unquote(parsed.path)
    zpid_match = re.search(r"(?:/|_)(\d+)_zpid", decoded, re.I)
    zpid = zpid_match.group(1) if zpid_match else ""
    candidate = ""
    if "zillow.com" in host:
        match = re.search(r"/homedetails/([^/]+?)(?:/|_zpid)", decoded, re.I)
        candidate = match.group(1) if match else ""
    elif "realtor.com" in host:
        match = re.search(r"/realestateandhomes-detail/([^/]+)", decoded, re.I)
        candidate = match.group(1).split("_M", 1)[0] if match else ""
    elif "redfin.com" in host:
        segments = [part for part in decoded.split("/") if part]
        candidate = " ".join(segments[-2:-1]) if len(segments) > 2 else ""
        # Redfin usually stores city/state immediately before the street slug.
        if "home" in segments:
            index = segments.index("home")
            if index >= 3:
                candidate = f"{segments[index - 1]}, {segments[index - 2]}, {segments[index - 3]}"
    else:
        match = re.search(r"/(?:property|home|homes|listing)/([^/?]+)", decoded, re.I)
        candidate = match.group(1) if match else ""

    candidate = re.sub(r"[_-]+", " ", candidate).strip()
    city_match = re.match(
        r"^(.+?)\s+(Fort Worth|Arlington|Mansfield|Bedford|Euless|Hurst|"
        r"North Richland Hills|Grapevine)\s+([A-Z]{2})\s+(\d{5})$",
        candidate,
        re.I,
    )
    if city_match:
        candidate = (
            f"{city_match.group(1)}, {city_match.group(2)}, "
            f"{city_match.group(3).upper()} {city_match.group(4)}"
        )
    zip_match = re.search(r"\b(\d{5})\b", candidate)
    state_match = re.search(r"\b([A-Z]{2})\b", candidate.upper())
    return {
        "url": url,
        "host": host,
        "zpid": zpid,
        "address": candidate,
        "zip": zip_match.group(1) if zip_match else "",
        "state": state_match.group(1) if state_match else "TX",
    }
