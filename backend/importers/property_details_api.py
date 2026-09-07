"""
RapidAPI Real-Time Real Estate Data Mega - Property Details Enricher
Calls: https://real-time-real-estate-data-mega.p.rapidapi.com/property-details-address

Provides rich property data: appliances, builder, HOA, schools, comparables, agent info, etc.
"""
import os
import logging
import asyncio
from typing import Dict, Any, Optional, List

logger = logging.getLogger(__name__)

RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY")
RAPIDAPI_HOST = "real-time-real-estate-data-mega.p.rapidapi.com"
BASE_URL = "https://real-time-real-estate-data-mega.p.rapidapi.com"

# Cache for property details (5 min TTL)
_property_details_cache: Dict[str, tuple[float, Dict[str, Any]]] = {}
CACHE_TTL = 300  # 5 minutes


async def fetch_property_details(address: str) -> Optional[Dict[str, Any]]:
    """
    Fetch full property details from RapidAPI.
    Returns enriched data dict or None on failure.
    """
    if not RAPIDAPI_KEY:
        logger.warning("RAPIDAPI_KEY not set — skipping property details enrichment")
        return None

    cache_key = address.lower().strip()
    now = __import__("time").monotonic()
    
    # Check cache
    if cache_key in _property_details_cache:
        cached_time, cached_data = _property_details_cache[cache_key]
        if now - cached_time < CACHE_TTL:
            logger.debug(f"Property details cache hit: {address}")
            return cached_data

    import httpx
    url = f"{BASE_URL}/property-details-address"
    params = {"address": address}
    headers = {
        "x-rapidapi-key": RAPIDAPI_KEY,
        "x-rapidapi-host": RAPIDAPI_HOST,
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, params=params, headers=headers)
            if resp.status_code == 429:
                logger.warning(f"Property details rate limited for {address}")
                return None
            resp.raise_for_status()
            data = resp.json()
            
            if data.get("status") != "OK" or "data" not in data:
                logger.warning(f"Property details API error: {data}")
                return None

            enriched = _parse_property_details(data["data"])
            
            # Cache it
            _property_details_cache[cache_key] = (now, enriched)
            # Prune old cache entries
            if len(_property_details_cache) > 500:
                oldest = min(_property_details_cache, key=lambda k: _property_details_cache[k][0])
                del _property_details_cache[oldest]
            
            return enriched

    except httpx.TimeoutException:
        logger.warning(f"Property details timeout for {address}")
        return None
    except Exception as e:
        logger.error(f"Property details error for {address}: {e}")
        return None


