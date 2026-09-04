"""
Bright Data Deal Finder - Multi-source pre-foreclosure + FSBO + distress scraper.

The strategy:
1. Tarrant County Clerk (public records) — Lis Pendens filings = pre-foreclosure
   BEFORE auction. These are the BEST leads. Owner is in distress, auction hasn't
   happened, you can still negotiate a deal.
2. ForeclosureListingsUSA.com — already working (2,410+ Fort Worth)
3. FSBO.com — For Sale By Owner (motivated sellers)
4. Hubzu.com — Zillow REO auction inventory

Uses Bright Data's Web Unlocker REST API to bypass anti-bot protections.
Cost: 5,000 credits/month free tier — plenty for daily deal-finding.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import urllib.parse
from datetime import datetime, timedelta
from typing import Any

import httpx

logger = logging.getLogger("brightdata_deals")

# ─── Bright Data Configuration ────────────────────────────────────────────────
BRIGHT_DATA_TOKEN = os.environ.get(
    "BRIGHT_DATA_TOKEN",
    "5809d6b4-75ec-44a2-83b2-dc98972e4727"
)
BRIGHT_DATA_ZONE = os.environ.get("BRIGHT_DATA_ZONE", "web_unlocker1")

# Bright Data REST API endpoints
BD_SEARCH_URL = "https://api.brightdata.com/serp/search"
BD_SCRAPE_URL = "https://api.brightdata.com/datasets/scrape"


# ─── TARRANT COUNTY CITIES ALLOWLIST ──────────────────────────────────────────
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
    return c.startswith("fort worth") or "fort worth" in c


# ─── BRIGHT DATA API HELPERS ──────────────────────────────────────────────────
async def bd_search(query: str, country: str = "us", num_results: int = 10) -> list[dict]:
    """
    Use Bright Data search engine (Google SERP) to find pages.
    1 credit per search.
    """
    payload = {
        "query": query,
        "country": country,
        "num_results": num_results,
        "parse": True,
    }
    headers = {
        "Authorization": f"Bearer {BRIGHT_DATA_TOKEN}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(BD_SEARCH_URL, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            return data.get("organic", [])
    except Exception as e:
        logger.error(f"Bright Data search failed for '{query}': {e}")
        return []


async def bd_scrape(url: str, format: str = "markdown") -> str:
    """
    Use Bright Data Web Unlocker to scrape a protected page.
    ~10-25 credits per page.
    """
    payload = {
        "url": url,
        "zone": BRIGHT_DATA_ZONE,
        "format": format,
        "country": "us",
    }
    headers = {
        "Authorization": f"Bearer {BRIGHT_DATA_TOKEN}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(BD_SCRAPE_URL, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            return data.get("body", data.get("content", ""))
    except Exception as e:
        logger.error(f"Bright Data scrape failed for {url}: {e}")
        return ""


# ─── TARRANT COUNTY CLERK (LIS PENDENS) ──────────────────────────────────────
TARRANT_BASE = "https://tarrant.tx.publicsearch.us/"


async def fetch_county_clerk_pre_foreclosure(days_back: int = 30) -> list[dict[str, Any]]:
    """
    Scrape Tarrant County Clerk records for Lis Pendens filings.
    These are pre-foreclosure notices — owner is being SUED by lender,
    but auction hasn't happened yet. This is the BEST pre-foreclosure data.
    """
    date_map = {7: "7D", 30: "30D", 90: "90D", 180: "6M", 365: "1Y"}
    date_param = date_map.get(days_back, "30D")
    
    # Keywords that signal pre-foreclosure activity
    keywords = [
        "lis pendens",
        "notice of default",
        "notice of trustee sale",
        "substitute trustee",
        "foreclosure deed of trust",
    ]
    
    all_records = []
    
    for kw in keywords:
        keyword_enc = urllib.parse.quote(kw)
        search_url = (
            f"{TARRANT_BASE}?"
            f"searchTerm={keyword_enc}"
            f"&dateRange={date_param}"
            f"&searchOcrText=false"
            f"&department=RP"
        )
        
        logger.info(f"County clerk: searching '{kw}' (last {days_back} days)")
        
        try:
            # Scrape search results page
            content = await bd_scrape(search_url, format="html")
            if not content:
                logger.warning(f"No content for: {kw}")
                continue
            
            # Parse the search results
            records = parse_clerk_results(content, instrument_type=kw.title())
            logger.info(f"  → {len(records)} records for '{kw}'")
            all_records.extend(records)
            
        except Exception as e:
            logger.error(f"County clerk search failed for '{kw}': {e}")
    
    # Deduplicate by instrument number
    seen = set()
    unique = []
    for r in all_records:
        key = r.get("instrument_num", r.get("address", ""))
        if key and key not in seen:
            seen.add(key)
            unique.append(r)
    
    logger.info(f"County clerk: {len(unique)} unique pre-foreclosure records")
    return unique


def parse_clerk_results(html: str, instrument_type: str = "") -> list[dict[str, Any]]:
    """
    Parse Tarrant County Clerk search results HTML.
    Looks for table rows containing instrument number, date, grantor/grantee, address.
    """
    records = []
    
    # Find all table rows
    row_pattern = re.compile(r"<tr[^>]*>(.*?)</tr>", re.DOTALL | re.IGNORECASE)
    cell_pattern = re.compile(r"<td[^>]*>(.*?)</td>", re.DOTALL | re.IGNORECASE)
    
    for row_match in row_pattern.finditer(html):
        row_html = row_match.group(1)
        cells = [
            re.sub(r"<[^>]+>", "", cell).strip()
            for cell in cell_pattern.findall(row_html)
        ]
        
        if len(cells) < 3:
            continue
        
        record = {
            "instrument_type": instrument_type,
            "data_source": "Tarrant County Clerk",
            "is_live_listing": False,
        }
        
        # First cell is usually instrument number
        for cell in cells:
            # Instrument number: D followed by 8+ digits
            m = re.search(r"\b(D?\d{8,})\b", cell)
            if m and not record.get("instrument_num"):
                record["instrument_num"] = m.group(1)
            
            # Date: MM/DD/YYYY
            m = re.search(r"\b(\d{1,2}/\d{1,2}/\d{2,4})\b", cell)
            if m and not record.get("filing_date"):
                record["filing_date"] = m.group(1)
            
            # Address pattern
            m = re.search(
                r"\b(\d+\s+[A-Z][A-Z\s]+(?:ST|AVENUE|AVE|ROAD|RD|DRIVE|DR|LANE|LN|COURT|CT|CIRCLE|CIR|BOULEVARD|BLVD|PLACE|PL|WAY|TERRACE|TER|TRL|TRAIL|PT|PKWY|HWY))\b",
                cell, re.I
            )
            if m and not record.get("address"):
                record["address"] = m.group(1).strip().upper()
        
        # If we found enough fields, keep the record
        if record.get("instrument_num") and (record.get("address") or len(cells) >= 4):
            # Try to extract owner from grantor field (first cell with all caps name)
            for cell in cells:
                if re.match(r"^[A-Z][A-Z\s,\.]{5,60}$", cell) and not record.get("owner_name"):
                    record["owner_name"] = cell.strip()
            
            records.append(record)
    
    return records


# ─── FSBO.COM SCRAPER ─────────────────────────────────────────────────────────
FSBO_BASE = "https://www.fsbo.com/"


async def fetch_fsbo_listings(city: str = "fort-worth-tx", max_pages: int = 5) -> list[dict[str, Any]]:
    """
    Scrape FSBO.com for For Sale By Owner listings in Fort Worth.
    FSBO sellers are often motivated — they don't want to pay agent commission.
    """
    listings = []
    
    for page in range(1, max_pages + 1):
        search_url = f"{FSBO_BASE}{city}?page={page}"
        logger.info(f"FSBO: {city} page {page}")
        
        try:
            content = await bd_scrape(search_url, format="html")
            if not content:
                continue
            
            # Parse listings
            page_listings = parse_fsbo_listings(content)
            listings.extend(page_listings)
            logger.info(f"  → {len(page_listings)} listings on page {page}")
            
            if not page_listings:
                break  # No more results
            
        except Exception as e:
            logger.error(f"FSBO scrape failed page {page}: {e}")
    
    return listings


def parse_fsbo_listings(html: str) -> list[dict[str, Any]]:
    """Parse FSBO.com listing cards."""
    listings = []
    
    # FSBO uses card-based layout. Look for address + price patterns.
    # Address: "123 Main St, Fort Worth, TX 76102"
    addr_pattern = re.compile(
        r"(\d+\s+[A-Za-z0-9\s\.]+(?:St|Ave|Rd|Dr|Ln|Ct|Cir|Blvd|Pl|Way|Ter|Trl|Pkwy)[^<]*?,\s*[A-Za-z\s]+,\s*TX\s*\d{5})",
        re.I
    )
    price_pattern = re.compile(r"\$([0-9,]+)", re.I)
    
    for addr_match in addr_pattern.finditer(html):
        address = addr_match.group(1).strip()
        
        # Find nearby price (within 500 chars)
        start = max(0, addr_match.start() - 200)
        end = min(len(html), addr_match.end() + 200)
        nearby = html[start:end]
        
        price_match = price_pattern.search(nearby)
        price = int(price_match.group(1).replace(",", "")) if price_match else None
        
        listing = {
            "address": address,
            "city": "Fort Worth",
            "state": "TX",
            "price": price,
            "listing_type": "FSBO",
            "data_source": "FSBO.com",
            "source_platform": "FSBO.com",
            "is_live_listing": True,
            "pre_foreclosure": False,
            "distress_score": 35,  # FSBO = moderate motivation
        }
        listings.append(listing)
    
    return listings


# ─── HUBZU (ZILLOW REO) ───────────────────────────────────────────────────────
HUBZU_BASE = "https://www.hubzu.com/"


async def fetch_hubzu_auctions(city: str = "Fort Worth", state: str = "TX", max_pages: int = 3) -> list[dict[str, Any]]:
    """
    Scrape Hubzu.com for auction inventory (Zillow's REO platform).
    These are bank-owned properties going to auction.
    """
    listings = []
    
    search_url = (
        f"{HUBZU_BASE}property-search?"
        f"state={state}&city={urllib.parse.quote(city)}"
    )
    
    logger.info(f"Hubzu: {city}, {state}")
    
    try:
        content = await bd_scrape(search_url, format="html")
        if not content:
            return listings
        
        # Hubzu uses Angular/React — look for JSON data in script tags
        json_pattern = re.compile(r'window\.__INITIAL_STATE__\s*=\s*(\{.*?\});', re.DOTALL)
        json_match = json_pattern.search(content)
        if json_match:
            try:
                import json
                state_data = json.loads(json_match.group(1))
                # Extract properties from state tree
                properties = extract_hubzu_properties(state_data)
                listings.extend(properties)
            except Exception as e:
                logger.debug(f"Hubzu JSON parse failed: {e}")
        
        # Fallback: HTML parse
        if not listings:
            listings = parse_hubzu_html(content, city, state)
        
    except Exception as e:
        logger.error(f"Hubzu scrape failed: {e}")
    
    return listings


def extract_hubzu_properties(data: Any) -> list[dict[str, Any]]:
    """Recursively find property objects in Hubzu state tree."""
    listings = []
    if isinstance(data, dict):
        for key, value in data.items():
            if key in ("properties", "listings", "results", "items"):
                if isinstance(value, list):
                    for item in value:
                        if isinstance(item, dict) and ("address" in item or "streetAddress" in item):
                            listings.append(normalize_hubzu_property(item))
            else:
                listings.extend(extract_hubzu_properties(value))
    elif isinstance(data, list):
        for item in data:
            listings.extend(extract_hubzu_properties(item))
    return listings


def normalize_hubzu_property(item: dict) -> dict[str, Any]:
    """Convert Hubzu property to InvestorFlip format."""
    address = item.get("address", item.get("streetAddress", ""))
    city = item.get("city", "")
    state = item.get("state", "TX")
    zip_code = item.get("zip", item.get("zipCode", ""))
    price = item.get("price", item.get("startingBid", item.get("auctionPrice")))
    beds = item.get("beds", item.get("bedrooms"))
    baths = item.get("baths", item.get("bathrooms"))
    sqft = item.get("sqft", item.get("squareFeet"))
    
    return {
        "address": f"{address}, {city}, {state} {zip_code}".strip(", "),
        "situs_address": address,
        "city": city,
        "state": state,
        "zip": zip_code,
        "price": int(price) if price else None,
        "beds": beds,
        "baths": baths,
        "sqft": int(sqft) if sqft else None,
        "listing_type": "REO Auction",
        "data_source": "Hubzu.com",
        "source_platform": "Hubzu",
        "is_live_listing": True,
        "distress_score": 60,  # Bank-owned = motivated
    }


def parse_hubzu_html(html: str, city: str, state: str) -> list[dict[str, Any]]:
    """Parse Hubzu HTML as fallback."""
    listings = []
    addr_pattern = re.compile(
        r'(\d+\s+[A-Za-z0-9\s\.]+(?:St|Ave|Rd|Dr|Ln|Ct|Cir|Blvd|Pl|Way|Ter|Trl|Pkwy))',
        re.I
    )
    for m in addr_pattern.finditer(html):
        listings.append({
            "address": f"{m.group(1).strip()}, {city}, {state}",
            "city": city,
            "state": state,
            "listing_type": "REO Auction",
            "data_source": "Hubzu.com",
            "source_platform": "Hubzu",
            "is_live_listing": True,
            "distress_score": 60,
        })
    return listings


# ─── MAIN INGESTION PIPELINE ──────────────────────────────────────────────────
async def fetch_all_pre_foreclosure_leads(
    days_back: int = 30,
    include_fsbo: bool = True,
    include_hubzu: bool = True,
) -> list[dict[str, Any]]:
    """
    Fetch pre-foreclosure + FSBO + REO leads from ALL sources in parallel.
    Returns deduplicated list sorted by distress score.
    """
    tasks = [
        ("county_clerk", fetch_county_clerk_pre_foreclosure(days_back)),
    ]
    
    if include_fsbo:
        tasks.append(("fsbo", fetch_fsbo_listings("fort-worth-tx", max_pages=3)))
    
    if include_hubzu:
        tasks.append(("hubzu", fetch_hubzu_auctions("Fort Worth", "TX", max_pages=2)))
    
    # Run all in parallel
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
    
    # Filter to Fort Worth / Tarrant County only
    fw_leads = [l for l in all_leads if is_fort_worth_area(l.get("city", l.get("address", "")))]
    
    # Deduplicate by address
    seen = set()
    unique = []
    for lead in fw_leads:
        addr = lead.get("address", lead.get("situs_address", "")).lower().strip()
        if addr and addr not in seen:
            seen.add(addr)
            unique.append(lead)
    
    # Sort by distress score (highest first)
    unique.sort(key=lambda x: x.get("distress_score", 0), reverse=True)
    
    logger.info(f"Total: {len(unique)} unique Fort Worth leads")
    return unique


async def import_brightdata_deals(
    db,
    days_back: int = 30,
) -> dict[str, Any]:
    """
    Main import: fetch all pre-foreclosure + FSBO + REO leads and save to DB.
    Cross-references with TAD data to enrich with owner info.
    """
    logger.info(f"Bright Data deal finder: last {days_back} days")
    
    leads = await fetch_all_pre_foreclosure_leads(days_back=days_back)
    
    if not leads:
        return {
            "imported": 0,
            "source": "brightdata_deal_finder",
            "status": "no_leads_found",
            "days_back": days_back,
        }
    
    imported = 0
    enriched = 0
    
    for lead in leads:
        address = lead.get("address") or lead.get("situs_address", "")
        if not address:
            continue
        
        # Normalize address
        prop = {
            "situs_address": address.upper().strip(),
            "city": lead.get("city", "Fort Worth"),
            "state": lead.get("state", "TX"),
            "zip": lead.get("zip", ""),
            "price": lead.get("price"),
            "beds": lead.get("beds"),
            "baths": lead.get("baths"),
            "sqft": lead.get("sqft"),
            "listing_type": lead.get("listing_type", "Pre-Foreclosure"),
            "pre_foreclosure": lead.get("pre_foreclosure", False),
            "data_source": lead.get("data_source", "Bright Data"),
            "source_platform": lead.get("source_platform", "Bright Data"),
            "is_live_listing": lead.get("is_live_listing", False),
            "distress_score": lead.get("distress_score", 50),
            "updated_at": datetime.utcnow().isoformat(),
            "opportunity_signals": [],
        }
        
        # Add signals based on type
        if lead.get("pre_foreclosure"):
            prop["opportunity_signals"].append("Pre-Foreclosure")
        if lead.get("listing_type") == "FSBO":
            prop["opportunity_signals"].append("FSBO - No Agent")
        if lead.get("listing_type") == "REO Auction":
            prop["opportunity_signals"].append("Bank-Owned Auction")
        
        # Add instrument info if from county clerk
        if lead.get("instrument_num"):
            prop["county_record_id"] = lead["instrument_num"]
        if lead.get("owner_name"):
            prop["owner_name"] = lead["owner_name"]
        if lead.get("filing_date"):
            prop["listing_date"] = lead["filing_date"]
        
        try:
            # Upsert by situs_address
            await db.properties.upsert_one(prop, ["situs_address"])
            imported += 1
            
            # Try to enrich with TAD data (owner, mailing, equity)
            tad_enriched = await enrich_with_tad(db, prop["situs_address"])
            if tad_enriched:
                enriched += 1
                
        except Exception as e:
            logger.debug(f"Failed to import {address}: {e}")
    
    logger.info(f"Bright Data: {imported} imported, {enriched} enriched with TAD")
    return {
        "imported": imported,
        "enriched_with_tad": enriched,
        "total_found": len(leads),
        "source": "brightdata_deal_finder",
        "days_back": days_back,
        "status": "success",
    }


async def enrich_with_tad(db, situs_address: str) -> bool:
    """
    Cross-reference new lead with TAD data to add owner/mailing/equity info.
    TAD = Tarrant Appraisal District — official county tax records.
    """
    try:
        # Search TAD properties by address
        existing = await db.properties.find_one({
            "situs_address": {"$regex": f"^{re.escape(situs_address[:20])}", "$options": "i"},
            "owner_name": {"$exists": True, "$ne": ""},
        })
        if existing:
            update = {
                "owner_name": existing.get("owner_name"),
                "owner_mailing_address": existing.get("owner_mailing_address"),
                "assessed_value": existing.get("assessed_value"),
                "market_value": existing.get("market_value"),
                "tad_id": existing.get("tad_id"),
            }
            update = {k: v for k, v in update.items() if v}
            if update:
                await db.properties.update_one(
                    {"situs_address": situs_address.upper().strip()},
                    {"$set": update}
                )
                return True
    except Exception as e:
        logger.debug(f"TAD enrichment failed for {situs_address}: {e}")
    return False


# ─── CLI TEST ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    
    async def test():
        print("Testing Bright Data deal finder...")
        leads = await fetch_all_pre_foreclosure_leads(days_back=30)
        print(f"\n=== {len(leads)} LEADS ===")
        for lead in leads[:10]:
            print(f"  {lead.get('distress_score', 0):3d} | "
                  f"{lead.get('listing_type', '?'):20s} | "
                  f"{lead.get('address', '?')}")
    
    asyncio.run(test())
