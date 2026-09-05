"""
Bright Data MCP Scraper — Unified scraper using the Bright Data MCP tools.

Tools available via MCP (no zone/zone_password needed):
  - search_engine         → Google/Bing/Yandex search (1 credit/search)
  - scrape_as_html        → Scrape any URL as HTML (bypasses anti-bot)
  - scrape_as_markdown    → Scrape any URL as Markdown
  - scrape_batch          → Scrape up to 10 URLs at once
  - search_engine_batch   → Run multiple searches at once

Sites tested working:
  - offmarketdeck.com    ✅ (202KB returned, real listings)
  - fsbo.com            ✅ (24KB returned)
  - hubzu.com           ✅ (10KB returned, JS-rendered but HTML has data)
  - zillow.com          ✅ (633KB returned)
  - tarrant.tx.publicsearch.us ❌ (blocked by robots.txt — covered by free scrapers)

Free tier: ~5,000 credits/month. Costs:
  - search_engine: 1 credit/search
  - scrape_as_html: ~1-5 credits/page
  - scrape_batch: ~1-5 credits/URL

No zone_password required — uses same BRIGHTDATA_TOKEN as MCP.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger("brightdata_mcp")

MCP_URL = "https://mcp.brightdata.com/mcp"
API_TOKEN = os.environ.get("BRIGHTDATA_TOKEN", "").strip() or \
            os.environ.get("BRIGHTDATA_TOKEN", "").strip()
GROUPS = "advanced_scraping"

HDRS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
    "MCP-Protocol-Version": "2025-06-18",
}


class BrightDataMCP:
    """Minimal MCP client for Bright Data (Streamable HTTP)."""

    def __init__(self, token: str = API_TOKEN):
        self.token = token.strip()
        self.url = f"{MCP_URL}?token={self.token}&groups={GROUPS}"
        self.session_id: Optional[str] = None
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *args):
        await self.close()

    async def connect(self) -> None:
        if not self.token:
            raise RuntimeError("BRIGHTDATA_TOKEN is not configured")
        self._client = httpx.AsyncClient(timeout=120)
        r = await self._client.post(self.url, headers=HDRS, json={
            "jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "investorflip", "version": "1.0"},
            }})
        self.session_id = r.headers.get("mcp-session-id")
        if not self.session_id:
            raise RuntimeError(f"No session id from MCP: {r.status_code} {r.text[:200]}")
        h = dict(HDRS); h["Mcp-Session-Id"] = self.session_id
        await self._client.post(self.url, headers=h, json={
            "jsonrpc": "2.0", "method": "notifications/initialized"})

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def call_tool(self, name: str, arguments: dict) -> str:
        """Call an MCP tool, return raw text response."""
        if not self._client or not self.session_id:
            await self.connect()
        h = dict(HDRS); h["Mcp-Session-Id"] = self.session_id
        r = await self._client.post(self.url, headers=h, json={
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": name, "arguments": arguments}})
        r.raise_for_status()
        # Parse SSE: extract data: lines
        for line in r.text.splitlines():
            if line.startswith("data: "):
                payload = json.loads(line[6:])
                for c in payload.get("result", {}).get("content", []):
                    if c.get("type") == "text":
                        return c.get("text", "")
        return ""

    # ─── High-level helpers ──────────────────────────────────────────────────

    async def search(self, query: str, engine: str = "google") -> List[Dict[str, Any]]:
        """Google search → returns organic results."""
        text = await self.call_tool("search_engine", {"query": query, "engine": engine})
        if not text:
            return []
        m = re.search(r"=====UNTRUSTED_[A-F0-9]+_BEGIN=====\n(\{.*?\})\n=====UNTRUSTED", text, re.DOTALL)
        raw = m.group(1) if m else text
        try:
            data = json.loads(raw)
        except Exception:
            m2 = re.search(r"\{.*\}", text, re.DOTALL)
            if not m2:
                return []
            try:
                data = json.loads(m2.group(0))
            except Exception:
                return []
        return data.get("organic", []) if isinstance(data, dict) else []

    async def scrape_html(self, url: str) -> str:
        """Scrape any URL as HTML, bypasses anti-bot."""
        text = await self.call_tool("scrape_as_html", {"url": url})
        return self._strip_untrusted(text)

    async def scrape_markdown(self, url: str) -> str:
        """Scrape any URL as Markdown."""
        text = await self.call_tool("scrape_as_markdown", {"url": url})
        return self._strip_untrusted(text)

    async def scrape_batch(self, urls: List[str]) -> List[str]:
        """Scrape up to 10 URLs at once as HTML."""
        if not urls:
            return []
        text = await self.call_tool("scrape_batch", {"urls": urls})
        # Parse each URL's result from the response
        results = []
        for line in text.split("\n"):
            if not line.strip():
                continue
            try:
                # Each line might be a JSON object
                data = json.loads(line)
                content = data.get("content", data.get("result", ""))
                if isinstance(content, str):
                    results.append(self._strip_untrusted(content))
                else:
                    results.append("")
            except Exception:
                results.append("")
        # If the response is one big block, split by URL marker
        if len(results) != len(urls):
            results = [self._strip_untrusted(text)] * len(urls)
        return results

    @staticmethod
    def _strip_untrusted(text: str) -> str:
        """Remove Bright Data security wrapper, return clean content."""
        m = re.search(
            r"=====[A-Z_]+_[A-F0-9]+_BEGIN=====\n(.*?)\n=====[A-Z_]+_[A-F0-9]+_END=====",
            text, re.DOTALL)
        return m.group(1).strip() if m else text.strip()


# ═══════════════════════════════════════════════════════════════════════════════
# TARRANT CITIES ALLOWLIST
# ═══════════════════════════════════════════════════════════════════════════════

TARRANT_CITIES = {
    "fort worth", "arlington", "north richland hills", "haltom city",
    "keller", "southlake", "colleyville", "grapevine", "bedford",
    "euless", "hurst", "benbrook", "white settlement", "saginaw",
    "watauga", "river oaks", "forest hill", "crowley", "burleson",
    "mansfield", "azle", "lake worth", "sansom park", "westworth village",
    "haslet", "eagle mountain", "blue mound", "pelican bay",
    "kennedale", "everman", "dalworthington gardens", "pantego",
}


def is_fort_worth_area(city: str) -> bool:
    c = (city or "").strip().lower()
    if not c:
        return False
    if c in TARRANT_CITIES:
        return True
    return "fort worth" in c or c.startswith("fort worth")


# ═══════════════════════════════════════════════════════════════════════════════
# OFFMARKETDECK SCRAPER
# ═══════════════════════════════════════════════════════════════════════════════

async def scrape_offmarketdeck(city: str = "fort-worth", max_pages: int = 3) -> List[Dict[str, Any]]:
    """
    Scrape OffMarketDeck for off-market / wholesale deals.
    
    Strategy mapping from OffMarketDeck:
      - Wholesale: "8599177e-faf4-4ca0-8955-cfb6efa3c5fa" (Fort Worth)
      - Fix & Flip: "..."
      - Buy & Hold: "..."
      - Multifamily: "..."
    """
    all_deals = []
    for page in range(1, max_pages + 1):
        url = f"https://offmarketdeck.com/texas/{city}"
        if page > 1:
            url = f"https://offmarketdeck.com/texas/{city}?page={page}"

        try:
            async with BrightDataMCP() as mcp:
                html = await mcp.scrape_html(url)

            if not html or len(html) < 1000:
                logger.warning(f"OffMarketDeck page {page}: empty or too short ({len(html)} chars)")
                continue

            deals = _parse_offmarketdeck_html(html)
            logger.info(f"OffMarketDeck page {page}: {len(deals)} deals")
            all_deals.extend(deals)

            if len(deals) == 0:
                break  # No more pages

            await asyncio.sleep(1.5)  # Rate limit

        except Exception as e:
            logger.error(f"OffMarketDeck page {page} failed: {e}")

    # Filter to Fort Worth area
    fw_deals = [d for d in all_deals if is_fort_worth_area(d.get("city_name", ""))]
    logger.info(f"OffMarketDeck: {len(all_deals)} total, {len(fw_deals)} in Fort Worth area")

    return fw_deals


def _parse_offmarketdeck_html(html: str) -> List[Dict[str, Any]]:
    """Extract deals from OffMarketDeck HTML (Next.js push payload format)."""
    deals = []

    # Find all self.__next_f.push([1,"...escaped JSON..."]) blocks
    # These contain the actual deal data
    push_blocks = re.findall(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)', html, re.DOTALL)

    for block in push_blocks:
        try:
            # Unescape once for JS string
            unescaped = block.encode().decode('unicode_escape', errors='ignore')
        except Exception:
            unescaped = block

        # Find all deal objects: "deal":{...all fields...}}
        # They span multiple lines so we need careful extraction
        # Pattern: capture everything between "deal":{ and the next }}
        for m in re.finditer(r'"deal":\{([^{}]*(?:\{[^{}]*\}[^{}]*)*)\}', unescaped):
            try:
                deal_str = '{"deal":{' + m.group(1) + '}}'
                deal = json.loads(deal_str)
                if 'deal' not in deal:
                    continue
                d = deal['deal']
                if not d.get('price_min'):
                    continue

                prop = _normalize_offmarketdeck_deal(d)
                if prop:
                    deals.append(prop)
            except json.JSONDecodeError:
                # Deal object spans across push blocks — try partial parse
                pass
            except Exception:
                pass

    # Deduplicate by ID
    seen = set()
    unique = []
    for d in deals:
        did = d.get('id') or d.get('slug')
        if did and did not in seen:
            seen.add(did)
            unique.append(d)

    return unique


def _normalize_offmarketdeck_deal(d: dict) -> Optional[Dict[str, Any]]:
    """Convert OffMarketDeck deal to InvestorFlip property format."""
    title = d.get('title', '') or ''
    if not title:
        return None

    # Extract address from title: "123 MAIN ST, FORT WORTH, TX 76108"
    address_match = re.match(r"^([\d\s]+[A-Za-z].*?),\s*([A-Za-z\s]+),\s*([A-Z]{2})\s*([\d-]+)?", title)

    price = d.get('price_min') or d.get('price_max') or 0
    if price and price > 5_000_000:
        price = 0  # Junk values from bad parsing

    prop = {
        "id": d.get('id') or d.get('slug', ''),
        "situs_address": title.split(',')[0].strip() if ',' in title else title,
        "city": d.get('city_name', ''),
        "state": d.get('state_code', 'TX'),
        "zip": d.get('zip', ''),
        "price": price,
        "beds": d.get('beds'),
        "baths": d.get('baths'),
        "sqft": d.get('sqft'),
        "lot_sqft": d.get('lot_sqft'),
        "year_built": d.get('year_built'),
        "property_type": d.get('property_type', 'house'),
        "listing_type": "Off-Market",
        "data_source": "OffMarketDeck",
        "source_platform": "OffMarketDeck",
        "is_live_listing": True,
        "description": d.get('description', ''),
        "latitude": d.get('lat'),
        "longitude": d.get('lng'),
        "status": d.get('status', 'active'),
        "slug": d.get('slug', ''),
        "distress_score": _calc_offmarket_distress_score(d),
        "opportunity_signals": _get_offmarket_signals(d),
        "updated_at": datetime.utcnow().isoformat(),
    }

    # Full address
    if address_match:
        prop["situs_address"] = address_match.group(1).strip()

    return prop


def _calc_offmarket_distress_score(d: dict) -> int:
    """Calculate distress score from OffMarketDeck signals."""
    score = 30  # base
    desc = (d.get('description') or '').lower()

    if any(w in desc for w in ['motivated', 'priced to sell', 'urgent', 'quick sale', 'must sell']):
        score += 20
    if any(w in desc for w in ['fix', 'rehab', 'repair', 'tlc', 'as-is']):
        score += 15
    if any(w in desc for w in ['tenant', 'renter', 'lease', 'occupied', 'tenant']):
        score += 10
    if d.get('seller_inquiry_only'):
        score += 5

    return min(95, score)


def _get_offmarket_signals(d: dict) -> List[str]:
    """Extract opportunity signals from OffMarketDeck deal."""
    signals = []
    desc = (d.get('description') or '').lower()

    if any(w in desc for w in ['fix', 'rehab', 'flip', 'renovate']):
        signals.append("Fix & Flip")
    if any(w in desc for w in ['rental', 'tenant', 'lease', 'cash flow']):
        signals.append("Rental Income")
    if d.get('seller_inquiry_only'):
        signals.append("Seller Inquiry Only")
    if d.get('priority'):
        signals.append("Priority Listing")
    if d.get('wholesaler'):
        signals.append("Wholesale")

    return signals


# ═══════════════════════════════════════════════════════════════════════════════
# FSBO SCRAPER
# ═══════════════════════════════════════════════════════════════════════════════

async def scrape_fsbo(city: str = "fort-worth-tx", max_pages: int = 3) -> List[Dict[str, Any]]:
    """Scrape FSBO.com for for-sale-by-owner listings."""
    all_listings = []

    for page in range(1, max_pages + 1):
        url = f"https://www.fsbo.com/{city}"
        if page > 1:
            url = f"https://www.fsbo.com/{city}?page={page}"

        try:
            async with BrightDataMCP() as mcp:
                html = await mcp.scrape_html(url)

            if not html or len(html) < 500:
                break

            listings = _parse_fsbo_html(html)
            logger.info(f"FSBO page {page}: {len(listings)} listings")
            all_listings.extend(listings)

            if not listings:
                break

            await asyncio.sleep(1.5)

        except Exception as e:
            logger.error(f"FSBO page {page} failed: {e}")

    fw_listings = [l for l in all_listings if is_fort_worth_area(l.get("city", ""))]
    return fw_listings


def _parse_fsbo_html(html: str) -> List[Dict[str, Any]]:
    """Parse FSBO.com listing cards."""
    listings = []

    # FSBO uses Next.js with same push format as OffMarketDeck
    push_blocks = re.findall(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)', html, re.DOTALL)
    for block in push_blocks:
        try:
            unescaped = block.encode().decode('unicode_escape', errors='ignore')
        except Exception:
            unescaped = block

        # Look for price + address patterns
        addr_price_blocks = re.finditer(
            r'\$(\d[\d,]+).*?(\d+\s+[A-Z][A-Za-z\s]+(?:St|Street|Ave|Avenue|Rd|Road|Dr|Drive|Ln|Lane|Ct|Court|Cir|Circle|Blvd|Boulevard|Pl|Place|Way|Ter|Terrace)[,.\s]*)',
            unescaped, re.I)
        for m in addr_price_blocks:
            price_str = m.group(1).replace(',', '')
            address = m.group(2).strip()
            if len(address) < 5:
                continue
            try:
                price = int(price_str)
                if price < 1000 or price > 10_000_000:
                    continue
                listings.append({
                    "price": price,
                    "address": address,
                    "listing_type": "FSBO",
                    "data_source": "FSBO.com",
                    "source_platform": "FSBO.com",
                    "is_live_listing": True,
                    "distress_score": 35,  # FSBO = moderate motivation
                    "opportunity_signals": ["FSBO"],
                })
            except ValueError:
                pass

    # Dedupe by address
    seen = set()
    unique = []
    for l in listings:
        addr = l.get('address', '').lower()
        if addr and addr not in seen:
            seen.add(addr)
            unique.append(l)

    return unique


# ═══════════════════════════════════════════════════════════════════════════════
# HUBZU SCRAPER
# ═══════════════════════════════════════════════════════════════════════════════

async def scrape_hubzu(city: str = "Fort Worth", state: str = "TX", max_pages: int = 3) -> List[Dict[str, Any]]:
    """Scrape Hubzu.com for REO auction / bank-owned listings."""
    all_listings = []

    for page in range(1, max_pages + 1):
        url = f"https://www.hubzu.com/property-search?state={state}&city={city.replace(' ', '%20')}"
        if page > 1:
            url = f"https://www.hubzu.com/property-search?state={state}&city={city.replace(' ', '%20')}&page={page}"

        try:
            async with BrightDataMCP() as mcp:
                html = await mcp.scrape_html(url)

            if not html or len(html) < 500:
                break

            listings = _parse_hubzu_html(html)
            logger.info(f"Hubzu page {page}: {len(listings)} listings")
            all_listings.extend(listings)

            if not listings:
                break

            await asyncio.sleep(1.5)

        except Exception as e:
            logger.error(f"Hubzu page {page} failed: {e}")

    fw_listings = [l for l in all_listings if is_fort_worth_area(l.get("city", ""))]
    return fw_listings


def _parse_hubzu_html(html: str) -> List[Dict[str, Any]]:
    """Parse Hubzu listing cards from HTML."""
    listings = []

    # Hubzu uses Next.js push format
    push_blocks = re.findall(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)', html, re.DOTALL)
    for block in push_blocks:
        try:
            unescaped = block.encode().decode('unicode_escape', errors='ignore')
        except Exception:
            unescaped = block

        # Look for price + address in the unescaped text
        for m in re.finditer(r'\$(\d[\d,]+).*?(\d+\s+[A-Z][A-Za-z\s]+(?:St|Street|Ave|Avenue|Rd|Road|Dr|Drive|Ln|Lane|Ct|Court|Cir|Circle|Blvd|Boulevard|Pl|Place|Way|Ter|Terrace)', unescaped, re.I):
            price_str = m.group(1).replace(',', '')
            address = m.group(2).strip()
            if len(address) < 5:
                continue
            try:
                price = int(price_str)
                if price < 1000 or price > 5_000_000:
                    continue
                listings.append({
                    "price": price,
                    "address": address,
                    "city": "Fort Worth",
                    "state": "TX",
                    "listing_type": "REO Auction",
                    "data_source": "Hubzu.com",
                    "source_platform": "Hubzu",
                    "is_live_listing": True,
                    "distress_score": 60,  # Bank-owned = motivated
                    "opportunity_signals": ["Bank-Owned", "REO"],
                })
            except ValueError:
                pass

    seen = set()
    unique = []
    for l in listings:
        addr = l.get('address', '').lower()
        if addr and addr not in seen:
            seen.add(addr)
            unique.append(l)
    return unique


# ═══════════════════════════════════════════════════════════════════════════════
# ZILLOW SCRAPER (for specific addresses)
# ═══════════════════════════════════════════════════════════════════════════════

async def scrape_zillow_listing(address: str) -> Optional[Dict[str, Any]]:
    """
    Scrape Zillow for a specific property address.
    Returns: price, beds, baths, sqft, zestimate, listing_url
    """
    city = "Fort Worth"
    state = "TX"
    url = f"https://www.zillow.com/homes/{address.replace(' ', '-')}_{city}_TX_rb/"

    try:
        async with BrightDataMCP() as mcp:
            html = await mcp.scrape_html(url)

        if not html or len(html) < 5000:
            return None

        return _parse_zillow_property(html, address)

    except Exception as e:
        logger.error(f"Zillow scrape failed for {address}: {e}")
        return None


def _parse_zillow_property(html: str, address: str) -> Dict[str, Any]:
    """Parse Zillow property details from scraped HTML."""
    result = {"address": address, "source": "Zillow"}

    # Zestimate pattern
    m = re.search(r'Zestimate[^$]*\$([\d,]+)', html, re.I)
    if m:
        result["zestimate"] = int(m.group(1).replace(',', ''))

    # Listing price
    m = re.search(r'"price"\s*:\s*"?(\d[\d,]*)"?', html)
    if m:
        result["price"] = int(m.group(1).replace(',', ''))

    # Beds/baths
    m = re.search(r'(\d+)\s*bed', html, re.I)
    if m:
        result["beds"] = int(m.group(1))
    m = re.search(r'([\d.]+)\s*bath', html, re.I)
    if m:
        result["baths"] = float(m.group(1))

    # Sqft
    m = re.search(r'([\d,]+)\s*sqft', html, re.I)
    if m:
        result["sqft"] = int(m.group(1).replace(',', ''))

    return result


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN PIPELINE
# ═══════════════════════════════════════════════════════════════════════════════

async def fetch_all_leads(
    include_offmarket: bool = True,
    include_fsbo: bool = True,
    include_hubzu: bool = True,
    max_pages: int = 3,
) -> List[Dict[str, Any]]:
    """
    Fetch all deal types in parallel from Bright Data MCP scrapers.
    Returns deduplicated list sorted by distress score.
    """
    tasks = []
    if include_offmarket:
        tasks.append(("offmarketdeck", scrape_offmarketdeck("fort-worth", max_pages)))
    if include_fsbo:
        tasks.append(("fsbo", scrape_fsbo("fort-worth-tx", max_pages)))
    if include_hubzu:
        tasks.append(("hubzu", scrape_hubzu("Fort Worth", "TX", max_pages)))

    if not tasks:
        return []

    results = await asyncio.gather(
        *[task for _, task in tasks],
        return_exceptions=True
    )

    all_leads = []
    for (name, _), result in zip(tasks, results):
        if isinstance(result, Exception):
            logger.error(f"{name} failed: {result}")
            continue
        logger.info(f"{name}: {len(result)} leads")
        all_leads.extend(result)

    # Deduplicate by address
    seen = set()
    unique = []
    for lead in all_leads:
        addr = lead.get("situs_address", lead.get("address", "")).lower().strip()
        if addr and addr not in seen:
            seen.add(addr)
            unique.append(lead)

    # Sort by distress score (highest first)
    unique.sort(key=lambda x: x.get("distress_score", 0), reverse=True)

    logger.info(f"Total unique leads: {len(unique)}")
    return unique


# ═══════════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    async def main():
        print("Testing Bright Data MCP scraper...\n")
        leads = await fetch_all_leads(max_pages=1)
        print(f"\nTotal leads: {len(leads)}")
        for lead in leads[:10]:
            addr = lead.get("situs_address") or lead.get("address", "Unknown")
            price = lead.get("price", 0)
            src = lead.get("data_source", "Unknown")
            score = lead.get("distress_score", 0)
            print(f"  ${price:>10,} | Score:{score:>3} | {addr[:50]:50s} | {src}")

    asyncio.run(main())


# ═══════════════════════════════════════════════════════════════════════════════
# DATABASE IMPORT
# ═══════════════════════════════════════════════════════════════════════════════

async def import_leads_to_db(db, leads: List[Dict[str, Any]]) -> dict[str, Any]:
    """
    Save MCP-scraped leads to the database.
    Normalizes fields to match existing schema.
    """
    imported = 0
    skipped = 0
    errors = 0

    for lead in leads:
        address = lead.get("situs_address") or lead.get("address", "")
        if not address:
            skipped += 1
            continue

        # Normalize to existing schema
        prop = {
            "situs_address": address.upper().strip(),
            "city": lead.get("city", "Fort Worth"),
            "state": lead.get("state", "TX"),
            "zip": str(lead.get("zip", "") or ""),
            "price": lead.get("price"),
            "beds": lead.get("beds"),
            "baths": lead.get("baths"),
            "sqft": lead.get("sqft"),
            "lot_sqft": lead.get("lot_sqft"),
            "year_built": lead.get("year_built"),
            "property_type": lead.get("property_type", "house"),
            "listing_type": lead.get("listing_type", "Off-Market"),
            "data_source": lead.get("data_source", "Bright Data MCP"),
            "source_platform": lead.get("source_platform", "Bright Data MCP"),
            "is_live_listing": lead.get("is_live_listing", True),
            "distress_score": lead.get("distress_score", 50),
            "opportunity_signals": lead.get("opportunity_signals", []),
            "description": lead.get("description", ""),
            "latitude": lead.get("latitude"),
            "longitude": lead.get("longitude"),
            "updated_at": datetime.utcnow().isoformat(),
        }

        # Remove None values
        prop = {k: v for k, v in prop.items() if v is not None and v != ""}

        try:
            await db.properties.upsert_one(prop, ["situs_address"])
            imported += 1
        except Exception as e:
            logger.debug(f"Failed to import {address}: {e}")
            errors += 1

    return {
        "imported": imported,
        "skipped": skipped,
        "errors": errors,
        "total": len(leads),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# TOP-LEVEL IMPORT FUNCTION (for daily cron + API routes)
# ═══════════════════════════════════════════════════════════════════════════════

async def import_brightdata_mcp(
    db,
    include_offmarket: bool = True,
    include_fsbo: bool = True,
    include_hubzu: bool = True,
    max_pages: int = 3,
) -> dict[str, Any]:
    """
    Main entry point for the Bright Data MCP scraper.
    Fetches all lead types and saves them to the database.
    Add to daily_cron.py as:
        ("brightdata_mcp", "importers.brightdata_mcp_scraper", "import_brightdata_mcp", (db,), {"max_pages": 3})
    """
    from datetime import datetime

    token = os.environ.get("BRIGHTDATA_TOKEN", "").strip()
    if not token:
        return {"ok": False, "error": "BRIGHTDATA_TOKEN not set", "imported": 0}

    logger.info("Starting Bright Data MCP scrape...")
    leads = await fetch_all_leads(
        include_offmarket=include_offmarket,
        include_fsbo=include_fsbo,
        include_hubzu=include_hubzu,
        max_pages=max_pages,
    )

    if not leads:
        return {"ok": True, "imported": 0, "skipped": 0, "message": "No leads found"}

    # Filter to Fort Worth area
    fw_leads = [l for l in leads if is_fort_worth_area(l.get("city", ""))]
    logger.info(f"Total leads: {len(leads)}, Fort Worth: {len(fw_leads)}")

    result = await import_leads_to_db(db, fw_leads)
    return {
        "ok": True,
        "total_fetched": len(leads),
        "fort_worth": len(fw_leads),
        **result,
    }
