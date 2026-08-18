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
        or item.get("propertyAddress") or item.get("PropertyAddress")
        or item.get("situsAddress") or item.get("situs_address")
        or item.get("siteAddress") or item.get("site_address")
        or item.get("public_address")
        or address_obj.get("streetAddress") or address_obj.get("street_address")
        or address_obj.get("street") or address_obj.get("line") or address_obj.get("address1")
        or location_obj.get("streetAddress") or location_obj.get("street")
        or location_address.get("streetAddress") or location_address.get("street")
        or location_address.get("line") or ""
    )
    city = (
        item.get("city") or item.get("addressCity") or item.get("locality")
        or item.get("propertyCity") or item.get("PropertyCity")
        or item.get("situsCity") or item.get("situs_city")
        or address_obj.get("city") or address_obj.get("locality")
        or location_obj.get("city") or location_obj.get("locality")
        or location_address.get("city") or "Fort Worth"
    )
    state = (
        item.get("state") or item.get("addressState") or item.get("region")
        or item.get("propertyState") or item.get("PropertyState")
        or item.get("situsState") or item.get("situs_state")
        or address_obj.get("state") or address_obj.get("region")
        or location_obj.get("state") or location_obj.get("region")
        or location_address.get("state") or "TX"
    )
    zip_code = (
        item.get("zipcode") or item.get("zip") or item.get("postal_code") or item.get("postalCode")
        or item.get("propertyZip") or item.get("PropertyZip")
        or item.get("situsZip") or item.get("situs_zip")
        or address_obj.get("zipcode") or address_obj.get("zip")
        or address_obj.get("postal_code") or address_obj.get("postalCode")
        or location_obj.get("postal_code") or location_obj.get("postalCode")
        or location_address.get("postal_code") or location_address.get("postalCode") or ""
    )
    full = (
        (raw_address if isinstance(raw_address, str) else "")
        or item.get("full_address") or item.get("fullAddress")
        or item.get("public_address")
        or item.get("formattedAddress") or item.get("formatted_address")
        or item.get("address_line") or item.get("addressLine")
        or item.get("propertyFullAddress") or item.get("situsFullAddress")
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
        or item.get("PropertyType")
        or item.get("property_type") or item.get("propertySubType")
        or item.get("PropertySubType")
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
            or item.get("ListPrice")
            or item.get("asking_price") or item.get("unformattedPrice"), 0
        ),
        "zestimate": safe_int(item.get("zestimate") or item.get("estimate") or item.get("estimated_value")),
        "assessed_value": safe_int(item.get("taxAssessedValue") or item.get("tax_assessed_value")),
        "annual_taxes": safe_int(item.get("annualTaxAmount") or item.get("annual_taxes") or item.get("taxAnnualAmount"), 0),
        "beds": safe_float(
            item.get("beds") or item.get("bedrooms") or item.get("bedroom_count")
            or item.get("BedroomsTotal") or description.get("beds")
        ),
        "baths": safe_float(
            item.get("baths") or item.get("bathrooms") or item.get("bathroom_count")
            or item.get("bathroomsFloat") or item.get("BathroomsTotalInteger")
            or item.get("BathroomsTotalDecimal")
            or description.get("baths") or description.get("baths_full_calc")
        ),
        "sqft": safe_int(
            item.get("livingArea") or item.get("living_area") or item.get("square_feet")
            or item.get("LivingArea")
            or item.get("building_size") or item.get("area") or item.get("area_sqft")
            or item.get("sq_footage")
            or description.get("sqft")
        ),
        "year_built": safe_int(
            item.get("yearBuilt") or item.get("year_built") or item.get("YearBuilt")
            or description.get("year_built")
        ),
        "lot_size_sqft": safe_int(
            item.get("lotSize") or item.get("lot_size") or item.get("lotAreaValue")
            or item.get("LotSizeSquareFeet") or item.get("lot_size_sqft")
            or description.get("lot_sqft")
        ),
        "photos": list(dict.fromkeys(photos)),
        "latitude": item.get("latitude") or item.get("lat") or coordinate.get("lat"),
        "longitude": item.get("longitude") or item.get("lng") or item.get("lon") or coordinate.get("lon"),
        "source": source,
        "source_agent": source_agent,
    }


