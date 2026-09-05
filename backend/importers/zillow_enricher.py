"""
Zillow Property Enricher — InvestorFlip V1

Uses Bright Data MCP to search Google for Zillow/Realtor property data and
extracts:
  - estimated_value   (Zillow Zestimate)
  - last_sold_price  (from Zillow snippet)
  - last_sold_date   (from Zillow snippet)
  - annual_taxes     (from Zillow snippet)
  - beds / baths / sqft
  - zpid / zillow_url

Workflow:
  1. Search Google for "{address} zillow zestimate sold" via Bright Data MCP
  2. Parse Zestimate, sold price, beds/baths from Google snippet
  3. Write back to DB via upsert_property (only fills empty fields)

Usage:
  python -m importers.zillow_enricher --address "3915 Meadowbrook Dr Fort Worth TX"
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


# ─── Bright Data MCP ──────────────────────────────────────────────────────────

from importers.brightdata_mcp_scraper import BrightDataMCP


# ─── Parser ──────────────────────────────────────────────────────────────────

def _extract_prices(text: str) -> list[int]:
    """Extract all dollar amounts that look like home prices ($10K–$10M)."""
    amounts = re.findall(r"\$[\d,]+", text)
    prices = []
    for amt in amounts:
        cleaned = amt.replace("$", "").replace(",", "")
        try:
            val = int(cleaned)
            if 10_000 <= val <= 10_000_000:
                prices.append(val)
        except ValueError:
            continue
    return prices


def parse_zillow_from_search_result(title: str, description: str, url: str = "") -> dict[str, Any]:
    """
    Parse Zillow property data from a Google search result snippet.

    Snippet patterns:
      "$234,100  3beds 2baths 1,700sqft"
      "Zestimate® $271,947  3beds 2baths"
      "Annual tax amount: $3,200"
      "Last sold: $95,000 on Jan 15, 2020"
    """
    result: dict[str, Any] = {
        "estimated_value": None,
        "last_sold_price": None,
        "last_sold_date": None,
        "annual_taxes": None,
        "beds": None,
        "baths": None,
        "sqft": None,
        "rent_zestimate": None,
        "zpid": None,
        "zillow_url": url,
    }

    text = f"{title} {description}"

    # ── Zestimate ────────────────────────────────────────────────────────
    prices = _extract_prices(text)
    if prices:
        # Zestimate is typically the largest price in the snippet
        # (sold prices are usually lower; asking price might be listed separately)
        result["estimated_value"] = max(prices)

    # Also try: "price Nbeds N baths" pattern (common Zillow format)
    m = re.search(r"\$([\d,]+)\D+(\d+)\s*bed", text, re.I)
    if m:
        val = int(m.group(1).replace(",", ""))
        if 10_000 <= val <= 10_000_000:
            result["estimated_value"] = val

    # ── Beds / Baths / Sqft ───────────────────────────────────────────
    m = re.search(r"(\d+)\s*bed", text, re.I)
    if m:
        result["beds"] = int(m.group(1))
    m = re.search(r"([\d.]+)\s*bath", text, re.I)
    if m:
        result["baths"] = float(m.group(1))
    m = re.search(r"([\d,]+)\s*sqft", text, re.I)
    if m:
        result["sqft"] = int(m.group(1).replace(",", ""))
    # Handle "1,700sqft" without space
    m = re.search(r"([\d,]+)sqft", text, re.I)
    if m and not result.get("sqft"):
        result["sqft"] = int(m.group(1).replace(",", ""))

    # ── Last Sold Price ────────────────────────────────────────────────
    sold_patterns = [
        r"last\s+sold[:\s]+\$?([\d,]+)",
        r"sold\s+(?:on\s+)?[\w\s,]+(?:\$|price)\s*([\d,]+)",
        r"sold\s+\$?([\d,]+)",
        r"sale\s+price[:\s]+\$?([\d,]+)",
    ]
    for pat in sold_patterns:
        m = re.search(pat, text, re.I)
        if m:
            val = int(m.group(1).replace(",", ""))
            if 10_000 <= val <= 10_000_000:
                result["last_sold_price"] = val
                break

    # ── Last Sold Date ────────────────────────────────────────────────
    m = re.search(
        r"(?:last\s+)?sold\s+(?:on\s+)?((?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\.?\s+\d{1,2},?\s+\d{4})",
        text, re.I,
    )
    if m:
        result["last_sold_date"] = m.group(1).strip()

    # ── Annual Taxes ─────────────────────────────────────────────────
    tax_patterns = [
        r"annual\s+tax\s+amount[:\s]+\$?([\d,]+)",
        r"annual\s+tax[:\s]+\$?([\d,]+)",
        r"property\s+tax[:\s]+\$?([\d,]+)",
        r"tax\s+amount[:\s]+\$?([\d,]+)",
    ]
    for pat in tax_patterns:
        m = re.search(pat, text, re.I)
        if m:
            val = int(m.group(1).replace(",", ""))
            if 500 <= val <= 100_000:
                result["annual_taxes"] = val
                break

    # ── Rent Zestimate ───────────────────────────────────────────────
    m = re.search(r"rent\s+zestimate[®\s:]+\$?([\d,]+)", text, re.I)
    if m:
        result["rent_zestimate"] = int(m.group(1).replace(",", ""))

    # ── ZPID from URL ────────────────────────────────────────────────
    m = re.search(r"_zpid|zpid[=/](\d+)", url, re.I)
    if m:
        result["zpid"] = m.group(1) if m.group(1) else url.split("_")[0].split("/")[-1]

    return result


# ─── Google Search (via Bright Data MCP) ──────────────────────────────────────

async def search_zillow_for_address(
    address: str,
    city: str = "Fort Worth",
    state: str = "TX",
) -> Optional[dict[str, Any]]:
    """
    Search Google for a property's Zillow data via Bright Data MCP.
    Returns parsed property data (estimate, sold price, beds/baths) or None.
    """
    # Two query angles — zip gets the best results
    zipcode = re.search(r"\b(\d{5})\b", address + " " + city)
    zip_part = f" {zipcode.group(1)}" if zipcode else ""

    queries = [
        f'"{address}{zip_part}" site:zillow.com',
        f'"{address}{zip_part}" site:zillow.com Zestimate',
        f'"{address}{zip_part}" zillow "sold" "zestimate"',
    ]

    async with BrightDataMCP() as mcp:
        for query in queries:
            logger.info("  Search: %s", query[:100])
            try:
                results = await mcp.search(query, engine="google")
            except Exception as e:
                logger.warning("  Search failed: %s", e)
                continue

            if not results:
                continue

            # Find the best Zillow result
            for r in results[:5]:
                title = r.get("title", "")
                desc = r.get("description", r.get("snippet", ""))
                url = r.get("url") or r.get("link") or ""

                # Must be Zillow or have Zillow data
                if "zillow" not in title.lower() and "zillow" not in desc.lower():
                    continue

                data = parse_zillow_from_search_result(title, desc, url)

                if data.get("estimated_value") or data.get("beds") or data.get("last_sold_price"):
                    logger.info(
                        "    ✓ Got: value=$%s | sold=$%s | beds=%s | baths=%s",
                        data.get("estimated_value"),
                        data.get("last_sold_price"),
                        data.get("beds"),
                        data.get("baths"),
                    )
                    return data

            await asyncio.sleep(0.5)

    return None


# ─── DB Write ────────────────────────────────────────────────────────────────

async def write_enrichment(db, address: str, enrichment: dict[str, Any]) -> dict:
    """Write Zillow enrichment data to the properties DB. Only fills empty fields."""
    from intake import upsert_property

    patch: dict[str, Any] = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "zillow_enriched_at": datetime.now(timezone.utc).isoformat(),
        "enrichment_source": "Zillow via Google (Bright Data MCP)",
    }

    # Fields that are ALWAYS written (replace any stale/old data)
    if enrichment.get("estimated_value"):
        patch["estimated_value"] = enrichment["estimated_value"]
        patch["zestimate"] = enrichment["estimated_value"]
    if enrichment.get("last_sold_price"):
        patch["last_sold_price"] = enrichment["last_sold_price"]
    if enrichment.get("last_sold_date"):
        patch["last_sold_date"] = enrichment["last_sold_date"]
    if enrichment.get("annual_taxes"):
        patch["annual_taxes"] = enrichment["annual_taxes"]
    if enrichment.get("rent_zestimate"):
        patch["rent_zestimate"] = enrichment["rent_zestimate"]
    if enrichment.get("zpid"):
        patch["zpid"] = enrichment["zpid"]
    if enrichment.get("zillow_url"):
        patch["zillow_url"] = enrichment["zillow_url"]

    # Beds/baths/sqft — only fill if not already set
    existing = await db.properties.find_one(
        {"$or": [{"situs_address": address}, {"address": address}]},
        {"_id": 0, "beds": 1, "baths": 1, "sqft": 1},
    )
    if existing:
        if not existing.get("beds") and enrichment.get("beds"):
            patch["beds"] = enrichment["beds"]
        if not existing.get("baths") and enrichment.get("baths"):
            patch["baths"] = enrichment["baths"]
        if not existing.get("sqft") and enrichment.get("sqft"):
            patch["sqft"] = enrichment["sqft"]

    try:
        await upsert_property(db, address, patch)
        return {"ok": True, "address": address, "patch": patch}
    except Exception as e:
        logger.error("DB write failed for %s: %s", address, e)
        return {"ok": False, "address": address, "error": str(e)}


# ─── Single Property Enrichment ───────────────────────────────────────────────

async def enrich_property(
    db,
    address: str,
    city: str = "Fort Worth",
    state: str = "TX",
) -> dict[str, Any]:
    """
    Enrich one property by address. Searches Google via Bright Data MCP,
    parses Zillow data, writes to DB.
    """
    if not address or not address.strip():
        return {"ok": False, "address": str(address), "error": "Empty address"}

    address = address.strip()
    logger.info("Enriching: %s", address)

    data = await search_zillow_for_address(address, city, state)
    if not data:
        return {
            "ok": False,
            "address": address,
            "error": "No Zillow data found for this address",
        }

    result = await write_enrichment(db, address, data)
    return {
        "ok": result.get("ok", False),
        "address": address,
        "estimated_value": data.get("estimated_value"),
        "last_sold_price": data.get("last_sold_price"),
        "last_sold_date": data.get("last_sold_date"),
        "annual_taxes": data.get("annual_taxes"),
        "beds": data.get("beds"),
        "baths": data.get("baths"),
        "sqft": data.get("sqft"),
        "zpid": data.get("zpid"),
        "zillow_url": data.get("zillow_url"),
        "source": "Zillow via Google (Bright Data MCP)",
    }


# ─── Batch Enrichment ────────────────────────────────────────────────────────

async def enrich_all_preforeclosures(
    db,
    limit: int = 200,
    skip_already_enriched: bool = True,
) -> dict[str, Any]:
    """
    Find all preforeclosure / foreclosure records missing estimated_value
    and enrich them from Zillow via Google.
    """
    logger.info("Scanning for preforeclosures needing enrichment...")

    query: dict[str, Any] = {
        "pre_foreclosure": True,
    }

    if skip_already_enriched:
        query["$and"] = [
            {"estimated_value": {"$exists": False}},
            {"zillow_enriched_at": {"$exists": False}},
        ]

    cursor = db.properties.find(
        query,
        {"_id": 0, "id": 1, "situs_address": 1, "address": 1, "city": 1, "state": 1},
    )

    results = {"total": 0, "enriched": 0, "failed": 0, "items": []}

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
            result = await enrich_property(db, address, city, state)
            if result.get("ok") and result.get("estimated_value"):
                results["enriched"] += 1
                results["items"].append({
                    "address": address,
                    "estimated_value": result["estimated_value"],
                    "last_sold_price": result.get("last_sold_price"),
                    "annual_taxes": result.get("annual_taxes"),
                })
            else:
                results["failed"] += 1
        except Exception as e:
            results["failed"] += 1
            logger.error("Exception for %s: %s", address, e)

        # Rate limit between searches (~2 search credits per address)
        await asyncio.sleep(1.2)

    logger.info(
        "Batch complete: %s/%s enriched, %s failed of %s total",
        results["enriched"], results["total"], results["failed"], results["total"],
    )
    return results


# ─── CLI ────────────────────────────────────────────────────────────────────

async def main():
    parser = argparse.ArgumentParser(description="Zillow property enricher via Bright Data MCP")
    parser.add_argument("--address", help='Full address, e.g. "3915 Meadowbrook Dr"')
    parser.add_argument("--city", default="Fort Worth")
    parser.add_argument("--state", default="TX")
    parser.add_argument("--batch", action="store_true")
    parser.add_argument("--limit", type=int, default=200)
    args = parser.parse_args()

    from database import PostgresDatabase
    db = PostgresDatabase()
    await db.connect()

    try:
        if args.batch:
            r = await enrich_all_preforeclosures(db, limit=args.limit)
        elif args.address:
            r = await enrich_property(db, args.address, args.city, args.state)
        else:
            print("Usage:")
            print("  python -m importers.zillow_enricher --address '3915 Meadowbrook Dr'")
            print("  python -m importers.zillow_enricher --batch --limit 50")
            return
        print(json.dumps(r, indent=2, default=str))
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
