"""Bright Data Zillow Scraper API client.

Bright Data performs collection; InvestorFlip keeps only the stable fields it
needs for matching, display, and analysis.  Large response branches such as
nearby homes, full photo galleries, and page internals are deliberately not
stored in PostgreSQL.
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any, Dict, List, Optional

import httpx


API_ROOT = "https://api.brightdata.com/datasets/v3"
ZILLOW_LISTINGS_DATASET = "gd_lfqkr8wm13ixtbd8f5"
TERMINAL_FAILURES = {"failed", "canceled"}


def brightdata_token() -> str:
    return (
        os.environ.get("BRIGHT_DATA_API_TOKEN", "").strip()
        or os.environ.get("BRIGHT_DATA_TOKEN", "").strip()
        or os.environ.get("BRIGHTDATA_TOKEN", "").strip()
    )


def _latest_tax_paid(history: Any) -> Optional[float]:
    if not isinstance(history, list):
        return None
    rows = sorted(
        (row for row in history if isinstance(row, dict)),
        key=lambda row: int(row.get("time") or 0),
        reverse=True,
    )
    for row in rows:
        value = row.get("taxPaid")
        if value not in (None, ""):
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
    return None


def _photo_urls(values: Any, limit: int = 5) -> List[str]:
    urls: List[str] = []
    if not isinstance(values, list):
        return urls
    for value in values:
        if isinstance(value, str):
            candidates = [value]
        elif isinstance(value, dict):
            mixed = value.get("mixedSources") if isinstance(value.get("mixedSources"), dict) else {}
            jpeg = mixed.get("jpeg") if isinstance(mixed.get("jpeg"), list) else []
            candidates = [
                item.get("url") for item in jpeg if isinstance(item, dict)
            ] or [value.get("url")]
        else:
            candidates = []
        for url in reversed(candidates):
            if isinstance(url, str) and url.strip() and url not in urls:
                urls.append(url.strip().replace("http://", "https://", 1))
                break
        if len(urls) >= limit:
            break
    return urls


def compact_zillow_record(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Return a bounded Zillow payload compatible with listing normalization."""
    address = raw.get("address") if isinstance(raw.get("address"), dict) else {}
    tax_history = raw.get("taxHistory") if isinstance(raw.get("taxHistory"), list) else []
    price_history = raw.get("priceHistory") if isinstance(raw.get("priceHistory"), list) else []
    attribution = raw.get("attributionInfo") if isinstance(raw.get("attributionInfo"), dict) else {}
    listed_by = raw.get("listing_provided_by") if isinstance(raw.get("listing_provided_by"), dict) else {}
    photos = _photo_urls(raw.get("photos"), limit=5)
    detail_url = raw.get("url") or raw.get("hdpUrl")

    return {
        "zpid": raw.get("zpid"),
        "address": {
            "streetAddress": address.get("streetAddress") or raw.get("streetAddress"),
            "city": address.get("city") or raw.get("city"),
            "state": address.get("state") or raw.get("state"),
            "zipcode": address.get("zipcode") or raw.get("zipcode"),
        },
        "homeStatus": raw.get("homeStatus"),
        "homeType": raw.get("homeType"),
        "price": raw.get("price"),
        "bedrooms": raw.get("bedrooms"),
        "bathrooms": raw.get("bathrooms"),
        "livingArea": raw.get("livingArea") or raw.get("livingAreaValue"),
        "yearBuilt": raw.get("yearBuilt"),
        "lotSize": raw.get("lotSize") or raw.get("lotAreaValue"),
        "zestimate": raw.get("zestimate"),
        "rent_zestimate": raw.get("rentZestimate"),
        "taxAssessedValue": raw.get("taxAssessedValue"),
        "tax_assessed_year": raw.get("taxAssessedYear"),
        "annualTaxAmount": _latest_tax_paid(tax_history),
        "parcel_id": raw.get("parcelId"),
        "latitude": raw.get("latitude"),
        "longitude": raw.get("longitude"),
        "description": raw.get("description"),
        "photos": photos,
        "image_url": photos[0] if photos else None,
        "url": detail_url,
        "hdpUrl": raw.get("hdpUrl"),
        "dateSoldString": raw.get("dateSoldString"),
        "dateSold": raw.get("dateSold"),
        "price_history": price_history[:25],
        "tax_history": tax_history[:25],
        "mls_id": raw.get("mls_id") or attribution.get("mlsId"),
        "listing_agent_name": listed_by.get("name") or attribution.get("agentName"),
        "broker_name": raw.get("brokerageName") or listed_by.get("company") or attribution.get("brokerName"),
        "is_foreclosure": bool((raw.get("listing_sub_type") or {}).get("is_foreclosure"))
        if isinstance(raw.get("listing_sub_type"), dict) else False,
        "is_bank_owned": bool((raw.get("listing_sub_type") or {}).get("is_bankOwned"))
        if isinstance(raw.get("listing_sub_type"), dict) else False,
        "is_fsbo": bool((raw.get("listing_sub_type") or {}).get("is_FSBO"))
        if isinstance(raw.get("listing_sub_type"), dict) else False,
        "brightdata_dataset_id": ZILLOW_LISTINGS_DATASET,
        "brightdata_collected_at": raw.get("timestamp"),
    }