def _parse_property_details(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Extract and structure the fields we care about."""
    reso = raw.get("resoFacts", {})
    attrib = raw.get("attributionInfo", {})
    address_info = raw.get("address", {})
    listing_sub = raw.get("listing_sub_type", {})
    
    # Extract agents
    agents = []
    for agent in attrib.get("listingAgents", []):
        if agent.get("memberFullName"):
            agents.append({
                "name": agent["memberFullName"],
                "type": agent.get("associatedAgentType"),
                "license": agent.get("memberStateLicense"),
                "phone": agent.get("memberPhoneNumber") or attrib.get("agentPhoneNumber"),
                "email": agent.get("memberEmail"),
            })
    
    # Extract comparables
    comps = []
    collections = raw.get("collections", {})
    for module in collections.get("modules", []):
        if module.get("name") == "Similar homes":
            for prop in module.get("propertyDetails", [])[:5]:
                comps.append({
                    "address": prop.get("address", {}).get("streetAddress"),
                    "city": prop.get("address", {}).get("city"),
                    "state": prop.get("address", {}).get("state"),
                    "zipcode": prop.get("address", {}).get("zipcode"),
                    "price": prop.get("price"),
                    "bedrooms": prop.get("bedrooms"),
                    "bathrooms": prop.get("bathrooms"),
                    "livingArea": prop.get("livingArea"),
                    "lotSize": prop.get("lotSize"),
                    "homeStatus": prop.get("homeStatus"),
                    "zpid": prop.get("zpid"),
                })

    return {
        # Core identifiers
        "zpid": raw.get("zpid"),
        "listing_id": raw.get("listingId"),
        "mls_id": attrib.get("mlsId"),
        "mls_name": attrib.get("mlsName"),
        
        # Address
        "street_address": address_info.get("streetAddress"),
        "city": address_info.get("city"),
        "state": address_info.get("state"),
        "zipcode": address_info.get("zipcode"),
        "subdivision": address_info.get("subdivision"),
        "neighborhood": address_info.get("neighborhood"),
        
        # Basic property
        "price": raw.get("price"),
        "home_status": raw.get("homeStatus"),
        "home_type": raw.get("homeType"),
        "bedrooms": raw.get("bedrooms"),
        "bathrooms": raw.get("bathrooms"),
        "living_area": raw.get("livingArea"),
        "lot_size": reso.get("lotSize"),
        "lot_size_sqft": reso.get("lotSizeDimensions"),
        "year_built": raw.get("yearBuilt") or reso.get("yearBuilt"),
        "stories": reso.get("stories"),
        "price_per_sqft": reso.get("pricePerSquareFoot"),
        
        # Financial
        "tax_annual_amount": reso.get("taxAnnualAmount"),
        "tax_assessed_value": reso.get("taxAssessedValue"),
        "hoa_fee": reso.get("hoaFee") or reso.get("associationFee"),
        "hoa_fee_total": reso.get("hoaFeeTotal"),
        
        # Features
        "appliances": reso.get("appliances", []),
        "interior_features": reso.get("interiorFeatures", []),
        "exterior_features": reso.get("exteriorFeatures", []),
        "community_features": reso.get("communityFeatures", []),
        "construction_materials": reso.get("constructionMaterials", []),
        "fencing": reso.get("fencing"),
        "fireplace_features": reso.get("fireplaceFeatures", []),
        "flooring": reso.get("flooring", []),
        "foundation_details": reso.get("foundationDetails", []),
        "roof_type": reso.get("roofType"),
        "heating": reso.get("heating", []),
        "cooling": reso.get("cooling", []),
        "parking_features": reso.get("parkingFeatures", []),
        "garage_capacity": reso.get("garageParkingCapacity"),
        "covered_parking": reso.get("coveredParkingCapacity"),
        "pool_features": reso.get("poolFeatures", []),
        "security_features": reso.get("securityFeatures", []),
        "utilities": reso.get("utilities", []),
        "window_features": reso.get("windowFeatures", []),
        "green_energy": reso.get("greenEnergyEfficient", []),
        
        # Structure details
        "architectural_style": reso.get("architecturalStyle"),
        "builder_name": reso.get("builderName"),
        "builder_model": reso.get("builderModel"),
        "property_sub_type": reso.get("propertySubType", []),
        "levels": reso.get("levels"),
        "basement": reso.get("basement"),
        "attic": reso.get("attic"),
        "patio_porch_features": reso.get("patioAndPorchFeatures", []),
        "laundry_features": reso.get("laundryFeatures", []),
        "other_equipment": reso.get("otherEquipment", []),
        "other_structures": reso.get("otherStructures", []),
        
        # Schools
        "elementary_school": reso.get("elementarySchool"),
        "elementary_district": reso.get("elementarySchoolDistrict"),
        "middle_school": reso.get("middleOrJuniorSchool"),
        "middle_district": reso.get("middleOrJuniorSchoolDistrict"),
        "high_school": reso.get("highSchool"),
        "high_district": reso.get("highSchoolDistrict"),
        
        # Listing info
        "listing_terms": reso.get("listingTerms"),
        "on_market_date": reso.get("onMarketDate"),
        "cumulative_dom": reso.get("cumulativeDaysOnMarket"),
        "listing_provider": raw.get("listingProvider"),
        "listing_data_source": raw.get("listingDataSource"),
        
        # Listing type flags
        "is_new_home": listing_sub.get("is_newHome"),
        "is_fsbo": listing_sub.get("is_FSBO"),
        "is_bank_owned": listing_sub.get("is_bankOwned"),
        "is_foreclosure": listing_sub.get("is_foreclosure"),
        "is_for_auction": listing_sub.get("is_forAuction"),
        "is_coming_soon": listing_sub.get("is_comingSoon"),
        "is_pending": listing_sub.get("is_pending"),
        
        # Agents (with phone numbers!)
        "agents": agents,
        "broker_name": attrib.get("brokerName"),
        "broker_phone": attrib.get("brokerPhoneNumber"),
        
        # Comparables
        "comparables": comps,
        
        # Raw for reference
        "_raw_reso_facts": reso,
    }


async def enrich_properties_batch(properties: List[Dict[str, Any]], max_concurrent: int = 3) -> Dict[str, int]:
    """
    Enrich multiple properties with property details.
    Returns counts: {"enriched": n, "skipped": m, "failed": k}
    """
    if not RAPIDAPI_KEY:
        logger.info("RAPIDAPI_KEY not set — skipping batch property details enrichment")
        return {"enriched": 0, "skipped": len(properties), "failed": 0}
    
    semaphore = asyncio.Semaphore(max_concurrent)
    results = {"enriched": 0, "skipped": 0, "failed": 0}
    
    async def enrich_one(prop: Dict[str, Any]):
        async with semaphore:
            address = prop.get("situs_address") or prop.get("address")
            if not address:
                results["skipped"] += 1
                return
            
            # Skip if already has rich data
            if prop.get("zpid") and prop.get("appliances"):
                results["skipped"] += 1
                return
            
            try:
                details = await fetch_property_details(address)
                if details:
                    # Merge into property
                    prop.update(details)
                    results["enriched"] += 1
                    logger.info(f"Enriched property with property details: {address}")
                else:
                    results["failed"] += 1
            except Exception as e:
                logger.error(f"Batch enrich error for {address}: {e}")
                results["failed"] += 1
    
    await asyncio.gather(*[enrich_one(p) for p in properties])
    return results


# Convenience function for single address lookup (calculator)
async def get_property_details_for_calculator(address: str) -> Dict[str, Any]:
    """Get property details formatted for the calculator screen."""
    details = await fetch_property_details(address)
    if not details:
        return {"error": "Could not fetch property details"}
    
    return {
        "address": f"{details.get('street_address', '')}, {details.get('city', '')}, {details.get('state', '')} {details.get('zipcode', '')}".strip(", "),
        "price": details.get("price"),
        "arv_estimate": details.get("price"),  # Use listing price as ARV starting point
        "bedrooms": details.get("bedrooms"),
        "bathrooms": details.get("bathrooms"),
        "living_area": details.get("living_area"),
        "lot_size": details.get("lot_size"),
        "year_built": details.get("year_built"),
        "stories": details.get("stories"),
        "hoa_fee": details.get("hoa_fee"),
        "tax_annual": details.get("tax_annual_amount"),
        "tax_assessed": details.get("tax_assessed_value"),
        "home_status": details.get("home_status"),
        "home_type": details.get("home_type"),
        "subdivision": details.get("subdivision"),
        "appliances": details.get("appliances"),
        "interior_features": details.get("interior_features"),
        "exterior_features": details.get("exterior_features"),
        "community_features": details.get("community_features"),
        "construction_materials": details.get("construction_materials"),
        "fencing": details.get("fencing"),
        "roof_type": details.get("roof_type"),
        "heating": details.get("heating"),
        "cooling": details.get("cooling"),
        "garage_capacity": details.get("garage_capacity"),
        "pool_features": details.get("pool_features"),
        "schools": {
            "elementary": details.get("elementary_school"),
            "middle": details.get("middle_school"),
            "high": details.get("high_school"),
        },
        "agents": details.get("agents"),
        "comparables": details.get("comparables"),
        "listing_terms": details.get("listing_terms"),
        "mls_id": details.get("mls_id"),
        "mls_name": details.get("mls_name"),
    }
