"""OpenWeb Ninja enrichment provider for Serenity.

The provider is intentionally configuration-driven because OpenWeb Ninja plans
may use different base URLs, paths, and authentication headers.

Railway variables:
- OPENWEB_NINJA_API_KEY
- OPENWEB_NINJA_BASE_URL
- OPENWEB_NINJA_PROPERTY_PATH (optional, defaults to /property)
- OPENWEB_NINJA_AUTH_HEADER (optional, defaults to X-API-Key)
- OPENWEB_NINJA_AUTH_SCHEME (optional, e.g. Bearer)
"""

from __future__ import annotations

import os
from typing import Any, Dict

import httpx


OPENWEB_NINJA_API_KEY = (
    os.environ.get("OPENWEB_NINJA_ZILLOW_API_KEY", "").strip()
    or os.environ.get("OPENWEB_NINJA_API_KEY", "").strip()
)
OPENWEB_NINJA_BASE_URL = os.environ.get(
    "OPENWEB_NINJA_BASE_URL",
    "https://api.openwebninja.com/realtime-zillow-data",
).strip().rstrip("/")
OPENWEB_NINJA_PROPERTY_PATH = os.environ.get(
    "OPENWEB_NINJA_PROPERTY_PATH", "/property-details-address"
).strip()
OPENWEB_NINJA_AUTH_HEADER = os.environ.get(
    "OPENWEB_NINJA_AUTH_HEADER", "X-API-Key"
).strip()
OPENWEB_NINJA_AUTH_SCHEME = os.environ.get(
    "OPENWEB_NINJA_AUTH_SCHEME", ""
).strip()


def openweb_ninja_status() -> Dict[str, Any]:
    """Return configuration status without exposing secrets."""
    return {
        "provider": "OpenWeb Ninja",
        "api_key_configured": bool(OPENWEB_NINJA_API_KEY),
        "base_url_configured": bool(OPENWEB_NINJA_BASE_URL),
        "property_path": OPENWEB_NINJA_PROPERTY_PATH,
        "ready": bool(OPENWEB_NINJA_API_KEY and OPENWEB_NINJA_BASE_URL),
    }


def _auth_headers() -> Dict[str, str]:
    value = OPENWEB_NINJA_API_KEY
    if OPENWEB_NINJA_AUTH_SCHEME:
        value = f"{OPENWEB_NINJA_AUTH_SCHEME} {value}"
    return {
        OPENWEB_NINJA_AUTH_HEADER: value,
        "Accept": "application/json",
    }


def _first_dict(payload: Any) -> Dict[str, Any]:
    """Find the most likely property dictionary in common API response shapes."""
    if not isinstance(payload, dict):
        return {}

    for key in ("property", "result", "data", "details"):
        value = payload.get(key)
        if isinstance(value, dict):
            return value

    return payload


def enrich_property(property_record: Dict[str, Any]) -> Dict[str, Any]:
    """Enrich a property while preserving trusted values already collected.

    The provider never replaces an existing non-empty field. If the provider is
    not fully configured or the request fails, Serenity keeps the original
    property and records the provider status instead of breaking analysis.
    """
    enriched = dict(property_record)
    sources = list(enriched.get("serenity_sources") or [])
    status = openweb_ninja_status()

    if not status["ready"]:
        sources.append({"provider": "OpenWeb Ninja", "status": "not_configured"})
        enriched["serenity_sources"] = sources
        return enriched

    address = (
        enriched.get("situs_address")
        or enriched.get("address")
        or enriched.get("full_address")
    )
    if not address:
        sources.append({"provider": "OpenWeb Ninja", "status": "missing_address"})
        enriched["serenity_sources"] = sources
        return enriched

    path = OPENWEB_NINJA_PROPERTY_PATH
    if not path.startswith("/"):
        path = f"/{path}"
    url = f"{OPENWEB_NINJA_BASE_URL}{path}"

    try:
        response = httpx.get(
            url,
            headers=_auth_headers(),
            params={"address": address},
            timeout=20.0,
        )
        response.raise_for_status()
        payload = response.json()
        data = _first_dict(payload)

        field_map = {
            "market_value": ("market_value", "estimated_value", "estimate", "zestimate"),
            "beds": ("beds", "bedrooms"),
            "baths": ("baths", "bathrooms"),
            "sqft": ("sqft", "living_area", "livingArea"),
            "year_built": ("year_built", "yearBuilt"),
            "property_type": ("property_type", "propertyType", "home_type", "homeType"),
            "annual_taxes": ("annual_taxes", "annualTaxAmount", "taxAnnualAmount"),
        }

        for target, candidates in field_map.items():
            if enriched.get(target) not in (None, "", 0):
                continue
            for candidate in candidates:
                value = data.get(candidate)
                if value not in (None, "", 0):
                    enriched[target] = value
                    break

        enriched["openweb_ninja_data"] = data
        sources.append({"provider": "OpenWeb Ninja", "status": "enriched"})
    except Exception as exc:  # Provider failure must never block Quill.
        sources.append(
            {
                "provider": "OpenWeb Ninja",
                "status": "error",
                "message": str(exc)[:200],
            }
        )

    enriched["serenity_sources"] = sources
    return enriched
