"""Opt-in, single-property provider lookups; no database writes or bulk jobs.

No calls occur at import time. API credentials are read only on invocation.
Do not log response bodies or keys; results must be matched to TAD before merging.
"""
from __future__ import annotations
import os
import re
from typing import Any
import httpx

ATTOM = "https://api.gateway.attomdata.com/propertyapi/v1.0.0"
REPLIERS = "https://api.repliers.io"
TARRANT = "https://mapit.tarrantcounty.com/arcgis/rest/services/Dynamic/TADParcels/FeatureServer/0/query"
TARRANT_FIELDS = "ACCOUNT,TAXPIN,OWNER_NAME,SubdivisionName"

class ProviderError(RuntimeError):
    pass

def _secret(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ProviderError(f"{name} is not configured")
    return value

def _request(url: str, *, headers: dict, params: dict) -> dict:
    try:
        with httpx.Client(timeout=12, follow_redirects=False) as client:
            response = client.get(url, headers=headers, params=params)
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        # Avoid echoing httpx exceptions, which can contain URLs or sensitive query data.
        raise ProviderError("Provider request failed; inspect provider status privately") from None
    if not isinstance(payload, dict) or "error" in payload:
        raise ProviderError("Provider returned an invalid or error response")
    return payload

def attom_by_apn(apn: str, fips: str) -> dict:
    """One ATTOM basicprofile, keyed by APN and county FIPS. May incur costs."""
    if not apn or not re.fullmatch(r"\d{5}", fips):
        raise ValueError("APN and five-digit county FIPS required")
    return _request(
        f"{ATTOM}/property/basicprofile",
        headers={"apikey": _secret("ATTOM_API_KEY"), "Accept": "application/json"},
        params={"apn": apn, "fips": fips},
    )

def repliers_listing(mls_number: str) -> dict:
    """One licensed MLS listing; provider subscription and MLS access required."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,48}", mls_number or ""):
        raise ValueError("Valid MLS listing number required")
    return _request(
        f"{REPLIERS}/listings/{mls_number}",
        headers={"REPLIERS-API-KEY": _secret("REPLIERS_API_KEY"), "Accept": "application/json"},
        params={},
    )

def tarrant_by_account(account: str) -> list[dict[str, Any]]:
    """Exact account lookup; query is deliberately limited to five candidate parcels."""
    if not re.fullmatch(r"[A-Za-z0-9]{1,30}", account or ""):
        raise ValueErrror("Alphanumeric Tarrant account required")
    # ArcGIS SQL literal is restricted to alphanumeric input above.
    payload = _request(
        TARRANT, headers={"Accept": "application/json"},
        params={"where": f"ACCOUNT='{account}'", "outFields": TARRANT_FIELDS,
                "returnGeometry": "false", "resultRecordCount": "5", "f": "json"},
     )
    features = payload.get("features", [])
    if not isinstance(features, list):
        raise ProviderError("Invalid ArcGIS features")
    return [f["attributes"] for f in features if isinstance(f, dict) and isinstance(f.get("attributes"), dict)]