async def fetch_fort_worth_zillow_listings(
    limit: int = 50,
    *,
    client: Optional[httpx.AsyncClient] = None,
    poll_interval: float = 10.0,
    timeout_seconds: float = 600.0,
) -> List[Dict[str, Any]]:
    """Discover current Fort Worth houses and return compact provider rows."""
    token = brightdata_token()
    if not token:
        raise RuntimeError("Bright Data API token is not configured")
    own_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=60.0)
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    try:
        response = await client.post(
            f"{API_ROOT}/trigger",
            params={
                "dataset_id": ZILLOW_LISTINGS_DATASET,
                "notify": "false",
                "include_errors": "true",
                "type": "discover_new",
                "discover_by": "input_filters",
            },
            headers=headers,
            json={
                "input": [{
                    "location": "Fort Worth, TX",
                    "listingCategory": "House for sale",
                    "HomeType": "Houses",
                    "days_on_zillow": "",
                    "exact_address": False,
                }],
                "limit_per_input": max(1, min(int(limit), 500)),
            },
        )
        response.raise_for_status()
        snapshot_id = str(response.json().get("snapshot_id") or "").strip()
        if not snapshot_id:
            raise RuntimeError("Bright Data did not return a snapshot_id")

        started = time.monotonic()
        while True:
            progress_response = await client.get(
                f"{API_ROOT}/progress/{snapshot_id}", headers=headers,
            )
            progress_response.raise_for_status()
            progress = progress_response.json()
            status = str(progress.get("status") or "").lower()
            if status == "ready":
                break
            if status in TERMINAL_FAILURES:
                raise RuntimeError(
                    f"Bright Data snapshot {status}: "
                    f"{progress.get('error_message') or progress.get('error') or 'no details'}"
                )
            if time.monotonic() - started >= timeout_seconds:
                raise TimeoutError(
                    f"Bright Data snapshot {snapshot_id} was not ready after {timeout_seconds:g}s"
                )
            await asyncio.sleep(max(0.0, poll_interval))

        result_response = await client.get(
            f"{API_ROOT}/snapshot/{snapshot_id}",
            params={"format": "json"},
            headers=headers,
        )
        result_response.raise_for_status()
        payload = result_response.json()
        rows = payload.get("data") if isinstance(payload, dict) else payload
        if not isinstance(rows, list):
            raise RuntimeError("Bright Data snapshot returned an unexpected result shape")
        return [compact_zillow_record(row) for row in rows if isinstance(row, dict)]
    finally:
        if own_client:
            await client.aclose()
