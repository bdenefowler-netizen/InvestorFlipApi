"""
TruePeopleSearch Skip Tracing API
Search by name → get person IDs → get phone + email for each

Calls:
1. GET /search/byname?name=... → Person IDs
2. GET /person_details_by_ID?id=... → Phone + email
"""
import os
import logging
import httpx
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY")
RAPIDAPI_HOST = "skip-tracing-working-api.p.rapidapi.com"
BASE_URL = "https://skip-tracing-working-api.p.rapidapi.com"

# Cache for skip trace results (1 hour TTL)
_skip_trace_cache: Dict[str, tuple[float, Dict[str, Any]]] = {}
CACHE_TTL = 3600  # 1 hour


def _headers() -> Dict[str, str]:
    return {
        "x-rapidapi-key": RAPIDAPI_KEY,
        "x-rapidapi-host": RAPIDAPI_HOST,
    }


async def search_by_name(name: str, page: int = 1) -> Optional[Dict[str, Any]]:
    """Search for people by name. Returns list of matches with Person IDs."""
    if not RAPIDAPI_KEY:
        logger.warning("RAPIDAPI_KEY not set — skipping skip trace")
        return None

    cache_key = f"search:{name.lower().strip()}:{page}"
    now = __import__("time").monotonic()
    if cache_key in _skip_trace_cache:
        cached_time, cached_data = _skip_trace_cache[cache_key]
        if now - cached_time < CACHE_TTL:
            return cached_data

    url = f"{BASE_URL}/search/byname"
    params = {"name": name, "page": str(page)}

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, params=params, headers=_headers())
            if resp.status_code == 429:
                logger.warning(f"Skip trace rate limited for {name}")
                return None
            resp.raise_for_status()
            data = resp.json()
            
            if data.get("Status") != 200 or "PeopleDetails" not in data:
                logger.warning(f"Skip trace search error for {name}: {data.get('Message', 'unknown')}")
                return None

            # Cache the full response
            _skip_trace_cache[cache_key] = (now, data)
            if len(_skip_trace_cache) > 500:
                oldest = min(_skip_trace_cache, key=lambda k: _skip_trace_cache[k][0])
                del _skip_trace_cache[oldest]

            return data

    except Exception as e:
        logger.error(f"Skip trace search error for {name}: {e}")
        return None


async def get_person_details(person_id: str) -> Optional[Dict[str, Any]]:
    """Get full details for a person by ID (phone, email, etc.)."""
    if not RAPIDAPI_KEY:
        return None

    cache_key = f"details:{person_id}"
    now = __import__("time").monotonic()
    if cache_key in _skip_trace_cache:
        cached_time, cached_data = _skip_trace_cache[cache_key]
        if now - cached_time < CACHE_TTL:
            return cached_data

    url = f"{BASE_URL}/person_details_by_ID"
    params = {"id": person_id}

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, params=params, headers=_headers())
            if resp.status_code == 429:
                return None
            resp.raise_for_status()
            data = resp.json()

            if data.get("Status") != 200:
                return None

            # Cache
            _skip_trace_cache[cache_key] = (now, data)
            if len(_skip_trace_cache) > 500:
                oldest = min(_skip_trace_cache, key=lambda k: _skip_trace_cache[k][0])
                del _skip_trace_cache[oldest]

            return data

    except Exception as e:
        logger.error(f"Skip trace details error for {person_id}: {e}")
        return None


async def lookup_people(name: str) -> List[Dict[str, Any]]:
    """
    Full skip trace lookup: search by name → get phone + email for each match.
    Returns a list of people with contact info.
    """
    search_results = await search_by_name(name)
    if not search_results or not search_results.get("PeopleDetails"):
        return []

    matches = []
    for person in search_results["PeopleDetails"]:
        person_id = person.get("Person ID")
        if not person_id:
            continue

        # Get full details (phone, email)
        details = await get_person_details(person_id)
        
        # Merge search results with detail data
        contact_info = _extract_contact_info(details) if details else {}
        
        matches.append({
            "name": person.get("Name", ""),
            "person_id": person_id,
            "age": person.get("Age", ""),
            "lives_in": person.get("Lives in", ""),
            "used_to_live_in": person.get("Used to live in", ""),
            "related_to": person.get("Related to", ""),
            "link": person.get("Link", ""),
            **contact_info,
        })

    return matches


def _extract_contact_info(details_data: Dict[str, Any]) -> Dict[str, Any]:
    """Extract phone + email from the person details response."""
    phone = ""
    email = ""
    
    # TruePeopleSearch stores contact info in various places
    # Check common locations in the response
    profile = details_data.get("data", {}).get("profile", {})
    
    phone = profile.get("phone", profile.get("phoneNumber", ""))
    email = profile.get("email", profile.get("emailAddress", ""))
    
    # Also check other possible locations
    if not phone:
        phone = details_data.get("phone", details_data.get("phoneNumber", ""))
    if not email:
        email = details_data.get("email", details_data.get("emailAddress", ""))
    
    # Some responses put it in the top level
    if not phone:
        phone = details_data.get("PhoneNumber", details_data.get("Mobile", ""))
    if not email:
        email = details_data.get("Email", details_data.get("EmailAddress", ""))
    
    return {"phone": phone, "email": email}


async def enrich_property_owners(properties: List[Dict[str, Any]]) -> int:
    """
    Enrich property owners with skip trace data.
    Uses owner_name or owner_mailing_address to look up people.
    Returns count of properties enriched.
    """
    count = 0
    for prop in properties:
        owner_name = prop.get("owner_name") or prop.get("purchaser")
        if not owner_name or len(owner_name.strip()) < 3:
            continue
        
        try:
            matches = await lookup_people(owner_name)
            if matches:
                prop["owner_contacts"] = matches
                count += 1
                logger.info(f"Skip traced {owner_name}: {len(matches)} matches")
        except Exception as e:
            logger.error(f"Skip trace enrich error for {owner_name}: {e}")
    
    return count
