"""Free skip tracing using public data sources.

This module provides owner contact information lookup using:
1. TAD (Tarrant Appraisal District) - FREE public data
2. Fort Worth ArcGIS - FREE public data
3. Texas Secretary of State - FREE business entity search

No API keys or subscriptions required.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger("tarrantrei.skip_trace")


async def lookup_owner_from_tad(address: str) -> Dict[str, Any]:
    """Look up owner contact info from TAD public data."""
    from importers.tad_scraper import search_tad_by_address
    
    try:
        results = await search_tad_by_address(address)
        
        if not results:
            return {"found": False, "source": "TAD", "address": address}
        
        # Get the first matching record
        record = results[0]
        
        owner_name = (record.get("OWNER_NAME") or "").strip()
        mail_addr = (record.get("MAIL_ADDR") or "").strip()
        mail_city = (record.get("MAIL_CITY") or "").strip()
        mail_state = (record.get("MAIL_STATE") or "").strip()
        mail_zip = (record.get("MAIL_ZIP") or "").strip()[:5]
        
        mailing_address = f"{mail_addr}, {mail_city}, {mail_state} {mail_zip}".strip(", ")
        
        return {
            "found": True,
            "source": "TAD",
            "address": address,
            "owner_name": owner_name,
            "mailing_address": mailing_address,
            "mail_city": mail_city,
            "mail_state": mail_state,
            "mail_zip": mail_zip,
            "out_of_state": mail_state.upper() != "TX" if mail_state else False,
            "parcel_id": record.get("TAXPIN"),
            "assessed_value": record.get("TOTALASSESSED"),
        }
    except Exception as e:
        logger.warning("TAD lookup failed: %s", e)
        return {"found": False, "source": "TAD", "error": str(e)}


async def lookup_owner_from_fort_worth(address: str) -> Dict[str, Any]:
    """Look up owner info from Fort Worth ArcGIS (code violations data)."""
    url = (
        "https://mapit.fortworthtexas.gov/ags/rest/services/"
        "CIVIC/Code_Violations_Experience_Builder/MapServer/4/query"
    )
    
    params = {
        "where": f"Address LIKE '%{address.upper()}%'",
        "outFields": "Address,Case_ID,Code_Officer,Case_Current_Status",
        "resultRecordCount": "10",
        "f": "json",
    }
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            data = response.json()
        
        features = data.get("features", [])
        
        if not features:
            return {"found": False, "source": "Fort Worth", "address": address}
        
        return {
            "found": True,
            "source": "Fort Worth",
            "address": address,
            "code_violations": len(features),
            "case_id": features[0].get("attributes", {}).get("Case_ID"),
            "officer": features[0].get("attributes", {}).get("Code_Officer"),
            "status": features[0].get("attributes", {}).get("Case_Current_Status"),
        }
    except Exception as e:
        logger.warning("Fort Worth lookup failed: %s", e)
        return {"found": False, "source": "Fort Worth", "error": str(e)}


async def lookup_business_entity(owner_name: str) -> Dict[str, Any]:
    """Look up business entity from Texas Secretary of State (for LLCs/Corps)."""
    # Texas SOS business search
    url = "https://mycpa.cpa.state.tx.us/coa/coaSearchResults"
    
    params = {
        "searchText": owner_name,
        "searchType": "OrgName",
        "searchSubType": "OrgName",
    }
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, params=params, follow_redirects=True)
            # Note: This may require more complex parsing
            # For now, return basic info
            pass
    except Exception as e:
        logger.warning("SOS lookup failed: %s", e)
    
    return {"found": False, "source": "Texas SOS"}


async def skip_trace_property(address: str, owner_name: str = "") -> Dict[str, Any]:
    """Perform a comprehensive skip trace using free public sources.
    
    Combines data from multiple free sources to build a complete owner profile.
    """
    results = {
        "address": address,
        "sources_queried": [],
        "owner_name": owner_name,
        "mailing_address": None,
        "phone_numbers": [],  # Would need additional sources
        "emails": [],  # Would need additional sources
        "property_info": {},
        "owner_info": {},
    }
    
    # 1. TAD lookup (primary source)
    tad_result = await lookup_owner_from_tad(address)
    results["sources_queried"].append("TAD")
    
    if tad_result.get("found"):
        results["owner_name"] = tad_result.get("owner_name") or owner_name
        results["mailing_address"] = tad_result.get("mailing_address")
        results["owner_info"]["tad"] = tad_result
        results["property_info"]["parcel_id"] = tad_result.get("parcel_id")
        results["property_info"]["assessed_value"] = tad_result.get("assessed_value")
    
    # 2. Fort Worth violations lookup
    fw_result = await lookup_owner_from_fort_worth(address)
    results["sources_queried"].append("Fort Worth")
    
    if fw_result.get("found"):
        results["property_info"]["code_violations"] = fw_result.get("code_violations")
        results["property_info"]["case_id"] = fw_result.get("case_id")
        results["property_info"]["officer"] = fw_result.get("officer")
    
    # 3. Business entity lookup (if owner looks like a company)
    if owner_name and any(keyword in owner_name.upper() for keyword in ["LLC", "INC", "CORP", "CO"]):
        biz_result = await lookup_business_entity(owner_name)
        results["sources_queried"].append("Texas SOS")
        if biz_result.get("found"):
            results["owner_info"]["business"] = biz_result
    
    # Determine overall success
    results["found"] = bool(results["owner_name"] or results["mailing_address"])
    
    return results


# CLI entry point
if __name__ == "__main__":
    import asyncio
    import sys
    
    async def main():
        if len(sys.argv) < 2:
            print("Usage: python free_skip_trace.py <address>")
            sys.exit(1)
        
        address = " ".join(sys.argv[1:])
        result = await skip_trace_property(address)
        
        import json
        print(json.dumps(result, indent=2))
    
    asyncio.run(main())
