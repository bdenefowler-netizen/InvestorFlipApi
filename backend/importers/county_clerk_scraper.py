"""
County Clerk Lis Pendens / Pre-Foreclosure Scraper via Bright Data Web Unlocker.

Uses Bright Data's Web Unlocker REST API to bypass Cloudflare protection
on the Tarrant County Clerk public search portal and scrape Lis Pendens filings.

Bright Data Web Unlocker API format:
  GET https:// Brigdata.com/gdpr?url=<encoded_url>&api_key=<token>&country=us&zone=<zone>&mobile=0

Cost: ~10-25 credits per page load (Web Unlocker premium proxy)
But: We use the MCP tools instead for efficiency.
Alternatively: use Bright Data's scrape API directly.

For now, this script uses Bright Data's REST Web Unlocker.
Get your zone from: Bright Data dashboard → Proxy & Scraping → Zones
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import urllib.parse
from datetime import datetime
from typing import Any

import httpx

logger = logging.getLogger("county_clerk")

# ─── Bright Data Configuration ────────────────────────────────────────────────
# Web Unlocker REST API — Bright Data's scraping browser proxy
BRIGHT_DATA_TOKEN = "5809d6b4-75ec-44a2-83b2-dc98972e4727"
BRIGHT_DATA_ZONE  = os.environ.get("BRIGHT_DATA_ZONE", "")  # e.g. "zone_name:password"

# Tarrant County Clerk search base URL
TARRANT_BASE = "https://tarrant.tx.publicsearch.us/"
SEARCH_API   = "https:// Brigdata.com/gdpr"  # Web Unlocker endpoint (space added to prevent link)


def _build_unlocker_url(target_url: str, country: str = "us") -> str:
    """Build Bright Data Web Unlocker URL with target embedded."""
    encoded = urllib.parse.quote(target_url, safe="")
    zone = BRIGHT_DATA_ZONE or ""
    return f"{SEARCH_API}?url={encoded}&api_key={BRIGHT_DATA_TOKEN}&country={country}&zone={zone}"


async def scrape_with_brightdata(url: str, prompt: str = "") -> str:
    """
    Scrape a URL using Bright Data Web Unlocker.
    Falls back to plain httpx if Web Unlocker isn't configured.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }

    if BRIGHT_DATA_ZONE:
        # Use Bright Data Web Unlocker
        unlocker_url = _build_unlocker_url(url)
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            resp = await client.get(unlocker_url, headers=headers)
            resp.raise_for_status()
            return resp.text
    else:
        # Fallback: try plain request with TLS spoofing
        try:
            import ssl
            import curl_cffi
            async with httpx.AsyncClient(
                timeout=30.0,
                follow_redirects=True,
                headers=headers,
            ) as client:
                resp = await client.get(url, headers=headers)
                resp.raise_for_status()
                return resp.text
        except Exception:
            # Last resort: use Bright Data scrape API
            return await _brightdata_scrape_api(url)


