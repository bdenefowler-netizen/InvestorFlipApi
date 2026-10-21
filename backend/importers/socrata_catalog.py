"""Search the public Socrata Open Data catalog without an API key."""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import httpx

CATALOG_URL = "https://api.us.socrata.com/api/catalog/v1"


def request_headers() -> Dict[str, str]:
    headers = {"User-Agent": "InvestorFlip/1.0 public-records-client"}
    token = os.environ.get("SOCRATA_APP_TOKEN", "").strip()
    if token:
        headers["X-App-Token"] = token
    return headers


async def search_catalog(
    query: str,
    *,
    domain: Optional[str] = None,
    limit: int = 10,
) -> List[Dict[str, Any]]:
    text = " ".join(str(query or "").split()).strip()[:160]
    if not text:
        return []
    params: Dict[str, Any] = {
        "q": text,
        "only": "datasets",
        "limit": min(max(int(limit), 1), 50),
    }
    if domain:
        params["domains"] = str(domain).strip().lower()[:120]
    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=httpx.Timeout(30.0),
        headers=request_headers(),
    ) as client:
        response = await client.get(CATALOG_URL, params=params)
        response.raise_for_status()
        payload = response.json()

    results: List[Dict[str, Any]] = []
    for item in payload.get("results") or []:
        resource = item.get("resource") or {}
        metadata = item.get("metadata") or {}
        classification = item.get("classification") or {}
        results.append(
            {
                "name": resource.get("name"),
                "dataset_id": resource.get("id"),
                "description": resource.get("description"),
                "type": resource.get("type"),
                "updated_at": resource.get("updatedAt"),
                "data_updated_at": resource.get("data_updated_at"),
                "domain": metadata.get("domain"),
                "license": metadata.get("license"),
                "category": classification.get("domain_category"),
                "tags": classification.get("domain_tags")
                or classification.get("tags")
                or [],
                "permalink": item.get("permalink"),
                "link": item.get("link"),
            }
        )
    return results
