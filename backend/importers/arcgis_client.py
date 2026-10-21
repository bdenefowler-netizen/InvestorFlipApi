"""Small, defensive client for public ArcGIS REST query layers.

ArcGIS services commonly return HTTP 200 even when the JSON body contains an
error.  This client handles that behavior, applies bounded pagination, and
keeps SQL-like user input out of query expressions.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional

import httpx


class ArcGISQueryError(RuntimeError):
    """Raised when ArcGIS returns an error payload or an invalid response."""


def clean_search_text(value: Any, *, max_length: int = 120) -> str:
    """Return a conservative value suitable for an ArcGIS WHERE literal."""
    text = re.sub(r"[^A-Za-z0-9 #&'./-]+", " ", str(value or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_length]


def sql_literal(value: Any) -> str:
    """Quote a cleaned string for a standardized ArcGIS SQL expression."""
    return "'" + clean_search_text(value).replace("'", "''") + "'"


def sql_like_contains(value: Any) -> str:
    """Return an escaped literal for a standardized SQL contains query."""
    cleaned = clean_search_text(value).replace("'", "''")
    return f"'%{cleaned}%'"


def attributes(features: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [dict(feature.get("attributes") or feature) for feature in features]


class ArcGISClient:
    def __init__(
        self,
        layer_url: str,
        *,
        timeout_seconds: float = 45.0,
        page_size: int = 1000,
        client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self.layer_url = layer_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.page_size = max(1, min(int(page_size), 10000))
        self._client = client

    async def _get(self, url: str, params: Dict[str, Any]) -> Dict[str, Any]:
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(
            follow_redirects=True,
            timeout=httpx.Timeout(self.timeout_seconds),
            headers={"User-Agent": "InvestorFlip/1.0 public-records-client"},
        )
        try:
            response = await client.get(url, params=params)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ArcGISQueryError(f"ArcGIS request failed: {exc}") from exc
        finally:
            if owns_client:
                await client.aclose()

        if not isinstance(payload, dict):
            raise ArcGISQueryError("ArcGIS returned a non-object response")
        if payload.get("error"):
            error = payload["error"]
            message = error.get("message") if isinstance(error, dict) else str(error)
            details = error.get("details") if isinstance(error, dict) else None
            detail_text = (
                f" ({'; '.join(details)})"
                if isinstance(details, list) and details
                else ""
            )
            raise ArcGISQueryError(f"ArcGIS error: {message or 'unknown'}{detail_text}")
        return payload

    async def metadata(self) -> Dict[str, Any]:
        return await self._get(self.layer_url, {"f": "json"})

    async def query(
        self,
        *,
        where: str = "1=1",
        out_fields: str = "*",
        offset: int = 0,
        count: Optional[int] = None,
        order_by: Optional[str] = None,
        return_geometry: bool = False,
        extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        record_count = (
            self.page_size if count is None else max(1, min(int(count), self.page_size))
        )
        params: Dict[str, Any] = {
            "where": where,
            "outFields": out_fields,
            "returnGeometry": "true" if return_geometry else "false",
            "resultOffset": max(0, int(offset)),
            "resultRecordCount": record_count,
            "f": "json",
        }
        if order_by:
            params["orderByFields"] = order_by
        if extra:
            params.update(extra)
        return await self._get(f"{self.layer_url}/query", params)

    async def fetch_all(
        self,
        *,
        where: str = "1=1",
        out_fields: str = "*",
        limit: Optional[int] = None,
        order_by: Optional[str] = None,
        return_geometry: bool = False,
    ) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        offset = 0
        while limit is None or len(rows) < limit:
            requested = (
                self.page_size
                if limit is None
                else min(self.page_size, limit - len(rows))
            )
            if requested <= 0:
                break
            payload = await self.query(
                where=where,
                out_fields=out_fields,
                offset=offset,
                count=requested,
                order_by=order_by,
                return_geometry=return_geometry,
            )
            page = payload.get("features") or []
            if not isinstance(page, list):
                raise ArcGISQueryError("ArcGIS response has no feature list")
            rows.extend(page)
            offset += len(page)
            if len(page) < requested:
                break
        return rows
