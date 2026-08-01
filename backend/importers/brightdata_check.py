"""Bright Data MCP cross-check — validates property values against real
Zillow/Realtor data via the Bright Data MCP (Web Unlocker).

Free tier: ~5,000 credits/month. 1 credit = 1 API call (search).
Each property check = 1 Google search → returns Zestimate + Cotality.

Auth: MCP initialize → session id header → tools/call.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger("brightdata")

MCP_URL = "https://mcp.brightdata.com/mcp"
API_TOKEN = os.environ.get("BRIGHTDATA_TOKEN", "5809d6b4-75ec-44a2-83b2-dc98972e4727")
GROUPS = "advanced_scraping"

HDRS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
    "MCP-Protocol-Version": "2025-06-18",
}


class BrightDataMCP:
    """Minimal MCP client for Bright Data (Streamable HTTP)."""

    def __init__(self, token: str = API_TOKEN):
        self.url = f"{MCP_URL}?token={token}&groups={GROUPS}"
        self.session_id: Optional[str] = None
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *args):
        await self.close()

    async def connect(self) -> None:
        self._client = httpx.AsyncClient(timeout=90)
        r = await self._client.post(self.url, headers=HDRS, json={
            "jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": "2025-06-18", "capabilities": {},
                "clientInfo": {"name": "investorflip", "version": "1.0"},
            }})
        self.session_id = r.headers.get("mcp-session-id")
        if not self.session_id:
            raise RuntimeError(f"No session id from MCP: {r.status_code}")
        # Send initialized notification
        h = dict(HDRS); h["Mcp-Session-Id"] = self.session_id
        await self._client.post(self.url, headers=h, json={
            "jsonrpc": "2.0", "method": "notifications/initialized"})

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def call_tool(self, name: str, arguments: dict) -> Any:
        if not self._client or not self.session_id:
            await self.connect()
        h = dict(HDRS); h["Mcp-Session-Id"] = self.session_id
        r = await self._client.post(self.url, headers=h, json={
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": name, "arguments": arguments}})
        r.raise_for_status()
        # Parse SSE: extract data: lines
        data_lines = []
        for line in r.text.splitlines():
            if line.startswith("data: "):
                data_lines.append(line[6:])
        if not data_lines:
            return None
        payload = json.loads(data_lines[-1])
        result = payload.get("result", {})
        content = result.get("content", [])
        texts = [c.get("text", "") for c in content if c.get("type") == "text"]
        return "\n".join(texts)

    async def search(self, query: str, engine: str = "google") -> List[Dict[str, Any]]:
        """Google search via Bright Data MCP → returns organic results."""
        text = await self.call_tool("search_engine", {
            "query": query, "engine": engine})
        if not text:
            return []
        # The text contains a JSON blob wrapped in security markers
        m = re.search(r"=====UNTRUSTED_.*?_BEGIN=====\n(\{.*?\})\n=====UNTRUSTED", text, re.DOTALL)
        raw = m.group(1) if m else text
        try:
            data = json.loads(raw)
        except Exception:
            # try to find first { ... } block
            m2 = re.search(r"\{.*\}", text, re.DOTALL)
            if not m2:
                return []
            try:
                data = json.loads(m2.group(0))
            except Exception:
                return []
        return data.get("organic", []) if isinstance(data, dict) else []


def _parse_zestimate(results: List[Dict[str, Any]]) -> Optional[int]:
    for r in results:
        snippet = r.get("description") or r.get("snippet") or ""
        m = re.search(r"\$([\d,]+)\s*Zestimate", snippet, re.IGNORECASE)
        if m:
            return int(m.group(1).replace(",", ""))
    return None


def _parse_cotality(results: List[Dict[str, Any]]) -> Optional[int]:
    for r in results:
        snippet = r.get("description") or r.get("snippet") or ""
        m = re.search(r"Cotality[^\d]*\$([\d,]+)", snippet, re.IGNORECASE)
        if m:
            return int(m.group(1).replace(",", ""))
    return None


async def cross_check_property(property_data: Dict[str, Any]) -> Dict[str, Any]:
    """Cross-check one property → Zestimate + Cotality + zillow url."""
    address = property_data.get("situs_address") or property_data.get("address") or ""
    city = property_data.get("city") or "Fort Worth"
    state = property_data.get("state") or "TX"
    zip_code = (property_data.get("zip") or "")[:5]

    street = address.split(",")[0].strip()
    query = f"{street} {city} {state} {zip_code} zestimate zillow"

    async with BrightDataMCP() as mcp:
        results = await mcp.search(query)

    zestimate = _parse_zestimate(results)
    cotality = _parse_cotality(results)
    zillow_url = next(
        (r.get("link") for r in results if "zillow.com" in (r.get("link") or "")), None
    )

    return {
        "zestimate": zestimate,
        "cotality": cotality,
        "zillow_url": zillow_url,
        "status": "ok" if (zestimate or cotality) else "not_found",
    }


async def cross_check_batch(properties: List[Dict[str, Any]],
                            concurrency: int = 3) -> List[Dict[str, Any]]:
    """Cross-check a batch of properties (1 credit each)."""
    sem = asyncio.Semaphore(concurrency)

    async def one(p):
        async with sem:
            try:
                result = await cross_check_property(p)
                result["address"] = p.get("situs_address")
                result["property_id"] = p.get("id")
                return result
            except Exception as e:
                return {"address": p.get("situs_address"), "property_id": p.get("id"),
                        "status": "error", "error": str(e)}

    return await asyncio.gather(*[one(p) for p in properties])


if __name__ == "__main__":
    import sys
    sys.path.insert(0, ".")

    async def main():
        sample = {
            "situs_address": "3308 Woodlark Dr",
            "city": "Fort Worth",
            "state": "TX",
            "zip": "76123",
        }
        result = await cross_check_property(sample)
        print(json.dumps(result, indent=2))

    asyncio.run(main())
