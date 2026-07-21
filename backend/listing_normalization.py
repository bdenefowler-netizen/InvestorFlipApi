"""Pure response-shape normalization for third-party real-estate listings."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional


def as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def safe_int(value: Any, default: Optional[int] = None) -> Optional[int]:
    try:
        if value in (None, ""):
            return default
        if isinstance(value, str):
            value = re.sub(r"[^0-9.-]", "", value)
        return int(float(value))
    except (TypeError, ValueError):
        return default


def safe_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if value in (None, ""):
            return default
        if isinstance(value, str):
            value = re.sub(r"[^0-9.-]", "", value)
        return float(value)
    except (TypeError, ValueError):
        return default


def photo_url(value: Any) -> Optional[str]:
    if isinstance(value, str) and value.strip():
        return value.strip().replace("http://", "https://", 1)
    if isinstance(value, dict):
        for key in ("href", "url", "src"):
            url = value.get(key)
            if isinstance(url, str) and url.strip():
                return url.strip().replace("http://", "https://", 1)
    return None


def extract_address(item: Dict[str, Any]) -> Dict[str, str]:
    raw_address = item.get("address")
    address_obj = raw_address if isinstance(raw_address, dict) else {}
    location_obj = as_dict(item.get("location"))
    location_address = as_dict(location_obj.get("address"))

    street = (
        item.get("streetAddress") or item.get("street_address") or item.get("street")
        or item.get("address1") or item.get("addressLine") or item.get("address_line_1")
        or address_obj.get("streetAddress") or address_obj.get("street_address")
        or address_obj.get("street") or address_obj.get("line") or address_obj.get("address1")
        or location_obj.get("streetAddress") or location_obj.get("street")
        or location_address.get("streetAddress") or location_address.get("street")
        or location_address.get("line") or ""
    )
    city = (
        item.get("city") or item.get("addressCity") or item.get("locality")
        or address_obj.get("city") or address_obj.get("locality")
        or location_obj.get("city") or location_obj.get("locality")
        or location_address.get("city") or "Fort Worth"
    )
    state = (
        item.get("state") or item.get("addressState") or item.get("region")
        or address_obj.get("state") or address_obj.get("region")
        or location_obj.get("state") or location_obj.get("region")
        or location_address.get("state") or "TX"
    )
    zip_code = (
        item.get("zipcode") or item.get("zip") or item.get("postal_code") or item.get("postalCode")
        or address_obj.get("zipcode") or address_obj.get("zip")
        or address_obj.get("postal_code") or address_obj.get("postalCode")
        or location_obj.get("postal_code") or location_obj.get("postalCode")
        or location_address.get("postal_code") or location_address.get("postalCode") or ""
    )
    full = (
        (raw_address if isinstance(raw_address, str) else "")
        or item.get("full_address") or item.get("fullAddress")
        or item.get("formattedAddress") or item.get("formatted_address")
        or item.get("address_line") or item.get("addressLine")
        or address_obj.get("formattedAddress") or address_obj.get("formatted_address")
        or location_obj.get("formattedAddress") or location_obj.get("formatted_address")
        or (f"{street}, {city}, {state} {zip_code}".strip(", ") if street else "")
    )
    return {
        "street": str(street).strip(), "city": str(city).strip(),
        "state": str(state).strip() or "TX", "zip": str(zip_code).strip(),
        "full": str(full).strip(),
    }


def extract_listing_fields(item: Dict[str, Any]) -> Dict[str, Any]:
    description = as_dict(item.get("description"))
    location_address = as_dict(as_dict(item.get("location")).get("address"))
    coordinate = as_dict(location_address.get("coordinate"))
    source = as_dict(item.get("source"))
    agents = source.get("agents") if isinstance(source.get("agents"), list) else []
    source_agent = as_dict(agents[0]) if agents else {}

    raw_type = (
        item.get("homeType") or item.get("home_type") or item.get("propertyType")
        or item.get("property_type") or item.get("propertySubType")
        or item.get("property_sub_type") or item.get("propertyTypeText")
        or item.get("property_type_name") or item.get("style") or item.get("type")
        or description.get("type") or description.get("sub_type")
    )
    if raw_type:
        raw_type = str(raw_type).replace("_", " ").strip()

    photos: List[str] = []
    for key in ("imgSrc", "image", "image_url", "photo_url", "primary_photo", "hiResImageLink"):
        url = photo_url(item.get(key))
        if url:
            photos.append(url)
    for array_key in ("photos", "originalPhotos", "responsivePhotos"):
        values = item.get(array_key)
        if not isinstance(values, list):
            continue
        for value in values[:5]:
            url = photo_url(value)
            if url:
                photos.append(url)
            mixed = as_dict(value).get("mixedSources") if isinstance(value, dict) else None
            jpeg = as_dict(mixed).get("jpeg")
            if isinstance(jpeg, list) and jpeg:
                url = photo_url(jpeg[-1])
                if url:
                    photos.append(url)

    return {
        "address": extract_address(item),
        "property_type": raw_type,
        "price": safe_int(
            item.get("price") or item.get("listPrice") or item.get("list_price")
            or item.get("asking_price") or item.get("unformattedPrice"), 0
        ),
        "zestimate": safe_int(item.get("zestimate") or item.get("estimate") or item.get("estimated_value")),
        "assessed_value": safe_int(item.get("taxAssessedValue") or item.get("tax_assessed_value")),
        "annual_taxes": safe_int(item.get("annualTaxAmount") or item.get("annual_taxes") or item.get("taxAnnualAmount"), 0),
        "beds": safe_float(item.get("beds") or item.get("bedrooms") or item.get("bedroom_count") or description.get("beds")),
        "baths": safe_float(
            item.get("baths") or item.get("bathrooms") or item.get("bathroom_count")
            or item.get("bathroomsFloat") or description.get("baths") or description.get("baths_full_calc")
        ),
        "sqft": safe_int(
            item.get("livingArea") or item.get("living_area") or item.get("square_feet")
            or item.get("building_size") or item.get("area") or item.get("area_sqft")
            or description.get("sqft")
        ),
        "year_built": safe_int(item.get("yearBuilt") or item.get("year_built") or description.get("year_built")),
        "lot_size_sqft": safe_int(
            item.get("lotSize") or item.get("lot_size") or item.get("lotAreaValue") or description.get("lot_sqft")
        ),
        "photos": list(dict.fromkeys(photos)),
        "latitude": item.get("latitude") or item.get("lat") or coordinate.get("lat"),
        "longitude": item.get("longitude") or item.get("lng") or item.get("lon") or coordinate.get("lon"),
        "source": source,
        "source_agent": source_agent,
    }
