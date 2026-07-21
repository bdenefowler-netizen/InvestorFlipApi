"""Normalize PropertyReach address-autocomplete responses for InvestorFlip."""

from typing import Any, Dict, List


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _first(value: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        candidate = value.get(key)
        if candidate not in (None, ""):
            return candidate
    return None


def _raw_suggestions(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []

    for candidate in (
        payload.get("suggestions"),
        _as_dict(payload.get("data")).get("suggestions"),
        payload.get("data"),
        payload.get("results"),
    ):
        if isinstance(candidate, list):
            return [item for item in candidate if isinstance(item, dict)]
    return []


def _is_fort_worth_texas(item: Dict[str, Any], title: str) -> bool:
    city = str(_first(item, "city", "locality") or "").strip().lower()
    state = str(_first(item, "state", "stateCode", "region") or "").strip().lower()
    searchable = " ".join((title, city, state)).lower()

    city_matches = city == "fort worth" or "fort worth" in searchable
    state_matches = state in {"tx", "texas"} or " tx " in f" {searchable} " or "texas" in searchable
    return city_matches and state_matches


def normalize_address_suggestions(payload: Any) -> List[Dict[str, Any]]:
    """Return stable, Fort Worth-only address suggestions.

    PropertyReach documents ``suggestions`` as the response array, while its
    RapidAPI proxy may wrap that array. Supporting the common wrappers keeps the
    public InvestorFlip response independent of provider response changes.
    """

    normalized: List[Dict[str, Any]] = []
    seen = set()

    for item in _raw_suggestions(payload):
        title = str(
            _first(item, "title", "formattedAddress", "fullAddress", "address") or ""
        ).strip()
        street = str(_first(item, "streetAddress", "street_address", "street") or "").strip()
        city = str(_first(item, "city", "locality") or "").strip()
        state = str(_first(item, "state", "stateCode", "region") or "").strip().upper()
        zipcode = str(_first(item, "zip", "zipcode", "zipCode", "postalCode") or "").strip()
        county = str(item.get("county") or "").strip()

        if not title:
            locality = ", ".join(part for part in (city, state) if part)
            title = ", ".join(part for part in (street, locality) if part)
            if zipcode:
                title = f"{title} {zipcode}".strip()

        if not title or not _is_fort_worth_texas(item, title):
            continue

        key = "|".join((street, city, state, zipcode, title)).lower()
        if key in seen:
            continue
        seen.add(key)

        normalized.append(
            {
                "type": str(item.get("type") or "address"),
                "title": title,
                "street_address": street,
                "city": city or "Fort Worth",
                "state": state or "TX",
                "zip": zipcode,
                "county": county,
                "property_reach_id": _first(item, "propertyId", "property_id"),
                "business_id": _first(item, "businessId", "business_id"),
            }
        )

    return normalized
