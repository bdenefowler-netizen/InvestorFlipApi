"""Live Fort Worth development-permit lookups from the official public view."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional

from address_utils import canonical_street_key
from importers.arcgis_client import (
    ArcGISClient,
    attributes,
    clean_search_text,
    sql_like_contains,
)

SOURCE_NAME = "City of Fort Worth Development Permits"
PERMITS_LAYER_URL = (
    "https://services5.arcgis.com/3ddLCBXe1bRt7mzj/arcgis/rest/services/"
    "CFW_Open_Data_Development_Permits_View/FeatureServer/0"
)


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
    return _text(value) or None


def _number(value: Any) -> Optional[float]:
    try:
        text = re.sub(r"[^0-9.-]", "", str(value or ""))
        return float(text) if text else None
    except (TypeError, ValueError):
        return None


def permit_is_open(status: Any) -> bool:
    value = _text(status).lower()
    if not value:
        return False
    return not any(
        term in value
        for term in (
            "finaled",
            "closed",
            "complete",
            "completed",
            "expired",
            "cancel",
            "void",
        )
    )


def _address_parts(address: str) -> tuple[Optional[int], str, str]:
    street = clean_search_text(address.split(",", 1)[0]).upper()
    match = re.match(r"^(\d+)\s+(.+)$", street)
    if not match:
        return None, street, street
    house = int(match.group(1))
    remainder = match.group(2)
    suffixes = r"\b(?:ST|STREET|RD|ROAD|AVE|AVENUE|DR|DRIVE|LN|LANE|CT|COURT|BLVD|BOULEVARD|PL|PLACE|WAY|TRL|TRAIL|CIR|CIRCLE|PKWY|PARKWAY)\b"
    street_name = re.sub(suffixes, " ", remainder, flags=re.IGNORECASE)
    street_name = re.sub(r"\s+", " ", street_name).strip()
    return house, street_name, street


async def search_permits_by_address(
    address: str, *, limit: int = 100
) -> List[Dict[str, Any]]:
    house, street_name, full_street = _address_parts(address)
    if len(street_name) < 2:
        return []
    if house is not None:
        where = f"Addr_No = {house} AND UPPER(Street_Name) LIKE {sql_like_contains(street_name)}"
    else:
        where = f"UPPER(Full_Street_Address) LIKE {sql_like_contains(full_street)}"
    client = ArcGISClient(PERMITS_LAYER_URL, page_size=min(max(limit, 1), 1000))
    features = await client.fetch_all(
        where=where,
        out_fields="*",
        limit=min(max(limit, 1), 500),
        order_by="ObjectId DESC",
    )
    return attributes(features)


def summarize_permits(records: Iterable[Mapping[str, Any]]) -> Dict[str, Any]:
    items: List[Dict[str, Any]] = []
    for raw in records:
        status = _text(raw.get("Current_Status"))
        address = _text(raw.get("Full_Street_Address") or raw.get("Location_1"))
        item = {
            "permit_number": _text(raw.get("Permit_No")),
            "permit_type": _text(raw.get("Permit_Type")),
            "permit_subtype": _text(raw.get("Permit_SubType")),
            "permit_category": _text(raw.get("Permit_Category")),
            "work_description": _text(raw.get("B1_WORK_DESC")),
            "address": address,
            "address_key": canonical_street_key(address),
            "zip": _text(raw.get("Zip_Code"))[:5],
            "status": status,
            "is_open": permit_is_open(status),
            "file_date": _epoch_to_iso(raw.get("File_Date")),
            "status_date": _epoch_to_iso(raw.get("Status_Date")),
            "job_value": _number(raw.get("JobValue")),
            "sqft": _number(raw.get("SqFt")),
            "units": _number(raw.get("Units")),
            "owner_at_permit": _text(raw.get("Owner_Full_Name")),
            "legal_description": _text(raw.get("B1_LEGAL_DESC")),
            "source": SOURCE_NAME,
        }
        items.append(
            {key: value for key, value in item.items() if value not in (None, "")}
        )

    items.sort(key=lambda item: item.get("file_date") or "", reverse=True)
    return {
        "source": SOURCE_NAME,
        "permit_count": len(items),
        "open_permit_count": sum(1 for item in items if item.get("is_open")),
        "latest_permit": items[0] if items else None,
        "permits": items,
    }
