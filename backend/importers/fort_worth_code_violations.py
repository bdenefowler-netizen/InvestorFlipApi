"""Fort Worth Code Violations ArcGIS importer.

Pulls the City's public code-violation dataset in pages, aggregates records by
property address, and merges distress signals into the durable county-record
store.  This is an address-based public-record signal; it must not be treated as
proof of vacancy by itself.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional

import httpx

from address_utils import canonical_street_key
from database import PostgresDatabase
from importers.county_records import county_record_id, upsert_county_records, utc_now


SOURCE_NAME = "City of Fort Worth Code Violations"
ARCGIS_QUERY_URL = (
    "https://services5.arcgis.com/3ddLCBXe1bRt7mzj/arcgis/rest/services/"
    "CFW_Open_Data_Code_Violations_Table_view/FeatureServer/0/query"
)
PAGE_SIZE = 1000


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _epoch_to_iso(value: Any) -> Optional[str]:
    if value in (None, ""):
        return None
    try:
        number = int(float(value))
        if number > 9_999_999_999:
            number //= 1000
        if number > 0:
            return datetime.fromtimestamp(number, tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError, OverflowError):
        pass
    text = _text(value)
    return text or None


def _status_open(value: Any) -> bool:
    status = _text(value).lower()
    if not status:
        return False
    return not any(term in status for term in ("closed", "resolved", "completed", "complied", "dismissed", "cancel"))


def _signal_flags(complaint: str) -> Dict[str, bool]:
    value = complaint.lower()
    return {
        "code_substandard_building": any(term in value for term in ("substandard", "unsafe structure", "dangerous building")),
        "code_property_maintenance": "property maintenance" in value,
        "code_high_grass_weeds": any(term in value for term in ("high grass", "weeds", "mow")),
        "code_health_hazard": "health hazard" in value,
        "code_solid_waste": any(term in value for term in ("solid waste", "trash", "debris")),
        "code_zoning": "zoning" in value,
        "code_vehicle": "vehicle" in value,
        "code_multifamily": "multi" in value and "family" in value,
    }


def _aggregate(features: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    grouped: Dict[str, Dict[str, Any]] = {}

    for feature in features:
        attrs = dict(feature.get("attributes") or feature)
        street = _text(attrs.get("Address"))
        if not street or street in {"0", "N/A", "NA"}:
            continue
        city = _text(attrs.get("City")) or "Fort Worth"
        state = _text(attrs.get("State")) or "TX"
        zip_code = _text(attrs.get("ZipCode"))[:5]
        full_address = ", ".join(part for part in (street, city, f"{state} {zip_code}".strip()) if part)
        key = f"{canonical_street_key(full_address)}|{zip_code}"
        if not key.strip("|"):
            continue

        complaint = _text(attrs.get("Complaint_Type_Description"))
        case_status = _text(attrs.get("Case_Current_Status"))
        violation_status = _text(attrs.get("Violation_Current_Status"))
        open_flag = _status_open(violation_status or case_status)
        created = _epoch_to_iso(attrs.get("Violation_Created_Date") or attrs.get("Case_Created_Date"))
        updated = _epoch_to_iso(attrs.get("Update_Date"))
        next_due = _epoch_to_iso(attrs.get("Next_Activity_Due_Date"))
        case_id = _text(attrs.get("Case_ID"))
        violation_id = _text(attrs.get("Violation_ID"))

        bucket = grouped.setdefault(key, {
            "situs_address": full_address,
            "city": city,
            "state": state,
            "zip": zip_code,
            "county": "Tarrant",
            "code_violation_count": 0,
            "open_code_violation_count": 0,
            "code_case_ids": [],
            "code_violation_ids": [],
            "code_complaint_types": [],
            "code_latest_complaint": "",
            "code_latest_status": "",
            "code_latest_update": "",
            "code_oldest_open_date": "",
            "code_next_activity_due": "",
            "code_geocode_score": None,
            "has_code_violations": True,
            "source_names": [SOURCE_NAME],
            "code_violation_updated_at": utc_now(),
            "updated_at": utc_now(),
        })

        bucket["code_violation_count"] += 1
        if open_flag:
            bucket["open_code_violation_count"] += 1
        if case_id and case_id not in bucket["code_case_ids"]:
            bucket["code_case_ids"].append(case_id)
        if violation_id and violation_id not in bucket["code_violation_ids"]:
            bucket["code_violation_ids"].append(violation_id)
        if complaint and complaint not in bucket["code_complaint_types"]:
            bucket["code_complaint_types"].append(complaint)

        if updated and (not bucket["code_latest_update"] or updated > bucket["code_latest_update"]):
            bucket["code_latest_update"] = updated
            bucket["code_latest_complaint"] = complaint
            bucket["code_latest_status"] = violation_status or case_status
            bucket["code_next_activity_due"] = next_due or ""

        if open_flag and created:
            if not bucket["code_oldest_open_date"] or created < bucket["code_oldest_open_date"]:
                bucket["code_oldest_open_date"] = created

        try:
            score = float(attrs.get("GeoCodeScore"))
            current = bucket.get("code_geocode_score")
            if current is None or score > float(current):
                bucket["code_geocode_score"] = score
        except (TypeError, ValueError):
            pass

        for flag, enabled in _signal_flags(complaint).items():
            if enabled:
                bucket[flag] = True

    return list(grouped.values())


async def fetch_code_violation_features(limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """Fetch all available code-violation features with ArcGIS pagination."""
    rows: List[Dict[str, Any]] = []
    offset = 0
    async with httpx.AsyncClient(timeout=45.0) as client:
        while True:
            batch_size = PAGE_SIZE if limit is None else min(PAGE_SIZE, max(0, limit - len(rows)))
            if batch_size <= 0:
                break
            response = await client.get(ARCGIS_QUERY_URL, params={
                "where": "1=1",
                "outFields": "Case_ID,Violation_ID,Address,City,State,Location_1,Case_Created_Date,Complaint_Type_Description,Violation_Current_Status,Case_Current_Status,Violation_Created_Date,Update_Date,ZipCode,Next_Activity_Due_Date,GeoCodeScore",
                "returnGeometry": "false",
                "f": "json",
                "resultOffset": offset,
                "resultRecordCount": batch_size,
                "orderByFields": "OBJECTID",
            })
            response.raise_for_status()
            payload = response.json()
            features = payload.get("features") or []
            if not features:
                break
            rows.extend(features)
            offset += len(features)
            if len(features) < batch_size or (limit is not None and len(rows) >= limit):
                break
    return rows


async def _attach_existing_ids(db: PostgresDatabase, records: List[Dict[str, Any]]) -> None:
    """Reuse an existing county-record id when address + ZIP deterministically match."""
    for record in records:
        street_key = canonical_street_key(record.get("situs_address"))
        if not street_key:
            continue
        zip_code = _text(record.get("zip"))[:5]
        regex = {"$regex": re.escape(_text(record.get("situs_address")).split(",")[0]), "$options": "i"}
        query: Dict[str, Any] = {"situs_address": regex}
        if zip_code:
            query["zip"] = zip_code
        candidates = await db.county_records.find(query, {"_id": 0}).limit(5).to_list(length=5)
        exact = [item for item in candidates if canonical_street_key(item.get("situs_address")) == street_key]
        if len(exact) == 1 and exact[0].get("id"):
            record["id"] = exact[0]["id"]
        else:
            record["id"] = county_record_id(None, record.get("situs_address"))


async def sync_fort_worth_code_violations(db: PostgresDatabase, limit: Optional[int] = None) -> Dict[str, Any]:
    features = await fetch_code_violation_features(limit=limit)
    records = _aggregate(features)
    await _attach_existing_ids(db, records)

    for record in records:
        signals = []
        keys = []
        if record.get("open_code_violation_count", 0) > 0:
            keys.append("code_violation")
            signals.append("Open code violation")
        for field, label in (
            ("code_substandard_building", "Substandard / unsafe structure"),
            ("code_property_maintenance", "Property maintenance issue"),
            ("code_high_grass_weeds", "High grass / weeds / repeat mowing"),
            ("code_health_hazard", "Health hazard"),
            ("code_solid_waste", "Solid waste / debris"),
        ):
            if record.get(field):
                keys.append(field)
                signals.append(label)
        if keys:
            record["opportunity_signal_keys"] = keys
            record["opportunity_signals"] = signals
            record["signal_sources"] = {key: [SOURCE_NAME] for key in keys}

    written = await upsert_county_records(db, records)
    await db.county_sync_log.insert_one({
        "id": f"code-violations-{datetime.now(timezone.utc).timestamp()}",
        "source": SOURCE_NAME,
        "status": "ok",
        "fetched": len(features),
        "written": written,
        "properties": len(records),
        "created_at": utc_now(),
    })
    return {"fetched": len(features), "properties": len(records), "written": written}
