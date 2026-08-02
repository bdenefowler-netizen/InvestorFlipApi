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


_STREET_SUFFIX = {
    "street": "st", "st": "st", "drive": "dr", "dr": "dr", "road": "rd", "rd": "rd",
    "avenue": "ave", "ave": "ave", "ave.": "ave", "lane": "ln", "ln": "ln",
    "court": "ct", "ct": "ct", "circle": "cir", "cir": "cir", "boulevard": "blvd",
    "blvd": "blvd", "place": "pl", "pl": "pl", "terrace": "ter", "ter": "ter",
    "trail": "trl", "trl": "trl", "highway": "hwy", "hwy": "hwy", "parkway": "pkwy",
    "pkwy": "pkwy", "way": "wy", "wy": "wy",
}
_DIRECTIONS = {"north": "n", "n": "n", "south": "s", "s": "s", "east": "e", "e": "e",
               "west": "w", "w": "w", "ne": "ne", "nw": "nw", "se": "se", "sw": "sw"}


def _clean_address(raw: Any) -> str:
    """Normalize situs_address: strip county suffix, normalize suffix/direction words,
    collapse whitespace — so '2401 Kelton Street' == '2401 Kelton St' for merging."""
    text = _norm(raw)
    if not text:
        return ""
    text = re.sub(r",?\s*tarrant\s+county,?\s*(tx)?\.?\s*$", "", text, flags=re.I).strip()
    text = re.sub(r",?\s*tarrant\s+county\s*$", "", text, flags=re.I).strip()
    # remove state + zip at end so address matching ignores formatting diffs
    text = re.sub(r",?\s*tx\.?\s*\d{5}(-\d{4})?\s*$", "", text, flags=re.I).strip()
    text = re.sub(r"\s+", " ", text).strip().rstrip(",")
    # token-level normalization of directions + suffixes
    tokens = text.split()
    out = []
    for tok in tokens:
        low = tok.strip(".,")
        if low in _DIRECTIONS:
            out.append(_DIRECTIONS[low])
        elif low in _STREET_SUFFIX:
            out.append(_STREET_SUFFIX[low])
        else:
            out.append(tok.strip(".,"))
    return " ".join(out)


def _duplicate_key(record: Dict[str, Any]) -> Optional[str]:
    """Stable dedupe key: normalized address+zip > parcel_id > coordinates.

    Address is preferred because different sources (foreclosure CSV, Zillow,
    code violations) store different parcel_ids for the same house, but the
    street address matches. This merges the Kelton-style splits."""
    address = _clean_address(record.get("situs_address"))
    if address:
        zip_code = _norm(record.get("zip"))
        return f"addr:{address}|zip:{zip_code}"

    parcel = _norm(record.get("parcel_id"))
    if parcel:
        return f"parcel:{parcel}"

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




def _merge_into_keeper(keeper: Dict[str, Any], dup: Dict[str, Any]) -> Dict[str, Any]:
    """Absorb missing fields and union list fields from a duplicate into the keeper."""
    merged = dict(keeper)

    # union data_source strings ("Foreclosure Finder + Zillow")
    sources = _clean_data_source(keeper.get("data_source"))
    dup_sources = _clean_data_source(dup.get("data_source"))
    source_parts = [s.strip() for s in str(sources).split("+") if s.strip()]
    for s in str(dup_sources).split("+"):
        s = s.strip()
        if s and s not in source_parts:
            source_parts.append(s)
    if source_parts:
        merged["data_source"] = " + ".join(source_parts)

    # union photos
    for field in ("photos", "image_urls"):
        keep_val = merged.get(field)
        dup_val = dup.get(field)
        if isinstance(keep_val, list) and isinstance(dup_val, list):
            seen = set(map(str, keep_val))
            for u in dup_val:
                if str(u) not in seen:
                    keep_val.append(u)
                    seen.add(str(u))
        elif not keep_val and dup_val:
            merged[field] = dup_val

    # fill missing scalar fields from the duplicate (keeper wins on conflicts)
    for field in _COMPLETENESS_FIELDS:
        if field in ("data_source", "photos", "image_urls"):
            continue
        if merged.get(field) in (None, "", 0) and dup.get(field) not in (None, "", 0):
            merged[field] = dup.get(field)

    return merged


async def run_data_cleanup(db, dry_run: bool = False) -> Dict[str, Any]:
    """Execute the cleanup pass. Returns a stats report."""
    stats: Dict[str, Any] = {
        "scanned": 0,
        "duplicates_deleted": 0,
        "duplicates_merged": 0,
        "out_of_tarrant_deleted": 0,
        "placeholder_prices_fixed": 0,
        "data_source_cleaned": 0,
        "address_cleaned": 0,
        "address_key_backfilled": 0,
        "scores_gated": 0,
        "records_updated": 0,
        "dry_run": dry_run,
    }

    docs = await db.properties.find({}, {"_id": 0}).to_list(length=10000)
    stats["scanned"] = len(docs)

    # --- Pass 0: backfill canonical address_key so every importer (and the
    # tax-roll matcher) can find records by normalized street ----------------
    for doc in docs:
        if doc.get("address_key"):
            continue
        key = canonical_street_key(doc.get("situs_address") or doc.get("address"))
        if not key:
            continue
        stats["address_key_backfilled"] += 1
        if not dry_run:
            await db.properties.update_one({"id": doc["id"]}, {"$set": {"address_key": key}})

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
    merged_updates: List[Dict[str, Any]] = []
    for key, group in groups.items():
        if len(group) <= 1:
            continue
        group.sort(key=lambda d: (_completeness(d), str(d.get("updated_at") or "")), reverse=True)
        keeper = group[0]
        for dup in group[1:]:
            if dup["id"] == keeper["id"]:
                continue
            merged_updates.append({"keeper": keeper, "dup": dup})
            duplicate_ids.append(dup["id"])

    if not dry_run:
        for m in merged_updates:
            merged = _merge_into_keeper(m["keeper"], m["dup"])
            # only write if something actually changed
            keeper_changed = any(merged.get(k) != m["keeper"].get(k) for k in merged)
            if keeper_changed:
                changes = {k: v for k, v in merged.items() if v != m["keeper"].get(k)}
                await db.properties.update_one({"id": m["keeper"]["id"]}, {"$set": changes})
        for doc_id in duplicate_ids:
            await db.properties.delete_one({"id": doc_id})
    stats["duplicates_deleted"] = len(duplicate_ids)
    stats["duplicates_merged"] = len(merged_updates)

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
