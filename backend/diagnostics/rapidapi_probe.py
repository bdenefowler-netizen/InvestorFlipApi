"""Safe RapidAPI listing diagnostic for InvestorFlip.

Run from the backend directory with:
    python -m diagnostics.rapidapi_probe

The probe never prints RAPIDAPI_KEY. It reports HTTP status, top-level response
shape, raw listing candidates, and residential candidates for each configured
listing endpoint.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Dict, List

import httpx


RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY", "").strip()
HOST_REALTIME = "real-time-real-estate-data.p.rapidapi.com"
HOST_LISTINGS = "us-real-estate-listings.p.rapidapi.com"

ATTEMPTS: List[Dict[str, Any]] = [
    {
        "host": HOST_REALTIME,
        "path": "/search",
        "params": {
            "location": "Fort Worth, TX",
            "status_type": "ForSale",
            "home_type": "Houses",
            "sort": "Newest",
            "limit": 10,
        },
    },
    {
        "host": HOST_REALTIME,
        "path": "/search-by-location",
        "params": {
            "location": "Fort Worth, TX",
            "status_type": "ForSale",
            "home_type": "Houses",
            "sort": "Newest",
            "limit": 10,
        },
    },
    {
        "host": HOST_REALTIME,
        "path": "/propertyExtendedSearch",
        "params": {
            "location": "Fort Worth, TX",
            "status_type": "ForSale",
            "home_type": "Houses",
            "sort": "Newest",
            "limit": 10,
        },
    },
    {
        "host": HOST_REALTIME,
        "path": "/properties/list",
        "params": {
            "location": "Fort Worth, TX",
            "status_type": "ForSale",
            "home_type": "Houses",
            "limit": 10,
        },
    },
    {
        "host": HOST_LISTINGS,
        "path": "/for-sale",
        "params": {
            "location": "Fort Worth, TX",
            "property_type": "single_family",
            "limit": 10,
        },
    },
]


def _shape(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return {
            "type": "object",
            "keys": list(value.keys())[:30],
        }
    if isinstance(value, list):
        first = value[0] if value else None
        return {
            "type": "array",
            "length": len(value),
            "first_item_type": type(first).__name__ if first is not None else None,
            "first_item_keys": list(first.keys())[:30] if isinstance(first, dict) else [],
        }
    return {"type": type(value).__name__}


def _listing_candidates(value: Any) -> List[Dict[str, Any]]:
    found: List[Dict[str, Any]] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            keys = {str(key).lower() for key in node}
            has_id = bool(keys & {"zpid", "property_id", "listing_id", "id"})
            has_price = bool(keys & {"price", "listprice", "list_price"})
            has_address = bool(
                keys & {"address", "streetaddress", "street_address", "location"}
            )
            if has_id and has_price and has_address:
                found.append(node)
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(value)
    return found


def _property_type(item: Dict[str, Any]) -> str:
    return str(
        item.get("homeType")
        or item.get("home_type")
        or item.get("propertyType")
        or item.get("property_type")
        or item.get("propertySubType")
        or item.get("type")
        or ""
    ).lower()


def _is_residential_candidate(item: Dict[str, Any]) -> bool:
    property_type = _property_type(item)
    blocked = (
        "commercial",
        "retail",
        "office",
        "warehouse",
        "industrial",
        "land",
        "lot",
        "condo",
        "townhome",
        "townhouse",
        "duplex",
        "triplex",
        "fourplex",
        "apartment",
        "mobile",
        "manufactured",
    )
    allowed = (
        "single family",
        "single-family",
        "singlefamily",
        "house",
        "detached",
        "multi family",
        "multi-family",
        "multifamily",
    )
    return bool(property_type) and not any(x in property_type for x in blocked) and any(
        x in property_type for x in allowed
    )


async def probe() -> Dict[str, Any]:
    if not RAPIDAPI_KEY:
        return {
            "ok": False,
            "rapidapi_configured": False,
            "message": "RAPIDAPI_KEY is missing.",
            "attempts": [],
        }

    results: List[Dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=30.0) as client:
        for attempt in ATTEMPTS:
            url = f"https://{attempt['host']}{attempt['path']}"
            record: Dict[str, Any] = {
                "host": attempt["host"],
                "path": attempt["path"],
                "params": attempt["params"],
            }
            try:
                response = await client.get(
                    url,
                    headers={
                        "x-rapidapi-key": RAPIDAPI_KEY,
                        "x-rapidapi-host": attempt["host"],
                        "Accept": "application/json",
                    },
                    params=attempt["params"],
                )
                record["http_status"] = response.status_code
                record["content_type"] = response.headers.get("content-type")

                try:
                    payload = response.json()
                    candidates = _listing_candidates(payload)
                    residential = [item for item in candidates if _is_residential_candidate(item)]
                    record["response_shape"] = _shape(payload)
                    record["raw_candidate_count"] = len(candidates)
                    record["residential_candidate_count"] = len(residential)
                    record["sample_property_types"] = sorted(
                        {_property_type(item) for item in candidates if _property_type(item)}
                    )[:20]
                    if response.status_code >= 400:
                        record["error_preview"] = str(payload)[:500]
                except ValueError:
                    record["response_shape"] = {"type": "non-json"}
                    record["body_preview"] = response.text[:500]
            except Exception as exc:
                record["request_error"] = str(exc)[:500]

            results.append(record)

    return {
        "ok": True,
        "rapidapi_configured": True,
        "secret_exposed": False,
        "attempts": results,
    }


if __name__ == "__main__":
    print(json.dumps(asyncio.run(probe()), indent=2, default=str))
