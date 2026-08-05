"""Build a durable, spreadsheet-friendly county property dataset.

Live listings and public records have different lifecycles.  A listing can
disappear tomorrow, while a TAD parcel and an official tax account remain useful
for owner research.  This module stores public records independently, merges
nonblank fields by account/address, and can enrich the live listing table without
turning incomplete public rows into blank listing cards.
"""

from __future__ import annotations

import asyncio
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional

from address_utils import canonical_street_key
from database import PostgresDatabase


COUNTY_SOURCE_TAD = "Tarrant Appraisal District (TAD)"
COUNTY_SOURCE_TAX = "Tarrant County Tax Roll"
DEFAULT_TAD_BATCH_SIZE = 500
DEFAULT_TAD_RECORDS_PER_RUN = 5000
VOLATILE_RECORD_FIELDS = {
    "updated_at", "tad_updated_at", "tax_roll_updated_at",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _number(value: Any) -> Optional[float]:
    try:
        number = float(value)
        return number if number != 0 else None
    except (TypeError, ValueError):
        return None


def normalize_account_id(value: Any) -> str:
    cleaned = re.sub(r"[^A-Z0-9]", "", _text(value).upper())
    if cleaned.isdigit():
        return cleaned.lstrip("0") or "0"
    return cleaned


def _location_text(value: Any) -> str:
    return re.sub(r"[^A-Z0-9]", "", _text(value).upper())


def county_candidate_matches(property_record: Mapping[str, Any], county_record: Mapping[str, Any]) -> bool:
    """Return true only for a deterministic parcel/address match.

    Account ID is authoritative. Address matching additionally requires ZIP or
    city context; a bare street key is not unique across a county.
    """
    property_account = normalize_account_id(property_record.get("account_id"))
    county_account = normalize_account_id(county_record.get("account_id"))
    if property_account:
        return bool(county_account and property_account == county_account)

    property_key = canonical_street_key(property_record.get("situs_address"))
    county_key = canonical_street_key(county_record.get("situs_address"))
    if not property_key or property_key != county_key:
        return False

    property_zip = _text(property_record.get("zip"))[:5]
    county_zip = _text(county_record.get("zip"))[:5]
    if property_zip and county_zip:
        return county_zip == property_zip

    property_city = _location_text(property_record.get("city"))
    county_city = _location_text(county_record.get("city"))
    return bool(property_city and county_city and property_city == county_city)


def choose_county_candidate(
    property_record: Mapping[str, Any],
    candidates: Iterable[Mapping[str, Any]],
) -> Optional[Dict[str, Any]]:
    matches = [dict(item) for item in candidates if county_candidate_matches(property_record, item)]
    return matches[0] if len(matches) == 1 else None


def county_record_id(account_id: Any, address: Any) -> Optional[str]:
    account = normalize_account_id(account_id)
    if account:
        return f"county-account-{account}"
    address_key = canonical_street_key(_text(address))
    if not address_key:
        return None
    return f"county-address-{uuid.uuid5(uuid.NAMESPACE_URL, address_key).hex}"


def _without_blanks(document: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        key: value
        for key, value in document.items()
        if value is not None and value != "" and value != [] and value != {}
    }


def _store_raw_payloads() -> bool:
    """Raw county payloads are opt-in because they make Postgres grow rapidly.

    Every field needed by the app/export is mapped onto the canonical county
    record.  The official source URL/file remains the audit trail.  Operators
    who have provisioned enough storage can explicitly retain the full source
    row with ``COUNTY_STORE_RAW_PAYLOADS=true``.
    """
    return os.environ.get("COUNTY_STORE_RAW_PAYLOADS", "false").strip().lower() == "true"


def _stable_record(record: Mapping[str, Any]) -> Dict[str, Any]:
    """Return the meaningful portion used to avoid no-op snapshot rewrites."""
    return {
        key: value for key, value in record.items()
        if key not in VOLATILE_RECORD_FIELDS
    }


def _merge_unique(existing: Any, incoming: Any) -> List[str]:
    def items(value: Any) -> List[Any]:
        if value in (None, ""):
            return []
        if isinstance(value, (list, tuple, set)):
            return list(value)
        return [value]

    values: List[str] = []
    for item in [*items(existing), *items(incoming)]:
        text = _text(item)
        if text and text not in values:
            values.append(text)
    return values


def _owner_signals(owner_name: str, mailing_state: str, situs_address: str, mailing_address: str) -> Dict[str, bool]:
    owner_upper = owner_name.upper()
    trust = any(marker in owner_upper for marker in ("TRUST", "TRUSTEE", "ESTATE", "HEIRS"))
    company = any(marker in owner_upper for marker in (" LLC", " L.L.C", " INC", " CORP", " LP", " LTD"))
    absentee = bool(
        canonical_street_key(mailing_address)
        and canonical_street_key(situs_address)
        and canonical_street_key(mailing_address) != canonical_street_key(situs_address)
    )
    return {
        "absentee_owner": absentee,
        "out_of_state_owner": bool(mailing_state and mailing_state.upper() not in {"TX", "TEXAS"}),
        "trust_owned": trust,
        "company_owned": company,
    }


def county_record_from_tad(
    raw: Mapping[str, Any], *, include_raw: Optional[bool] = None,
) -> Optional[Dict[str, Any]]:
    street = _text(raw.get("SITUS_ADDR"))
    city = _text(raw.get("CITY")) or "Fort Worth"
    state = _text(raw.get("STATE")) or "TX"
    zip_code = _text(raw.get("ZIPCODE") or raw.get("SITUS_ZIP"))[:5]
    if not street:
        return None
    address = ", ".join(part for part in (street, city, f"{state} {zip_code}".strip()) if part)
    account = _text(raw.get("ACCOUNT"))
    record_id = county_record_id(account, address)
    if not record_id:
        return None

    owner_name = _text(raw.get("OWNER_NAME"))
    owner_street = _text(raw.get("OWNER_ADDR"))
    owner_city_state = _text(raw.get("OWNER_CITY"))
    owner_zip = _text(raw.get("OWNER_ZIP"))[:5]
    mailing_address = ", ".join(part for part in (owner_street, owner_city_state, owner_zip) if part)
    mailing_state = ""
    state_match = re.search(r"(?:,|\s)([A-Z]{2})(?:\s|$)", owner_city_state.upper())
    if state_match:
        mailing_state = state_match.group(1)

    record: Dict[str, Any] = {
        "id": record_id,
        "record_kind": "county",
        "account_id": account,
        "normalized_account_id": normalize_account_id(account),
        "parcel_id": _text(raw.get("TAXPIN")),
        "situs_address": address,
        "address_key": canonical_street_key(address),
        "city": city,
        "state": state,
        "zip": zip_code,
        "county": "Tarrant",
        "owner_name": owner_name,
        "owner_mailing_address": mailing_address,
        "mailing_state": mailing_state,
        "beds": _number(raw.get("BEDROOMS")),
        "baths": _number(raw.get("BATHROOMS")),
        "sqft": _number(raw.get("LIVING_ARE")),
        "year_built": int(raw["YEAR_BUILT"]) if _number(raw.get("YEAR_BUILT")) else None,
        "lot_size_sqft": _number(raw.get("LAND_SQFT")),
        "lot_size_acres": _number(raw.get("LAND_ACRES")),
        "garage_capacity": _number(raw.get("GARAGE_CAP")),
        "appraised_value": _number(raw.get("APPRAISEDV")),
        "market_value": _number(raw.get("TOTAL_VALU")),
        "land_value": _number(raw.get("LAND_VALUE")),
        "improvement_value": _number(raw.get("IMPR_VALUE")),
        "school_district": _text(raw.get("SCHOOL")),
        "has_tad": True,
        "source_names": [COUNTY_SOURCE_TAD],
        "tad_updated_at": utc_now(),
        "updated_at": utc_now(),
    }
    keep_raw = include_raw if include_raw is not None else _store_raw_payloads()
    if keep_raw:
        record["tad_raw"] = dict(raw)
    record.update(_owner_signals(owner_name, mailing_state, address, mailing_address))
    return _without_blanks(record)


def county_record_from_tax_roll(
    record: Mapping[str, Any], source_name: str, *, include_raw: Optional[bool] = None,
) -> Optional[Dict[str, Any]]:
    from importers.tax_roll import build_situs_address

    address = build_situs_address(record)
    account = _text(record.get("account_id"))
    record_id = county_record_id(account, address)
    if not record_id or not address:
        return None
    owner_name = _text(f"{record.get('owner_name_1') or ''} {record.get('owner_name_2') or ''}")
    owner_street = _text(f"{record.get('owner_address_1') or ''} {record.get('owner_address_2') or ''}")
    owner_city = _text(record.get("owner_city"))
    owner_state = _text(record.get("owner_state"))
    owner_zip = _text(record.get("owner_zip"))[:5]
    mailing_address = ", ".join(part for part in (owner_street, owner_city, f"{owner_state} {owner_zip}".strip()) if part)
    current_due = _number(record.get("current_amount_due")) or 0.0
    prior_due = _number(record.get("prior_amount_due")) or 0.0
    land_value = _number(record.get("land_value")) or 0.0
    improvement_value = _number(record.get("improvement_value")) or 0.0

    county: Dict[str, Any] = {
        "id": record_id,
        "record_kind": "county",
        "account_id": account,
        "normalized_account_id": normalize_account_id(account),
        "situs_address": address,
        "address_key": canonical_street_key(address),
        "county": "Tarrant",
        "owner_name": owner_name,
        "owner_mailing_address": mailing_address,
        "mailing_city": owner_city,
        "mailing_state": owner_state,
        "mailing_zip": owner_zip,
        "sqft": _number(record.get("sqft")),
        "year_built": int(record["year_built"]) if _number(record.get("year_built")) else None,
        "lot_size_sqft": _number(record.get("lot_size_sqft")),
        "lot_size_acres": _number(record.get("acres")),
        "land_value": land_value,
        "improvement_value": improvement_value,
        "tax_roll_market_value": land_value + improvement_value,
        "annual_taxes": _number(record.get("adjusted_levy")),
        "current_tax_amount_due": current_due,
        "prior_tax_amount_due": prior_due,
        "tax_delinquent": bool(current_due > 0 or prior_due > 0),
        "delinquency_date": _text(record.get("delinquency_date")),
        "legal_description": _text(record.get("legal_description")),
        "roll_code": _text(record.get("roll_code")),
        "account_status_codes": _text(record.get("account_status_codes")),
        "owner_exemption_codes": _text(record.get("owner_exemption_codes")),
        "tad_litigation_flag": _text(record.get("tad_litigation_flag")),
        "has_tax_roll": True,
        "source_names": [source_name],
        "tax_roll_source": source_name,
        "tax_roll_updated_at": utc_now(),
        "updated_at": utc_now(),
    }
    if current_due > 0 or prior_due > 0:
        county["opportunity_signal_keys"] = ["tax_lien"]
        county["opportunity_signals"] = ["Tax lien / delinquency"]
        county["signal_sources"] = {"tax_lien": [source_name]}
    keep_raw = include_raw if include_raw is not None else _store_raw_payloads()
    if keep_raw:
        county["tax_roll_raw"] = dict(record)
    county.update(_owner_signals(owner_name, owner_state, address, mailing_address))
    return _without_blanks(county)


def completeness(record: Mapping[str, Any]) -> Dict[str, Any]:
    important = (
        "situs_address", "owner_name", "account_id", "owner_mailing_address",
        "market_value", "year_built", "sqft",
    )
    missing = [field for field in important if record.get(field) in (None, "")]
    return {
        "completeness_score": round(100 * (len(important) - len(missing)) / len(important)),
        "missing_fields": missing,
    }


async def upsert_county_records(db: PostgresDatabase, records: Iterable[Mapping[str, Any]]) -> int:
    incoming: List[Dict[str, Any]] = []
    for item in records:
        document = _without_blanks(item)
        if not document.get("id") or not document.get("situs_address"):
            continue
        incoming.append(document)
    existing_by_id: Dict[str, Dict[str, Any]] = {}
    if incoming:
        existing = await db.county_records.find(
            {"id": {"$in": [item["id"] for item in incoming]}}, {"_id": 0}
        ).to_list(length=len(incoming))
        existing_by_id = {str(item["id"]): item for item in existing if item.get("id")}

    prepared: List[Dict[str, Any]] = []
    for document in incoming:
        previous = existing_by_id.get(str(document["id"]), {})
        merged = {**previous, **document}
        merged["source_names"] = _merge_unique(
            previous.get("source_names"), document.get("source_names")
        )
        merged["opportunity_signal_keys"] = _merge_unique(
            previous.get("opportunity_signal_keys"), document.get("opportunity_signal_keys")
        )
        merged["opportunity_signals"] = _merge_unique(
            previous.get("opportunity_signals"), document.get("opportunity_signals")
        )
        signal_sources = dict(previous.get("signal_sources") or {})
        for signal, sources in dict(document.get("signal_sources") or {}).items():
            signal_sources[signal] = _merge_unique(signal_sources.get(signal), sources)
        if signal_sources:
            merged["signal_sources"] = signal_sources
        # The tax-roll master contains only a street, while TAD supplies the
        # complete city/state address. Never replace the richer display value.
        if len(_text(previous.get("situs_address"))) > len(_text(document.get("situs_address"))):
            merged["situs_address"] = previous["situs_address"]
            merged["address_key"] = previous.get("address_key") or merged.get("address_key")
        merged.update(completeness(merged))
        # TAD is a snapshot. Re-reading an unchanged row should not create a new
        # JSONB tuple (and associated index/TOAST churn) just to refresh a
        # timestamp. Source-level freshness is recorded in county_sync_log.
        if previous and _stable_record(previous) == _stable_record(merged):
            continue
        prepared.append(merged)
    if prepared:
        await db.county_records.upsert_many(prepared)
    return len(prepared)


async def sync_tad_county_records(
    db: PostgresDatabase,
    records_per_run: Optional[int] = None,
    batch_size: int = DEFAULT_TAD_BATCH_SIZE,
) -> Dict[str, Any]:
    """Persist the next deterministic page range from TAD and remember progress."""
    from importers.tad_scraper import _query_tad

    per_run = records_per_run or int(
        os.environ.get("COUNTY_TAD_RECORDS_PER_RUN", str(DEFAULT_TAD_RECORDS_PER_RUN))
    )
    state = await db.sync_log.find_one({"name": "county_tad_cursor"}) or {}
    if state.get("scope") != "tarrant_all":
        # Older deployments paged only Fort Worth. The offset cannot be reused
        # after expanding to the complete Tarrant County parcel layer.
        state = {}
    offset = max(0, int(state.get("next_offset") or 0))
    starting_offset = offset
    fetched = written = rejected = 0
    complete = False

    while fetched < per_run:
        request_count = min(batch_size, per_run - fetched)
        raw_batch = await asyncio.to_thread(
            _query_tad,
            "1=1",
            "*",
            offset,
            request_count,
            "TAXPIN ASC",
        )
        if not raw_batch:
            complete = True
            break
        normalized = [record for raw in raw_batch if (record := county_record_from_tad(raw))]
        rejected += len(raw_batch) - len(normalized)
        written += await upsert_county_records(db, normalized)
        fetched += len(raw_batch)
        offset += len(raw_batch)
        if len(raw_batch) < request_count:
            complete = True
            break

    next_offset = 0 if complete else offset
    now = utc_now()
    await db.sync_log.update_one(
        {"name": "county_tad_cursor"},
        {"$set": {
            "name": "county_tad_cursor",
            "scope": "tarrant_all",
            "next_offset": next_offset,
            "last_start_offset": starting_offset,
            "last_fetched": fetched,
            "last_written": written,
            "last_completed_snapshot": now if complete else state.get("last_completed_snapshot"),
            "updated_at": now,
        }},
        upsert=True,
    )
    await db.county_sync_log.insert_one({
        "id": str(uuid.uuid4()),
        "source": "tad",
        "status": "success",
        "start_offset": starting_offset,
        "next_offset": next_offset,
        "fetched": fetched,
        "written": written,
        "rejected_blank": rejected,
        "snapshot_complete": complete,
        "created_at": now,
    })
    return {
        "ok": True,
        "source": "tad",
        "start_offset": starting_offset,
        "next_offset": next_offset,
        "fetched": fetched,
        "written": written,
        "rejected_blank": rejected,
        "snapshot_complete": complete,
    }


def _unique_source(existing: Any, source: str) -> str:
    labels = [part.strip() for part in str(existing or "").split("+") if part.strip()]
    if source not in labels:
        labels.append(source)
    return " + ".join(labels)


async def enrich_live_properties_from_county_records(
    db: PostgresDatabase,
    property_ids: Optional[Iterable[str]] = None,
    lookup_missing_tad: bool = True,
) -> Dict[str, int]:
    query: Dict[str, Any] = {"is_live_listing": True}
    if property_ids:
        query["id"] = {"$in": list(property_ids)}
    live = await db.properties.find(query, {"_id": 0}).to_list(length=250)
    enriched = looked_up = missing = 0

    async def find_or_fetch(prop: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
        nonlocal looked_up
        account = normalize_account_id(prop.get("account_id"))
        county = None
        if account:
            county = await db.county_records.find_one({"normalized_account_id": account})
        if not county:
            key = canonical_street_key(_text(prop.get("situs_address")))
            if key:
                candidates = await db.county_records.find(
                    {"address_key": key}, {"_id": 0}
                ).limit(20).to_list(length=20)
                county = choose_county_candidate(prop, candidates)
        if county or not lookup_missing_tad:
            return county

        from importers.tad_scraper import _query_tad

        street = _text(prop.get("situs_address")).split(",", 1)[0].replace("'", "''")
        if not street:
            return None
        raw = await asyncio.to_thread(
            _query_tad,
            f"UPPER(SITUS_ADDR) = '{street.upper()}'",
            "*",
            0,
            20,
            "TAXPIN ASC",
        )
        if not raw:
            return None
        normalized = [record for item in raw if (record := county_record_from_tad(item))]
        record = choose_county_candidate(prop, normalized)
        if not record:
            return None
        await upsert_county_records(db, [record])
        looked_up += 1
        return await db.county_records.find_one({"id": record["id"]}) or record

    for prop in live:
        county = await find_or_fetch(prop)
        if not county:
            missing += 1
            continue
        updates = _without_blanks({
            "account_id": prop.get("account_id") or county.get("account_id"),
            "parcel_id": prop.get("parcel_id") or county.get("parcel_id"),
            "owner_name": county.get("owner_name"),
            "owner_mailing_address": county.get("owner_mailing_address"),
            "absentee_owner": county.get("absentee_owner"),
            "out_of_state_owner": county.get("out_of_state_owner"),
            "beds": prop.get("beds") or county.get("beds"),
            "baths": prop.get("baths") or county.get("baths"),
            "sqft": prop.get("sqft") or county.get("sqft"),
            "year_built": prop.get("year_built") or county.get("year_built"),
            "lot_size_sqft": prop.get("lot_size_sqft") or county.get("lot_size_sqft"),
            "assessed_value": county.get("appraised_value") or prop.get("assessed_value"),
            "tax_roll_market_value": county.get("tax_roll_market_value"),
            "annual_taxes": county.get("annual_taxes"),
            "current_tax_amount_due": county.get("current_tax_amount_due"),
            "prior_tax_amount_due": county.get("prior_tax_amount_due"),
            "tax_delinquent": county.get("tax_delinquent", False),
            "legal_description": county.get("legal_description"),
            "county_record_id": county.get("id"),
            "county_match_method": (
                "account_id"
                if normalize_account_id(prop.get("account_id"))
                else "address_and_location"
            ),
            "county_enriched_at": utc_now(),
            "data_source": _unique_source(prop.get("data_source"), "TAD/Tax Roll"),
        })
        await db.properties.update_one({"id": prop["id"]}, {"$set": updates})
        enriched += 1
    return {"live_checked": len(live), "enriched": enriched, "tad_lookups": looked_up, "missing": missing}