async def _brightdata_scrape_api(url: str) -> str:
    """
    Use Bright Data's scrape API directly.
    Endpoint: https:// .brightdata.com/api/v1/scrape
    Requires: zone configured with scraping browser enabled.
    """
    scrape_url = "https://www.brightdata.com/api/v1/scrape"
    payload = {
        "url": url,
        "country": "us",
        "zone": BRIGHT_DATA_ZONE,
        "format": "raw",
    }
    headers = {
        "Authorization": f"Bearer {BRIGHT_DATA_TOKEN}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(scrape_url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            return data.get("content", "")
    except Exception as e:
        logger.error(f"Bright Data scrape API failed: {e}")
        return ""


async def search_county_clerk(keyword: str, days_back: int = 30) -> list[dict[str, Any]]:
    """
    Search Tarrant County Clerk for a keyword and parse results.
    
    Keyword ideas for pre-foreclosure:
      - "lis pendens"
      - "notice of default"  
      - "foreclosure deed"
      - "substitute trustee"
    """
    from database import PostgresDatabase

    date_map = {7: "7D", 30: "30D", 90: "90D", 180: "6M", 365: "1Y"}
    date_param = date_map.get(days_back, "30D")
    
    # Build the search URL (Tarrant County Clerk accepts query params)
    keyword_enc = urllib.parse.quote(keyword)
    search_url = (
        f"{TARRANT_BASE}?"
        f"searchTerm={keyword_enc}"
        f"&dateRange={date_param}"
        f"&searchOcrText=false"
        f"&department=RP"
    )
    
    logger.info(f"County clerk search: {keyword} (last {days_back} days)")
    
    try:
        content = await scrape_with_brightdata(
            search_url,
            prompt=(
                f"Extract all real property records from this search for '{keyword}'. "
                "For each record, extract: (1) instrument/document number, "
                "(2) date recorded, (3) document type, (4) grantor (borrower/owner name), "
                "(5) grantee (lender name), (6) property address or legal description. "
                "Return in structured format, one record per line."
            )
        )
        
        if not content:
            logger.warning(f"No content from county clerk for: {keyword}")
            return []
        
        records = _parse_records(content)
        logger.info(f"Found {len(records)} records for: {keyword}")
        return records
        
    except Exception as e:
        logger.error(f"County clerk search failed for '{keyword}': {e}")
        return []


def _parse_records(content: str) -> list[dict[str, Any]]:
    """Parse county clerk search results from HTML/text content."""
    records = []
    lines = [l.strip() for l in content.split('\n') if l.strip()]
    
    current = {}
    for line in lines:
        # Instrument number: e.g. D12345678 or 00-000000000001234
        if not current.get('instrument_num'):
            m = re.search(r'\b(D?\d{8,})\b', line)
            if m:
                current['instrument_num'] = m.group(1)
        
        # Date: MM/DD/YYYY or YYYY-MM-DD
        if not current.get('filing_date'):
            m = re.search(r'\b(\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4})\b', line)
            if m:
                current['filing_date'] = m.group(1)
        
        # Document type keywords
        if not current.get('instrument_type'):
            lower = line.lower()
            if any(k in lower for k in ['lis pendens', 'notice of default', 
                    'notice of trustee', 'substitute trustee', 'foreclosure']):
                # Find the type phrase
                for phrase in ['Lis Pendens', 'Notice of Default', 'Notice of Trustee',
                               'Substitute Trustee', 'Foreclosure']:
                    if phrase.lower() in lower:
                        current['instrument_type'] = phrase
                        break
        
        # Owner/grantor name (cap words)
        if not current.get('owner_name'):
            m = re.search(r'Grantor[:\s]+([A-Z][A-Z\s,\.]+)', line)
            if m:
                name = m.group(1).strip().rstrip(',.')
                if len(name) > 2 and len(name) < 60:
                    current['owner_name'] = name
        
        # Address detection (number + street name pattern)
        if not current.get('address'):
            m = re.search(
                r'\b(\d+\s+[A-Z][A-Z\s]+(?:ST|AVENUE|AVE|ROAD|RD|DRIVE|DR|LANE|LN|COURT|CT|CIRCLE|CIR|BOULEVARD|BLVD|PLACE|PL|WAY|TERRACE|TER))\b',
                line, re.I
            )
            if m:
                addr = m.group(1).strip()
                if len(addr) > 5:
                    current['address'] = addr
        
        # End of record marker or new record
        if '|' in line or '---' in line or 'Document' in line:
            if current and (current.get('instrument_num') or current.get('address')):
                records.append(current)
                current = {}
    
    if current and (current.get('instrument_num') or current.get('address')):
        records.append(current)
    
    return records


async def fetch_pre_foreclosure_leads(days_back: int = 30) -> list[dict[str, Any]]:
    """
    Fetch all pre-foreclosure lead types from county clerk.
    These are the BEST leads because they're public record and
    identify owners BEFORE the auction happens.
    """
    keywords = [
        "lis pendens",
        "notice of default", 
        "notice of trustee's sale",
        "substitute trustee",
        "foreclosure deed of trust",
    ]
    
    all_records = []
    for kw in keywords:
        records = await search_county_clerk(kw, days_back=days_back)
        all_records.extend(records)
    
    # Deduplicate by instrument number
    seen = set()
    unique = []
    for r in all_records:
        key = r.get('instrument_num', r.get('address', ''))
        if key and key not in seen:
            seen.add(key)
            unique.append(r)
    
    return unique


async def import_county_clerk_records(
    db: PostgresDatabase,
    days_back: int = 30,
) -> dict[str, Any]:
    """
    Main import function: fetch county clerk records and save to DB.
    Cross-references with existing properties to avoid duplicates.
    """
    logger.info(f"Importing county clerk records (last {days_back} days)...")
    
    records = await fetch_pre_foreclosure_leads(days_back=days_back)
    
    if not records:
        return {
            "imported": 0,
            "source": "county_clerk",
            "status": "no_records_found",
            "days_back": days_back,
        }
    
    imported = 0
    for rec in records:
        address = rec.get('address', '')
        if not address:
            continue
        
        # Build investorflip property record
        prop = {
            "situs_address": address.upper().strip(),
            "owner_name": rec.get('owner_name', ''),
            "listing_type": "Pre-Foreclosure",
            "pre_foreclosure": True,
            "source_platform": "Tarrant County Clerk",
            "data_source": "Tarrant County Clerk Official Records",
            "listing_status": rec.get('instrument_type', 'Lis Pendens'),
            "legal_description": rec.get('legal_description', ''),
            "listing_agent_name": rec.get('grantee', ''),  # lender
            "listing_date": rec.get('filing_date', ''),
            "county_record_id": rec.get('instrument_num', ''),
            "updated_at": datetime.utcnow().isoformat(),
            "is_live_listing": False,
            "distress_score": 75,  # Lis Pendens = high distress
            "opportunity_signals": ["Pre-Foreclosure", "Lis Pendens Filed"],
        }
        
        try:
            await db.properties.upsert_one(prop, ["situs_address"])
            imported += 1
        except Exception as e:
            logger.debug(f"Failed to import {address}: {e}")
    
    logger.info(f"County clerk: imported {imported}/{len(records)} records")
    return {
        "imported": imported,
        "total_found": len(records),
        "source": "county_clerk",
        "days_back": days_back,
        "status": "success",
    }


# ─── CLI ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    
    async def test():
        print("Testing county clerk scraper...")
        # Quick test without DB
        results = await search_county_clerk("lis pendens", days_back=30)
        print(f"Results: {len(results)}")
        for r in results[:10]:
            print(f"  {r}")
    
    asyncio.run(test())
