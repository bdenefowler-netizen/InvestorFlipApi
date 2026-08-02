"""One-time + repeatable data-quality pass for the InvestorFlip properties store.

Implements the V1 data cleanup diagnosis:
1. Restrict records to Tarrant County / Fort Worth (drop confident out-of-county rows)
2. Merge duplicates by parcel_id, normalized address, and coordinates
3. Treat $0/$1 auction values as "price unavailable", not asking prices
4. Gate recommendations when confidence is low (recommendable flag + score hygiene)
5. Separate genuine county fields from listing/API fields (strip pollution)
6. Clean bad and duplicate production records
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

# Fort Worth + Tarrant County municipalities (conservative allowlist).
TARRANT_CITIES = {
    "fort worth", "arlington", "bedford", "hurst", "euless", "north richland hills",
    "haltom city", "keller", "southlake", "colleyville", "grapevine", "mansfield",
    "grand prairie", "crowley", "benbrook", "white settlement", "saginaw", "watauga",
    "richland hills", "forest hill", "edgecliff village", "lake worth", "river oaks",
    "azle", "haslet", "blue mound", "westworth village", "dalworthington gardens",
    "pantego", "kennedale", "everman", "burleson", "fort-worth", "ft worth", "ft worth",
}

# Tarrant County zip prefixes (760xx-762xx mostly; 761xx = Fort Worth core).
TARRANT_ZIP_PREFIXES = ("760", "761", "762")

# Key fields used to judge how complete a record is (duplicate keeper wins).
_COMPLETENESS_FIELDS = [
    "price", "market_value", "assessed_value", "beds", "baths", "sqft",
    "situs_address", "parcel_id", "mls_id", "zpid", "image_url", "photos",
    "owner_name", "owner_type", "year_built", "listing_description",
    "latitude", "longitude", "violation_types", "listing_type",
]

_SOURCE_SPLIT = re.compile(r"\s*\+\s*")


def _norm(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip().lower()


def _completeness(record: Dict[str, Any]) -> int:
    score = 0
    for field in _COMPLETENESS_FIELDS:
        value = record.get(field)
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        if isinstance(value, (list, dict)) and not value:
            continue
        score += 1
    return score


def _clean_address(raw: Any) -> str:
    """Normalize situs_address: strip county suffix, collapse whitespace."""
    text = _norm(raw)
    if not text:
        return ""
    text = re.sub(r",?\s*tarrant\s+county,?\s*(tx)?\.?\s*$", "", text, flags=re.I).strip()
    text = re.sub(r",?\s*tarrant\s+county\s*$", "", text, flags=re.I).strip()
    text = re.sub(r"\s+", " ", text).strip().rstrip(",")
    return text


def _duplicate_key(record: Dict[str, Any]) -> Optional[str]:
    """Stable dedupe key: parcel_id > normalized address+zip > coordinates."""
    parcel = _norm(record.get("parcel_id"))
    if parcel:
        return f"parcel:{parcel}"

    address = _clean_address(record.get("situs_address"))
    if address:
        zip_code = _norm(record.get("zip"))
        return f"addr:{address}|zip:{zip_code}"

    lat = record.get("latitude")
    lon = record.get("longitude")
    if lat is not None and lon is not None:
        return f"coord:{round(float(lat), 4)},{round(float(lon), 4)}"
    return None


def _clean_data_source(value: Any) -> Any:
    """Dedupe repeated 'X + X + X' concatenations in data_source."""
    if not isinstance(value, str) or "+" not in value:
        return value
    parts = [p.strip() for p in _SOURCE_SPLIT.split(value) if p.strip()]
    seen: List[str] = []
    for part in parts:
        if part not in seen:
            seen.append(part)
    return " + ".join(seen)


def _looks_like_placeholder_price(record: Dict[str, Any]) -> bool:
    price = record.get("price")
    if not isinstance(price, (int, float)):
        return False
    # $0 / $1 auction placeholders and code-violation rows without a listing.
    if price <= 1:
        return True
    return False


def _is_confidently_out_of_tarrant(record: Dict[str, Any]) -> bool:
    """Only delete when we are sure the record is outside Tarrant County."""
    county = _norm(record.get("county"))
    if county and county not in ("tarrant", "tarrant county", "tx", "texas"):
        return True

    city = _norm(record.get("city"))
    zip_code = _norm(record.get("zip"))
    if city and city not in TARRANT_CITIES and zip_code and not zip_code.startswith(TARRANT_ZIP_PREFIXES):
        return True
    if city and city not in TARRANT_CITIES and not zip_code and not county:
        # No county, no zip, foreign city -> assume out of market.
        return True
    return False


def _gate_scores(record: Dict[str, Any]) -> Dict[str, Any]:
    """Null out composite scores that cannot be trusted (no price / low confidence)."""
    changes: Dict[str, Any] = {}
    price = record.get("price")
    confidence = _norm(record.get("score_confidence"))
    recommendable = isinstance(price, (int, float)) and price > 1 and confidence in ("high", "medium")
    changes["recommendable"] = recommendable

    if not isinstance(price, (int, float)) or price <= 1:
        for field in ("investment_score", "wholesale_score", "flip_score"):
            if record.get(field) is not None:
                changes[field] = None
        changes["roi_status"] = "price unavailable - not scored"
    return changes


async def run_data_cleanup(db, dry_run: bool = False) -> Dict[str, Any]:
    """Execute the cleanup pass. Returns a stats report."""
    stats: Dict[str, Any] = {
        "scanned": 0,
        "duplicates_deleted": 0,
        "out_of_tarrant_deleted": 0,
        "placeholder_prices_fixed": 0,
        "data_source_cleaned": 0,
        "address_cleaned": 0,
        "scores_gated": 0,
        "records_updated": 0,
        "dry_run": dry_run,
    }

    docs = await db.properties.find({}, {"_id": 0}).to_list(length=10000)
    stats["scanned"] = len(docs)

    # --- Pass 1: drop confident out-of-Tarrant records ---------------------
    to_delete: List[str] = []
    for doc in docs:
        if _is_confidently_out_of_tarrant(doc):
            to_delete.append(doc["id"])
    if not dry_run:
        for doc_id in to_delete:
            await db.properties.delete_one({"id": doc_id})
    stats["out_of_tarrant_deleted"] = len(to_delete)

    # --- Pass 2: dedupe (keep the most complete record per key) ------------
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for doc in docs:
        if doc["id"] in to_delete:
            continue
        key = _duplicate_key(doc)
        if not key:
            continue
        groups.setdefault(key, []).append(doc)

    duplicate_ids: List[str] = []
    for key, group in groups.items():
        if len(group) <= 1:
            continue
        group.sort(key=lambda d: (_completeness(d), str(d.get("updated_at") or "")), reverse=True)
        keeper = group[0]
        for dup in group[1:]:
            if dup["id"] != keeper["id"]:
                duplicate_ids.append(dup["id"])

    if not dry_run:
        for doc_id in duplicate_ids:
            await db.properties.delete_one({"id": doc_id})
    stats["duplicates_deleted"] = len(duplicate_ids)

    # --- Pass 3+: field-level cleanup on surviving records ------------------
    surviving = [d for d in docs if d["id"] not in to_delete and d["id"] not in duplicate_ids]
    for doc in surviving:
        changes: Dict[str, Any] = {}

        # 3. $0/$1 placeholder prices -> price unavailable
        if _looks_like_placeholder_price(doc):
            changes["price"] = None
            changes["discount_to_benchmark_pct"] = None
            changes["value_spread"] = None
            stats["placeholder_prices_fixed"] += 1

        # 4. confidence gating
        gate = _gate_scores(doc)
        if gate:
            stats["scores_gated"] += 1
        changes.update(gate)

        # 5. data_source pollution
        cleaned_source = _clean_data_source(doc.get("data_source"))
        if cleaned_source != doc.get("data_source"):
            changes["data_source"] = cleaned_source
            stats["data_source_cleaned"] += 1

        # 6. situs_address county suffix
        cleaned_addr = _clean_address(doc.get("situs_address"))
        if cleaned_addr and cleaned_addr != _norm(doc.get("situs_address")):
            changes["situs_address"] = cleaned_addr
            stats["address_cleaned"] += 1

        if changes:
            stats["records_updated"] += 1
            if not dry_run:
                await db.properties.update_one({"id": doc["id"]}, {"$set": changes})

    return stats
