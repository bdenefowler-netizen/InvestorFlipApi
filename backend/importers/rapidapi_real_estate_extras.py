"""Optional RapidAPI real-estate provider clients for InvestorFlip.

These integrations are OFF by default. Railway owns the shared RAPIDAPI_KEY;
provider-specific *_ENABLED flags decide which endpoints may consume credits.
No RapidAPI secret is stored in GitHub.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

import httpx

PROVIDERS: Dict[str, Dict[str, str]] = {
    "fsbo_owner_finder": {
        "host": "fsbo-owner-finder.p.rapidapi.com",
        "base": "https://fsbo-owner-finder.p.rapidapi.com",
        "flag": "RAPIDAPI_FSBO_OWNER_FINDER_ENABLED",
    },
    "fsbo_api": {
        "host": "fsbo-api.p.rapidapi.com",
        "base": "https://fsbo-api.p.rapidapi.com",
        "flag": "RAPIDAPI_FSBO_API_ENABLED",
    },
    "realtor_api_data": {
        "host": "realtor-api-data.p.rapidapi.com",
        "base": "https://realtor-api-data.p.rapidapi.com",
        "flag": "RAPIDAPI_REALTOR_DATA_ENABLED",
    },
    "foreclosed_properties": {
        "host": "foreclosed-properties-list.p.rapidapi.com",
        "base": "https://foreclosed-properties-list.p.rapidapi.com",
        "flag": "RAPIDAPI_FORECLOSED_PROPERTIES_ENABLED",
    },
    "ai_property_valuation": {
        "host": "real-estate-data-api-ai-property-valuation-market-data.p.rapidapi.com",
        "base": "https://real-estate-data-api-ai-property-valuation-market-data.p.rapidapi.com",
        "flag": "RAPIDAPI_AI_VALUATION_ENABLED",
    },
}


def _enabled(name: str) -> bool:
    return os.environ.get(PROVIDERS[name]["flag"], "false").strip().lower() == "true"


def provider_status() -> Dict[str, Any]:
    return {
        "rapidapi_key_set": bool(os.environ.get("RAPIDAPI_KEY", "").strip()),
        "providers": {
            name: {
                "host": config["host"],
                "enabled": _enabled(name),
                "enable_variable": config["flag"],
            }
            for name, config in PROVIDERS.items()
        },
    }


def _headers(name: str) -> Dict[str, str]:
    key = os.environ.get("RAPIDAPI_KEY", "").strip()
    if not key:
        raise RuntimeError("RAPIDAPI_KEY is not configured")
    if not _enabled(name):
        raise RuntimeError(
            f"{name} is disabled; set {PROVIDERS[name]['flag']}=true in Railway to enable it"
        )
    return {
        "x-rapidapi-key": key,
        "x-rapidapi-host": PROVIDERS[name]["host"],
    }


async def _request(
    name: str,
    method: str,
    path: str = "",
    *,
    params: Optional[Dict[str, Any]] = None,
    payload: Optional[Dict[str, Any]] = None,
    timeout: float = 45.0,
) -> Any:
    base = PROVIDERS[name]["base"].rstrip("/")
    url = f"{base}/{path.lstrip('/')}" if path else f"{base}/"
    headers = _headers(name)
    if payload is not None:
        headers["Content-Type"] = "application/json"

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.request(
            method.upper(), url, headers=headers, params=params, json=payload
        )
    response.raise_for_status()
    try:
        return response.json()
    except ValueError:
        return {"text": response.text, "status_code": response.status_code}


async def fsbo_owner_lookup(address: str, request_timeout_secs: int = 30) -> Any:
    return await _request(
        "fsbo_owner_finder",
        "GET",
        params={
            "requestTimeoutSecs": str(max(1, min(int(request_timeout_secs), 120))),
            "address": address,
        },
        timeout=max(35.0, float(request_timeout_secs) + 5.0),
    )


async def fsbo_property_details(property_id: str) -> Any:
    return await _request(
        "fsbo_api",
        "GET",
        "/v4/get_property_details",
        params={"property_id": property_id},
    )


async def realtor_autocomplete(query: str) -> Any:
    return await _request(
        "realtor_api_data",
        "GET",
        "/search/autocomplete",
        params={"query": query},
    )


async def foreclosed_properties(
    *,
    page: int = 1,
    city: Optional[str] = None,
    no_hoa_fee: Optional[bool] = None,
    extra_params: Optional[Dict[str, Any]] = None,
) -> Any:
    params: Dict[str, Any] = {"page": str(max(1, int(page)))}
    if city:
        params["city"] = city
    if no_hoa_fee is not None:
        params["no_hoa_fee"] = "true" if no_hoa_fee else "false"
    if extra_params:
        params.update({k: v for k, v in extra_params.items() if v is not None})
    return await _request("foreclosed_properties", "GET", params=params)


async def ai_property_market_search(payload: Dict[str, Any], noqueue: bool = True) -> Any:
    return await _request(
        "ai_property_valuation",
        "POST",
        "/search",
        params={"noqueue": "1" if noqueue else "0"},
        payload=payload,
        timeout=60.0,
    )
