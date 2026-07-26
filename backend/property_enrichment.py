"""Normalize full property-detail responses from us-real-estate-data1.

The provider can evolve its response wrapper without forcing the rest of
InvestorFlip to depend on provider-specific field names.  Only normalized,
useful fields are persisted; the potentially large raw response is not stored.
"""

from __future__ import annotations

from typing import Any, Dict, List

from listing_normalization import as_dict, extract_listing_fields, safe_int


def _first_present(*values: Any) -> Any:
    for value in values:
        if value is not None and value != "":
            return value
    return None


def _subject(payload: Any) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    data = payload.get("data")
    if isinstance(data, dict):
        for key in ("property", "home", "details", "result", "listing", "mls"):
            value = data.get(key)
            if isinstance(value, dict):
                return value
        return data
    if isinstance(data, list):
        return next((item for item in data if isinstance(item, dict)), {})
    listings = payload.get("listings") or payload.get("results")
    if isinstance(listings, list):
        return next((item for item in listings if isinstance(item, dict)), {})
    for key in ("property", "home", "details", "result", "listing", "mls"):
        value = payload.get(key)
        if isinstance(value, dict):
            return value
    return payload


def _dict_list(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def normalize_property_detail(payload: Any) -> Dict[str, Any]:
    """Return a stable InvestorFlip enrichment patch from a provider response."""
    subject = _subject(payload)
    if not subject:
        return {"detail_found": False}

    fields = extract_listing_fields(subject)
    address = fields["address"]
    facts = as_dict(subject.get("resoFacts") or subject.get("facts"))
    attribution = as_dict(subject.get("attributionInfo") or subject.get("attribution"))

    zpid = _first_present(subject.get("zpid"), subject.get("property_id"), subject.get("id"))
    lot_size = _first_present(fields.get("lot_size_sqft"), facts.get("lotSize"), facts.get("lot_size"))
    parcel_id = _first_present(
        subject.get("parcelId"), subject.get("parcel_id"), subject.get("apn"), facts.get("parcelNumber")
    )
    mls_id = _first_present(
        subject.get("mlsId"), subject.get("mls_id"), subject.get("mlsNumber"),
        attribution.get("mlsId"),
    )
    source_mls = _first_present(subject.get("mlsName"), attribution.get("mlsName"))
    description_value = subject.get("description")
    if isinstance(description_value, dict):
        description_value = _first_present(description_value.get("text"), description_value.get("value"))

    patch: Dict[str, Any] = {
        "detail_found": True,
        "zpid": zpid,
        "beds": _first_present(fields.get("beds"), facts.get("bedrooms")),
        "baths": _first_present(fields.get("baths"), facts.get("bathrooms")),
        "sqft": _first_present(fields.get("sqft"), facts.get("livingArea")),
        "lot_size_sqft": safe_int(lot_size),
        "year_built": _first_present(fields.get("year_built"), facts.get("yearBuilt")),
        "home_type": _first_present(fields.get("property_type"), subject.get("homeType")),
        "home_status": _first_present(subject.get("homeStatus"), subject.get("status")),
        "list_price": fields.get("price") or None,
        "zestimate": fields.get("zestimate"),
        "rent_zestimate": _first_present(subject.get("rentZestimate"), subject.get("rent_zestimate")),
        "tax_assessed_value": _first_present(
            fields.get("assessed_value"), subject.get("taxAssessedValue"), subject.get("tax_assessed_value")
        ),
        "latitude": fields.get("latitude"),
        "longitude": fields.get("longitude"),
        "rapidapi_address": address.get("street"),
        "rapidapi_city": address.get("city"),
        "rapidapi_state": address.get("state"),
        "rapidapi_zip": address.get("zip"),
        "mls_id": mls_id,
        "source_mls": source_mls,
        "parcel_id": parcel_id,
        "listing_agent_name": _first_present(
            attribution.get("agentName"), attribution.get("agent_name"), subject.get("listing_agent_name")
        ),
        "listing_agent_phone": _first_present(
            attribution.get("agentPhoneNumber"), attribution.get("agent_phone"), subject.get("listing_agent_phone")
        ),
        "listing_agent_email": _first_present(
            attribution.get("agentEmail"), attribution.get("agent_email"),
            subject.get("listing_agent_email"), subject.get("ListAgentEmail"),
        ),
        "listing_agent_url": _first_present(
            attribution.get("agentUrl"), attribution.get("agent_url"),
            attribution.get("agentProfileUrl"), subject.get("listing_agent_url"),
            subject.get("agentUrl"), subject.get("ListAgentURL"),
        ),
        "listing_agent_fulfillment_id": _first_present(
            attribution.get("agentFulfillmentId"),
            attribution.get("fulfillmentId"),
            subject.get("agent_fulfillment_id"),
            subject.get("fulfillmentId"),
        ),
        "broker_name": _first_present(
            attribution.get("brokerName"), attribution.get("broker_name"), subject.get("broker_name")
        ),
        "description": _first_present(description_value, subject.get("text")),
        "photos": fields.get("photos") or [],
        "price_history": _dict_list(subject.get("priceHistory") or subject.get("price_history")),
        "provider_tax_history": _dict_list(subject.get("taxHistory") or subject.get("tax_history")),
        "schools": _dict_list(subject.get("schools")),
    }

    return {key: value for key, value in patch.items() if value not in (None, "", [])}