def hydrate_listing_record(record: Dict[str, Any]) -> Dict[str, Any]:
    """Fill legacy top-level listing fields from the provider payload.

    Older live-listing syncs stored the complete Realtor payload in
    ``raw_source_excerpt`` but left the mobile-facing fields empty.  Hydrating
    on read makes those records useful immediately and remains safe after a
    fresh sync because populated top-level values always win.
    """
    hydrated = dict(record)
    raw = as_dict(record.get("raw_source_excerpt"))
    if not raw:
        return hydrated

    fields = extract_listing_fields(raw)
    source = fields["source"]
    source_agent = fields["source_agent"]
    description = as_dict(raw.get("description"))

    fallbacks = {
        "beds": fields["beds"],
        "baths": fields["baths"],
        "sqft": fields["sqft"],
        "year_built": fields["year_built"],
        "lot_size_sqft": fields["lot_size_sqft"],
        "latitude": fields["latitude"],
        "longitude": fields["longitude"],
        "source_mls": source.get("name"),
        "mls_id": source.get("listing_id"),
        "listing_agent_name": source_agent.get("agent_name"),
        "listing_agent_url": (
            source_agent.get("agent_url")
            or source_agent.get("profile_url")
            or source_agent.get("href")
        ),
        "broker_name": source_agent.get("office_name"),
        "listing_description": description.get("text"),
        "property_type": fields["property_type"],
        "home_type": fields["property_type"],
        "detail_url": raw.get("href") or raw.get("detail_url"),
        "listing_date": raw.get("list_date") or raw.get("listDate"),
    }
    for key, value in fallbacks.items():
        if hydrated.get(key) in (None, "") and value not in (None, ""):
            hydrated[key] = value

    photos = list(fields["photos"])
    raw_photos = raw.get("photos") if isinstance(raw.get("photos"), list) else []
    for value in raw_photos:
        url = photo_url(value)
        if url and url not in photos:
            photos.append(url)
    if photos:
        hydrated["photos"] = photos
        if not photo_url(hydrated.get("image_url")):
            hydrated["image_url"] = photos[0]

    advertisers = raw.get("advertisers") if isinstance(raw.get("advertisers"), list) else []
    advertiser = as_dict(advertisers[0]) if advertisers else {}
    office = as_dict(advertiser.get("office"))
    phones = office.get("phones") if isinstance(office.get("phones"), list) else []
    phone = as_dict(phones[0]).get("number") if phones else None
    if hydrated.get("listing_agent_phone") in (None, "") and phone:
        hydrated["listing_agent_phone"] = phone
    fulfillment_id = (
        advertiser.get("fulfillment_id")
        or advertiser.get("fulfillmentId")
        or office.get("fulfillment_id")
        or office.get("fulfillmentId")
    )
    if hydrated.get("listing_agent_fulfillment_id") in (None, "") and fulfillment_id:
        hydrated["listing_agent_fulfillment_id"] = str(fulfillment_id)
    if hydrated.get("broker_name") in (None, "") and office.get("name"):
        hydrated["broker_name"] = office["name"]

    return hydrated


def build_provider_address_query(record: Dict[str, Any]) -> str:
    """Return one valid provider address without duplicating city/state."""
    raw = as_dict(record.get("raw_source_excerpt"))
    if raw:
        address = extract_address(raw)
        if address["street"]:
            state = address["state"]
            if state.casefold() == "texas":
                state = "TX"
            return ", ".join(
                part for part in (
                    address["street"],
                    address["city"] or "Fort Worth",
                    f"{state or 'TX'} {address['zip']}".strip(),
                )
                if part
            )

    situs = str(record.get("situs_address") or "").strip()
    base = re.sub(r",?\s*Tarrant County,?\s*(TX)?\.?\s*$", "", situs, flags=re.I).strip().rstrip(",")
    base = re.sub(r"\bTexas\b", "TX", base, flags=re.I)
    if re.search(r"\b(?:TX)\s*\d{5}\b", base, flags=re.I):
        return base

    city = str(record.get("city") or "Fort Worth").title().strip()
    zip_code = str(record.get("zip") or "").strip()
    street = base.split(",", 1)[0].strip()
    return ", ".join(part for part in (street, city, f"TX {zip_code}".strip()) if part)
