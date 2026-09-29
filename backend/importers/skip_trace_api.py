"""RapidAPI skip tracing integration.

Workflow:
  1. Search by property address (preferred) or owner name.
  2. Extract candidate Person IDs.
  3. Verify candidate names against the known owner when possible.
  4. Fetch full person details by peo_id.
  5. Return normalized phone/email contact records.

Credentials are read only from RAPIDAPI_KEY.
"""

from __future__ import annotations

import logging
import os
import re
import time
from difflib import SequenceMatcher
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import httpx

logger = logging.getLogger(__name__)

RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY", "").strip()
RAPIDAPI_HOST = "skip-tracing-working-api.p.rapidapi.com"
BASE_URL = f"https://{RAPIDAPI_HOST}"

CACHE_TTL = 3600
CACHE_MAX_ITEMS = 500
MAX_DETAILS_PER_LOOKUP = 3
_skip_trace_cache: Dict[str, Tuple[float, Any]] = {}


def _headers() -> Dict[str, str]:
    return {
        "x-rapidapi-key": RAPIDAPI_KEY,
        "x-rapidapi-host": RAPIDAPI_HOST,
    }


def _cache_get(key: str) -> Any:
    item = _skip_trace_cache.get(key)
    if not item:
        return None
    created, value = item
    if time.monotonic() - created >= CACHE_TTL:
        _skip_trace_cache.pop(key, None)
        return None
    return value


def _cache_set(key: str, value: Any) -> None:
    _skip_trace_cache[key] = (time.monotonic(), value)
    if len(_skip_trace_cache) <= CACHE_MAX_ITEMS:
        return
    oldest = min(_skip_trace_cache, key=lambda k: _skip_trace_cache[k][0])
    _skip_trace_cache.pop(oldest, None)


async def _request(path: str, params: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    if not RAPIDAPI_KEY:
        logger.warning("RAPIDAPI_KEY not set — skip tracing disabled")
        return None

    cache_key = f"{path}:{sorted((str(k), str(v)) for k, v in params.items())}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            response = await client.get(
                f"{BASE_URL}{path}",
                params=dict(params),
                headers=_headers(),
            )
        if response.status_code == 429:
            logger.warning("Skip tracing provider rate limited request: %s", path)
            return None
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            data = {"data": data}
        _cache_set(cache_key, data)
        return data
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code if exc.response is not None else "unknown"
        logger.warning("Skip tracing provider returned HTTP %s for %s", status, path)
    except Exception as exc:
        logger.warning("Skip tracing request failed for %s: %s", path, exc)
    return None


async def search_by_name(name: str, page: int = 1) -> Optional[Dict[str, Any]]:
    clean = " ".join(str(name or "").split())
    if len(clean) < 2:
        return None
    return await _request("/search/byname", {"page": str(max(1, page)), "name": clean})


async def search_by_address(
    street: str,
    citystatezip: str,
    page: int = 1,
) -> Optional[Dict[str, Any]]:
    street = " ".join(str(street or "").split())
    citystatezip = " ".join(str(citystatezip or "").split())
    if len(street) < 3 or len(citystatezip) < 3:
        return None
    return await _request(
        "/search/byaddress",
        {
            "page": str(max(1, page)),
            "street": street,
            "citystatezip": citystatezip,
        },
    )


async def get_person_details(person_id: str) -> Optional[Dict[str, Any]]:
    person_id = str(person_id or "").strip()
    if not person_id:
        return None
    return await _request("/search/detailsbyID", {"peo_id": person_id})


def _normalize_name(value: Any) -> str:
    text = re.sub(r"[^a-z0-9 ]+", " ", str(value or "").lower())
    return " ".join(text.split())


def _name_tokens(value: Any) -> List[str]:
    return [token for token in _normalize_name(value).split() if token]


def _candidate_name(candidate: Mapping[str, Any]) -> str:
    for key in (
        "Name", "name", "Full Name", "full_name", "fullName",
        "Person Name", "person_name",
    ):
        value = candidate.get(key)
        if value:
            return str(value)
    first = candidate.get("First Name") or candidate.get("first_name") or candidate.get("firstName") or ""
    middle = candidate.get("Middle Name") or candidate.get("middle_name") or candidate.get("middleName") or ""
    last = candidate.get("Last Name") or candidate.get("last_name") or candidate.get("lastName") or ""
    return " ".join(str(x).strip() for x in (first, middle, last) if str(x).strip())


