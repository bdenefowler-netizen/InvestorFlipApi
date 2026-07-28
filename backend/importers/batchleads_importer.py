"""BatchLeads API importer — pulls wholesale leads and distressed properties.

BatchLeads has an official API for real estate investors.
API Documentation: https://developer.batchservice.com

To use this importer, you need a BatchLeads API key:
1. Sign up at https://batchleads.io
2. Get your API key from the dashboard
3. Set BATCHLEADS_API_KEY in your environment

The API provides:
- Property search with filters
- Skip tracing (contact info)
- Owner information
- Foreclosure data
- Tax delinquent properties
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger("tarrantrei.batchleads")

# BatchLeads API configuration
BATCHLEADS_API_KEY = os.environ.get("BATCHLEADS_API_KEY", "").strip()
BATCHLEADS_BASE_URL = "https://api.batchservice.com"

# Fort Worth, TX area filters
FORT_WORTH_FILTERS = {
    "city": "Fort Worth",
    "state": "TX",
    "county": "Tarrant",
}


def _auth_headers() -> Dict[str, str]:
    """Return authentication headers for BatchLeads API."""
    return {
        "Authorization": f"Bearer {BATCHLEADS_API_KEY}",
        "Content-Type": "application/json",
    }


def batchleads_status() -> Dict[str, Any]:
    """Check if BatchLeads API is configured and available."""
    return {
        "provider": "BatchLeads",
        "api_key_configured": bool(BATCHLEADS_API_KEY),
        "ready": bool(BATCHLEADS_API_KEY),
    }


async def search_properties(
    filters: Optional[Dict[str, Any]] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """Search for properties using BatchLeads API.
    
    Common filters:
    - city, state, county, zip
    - property_type (single_family, multi_family, etc.)
    - owner_type (individual, corporate, etc.)
    - equity_percentage (min equity)
    - days_on_market
    - foreclosure_status
    - tax_delinquent (true/false)
    - vacancy_status (true/false)
    """
    if not BATCHLEADS_API_KEY:
        logger.warning("BatchLeads API key not configured")
        return []
    
    search_filters = {**FORT_WORTH_FILTERS}
    if filters:
        search_filters.update(filters)
    
    payload = {
        "filters": search_filters,
        "limit": limit,
        "offset": 0,
    }
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{BATCHLEADS_BASE_URL}/api/v1/properties/search",
                headers=_auth_headers(),
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
            
            properties = data.get("properties", [])
            logger.info("BatchLeads: Found %d properties", len(properties))
            return properties
            
    except httpx.HTTPStatusError as e:
        logger.error("BatchLeads API error: %s", e.response.status_code)
        return []
    except Exception as e:
        logger.error("BatchLeads search failed: %s", e)
        return []


async def get_property_details(property_id: str) -> Optional[Dict[str, Any]]:
    """Get detailed property information from BatchLeads."""
    if not BATCHLEADS_API_KEY:
        return None
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{BATCHLEADS_BASE_URL}/api/v1/properties/{property_id}",
                headers=_auth_headers(),
            )
            response.raise_for_status()
            return response.json()
            
    except Exception as e:
        logger.error("BatchLeads property details failed: %s", e)
        return None


async def skip_trace(address: str, owner_name: str = "") -> Dict[str, Any]:
    """Run skip tracing to get contact information.
    
    Returns phone numbers, emails, and other contact info for the property owner.
    """
    if not BATCHLEADS_API_KEY:
        return {"error": "API key not configured"}
    
    payload = {
        "address": address,
        "owner_name": owner_name,
    }
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{BATCHLEADS_BASE_URL}/api/v1/skip-trace",
                headers=_auth_headers(),
                json=payload,
            )
            response.raise_for_status()
            return response.json()
            
    except Exception as e:
        logger.error("BatchLeads skip trace failed: %s", e)
        return {"error": str(e)}


def build_batchleads_property(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Convert a BatchLeads property to InvestorFlip format."""
    return {
        "situs_address": raw.get("address") or raw.get("full_address", ""),
        "city": raw.get("city", "Fort Worth"),
        "state": raw.get("state", "TX"),
        "zip": raw.get("zip") or raw.get("postal_code", ""),
        "county": raw.get("county", "Tarrant"),
        "latitude": raw.get("latitude"),
        "longitude": raw.get("longitude"),
        
        # Property facts
        "beds": raw.get("bedrooms") or raw.get("beds"),
        "baths": raw.get("bathrooms") or raw.get("baths"),
        "sqft": raw.get("square_feet") or raw.get("sqft"),
        "year_built": raw.get("year_built"),
        "lot_size_sqft": raw.get("lot_size"),
        "property_type": raw.get("property_type", "Single Family Residential"),
        
        # Pricing
        "price": raw.get("price") or raw.get("list_price") or 0,
        "market_value": raw.get("estimated_value") or raw.get("avm"),
        "assessed_value": raw.get("assessed_value"),
        "annual_taxes": raw.get("annual_taxes"),
        
        # Owner info
        "owner_name": raw.get("owner_name") or raw.get("owner", ""),
        "owner_type": raw.get("owner_type", "Unknown"),
        "owner_mailing_address": raw.get("mailing_address", ""),
        "out_of_state_owner": raw.get("out_of_state", False),
        "tax_delinquent": raw.get("tax_delinquent", False),
        "vacant": raw.get("vacant", False),
        "high_equity": raw.get("high_equity", False),
        "cash_buyer": raw.get("cash_buyer", False),
        "investor_owned": raw.get("investor_owned", False),
        
        # Listing info
        "listing_type": "Wholesale",
        "data_source": "BatchLeads",
        "source_platform": "BatchLeads",
        
        # Distress indicators
        "distress_score": raw.get("distress_score"),
        "violation_count": raw.get("violation_count"),
        
        # Image
        "image_url": raw.get("photo_url") or raw.get("image"),
        
        # Metadata
        "is_synthetic": False,
    }
