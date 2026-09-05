"""
Zillow + Redfin Property Enricher — InvestorFlip V1

Strategy:
  1. Google search via Bright Data MCP for "address zillow" and "address redfin"
  2. Parse Zestimate, sold price, tax, beds/baths/sqft from BOTH snippets
  3. Cross-check between Zillow and Redfin — flag big disagreements
  4. Write back to DB: estimated_value (Zillow), redfin_estimate (Redfin),
     last_sold_price, annual_taxes, beds/baths/sqft, zpid, zillow_url, redfin_url

Zillow snippets look like:
  "Off market. Zestimate. $234,100. 3beds 2baths 1,700sqft."
  "$261,900 2beds 1bath 1,350sqft. Annual tax amount: $3,450"

Redfin snippets look like:
  "Redfin Estimate: $245,000"
  "Sold: $185,000 on Feb 12, 2020"
  "Est. payment: $1,520/mo"

Usage:
  python -m importers.zillow_enricher --address "3915 Meadowbrook Dr"
  python -m importers.zillow_enricher --batch --limit 50
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from typing import Any, Optional

sys.path.insert(0, str(__file__).rsplit("/", 2)[0])

logger = logging.getLogger("zillow_enricher")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")


from importers.brightdata_mcp_scraper import BrightDataMCP


# ─── Generic price extraction ────────────────────────────────────────────────

_PRICE_RE = re.compile(r"\$([\d,]+)")


def _extract_prices(text: str) -> list[int]:
    """All dollar amounts that look like home prices ($10K–$10M)."""
    out: list[int] = []
    for m in _PRICE_RE.finditer(text):
        try:
            v = int(m.group(1).replace(",", ""))
            if 10_000 <= v <= 10_000_000:
                out.append(v)
        except ValueError:
            continue
    return out


# ─── Zillow snippet parser ────────────────────────────────────────────────────

def parse_zillow(title: str, desc: str, url: str = "") -> dict[str, Any]:
    """
    Parse Zillow data from a Google result.

    Patterns handled:
      "$234,100. 3beds. 2baths. 1,700sqft."   (off-market format)
      "$261,900 2beds 1bath 1,350sqft"        (active listing format)
      "Zestimate $271,947"                     (Zestimate explicit)
      "Annual tax amount: $3,200"              (Zillow taxes)
      "Last sold: $95,000 on Jan 15, 2020"
    """
    text = f"{title} {desc}"
    result: dict[str, Any] = {
        "zillow_estimate": None,
        "zillow_sold_price": None,
        "zillow_sold_date": None,
        "zillow_tax_amount": None,
        "beds": None,
        "baths": None,
        "sqft": None,
        "zpid": None,
        "zillow_url": url or None,
    }

    # Zestimate: pick largest price, or the one right before "bed(s)"
    prices = _extract_prices(text)
    if prices:
        result["zillow_estimate"] = max(prices)
    m = re.search(r"\$([\d,]+)\D+(\d+)\s*bed", text, re.I)
    if m:
        v = int(m.group(1).replace(",", ""))
        if 10_000 <= v <= 10_000_000:
            result["zillow_estimate"] = v

    # Beds / Baths / Sqft
    m = re.search(r"(\d+)\s*bed", text, re.I)
    if m:
        result["beds"] = int(m.group(1))
    m = re.search(r"([\d.]+)\s*bath", text, re.I)
    if m:
        result["baths"] = float(m.group(1))
    m = re.search(r"([\d,]+)\s*sqft", text, re.I)
    if m:
        result["sqft"] = int(m.group(1).replace(",", ""))

    # Sold price
    for pat in [r"last\s+sold[:\s]+\$?([\d,]+)",
                r"sold[:\s]+on\s+[\w\s,]+\$?([\d,]+)",
                r"sold\s+\$?([\d,]+)"]:
        m = re.search(pat, text, re.I)
        if m:
            v = int(m.group(1).replace(",", ""))
            if 10_000 <= v <= 10_000_000:
                result["zillow_sold_price"] = v
                break

    # Sold date
    m = re.search(
        r"(?:last\s+)?sold\s+(?:on\s+)?((?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\.?\s+\d{1,2},?\s+\d{4})",
        text, re.I)
    if m:
        result["zillow_sold_date"] = m.group(1).strip()

    # Taxes
    for pat in [r"annual\s+tax\s+amount[:\s]+\$?([\d,]+)",
                r"annual\s+tax[:\s]+\$?([\d,]+)",
                r"property\s+tax[:\s]+\$?([\d,]+)"]:
        m = re.search(pat, text, re.I)
        if m:
            v = int(m.group(1).replace(",", ""))
            if 500 <= v <= 100_000:
                result["zillow_tax_amount"] = v
                break

    # ZPID from URL
    if url:
        m = re.search(r"_(\d{6,15})_zpid", url)
        if m:
            result["zpid"] = m.group(1)

    return result


# ─── Redfin snippet parser ────────────────────────────────────────────────────

def parse_redfin(title: str, desc: str, url: str = "") -> dict[str, Any]:
    """
    Parse Redfin data from a Google result.

    Patterns handled:
      "Redfin Estimate: $245,000"
      "Sold: $185,000 on Feb 12, 2020"
      "Est. payment: $1,520/mo"
      "3 beds 2 baths 1,700 sqft"  (Redfin format)
      "$XYZ. 3 beds. 2 baths."      (Redfin snippet format)
    """
    text = f"{title} {desc}"
    result: dict[str, Any] = {
        "redfin_estimate": None,
        "redfin_sold_price": None,
        "redfin_sold_date": None,
        "redfin_url": url or None,
    }

    # Redfin estimate
    m = re.search(r"redfin\s+estimate[:\s]+\$?([\d,]+)", text, re.I)
    if m:
        v = int(m.group(1).replace(",", ""))
        if 10_000 <= v <= 10_000_000:
            result["redfin_estimate"] = v
    # Fallback: any "Estimate $X" pattern
    if not result["redfin_estimate"]:
        m = re.search(r"estimate[:\s]+\$?([\d,]+)", text, re.I)
        if m:
            v = int(m.group(1).replace(",", ""))
            if 10_000 <= v <= 10_000_000:
                result["redfin_estimate"] = v
    # Fallback: largest price if no Zillow data
    if not result["redfin_estimate"]:
        prices = _extract_prices(text)
        if prices:
            result["redfin_estimate"] = max(prices)

    # Sold price
    for pat in [r"sold[:\s]+on\s+[\w\s,]+\$?([\d,]+)",
                r"sold[:\s]+\$?([\d,]+)",
                r"sale\s+price[:\s]+\$?([\d,]+)"]:
        m = re.search(pat, text, re.I)
        if m:
            v = int(m.group(1).replace(",", ""))
            if 10_000 <= v <= 10_000_000:
                result["redfin_sold_price"] = v
                break

    # Sold date
    m = re.search(
        r"sold\s+(?:on\s+)?((?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\.?\s+\d{1,2},?\s+\d{4})",
        text, re.I)
    if m:
        result["redfin_sold_date"] = m.group(1).strip()

    return result


# ─── Combined Google search (Zillow + Redfin) ─────────────────────────────────

async def _search_google(mcp: BrightDataMCP, address: str, site: str) -> list[dict[str, Any]]:
    """Helper: run 2 Google queries targeting a specific site."""
    queries = [
        f'"{address}" site:{site}',
        f'"{address}" site:{site} estimate',
    ]
    all_results: list[dict[str, Any]] = []
    for q in queries:
        try:
            r = await mcp.search(q, engine="google")
            all_results.extend(r[:5])
        except Exception as e:
            logger.warning("Search '%s' failed: %s", q[:60], e)
        await asyncio.sleep(0.3)
    return all_results


def _is_address_match(address: str, text: str) -> bool:
    """Fuzzy check: at least 1 unique word from address is in text."""
    parts = [p.lower() for p in re.split(r"[\s,]+", address) if len(p) > 3 and p.lower() not in {"fort", "worth", "tx", "texas"}]
    if not parts:
        return True
    t = text.lower()
    return any(p in t for p in parts)


# ─── The Enricher class (used by routes) ──────────────────────────────────────

class ZillowRedfinEnricher:
    """Enrich properties with Zillow + Redfin data via Bright Data MCP."""

    def __init__(self):
        self.source_name = "Zillow+Redfin via Bright Data MCP"

    async def enrich(
        self,
        db,
        address: str,
        city: str = "Fort Worth",
        state: str = "TX",
    ) -> dict[str, Any]:
        """Enrich one property. Returns summary dict."""
        if not address or not address.strip():
            return {"ok": False, "address": str(address), "error": "Empty address"}
        address = address.strip()

        # Strip city/state from address if already included
        search_address = re.sub(r",?\s*(fort worth|tx|texas)\s*$", "", address, flags=re.I).strip()

        logger.info("Enriching: %s, %s %s", search_address, city, state)
        result: dict[str, Any] = {
            "address": address,
            "zillow": {},
            "redfin": {},
            "merged": {},
            "ok": False,
        }

        async with BrightDataMCP() as mcp:
            # ── Zillow ──
            z_results = await _search_google(mcp, search_address, "zillow.com")
            for r in z_results:
                title = r.get("title", "")
                desc = r.get("description", r.get("snippet", ""))
                url = r.get("url") or r.get("link") or ""
                combined = f"{title} {desc}"
                if "zillow" not in combined.lower() and "zillow" not in url.lower():
                    continue
                if not _is_address_match(search_address, combined):
                    continue
                parsed = parse_zillow(title, desc, url)
                if parsed.get("zillow_estimate") or parsed.get("beds"):
                    result["zillow"] = parsed
                    break

            await asyncio.sleep(0.5)

            # ── Redfin ──
            r_results = await _search_google(mcp, search_address, "redfin.com")
            for r in r_results:
                title = r.get("title", "")
                desc = r.get("description", r.get("snippet", ""))
                url = r.get("url") or r.get("link") or ""
                combined = f"{title} {desc}"
                if "redfin" not in combined.lower() and "redfin" not in url.lower():
                    continue
                if not _is_address_match(search_address, combined):
                    continue
                parsed = parse_redfin(title, desc, url)
                if parsed.get("redfin_estimate") or parsed.get("redfin_sold_price"):
                    result["redfin"] = parsed
                    break

        # ── Cross-check & merge ──
        result["merged"] = self._merge(result["zillow"], result["redfin"])
        result["ok"] = bool(result["merged"].get("estimated_value"))

        # ── Write to DB ──
        if result["ok"]:
            write = await self._write_to_db(db, address, result["merged"])
            result["db_write"] = write

        return result

    def _merge(self, z: dict, r: dict) -> dict[str, Any]:
        """Merge Zillow + Redfin data, cross-check, return unified dict."""
        merged: dict[str, Any] = {}

        # Estimated value: prefer Zillow (more common), cross-check Redfin
        z_est = z.get("zillow_estimate")
        r_est = r.get("redfin_estimate")
        if z_est and r_est:
            # Both available — cross-check
            diff_pct = abs(z_est - r_est) / max(z_est, r_est) * 100
            merged["estimated_value"] = z_est  # Zillow is primary
            merged["redfin_estimate"] = r_est
            merged["estimate_cross_check_diff_pct"] = round(diff_pct, 1)
            if diff_pct > 25:
                merged["estimate_confidence"] = "low"
                merged["estimate_warning"] = f"Zillow and Redfin disagree by {diff_pct:.0f}%"
            elif diff_pct > 10:
                merged["estimate_confidence"] = "medium"
            else:
                merged["estimate_confidence"] = "high"
        elif z_est:
            merged["estimated_value"] = z_est
            merged["estimate_confidence"] = "medium (Zillow only)"
        elif r_est:
            merged["estimated_value"] = r_est
            merged["estimate_confidence"] = "medium (Redfin only)"

        # Sold price
        if z.get("zillow_sold_price"):
            merged["last_sold_price"] = z["zillow_sold_price"]
            merged["last_sold_date"] = z.get("zillow_sold_date")
        elif r.get("redfin_sold_price"):
            merged["last_sold_price"] = r["redfin_sold_price"]
            merged["last_sold_date"] = r.get("redfin_sold_date")

        # Tax
        if z.get("zillow_tax_amount"):
            merged["annual_taxes"] = z["zillow_tax_amount"]

        # Beds / Baths / Sqft (Zillow first)
        for k, src in [("beds", z), ("baths", z), ("sqft", z)]:
            if src.get(k):
                merged[k] = src[k]

        # IDs / URLs
        if z.get("zpid"):
            merged["zpid"] = z["zpid"]
        if z.get("zillow_url"):
            merged["zillow_url"] = z["zillow_url"]
        if r.get("redfin_url"):
            merged["redfin_url"] = r["redfin_url"]

        # Provenance
        sources = []
        if z.get("zillow_estimate"): sources.append("Zillow")
        if r.get("redfin_estimate"): sources.append("Redfin")
        merged["enrichment_source"] = " + ".join(sources) if sources else "Google search (no direct source)"

        return merged

    async def _write_to_db(self, db, address: str, data: dict) -> dict:
        """Write merged enrichment to the properties DB."""
        from intake import upsert_property
        from database import PostgresDatabase

        patch = dict(data)
        patch["updated_at"] = datetime.now(timezone.utc).isoformat()
        patch["enriched_at"] = datetime.now(timezone.utc).isoformat()

        try:
            # Don't overwrite existing beds/baths/sqft if they're populated
            existing = await db.properties.find_one(
                {"$or": [{"situs_address": address}, {"address": address}]},
                {"_id": 0, "beds": 1, "baths": 1, "sqft": 1}
            )
            if existing:
                for k in ("beds", "baths", "sqft"):
                    if existing.get(k) and k in patch:
                        del patch[k]

            await upsert_property(db, address, patch)
            return {"ok": True, "fields_written": list(patch.keys())}
        except Exception as e:
            logger.error("DB write failed for %s: %s", address, e)
            return {"ok": False, "error": str(e)}

    async def enrich_all_preforeclosures(
        self,
        db,
        limit: int = 200,
    ) -> dict[str, Any]:
        """Batch enrich all preforeclosure records missing estimated_value."""
        cursor = db.properties.find(
            {
                "pre_foreclosure": True,
                "$and": [
                    {"$or": [{"estimated_value": {"$exists": False}}, {"estimated_value": None}]},
                ],
            },
            {"_id": 0, "id": 1, "situs_address": 1, "address": 1, "city": 1, "state": 1},
        )

        results = {"total": 0, "enriched": 0, "zillow_only": 0, "redfin_only": 0, "both": 0, "failed": 0, "items": []}

        async for prop in cursor:
            results["total"] += 1
            if results["total"] > limit:
                break

            address = prop.get("situs_address") or prop.get("address")
            if not address:
                results["failed"] += 1
                continue

            city = prop.get("city") or "Fort Worth"
            state = prop.get("state") or "TX"

            try:
                r = await self.enrich(db, address, city, state)
                merged = r.get("merged", {})
                if r.get("ok") and merged.get("estimated_value"):
                    results["enriched"] += 1
                    has_z = bool(r.get("zillow"))
                    has_r = bool(r.get("redfin"))
                    if has_z and has_r: results["both"] += 1
                    elif has_z: results["zillow_only"] += 1
                    elif has_r: results["redfin_only"] += 1
                    results["items"].append({
                        "address": address,
                        "estimated_value": merged.get("estimated_value"),
                        "redfin_estimate": merged.get("redfin_estimate"),
                        "cross_check_diff_pct": merged.get("estimate_cross_check_diff_pct"),
                        "confidence": merged.get("estimate_confidence"),
                    })
                else:
                    results["failed"] += 1
            except Exception as e:
                results["failed"] += 1
                logger.error("Enrich exception for %s: %s", address, e)

            await asyncio.sleep(1.5)  # rate limit

        logger.info(
            "Batch done: %s/%s enriched (both: %s, zillow only: %s, redfin only: %s), %s failed",
            results["enriched"], results["total"], results["both"], results["zillow_only"],
            results["redfin_only"], results["failed"],
        )
        return results


# ─── CLI ────────────────────────────────────────────────────────────────────

async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--address")
    p.add_argument("--city", default="Fort Worth")
    p.add_argument("--state", default="TX")
    p.add_argument("--batch", action="store_true")
    p.add_argument("--limit", type=int, default=200)
    args = p.parse_args()

    from database import PostgresDatabase
    db = PostgresDatabase()
    await db.connect()
    try:
        e = ZillowRedfinEnricher()
        if args.batch:
            r = await e.enrich_all_preforeclosures(db, limit=args.limit)
        elif args.address:
            r = await e.enrich(db, args.address, args.city, args.state)
        else:
            print("Usage: --address '3915 Meadowbrook Dr' or --batch --limit 50")
            return
        print(json.dumps(r, indent=2, default=str))
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