def _person_id(candidate: Mapping[str, Any]) -> str:
    for key in ("Person ID", "person_id", "personId", "peo_id", "peoId"):
        value = candidate.get(key)
        if value:
            return str(value).strip()
    return ""


def _looks_like_candidate(item: Any) -> bool:
    return isinstance(item, Mapping) and bool(_person_id(item) or _candidate_name(item))


def _candidate_lists(payload: Any) -> Iterable[List[Mapping[str, Any]]]:
    """Yield likely candidate arrays from provider responses without assuming one schema."""
    if isinstance(payload, list):
        candidates = [item for item in payload if _looks_like_candidate(item)]
        if candidates:
            yield candidates
        for item in payload:
            yield from _candidate_lists(item)
        return

    if not isinstance(payload, Mapping):
        return

    preferred = (
        "PeopleDetails", "peopleDetails", "people", "People",
        "results", "Results", "items", "Items", "data", "Data",
    )
    for key in preferred:
        value = payload.get(key)
        if isinstance(value, list):
            candidates = [item for item in value if _looks_like_candidate(item)]
            if candidates:
                yield candidates

    for value in payload.values():
        if isinstance(value, (list, Mapping)):
            yield from _candidate_lists(value)


def _extract_candidates(payload: Optional[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    if not payload:
        return []
    seen: set[str] = set()
    output: List[Dict[str, Any]] = []
    for group in _candidate_lists(payload):
        for candidate in group:
            cid = _person_id(candidate)
            name = _candidate_name(candidate)
            dedupe = cid or f"name:{_normalize_name(name)}"
            if not dedupe or dedupe in seen:
                continue
            seen.add(dedupe)
            output.append(dict(candidate))
    return output


def _name_score(known_name: str, candidate_name: str) -> float:
    known = _normalize_name(known_name)
    candidate = _normalize_name(candidate_name)
    if not known or not candidate:
        return 0.0
    if known == candidate:
        return 1.0

    known_tokens = _name_tokens(known)
    candidate_tokens = _name_tokens(candidate)
    if not known_tokens or not candidate_tokens:
        return 0.0

    # Last-name agreement is a strong gate when both are present.
    surname_bonus = 0.16 if known_tokens[-1] == candidate_tokens[-1] else 0.0
    common = set(known_tokens) & set(candidate_tokens)
    token_score = len(common) / max(len(set(known_tokens)), 1)
    sequence = SequenceMatcher(None, known, candidate).ratio()

    # Initials count as partial first-name agreement.
    initial_bonus = 0.0
    if known_tokens[0][0:1] and candidate_tokens[0][0:1] and known_tokens[0][0] == candidate_tokens[0][0]:
        initial_bonus = 0.08

    return min(1.0, (0.48 * sequence) + (0.36 * token_score) + surname_bonus + initial_bonus)


def _flatten_scalars(value: Any) -> Iterable[Tuple[str, Any]]:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if isinstance(child, (Mapping, list)):
                for nested_key, nested_value in _flatten_scalars(child):
                    yield f"{key}.{nested_key}", nested_value
            else:
                yield str(key), child
    elif isinstance(value, list):
        for item in value:
            yield from _flatten_scalars(item)


def _collect_values(payload: Any, key_terms: Sequence[str]) -> List[str]:
    values: List[str] = []
    seen: set[str] = set()
    terms = tuple(term.lower() for term in key_terms)
    for key, value in _flatten_scalars(payload):
        key_lower = key.lower()
        if not any(term in key_lower for term in terms):
            continue
        if value is None:
            continue
        if isinstance(value, (str, int, float)):
            text = str(value).strip()
            if text and text.lower() not in {"none", "null", "n/a"} and text not in seen:
                seen.add(text)
                values.append(text)
    return values


def _extract_contact_info(details: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    if not details:
        return {"phone": "", "email": "", "phones": [], "emails": []}

    phones = _collect_values(details, ("phone", "mobile", "telephone"))
    emails = _collect_values(details, ("email", "e-mail"))

    return {
        "phone": phones[0] if phones else "",
        "email": emails[0] if emails else "",
        "phones": phones,
        "emails": emails,
    }


def _split_address(address: str) -> Tuple[str, str]:
    """Split 'street, city, ST 00000' into provider street + citystatezip fields."""
    text = " ".join(str(address or "").replace("\n", " ").split())
    if not text:
        return "", ""
    parts = [part.strip() for part in text.split(",") if part.strip()]
    if len(parts) >= 2:
        return parts[0], ", ".join(parts[1:])
    return "", ""


def _candidate_summary(candidate: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "name": _candidate_name(candidate),
        "person_id": _person_id(candidate),
        "age": candidate.get("Age") or candidate.get("age") or "",
        "lives_in": (
            candidate.get("Lives in")
            or candidate.get("lives_in")
            or candidate.get("address")
            or ""
        ),
    }


async def _details_for_candidates(
    candidates: List[Dict[str, Any]],
    *,
    known_name: str = "",
    match_method: str,
) -> List[Dict[str, Any]]:
    ranked: List[Tuple[float, Dict[str, Any]]] = []
    for candidate in candidates:
        score = _name_score(known_name, _candidate_name(candidate)) if known_name else 0.5
        ranked.append((score, candidate))
    ranked.sort(key=lambda pair: pair[0], reverse=True)

    if known_name:
        plausible = [pair for pair in ranked if pair[0] >= 0.54]
        if plausible:
            ranked = plausible
        elif match_method == "address":
            # Address itself is useful evidence, but cap details calls aggressively.
            ranked = ranked[:1]
        else:
            ranked = []

    output: List[Dict[str, Any]] = []
    for score, candidate in ranked[:MAX_DETAILS_PER_LOOKUP]:
        pid = _person_id(candidate)
        if not pid:
            continue
        details = await get_person_details(pid)
        contact = _extract_contact_info(details)
        summary = _candidate_summary(candidate)
        summary.update(
            {
                **contact,
                "source": RAPIDAPI_HOST,
                "match_method": match_method,
                "match_confidence": round(score, 3),
            }
        )
        output.append(summary)
    return output


async def lookup_people(
    name: str,
    *,
    street: str = "",
    citystatezip: str = "",
) -> List[Dict[str, Any]]:
    """Address-first owner lookup with name fallback, then details-by-ID."""
    name = " ".join(str(name or "").split())

    if street and citystatezip:
        address_payload = await search_by_address(street, citystatezip)
        address_candidates = _extract_candidates(address_payload)
        if address_candidates:
            contacts = await _details_for_candidates(
                address_candidates,
                known_name=name,
                match_method="address",
            )
            if contacts:
                return contacts

    if name:
        name_payload = await search_by_name(name)
        name_candidates = _extract_candidates(name_payload)
        if name_candidates:
            return await _details_for_candidates(
                name_candidates,
                known_name=name,
                match_method="name",
            )

    return []



async def lookup_owner(owner_name: str, property_address: str = "") -> List[Dict[str, Any]]:
    street, citystatezip = _split_address(property_address)
    return await lookup_people(
        owner_name,
        street=street,
        citystatezip=citystatezip,
    )


async def enrich_property_owners(properties: List[Dict[str, Any]]) -> int:
    """Enrich property dicts in-place, preferring address search over name-only search."""
    count = 0
    for prop in properties:
        owner_name = str(prop.get("owner_name") or prop.get("purchaser") or "").strip()
        if len(owner_name) < 3:
            continue

        address = str(
            prop.get("situs_address")
            or prop.get("address")
            or prop.get("property_address")
            or ""
        ).strip()

        try:
            matches = await lookup_owner(owner_name, address)
            if matches:
                prop["owner_contacts"] = matches
                count += 1
                logger.info(
                    "Skip traced %s: %d plausible contact match(es)",
                    owner_name,
                    len(matches),
                )
        except Exception as exc:
            logger.warning("Skip trace enrichment failed for %s: %s", owner_name, exc)

    return count
