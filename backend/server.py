"""TarrantREI / InvestorFlip backend.

InvestorFlip V1 Live Residential Rule:
- Pull live Fort Worth residential listings from RapidAPI when available.
- Only show/analyze house-flip targets:
  - Single-family houses
  - Residential multi-family houses
- Blocks commercial, land, apartments, condos, townhomes, duplex, triplex, fourplex, etc.
- No Fort Worth streets/corridors are blocked. If it is a verified house, it can show.

Important:
- Set RAPIDAPI_KEY in Railway environment variables.
- This version adds live listing endpoints:
  POST /api/live/sync-fort-worth
  GET  /api/live/fort-worth-listings
  GET  /api/live/status
"""

from fastapi import FastAPI, APIRouter, HTTPException, Query, UploadFile, File, Form
from fastapi.responses import StreamingResponse, Response
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from ai.models import QuillAnalyzeRequest, QuillAnalyzeResponse
from ai.quill import analyze_property_with_quill
from ai.scout import scout_analyze_property
from database import PostgresDatabase
from investor_logic import (
    classify_owner,
    compute_scores,
    derive_owner_signals,
    is_synthetic_property,
    merge_live_refresh,
)
from listing_normalization import (
    build_provider_address_query,
    extract_listing_fields,
    hydrate_listing_record,
)
from property_enrichment import normalize_property_detail
from address_suggestions import normalize_address_suggestions
import os
import re
import random
import logging
import time
import pandas as pd
from io import BytesIO
from pathlib import Path
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
import uuid
from datetime import datetime, timezone
import httpx
from importers import feeds as feeds_mod
from add_all_routes import router as all_router
from saved_searches_routes import router as saved_searches_router
from auto_sync import start_background_sync

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

# PostgreSQL
db = PostgresDatabase()

app = FastAPI(title="TarrantREI / InvestorFlip API")
api_router = APIRouter(prefix="/api")

logger = logging.getLogger("tarrantrei")
logging.basicConfig(level=logging.INFO)


# ---------- Flip House Validator ----------
ALLOWED_FLIP_TYPES = [
    "single family",
    "single family residential",
    "residential single family",
    "single-family",
    "single-family residential",
    "singlefamily",
    "house",
    "detached",
    "multi family",
    "multifamily",
    "multi-family",
    "residential multi family",
    "residential multifamily",
]

BLOCKED_FLIP_TYPES = [
    "commercial",
    "retail",
    "office",
    "restaurant",
    "warehouse",
    "industrial",
    "land",
    "lot",
    "mixed use",
    "hotel",
    "motel",
    "medical",
    "shopping center",
    "condo",
    "condominium",
    "townhome",
    "townhouse",
    "duplex",
    "triplex",
    "fourplex",
    "quadplex",
    "apartment",
    "mobile",
    "manufactured",
]


def get_property_type(p: Dict[str, Any]) -> str:
    return str(
        p.get("property_type")
        or p.get("home_type")
        or p.get("homeType")
        or p.get("property_subtype")
        or p.get("propertyType")
        or p.get("land_use")
        or p.get("use_code")
        or p.get("property_class")
        or ""
    ).lower().strip()


def has_basic_house_facts(p: Dict[str, Any]) -> bool:
    """Legacy safety net for older records created before property_type existed.

    It does not block any Fort Worth streets/corridors; it only checks house-like facts.
    """
    try:
        beds = float(p.get("beds") or p.get("bedrooms") or 0)
        baths = float(p.get("baths") or p.get("bathrooms") or 0)
        sqft = float(p.get("sqft") or p.get("living_area") or p.get("livingArea") or 0)
        year_built = int(p.get("year_built") or p.get("yearBuilt") or 0)
    except Exception:
        return False

    return beds >= 1 and baths >= 1 and 500 <= sqft <= 8000 and 1800 <= year_built <= 2035


def infer_legacy_property_type(p: Dict[str, Any]) -> Optional[str]:
    if get_property_type(p):
        return None
    if has_basic_house_facts(p):
        return "Single Family Residential"
    return None


def is_allowed_flip_house(p: Dict[str, Any]) -> bool:
    t = get_property_type(p)

    if not t:
        return infer_legacy_property_type(p) is not None

    if any(blocked in t for blocked in BLOCKED_FLIP_TYPES):
        return False

    return any(allowed in t for allowed in ALLOWED_FLIP_TYPES)


def is_fort_worth_property(p: Dict[str, Any]) -> bool:
    city = str(p.get("city") or p.get("situs_city") or p.get("property_city") or "").lower()
    address = str(p.get("situs_address") or p.get("address") or p.get("full_address") or "").lower()
    return city == "fort worth" or "fort worth" in address


def safe_int(v: Any, default: Optional[int] = None) -> Optional[int]:
    try:
        if v is None or v == "":
            return default
        if isinstance(v, str):
            v = re.sub(r"[^0-9.]", "", v)
        return int(float(v))
    except Exception:
        return default


def safe_float(v: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if v is None or v == "":
            return default
        if isinstance(v, str):
            v = re.sub(r"[^0-9.]", "", v)
        return float(v)
    except Exception:
        return default


# ---------- Seed Data ----------
# Demo seed data remains only as a fallback if DB is empty.
FW_STREETS = [
    "Oak Grove Ln", "Sycamore St", "Magnolia Ave", "Hemphill St",
    "Hulen St", "McCart Ave", "Granbury Rd", "Trail Lake Dr",
    "White Settlement Rd", "Berry St", "Vickery Blvd", "Lancaster Ave",
    "Riverside Dr", "Beach St", "Meadowbrook Dr", "Forest Park Blvd",
    "8th Ave", "Park Hill Dr", "Stalcup Rd",
]
CITIES = [
    ("Fort Worth", "76104"), ("Fort Worth", "76110"), ("Fort Worth", "76112"),
    ("Fort Worth", "76116"), ("Fort Worth", "76119"), ("Arlington", "76010"),
    ("Arlington", "76013"), ("Arlington", "76018"), ("Mansfield", "76063"),
    ("Bedford", "76021"), ("Euless", "76039"), ("Hurst", "76053"),
    ("North Richland Hills", "76180"), ("Grapevine", "76051"),
]
LISTING_TYPES = ["REO", "As-Is", "Investor", "Cash House", "Foreclosure"]

OWNER_POOL = [
    ("John & Mary Henderson", "Individual"),
    ("Robert Salazar", "Individual"),
    ("Linda Patterson", "Individual"),
    ("BlueStone Holdings LLC", "LLC"),
    ("Lone Star Property Group LLC", "LLC"),
    ("Trinity Real Estate Investments LLC", "LLC"),
    ("Cowtown Capital LLC", "LLC"),
    ("Wells Fargo Bank NA", "Bank"),
    ("Bank of America N.A.", "Bank"),
    ("Fannie Mae", "Bank"),
    ("Nationstar Mortgage LLC", "Bank"),
    ("The Henderson Family Trust", "Trust"),
    ("Patterson Living Trust", "Trust"),
    ("McKinney Revocable Trust", "Trust"),
]

PROPERTY_IMAGES = [
    "https://images.pexels.com/photos/1396122/pexels-photo-1396122.jpeg?auto=compress&cs=tinysrgb&dpr=2&h=650&w=940",
    "https://images.pexels.com/photos/106399/pexels-photo-106399.jpeg?auto=compress&cs=tinysrgb&dpr=2&h=650&w=940",
    "https://images.pexels.com/photos/1370704/pexels-photo-1370704.jpeg?auto=compress&cs=tinysrgb&dpr=2&h=650&w=940",
]
OUT_OF_STATE_STATES = ["CA", "FL", "NY", "NV", "AZ", "CO"]


def generate_seed_properties(n: int = 36) -> List[Dict[str, Any]]:
    rng = random.Random(7)
    props: List[Dict[str, Any]] = []
    for _ in range(n):
        street_num = rng.randint(100, 9999)
        street = rng.choice(FW_STREETS)
        city, zipc = rng.choice(CITIES)
        situs = f"{street_num} {street}, {city}, TX {zipc}"

        owner_name, _owner_type_seed = rng.choice(OWNER_POOL)
        owner_type = classify_owner(owner_name)

        out_of_state = owner_type in ("LLC", "Corporation", "Bank") and rng.random() < 0.45
        if out_of_state:
            st = rng.choice(OUT_OF_STATE_STATES)
            mailing = f"PO Box {rng.randint(1000, 99999)}, {rng.choice(['Los Angeles', 'Miami', 'New York', 'Las Vegas', 'Phoenix', 'Denver'])}, {st} {rng.randint(10000, 99999)}"
        else:
            mailing = situs if owner_type == "Individual" else f"{rng.randint(100, 9999)} Commerce St, Dallas, TX {rng.choice(['75201', '75204', '75219'])}"

        listing_type = rng.choices(LISTING_TYPES, weights=[2, 3, 3, 2, 2])[0]
        if owner_type == "Bank":
            listing_type = rng.choice(["REO", "Foreclosure"])

        property_type = rng.choice([
            "Single Family Residential",
            "Single Family Residential",
            "Single Family Residential",
            "Residential Multi Family",
        ])

        beds = rng.choice([2, 3, 3, 3, 4, 4, 5])
        baths = rng.choice([1, 2, 2, 2.5, 3])
        sqft = rng.randint(900, 3200)
        year_built = rng.randint(1948, 2018)
        lot_size = rng.randint(4500, 12000)

        market_value = rng.randint(120_000, 480_000)
        discount = rng.uniform(0.05, 0.35) if listing_type in ("REO", "Foreclosure", "Cash House") else rng.uniform(-0.05, 0.15)
        price = int(market_value * (1 - discount))
        assessed_value = int(market_value * rng.uniform(0.78, 0.96))
        annual_taxes = int(assessed_value * rng.uniform(0.022, 0.028))
        equity_estimate = market_value - price
        est_roi = round((equity_estimate / max(price, 1)) * 100, 1)

        tax_delinquent = rng.random() < 0.18
        vacant = rng.random() < 0.22
        high_equity = equity_estimate / market_value >= 0.20
        cash_buyer = owner_type in ("LLC", "Corporation") and rng.random() < 0.6
        investor_owned = owner_type in ("LLC", "Corporation", "Trust")

        prop = {
            "id": str(uuid.uuid4()),
            "situs_address": situs,
            "city": city,
            "state": "TX",
            "zip": zipc,
            "county": "Tarrant",
            "property_type": property_type,
            "home_type": property_type,
            "beds": beds,
            "baths": baths,
            "sqft": sqft,
            "year_built": year_built,
            "lot_size_sqft": lot_size,
            "image_url": rng.choice(PROPERTY_IMAGES),
            "price": price,
            "market_value": market_value,
            "assessed_value": assessed_value,
            "annual_taxes": annual_taxes,
            "equity_estimate": equity_estimate,
            "est_roi_pct": est_roi,
            "legal_description": f"LOT {rng.randint(1, 40)} BLK {rng.randint(1, 30)}, {rng.choice(['MEADOWBROOK', 'RYAN PLACE', 'POLYTECHNIC', 'WEDGWOOD', 'ARLINGTON HEIGHTS'])} ADDITION",
            "listing_type": listing_type,
            "owner_name": owner_name,
            "owner_type": owner_type,
            "owner_mailing_address": mailing,
            "out_of_state_owner": out_of_state,
            "tax_delinquent": tax_delinquent,
            "vacant": vacant,
            "high_equity": high_equity,
            "cash_buyer": cash_buyer,
            "investor_owned": investor_owned,
            "data_source": "Demo Seed Data - NOT LIVE",
            "is_synthetic": True,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        prop.update(compute_scores(prop))
        props.append(prop)
    return props


# ---------- Filter Definitions ----------
INVESTOR_FILTERS = [
    {"key": "all", "label": "All"},
    {"key": "live", "label": "Live Listings"},
    {"key": "reo", "label": "REO"},
    {"key": "foreclosure", "label": "Foreclosure"},
    {"key": "as_is", "label": "As-Is"},
    {"key": "investor", "label": "Investor"},
    {"key": "cash_house", "label": "Cash House"},
    {"key": "high_equity", "label": "High Equity"},
    {"key": "cash_buyer", "label": "Cash Buyer"},
    {"key": "investor_owned", "label": "Investor-Owned"},
    {"key": "llc", "label": "LLC"},
    {"key": "law_firm", "label": "Law Firm"},
    {"key": "tax_delinquent", "label": "Tax Delinquent"},
    {"key": "absentee_owner", "label": "Absentee Owner"},
    {"key": "out_of_state", "label": "Out-of-State Owner"},
    {"key": "vacant", "label": "Vacant"},
    {"key": "corporate", "label": "Corporate Owner"},
    {"key": "trust", "label": "Trust-Owned"},
    {"key": "bank_owned", "label": "Bank-Owned"},
    {"key": "distressed", "label": "Distressed"},
    {"key": "code_violation", "label": "Code Violations"},
    {"key": "pre_foreclosure", "label": "Pre-Foreclosure"},
    {"key": "motivated_seller", "label": "Motivated Seller"},
    {"key": "tax_lien", "label": "Tax Lien"},
    {"key": "wholesale", "label": "Wholesale Deal"},
]


def apply_filter(filter_key: str, query: Dict[str, Any]) -> Dict[str, Any]:
    f = filter_key.lower()
    if f in ("all", ""):
        return query
    if f == "live":
        query["is_live_listing"] = True
    elif f == "reo":
        query["listing_type"] = "REO"
    elif f == "foreclosure":
        query["listing_type"] = "Foreclosure"
    elif f == "as_is":
        query["listing_type"] = "As-Is"
    elif f == "investor":
        query["listing_type"] = "Investor"
    elif f == "cash_house":
        query["listing_type"] = "Cash House"
    elif f == "high_equity":
        query["high_equity"] = True
    elif f == "cash_buyer":
        query["cash_buyer"] = True
    elif f == "investor_owned":
        query["investor_owned"] = True
    elif f == "llc":
        query["owner_type"] = "LLC"
    elif f == "law_firm":
        query["owner_type"] = {"$in": ["Law Firm", "Attorney"]}
    elif f == "tax_delinquent":
        query["tax_delinquent"] = True
    elif f == "absentee_owner":
        query["absentee_owner"] = True
    elif f == "out_of_state":
        query["out_of_state_owner"] = True
    elif f == "vacant":
        query["vacant"] = True
    elif f == "corporate":
        query["owner_type"] = "Corporation"
    elif f == "trust":
        query["owner_type"] = "Trust"
    elif f == "bank_owned":
        query["owner_type"] = "Bank"
    elif f == "distressed":
        query["distress_score"] = {"$gte": 50}
    elif f == "code_violation":
        query["violation_count"] = {"$gte": 1}
    elif f == "pre_foreclosure":
        query["$or"] = [
            {"pre_foreclosure": True},
            {"listing_status": "Pre-Foreclosure"},
        ]
    elif f == "motivated_seller":
        query["motivation_score"] = {"$gte": 50}
    elif f == "tax_lien":
        query["tax_delinquent"] = True
    elif f == "wholesale":
        query["wholesale"] = True
    return query


def is_user_visible_property(property_record: Dict[str, Any]) -> bool:
    return not is_synthetic_property(property_record) and is_allowed_flip_house(property_record)


def matches_investor_filter(property_record: Dict[str, Any], filter_key: str) -> bool:
    key = filter_key.lower()
    if key in ("all", ""):
        return True
    if key == "live":
        return property_record.get("is_live_listing") is True
    if key in {"reo", "foreclosure", "as_is", "investor", "cash_house"}:
        expected = {
            "reo": "REO", "foreclosure": "Foreclosure", "as_is": "As-Is",
            "investor": "Investor", "cash_house": "Cash House",
        }[key]
        return property_record.get("listing_type") == expected
    if key in {"high_equity", "cash_buyer", "investor_owned", "tax_delinquent", "vacant", "absentee_owner"}:
        return property_record.get(key) is True
    if key == "out_of_state":
        return property_record.get("out_of_state_owner") is True
    if key == "llc":
        return property_record.get("owner_type") == "LLC"
    if key == "law_firm":
        return property_record.get("owner_type") in {"Law Firm", "Attorney"}
    if key == "corporate":
        return property_record.get("owner_type") == "Corporation"
    if key == "trust":
        return property_record.get("owner_type") == "Trust"
    if key == "bank_owned":
        return property_record.get("owner_type") == "Bank"
    if key == "distressed":
        return (property_record.get("distress_score") or 0) >= 50 or (property_record.get("violation_count") or 0) >= 1
    if key == "code_violation":
        return (property_record.get("violation_count") or 0) >= 1
    if key == "pre_foreclosure":
        return property_record.get("pre_foreclosure") is True or property_record.get("listing_status") == "Pre-Foreclosure"
    if key == "motivated_seller":
        return (property_record.get("motivation_score") or 0) >= 50
    if key == "tax_lien":
        return property_record.get("tax_delinquent") is True
    if key == "wholesale":
        return property_record.get("wholesale") is True
    return True


# ---------- Models ----------
class AIAnalysisResponse(BaseModel):
    property_id: str
    narrative: str


class SaveRequest(BaseModel):
    property_id: str


# ---------- RapidAPI Helpers ----------
RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY", "")
OPENWEB_NINJA_ZILLOW_API_KEY = (
    os.environ.get("OPENWEB_NINJA_ZILLOW_API_KEY", "").strip()
    or os.environ.get("OPENWEB_NINJA_API_KEY", "").strip()
)
OPENWEB_NINJA_REAL_ESTATE_API_KEY = (
    os.environ.get("OPENWEB_NINJA_REAL_ESTATE_API_KEY", "").strip()
    or os.environ.get("OPENWEB_NINJA_KEY", "").strip()
    or os.environ.get("OPENWEB_NINJA_API_KEY", "").strip()
)
OPENWEB_NINJA_ZILLOW_BASE_URL = os.environ.get(
    "OPENWEB_NINJA_ZILLOW_BASE_URL",
    "https://api.openwebninja.com/realtime-zillow-data",
).rstrip("/")
OPENWEB_NINJA_REAL_ESTATE_BASE_URL = os.environ.get(
    "OPENWEB_NINJA_REAL_ESTATE_BASE_URL",
    "https://api.openwebninja.com/realtime-real-estate-data/zillow",
).rstrip("/")
HOST_LOOKUP = "us-real-estate-data1.p.rapidapi.com"
HOST_LISTINGS = "us-real-estate-listings.p.rapidapi.com"
HOST_REALTIME = "real-time-real-estate-data.p.rapidapi.com"
HOST_PROPERTY_REACH = "property-reach.p.rapidapi.com"
HOST_CAKEMLS = "cakemls.p.rapidapi.com"
HOST_REALTOR_SEARCH = "realtor-search.p.rapidapi.com"
HOST_REALTY_US = "realty-us.p.rapidapi.com"
RAPIDAPI_CAKEMLS_ENABLED = os.environ.get(
    "RAPIDAPI_CAKEMLS_ENABLED",
    "false",
).lower() == "true"
RAPIDAPI_REALTOR_SEARCH_ENABLED = os.environ.get(
    "RAPIDAPI_REALTOR_SEARCH_ENABLED",
    "false",
).lower() == "true"
RAPIDAPI_REALTY_US_ENABLED = os.environ.get(
    "RAPIDAPI_REALTY_US_ENABLED",
    "false",
).lower() == "true"
PROPERTY_DETAIL_CACHE_VERSION = 1
ADDRESS_SUGGESTION_CACHE_SECONDS = 24 * 60 * 60
_address_suggestion_cache: Dict[str, tuple[float, List[Dict[str, Any]]]] = {}


async def _rapid_get(host: str, path: str, params: Dict[str, Any]) -> Dict[str, Any]:
    if not RAPIDAPI_KEY:
        raise HTTPException(503, "RAPIDAPI_KEY not configured in environment variables")
    headers = {
        "x-rapidapi-key": RAPIDAPI_KEY,
        "x-rapidapi-host": host,
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.get(f"https://{host}{path}", headers=headers, params=params)
        if r.status_code >= 400:
            raise HTTPException(r.status_code, f"RapidAPI error from {host}{path}: {r.text[:300]}")
        return r.json()


async def _rapid_post(host: str, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    if not RAPIDAPI_KEY:
        raise HTTPException(503, "RAPIDAPI_KEY not configured in environment variables")
    headers = {
        "x-rapidapi-key": RAPIDAPI_KEY,
        "x-rapidapi-host": host,
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"https://{host}{path}",
            headers=headers,
            json=payload,
        )
        if response.status_code >= 400:
            raise HTTPException(
                response.status_code,
                f"RapidAPI error from {host}{path}: {response.text[:300]}",
            )
        return response.json()


async def _openweb_get(
    path: str,
    params: Dict[str, Any],
    *,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
) -> Dict[str, Any]:
    """Call OpenWeb Ninja's direct API without mixing in RapidAPI headers."""
    key = api_key or OPENWEB_NINJA_ZILLOW_API_KEY
    root = (base_url or OPENWEB_NINJA_ZILLOW_BASE_URL).rstrip("/")
    if not key:
        raise HTTPException(503, "OpenWeb Ninja API key not configured")
    normalized_path = path if path.startswith("/") else f"/{path}"
    headers = {
        "X-API-Key": key,
        "Accept": "application/json",
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            f"{root}{normalized_path}",
            headers=headers,
            params=params,
        )
        if response.status_code >= 400:
            raise HTTPException(
                response.status_code,
                f"OpenWeb Ninja error from {normalized_path}: {response.text[:300]}",
            )
        return response.json()


def _agent_detail_patch(payload: Any) -> Dict[str, Any]:
    """Extract a small, stable agent profile from Realtor Search responses."""
    candidates: List[Dict[str, Any]] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            lowered = {str(key).lower() for key in value}
            if lowered & {
                "agentname", "agent_name", "fullname", "full_name",
                "phone", "email", "rating", "reviewcount",
            }:
                candidates.append(value)
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(payload)
    if not candidates:
        return {}

    record = max(candidates, key=lambda value: len(value))

    def first(*keys: str) -> Any:
        for key in keys:
            value = record.get(key)
            if value not in (None, ""):
                return value
        return None

    photo = first("photo", "photo_url", "image", "avatar", "profilePhoto")
    photo_url = _photo_url(photo)
    return {
        key: value
        for key, value in {
            "listing_agent_name": first("agentName", "agent_name", "fullName", "full_name", "name"),
            "listing_agent_phone": first("phone", "phoneNumber", "phone_number", "mobile"),
            "listing_agent_email": first("email", "emailAddress", "email_address"),
            "listing_agent_rating": safe_float(first("rating", "averageRating", "reviewAverage")),
            "listing_agent_review_count": safe_int(first("reviewCount", "review_count", "reviews")),
            "listing_agent_photo_url": photo_url,
            "broker_name": first("brokerName", "broker_name", "officeName", "office_name", "brokerage"),
        }.items()
        if value not in (None, "")
    }


async def _add_realtor_agent_details(enriched: Dict[str, Any]) -> Dict[str, Any]:
    if not (RAPIDAPI_REALTOR_SEARCH_ENABLED and RAPIDAPI_KEY):
        return enriched
    url = str(enriched.get("listing_agent_url") or "").strip()
    if not re.match(r"^https://(?:www\.)?realtor\.com/realestateagents/", url, flags=re.I):
        return enriched
    try:
        raw = await _rapid_get(
            HOST_REALTOR_SEARCH,
            "/agents/detail-url",
            {"url": url},
        )
        patch = _agent_detail_patch(raw)
        return {**enriched, **{key: value for key, value in patch.items() if value not in (None, "")}}
    except HTTPException as exc:
        logger.info("Realtor Search agent detail failed: %s", exc.detail)
        return enriched


async def _add_realty_us_agent_listings(
    enriched: Dict[str, Any],
    property_record: Dict[str, Any],
) -> Dict[str, Any]:
    if not (RAPIDAPI_REALTY_US_ENABLED and RAPIDAPI_KEY):
        return enriched
    if enriched.get("agent_listings"):
        return enriched

    fulfillment_id = str(
        enriched.get("listing_agent_fulfillment_id")
        or property_record.get("listing_agent_fulfillment_id")
        or ""
    ).strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", fulfillment_id):
        return enriched

    try:
        raw = await _rapid_get(
            HOST_REALTY_US,
            "/agents/v2/listings",
            {"fulfillmentId": fulfillment_id},
        )
        compact = []
        seen = set()
        for item in _deep_find_items(raw):
            fields = extract_listing_fields(item)
            address = fields["address"]
            identity = str(
                item.get("property_id")
                or item.get("zpid")
                or item.get("listing_id")
                or address.get("full")
                or ""
            )
            if not identity or identity in seen:
                continue
            seen.add(identity)
            compact.append({
                "id": identity,
                "address": address.get("full"),
                "price": fields.get("price"),
                "beds": fields.get("beds"),
                "baths": fields.get("baths"),
                "sqft": fields.get("sqft"),
                "image_url": fields.get("photos", [None])[0] if fields.get("photos") else None,
                "detail_url": item.get("href") or item.get("detail_url") or item.get("url"),
            })
            if len(compact) >= 8:
                break
        if compact:
            return {
                **enriched,
                "listing_agent_fulfillment_id": fulfillment_id,
                "agent_listings": compact,
                "agent_listings_source": "Realty in US via RapidAPI",
                "agent_listings_fetched_at": datetime.now(timezone.utc).isoformat(),
            }
    except HTTPException as exc:
        logger.info("Realty in US agent listings failed: %s", exc.detail)
    return enriched


def _deep_find_items(obj: Any) -> List[Dict[str, Any]]:
    """Find likely property listing dicts inside unknown RapidAPI response shapes."""
    found: List[Dict[str, Any]] = []

    def walk(x: Any):
        if isinstance(x, dict):
            keys = set(k.lower() for k in x.keys())
            looks_like_listing = (
                any(k in keys for k in ["zpid", "property_id", "listing_id", "id"])
                and any(k in keys for k in ["price", "listprice", "list_price"])
                and any(k in keys for k in ["address", "streetaddress", "street_address", "location"])
            )
            if looks_like_listing:
                found.append(x)
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for item in x:
                walk(item)

    walk(obj)

    # Deduplicate
    out = []
    seen = set()
    for item in found:
        ident = str(item.get("zpid") or item.get("property_id") or item.get("propertyId") or item.get("listing_id") or item.get("listingId") or item.get("id") or item.get("address") or item)
        if ident in seen:
            continue
        seen.add(ident)
        out.append(item)
    return out


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _photo_url(value: Any) -> Optional[str]:
    if isinstance(value, str) and value.strip():
        return value.strip().replace("http://", "https://", 1)
    if isinstance(value, dict):
        for key in ("href", "url", "src"):
            url = value.get(key)
            if isinstance(url, str) and url.strip():
                return url.strip().replace("http://", "https://", 1)
    return None


def normalize_live_listing(item: Dict[str, Any], source_name: str) -> Optional[Dict[str, Any]]:
    fields = extract_listing_fields(item)
    addr = fields["address"]
    source = fields["source"]
    source_agent = fields["source_agent"]

    if not addr["street"] and not addr["full"]:
        return None

    raw_type = fields["property_type"]
    # The us-real-estate-listings request is explicitly constrained to
    # property_type=single_family, so a missing type field is safe to infer here.
    if not raw_type and "us-real-estate-listings" in source_name.lower():
        raw_type = "Single Family Residential"

    candidate = {
        "property_type": raw_type,
        "home_type": raw_type,
        "city": addr["city"] or "Fort Worth",
        "situs_address": addr["full"] or f"{addr['street']}, {addr['city']}, {addr['state']} {addr['zip']}",
    }

    if not is_fort_worth_property(candidate):
        return None

    if not is_allowed_flip_house(candidate):
        return None

    price = fields["price"]
    zestimate = fields["zestimate"]
    market_value = zestimate
    assessed_value = fields["assessed_value"]
    annual_taxes = fields["annual_taxes"]
    beds = fields["beds"]
    baths = fields["baths"]
    sqft = fields["sqft"]
    year_built = fields["year_built"]
    photos = fields["photos"]

    listing_status = str(item.get("homeStatus") or item.get("status") or item.get("listingStatus") or "For Sale")
    listing_type = "For Sale"
    if "foreclosure" in listing_status.lower():
        listing_type = "Foreclosure"
    elif "reo" in listing_status.lower():
        listing_type = "REO"

    zpid = item.get("zpid") or item.get("property_id") or item.get("propertyId") or item.get("listing_id") or item.get("listingId") or item.get("id")
    stable_key = f"{source_name}:{zpid or candidate['situs_address']}"

    prop = {
        "id": str(uuid.uuid5(uuid.NAMESPACE_URL, stable_key)),
        "external_id": str(zpid or ""),
        "situs_address": candidate["situs_address"],
        "city": addr["city"] or "Fort Worth",
        "state": addr["state"] or "TX",
        "zip": addr["zip"],
        "county": "Tarrant",
        "property_type": raw_type,
        "home_type": raw_type,
        "beds": beds,
        "baths": baths,
        "sqft": sqft,
        "year_built": year_built,
        "lot_size_sqft": fields["lot_size_sqft"],
        "image_url": photos[0] if photos else None,
        "photos": photos,
        "price": price,
        "market_value": market_value,
        "market_value_source": "third-party automated estimate" if zestimate else None,
        "assessed_value": assessed_value,
        "annual_taxes": annual_taxes,
        "equity_estimate": None,
        "equity_status": "unknown - mortgage balance required",
        "est_roi_pct": None,
        "roi_status": "unknown - ARV, repairs, holding, and selling costs required",
        "legal_description": "",
        "listing_type": listing_type,
        "listing_status": listing_status,
        "owner_name": item.get("owner_name") or "",
        "owner_type": classify_owner(item.get("owner_name") or ""),
        "owner_mailing_address": "",
        "out_of_state_owner": False,
        "tax_delinquent": False,
        "vacant": False,
        "high_equity": False,
        "cash_buyer": False,
        "investor_owned": False,
        "latitude": fields["latitude"],
        "longitude": fields["longitude"],
        "zpid": item.get("zpid"),
        "mls_id": item.get("mlsId") or item.get("mls_id") or source.get("listing_id"),
        "listing_agent_name": item.get("listing_agent_name") or item.get("agentName") or source_agent.get("agent_name"),
        "listing_agent_phone": item.get("listing_agent_phone") or item.get("agentPhone"),
        "broker_name": item.get("broker_name") or item.get("brokerName") or source_agent.get("office_name"),
        "detail_url": item.get("detailUrl") or item.get("detail_url") or item.get("href") or item.get("url"),
        "listing_date": item.get("list_date") or item.get("listDate"),
        "last_sold_date": item.get("last_sold_date") or item.get("lastSoldDate"),
        "last_sold_price": safe_int(item.get("last_sold_price") or item.get("lastSoldPrice"), None),
        "is_live_listing": True,
        "data_source": source_name,
        "source_platform": "Realtor.com" if "realtor.com" in str(item.get("href") or "").lower() else None,
        "source_mls": source.get("name"),
        "source_disclaimer": _as_dict(source.get("disclaimer")).get("text"),
        "data_provenance": {
            "listing": source_name,
            "underlying_platform": "Realtor.com" if "realtor.com" in str(item.get("href") or "").lower() else None,
            "mls": source.get("name"),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        },
        "raw_source_excerpt": item,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "listing_last_seen_at": datetime.now(timezone.utc).isoformat(),
        "missed_syncs": 0,
    }
    prop.update(derive_owner_signals(
        prop.get("owner_name") or "",
        prop.get("owner_mailing_address") or "",
        prop.get("situs_address") or "",
        prop.get("state") or "TX",
    ))
    prop.update(compute_scores(prop))
    return prop


async def fetch_live_fort_worth_residential_listings(limit: int = 50) -> List[Dict[str, Any]]:
    """Try multiple RapidAPI listing endpoints and normalize live Fort Worth residential listings.

    The endpoint names vary between RapidAPI providers/plans, so this function tries
    several common patterns and uses the first successful responses.
    """
    attempts = []
    if OPENWEB_NINJA_REAL_ESTATE_API_KEY:
        attempts.append({
            "provider": "openweb",
            "api_key": OPENWEB_NINJA_REAL_ESTATE_API_KEY,
            "base_url": OPENWEB_NINJA_REAL_ESTATE_BASE_URL,
            "path": "/search",
            "params": {
                "location": "Fort Worth, TX",
                "home_status": "FOR_SALE",
            },
            "source": "OpenWeb Ninja Real-Time Real Estate Data /zillow/search",
        })
    if OPENWEB_NINJA_ZILLOW_API_KEY:
        attempts.append({
            "provider": "openweb",
            "api_key": OPENWEB_NINJA_ZILLOW_API_KEY,
            "base_url": OPENWEB_NINJA_ZILLOW_BASE_URL,
            "path": "/search",
            "params": {
                "location": "Fort Worth, TX",
                "home_status": "FOR_SALE",
            },
            "source": "OpenWeb Ninja Real-Time Zillow Data /search",
        })
    attempts.extend([
        {
            "provider": "rapidapi",
            "host": HOST_REALTIME,
            "path": "/search",
            "params": {"location": "Fort Worth, TX", "status_type": "ForSale", "home_type": "Houses", "sort": "NEWEST", "limit": limit},
            "source": "RapidAPI real-time-real-estate-data /search",
        },
        {
            "provider": "rapidapi",
            "host": HOST_REALTIME,
            "path": "/search-by-location",
            "params": {"location": "Fort Worth, TX", "status_type": "ForSale", "home_type": "Houses", "sort": "NEWEST", "limit": limit},
            "source": "RapidAPI real-time-real-estate-data /search-by-location",
        },
        {
            "provider": "rapidapi",
            "host": HOST_REALTIME,
            "path": "/propertyExtendedSearch",
            "params": {"location": "Fort Worth, TX", "status_type": "ForSale", "home_type": "Houses", "sort": "NEWEST", "limit": limit},
            "source": "RapidAPI real-time-real-estate-data /propertyExtendedSearch",
        },
        {
            "provider": "rapidapi",
            "host": HOST_REALTIME,
            "path": "/properties/list",
            "params": {"location": "Fort Worth, TX", "status_type": "ForSale", "home_type": "Houses", "limit": limit},
            "source": "RapidAPI real-time-real-estate-data /properties/list",
        },
        {
            "provider": "rapidapi",
            "host": HOST_LISTINGS,
            "path": "/for-sale",
            "params": {"location": "Fort Worth, TX", "property_type": "single_family", "limit": limit},
            "source": "RapidAPI us-real-estate-listings /for-sale",
        },
    ])

    normalized: List[Dict[str, Any]] = []
    errors: List[str] = []

    for attempt in attempts:
        try:
            if attempt["provider"] == "openweb":
                raw = await _openweb_get(
                    attempt["path"],
                    attempt["params"],
                    api_key=attempt["api_key"],
                    base_url=attempt["base_url"],
                )
            else:
                raw = await _rapid_get(attempt["host"], attempt["path"], attempt["params"])
            items = _deep_find_items(raw)
            for item in items:
                prop = normalize_live_listing(item, attempt["source"])
                if prop:
                    normalized.append(prop)
            if normalized:
                break
        except Exception as e:
            errors.append(f"{attempt['source']}: {str(e)[:200]}")
            logger.info("Live listing attempt failed: %s", errors[-1])

    # Deduplicate by address / id
    out = []
    seen = set()
    for p in normalized:
        key = (p.get("external_id") or p.get("situs_address") or "").lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(p)

    if not out and errors:
        logger.warning("No live listings fetched. Attempts: %s", errors)

    return out[:limit]


async def sync_live_listings_to_database(
    database: PostgresDatabase,
    limit: int = 50,
) -> Dict[str, Any]:
    """Upsert current listings and retire a listing only after two missed syncs.

    An empty provider response never marks existing data stale; this protects the
    database from transient provider failures or exhausted API quotas.
    """
    listings = await fetch_live_fort_worth_residential_listings(limit=limit)
    previous = await database.properties.find(
        {"is_live_listing": True}, {"_id": 0}
    ).to_list(length=5000)
    previous_by_id = {record.get("id"): record for record in previous if record.get("id")}
    def address_key(record: Dict[str, Any]) -> str:
        street = str(record.get("situs_address") or "").split(",", 1)[0].upper()
        return re.sub(r"[^A-Z0-9]", "", street) + ":" + str(record.get("zip") or "")[:5]
    previous_by_address = {address_key(record): record for record in previous if address_key(record) != ":"}

    upserted = 0
    returned_ids = set()
    for property_record in listings:
        existing = previous_by_id.get(property_record.get("id")) or previous_by_address.get(address_key(property_record), {})
        property_record = merge_live_refresh(
            existing, property_record
        )
        returned_ids.add(property_record["id"])
        await database.properties.update_one(
            {"id": property_record["id"]},
            {"$set": property_record},
            upsert=True,
        )
        upserted += 1

    missed = retired = 0
    if listings:
        now = datetime.now(timezone.utc).isoformat()
        for property_record in previous:
            if is_synthetic_property(property_record) or property_record.get("id") in returned_ids:
                continue
            missed_syncs = int(property_record.get("missed_syncs") or 0) + 1
            updates: Dict[str, Any] = {
                "missed_syncs": missed_syncs,
                "last_checked_at": now,
            }
            missed += 1
            if missed_syncs >= 2:
                updates.update({"is_live_listing": False, "listing_status": "stale"})
                retired += 1
            await database.properties.update_one(
                {"id": property_record["id"]}, {"$set": updates}
            )

    await database.live_sync_log.insert_one({
        "id": str(uuid.uuid4()),
        "sync_type": "live_listings",
        "source": "live Fort Worth residential listings",
        "status": "success" if listings else "empty",
        "count": upserted,
        "missed": missed,
        "retired": retired,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    return {
        "ok": bool(listings),
        "upserted": upserted,
        "missed": missed,
        "retired": retired,
        "items": listings,
        "rule": "Synced current Fort Worth for-sale houses; two missed syncs retire stale rows.",
        "note": (
            "No existing listings were retired because the provider returned no usable records."
            if not listings else None
        ),
    }


# ---------- Routes ----------
@api_router.get("/")
async def root():
    return {
        "name": "TarrantREI / InvestorFlip API",
        "status": "ok",
        "mode": "live residential listings enabled",
        "live_endpoints": [
            "POST /api/live/sync-fort-worth",
            "GET /api/live/fort-worth-listings",
            "GET /api/live/status",
        ],
    }


@api_router.get("/filters")
async def get_filters():
    out = []
    raw_total = await db.properties.count_documents({})
    all_docs = await db.properties.find({}, {"_id": 0}).to_list(length=5000)
    visible = [p for p in all_docs if is_user_visible_property(p)]

    for f in INVESTOR_FILTERS:
        count = sum(matches_investor_filter(p, f["key"]) for p in visible)
        out.append({**f, "count": count})

    return {
        "filters": out,
        "raw_total_before_flip_filter": raw_total,
        "synthetic_records_hidden": sum(is_synthetic_property(p) for p in all_docs),
        "rule": "InvestorFlip V1 only shows live/verified single-family houses and residential multi-family houses.",
    }


@api_router.post("/live/sync-fort-worth")
async def sync_live_fort_worth(limit: int = Query(50, ge=1, le=100)):
    return await sync_live_listings_to_database(db, limit=limit)


@api_router.get("/live/fort-worth-listings")
async def live_fort_worth_listings(limit: int = Query(50, ge=1, le=100)):
    docs = await db.properties.find(
        {"is_live_listing": True},
        {"_id": 0},
    ).sort("updated_at", -1).limit(limit * 3).to_list(length=limit * 3)

    items = [
        hydrate_listing_record(p)
        for p in docs
        if is_fort_worth_property(p) and is_user_visible_property(p)
    ][:limit]

    return {
        "count": len(items),
        "items": items,
        "rule": "Live Fort Worth residential for-sale listings only.",
    }


@api_router.get("/live/status")
async def live_status():
    total_live = await db.properties.count_documents({"is_live_listing": True})
    latest = await db.live_sync_log.find({}, {"_id": 0}).sort("created_at", -1).limit(5).to_list(length=5)
    rapidapi_ready = bool(RAPIDAPI_KEY)
    return {
        "rapidapi_configured": rapidapi_ready,
        "rapidapi_cakemls_enabled": RAPIDAPI_CAKEMLS_ENABLED,
        "rapidapi_realtor_search_enabled": RAPIDAPI_REALTOR_SEARCH_ENABLED,
        "rapidapi_realty_us_enabled": RAPIDAPI_REALTY_US_ENABLED,
        "openweb_ninja_zillow_configured": bool(OPENWEB_NINJA_ZILLOW_API_KEY),
        "openweb_ninja_real_estate_configured": bool(OPENWEB_NINJA_REAL_ESTATE_API_KEY),
        "providers": {
            "openweb_ninja_real_estate": {
                "configured": bool(OPENWEB_NINJA_REAL_ESTATE_API_KEY),
                "method": "GET",
                "endpoint": f"{OPENWEB_NINJA_REAL_ESTATE_BASE_URL}/search",
                "detail_endpoint": f"{OPENWEB_NINJA_REAL_ESTATE_BASE_URL}/property-details-address",
                "trigger": "live sync and property detail",
            },
            "openweb_ninja_zillow": {
                "configured": bool(OPENWEB_NINJA_ZILLOW_API_KEY),
                "method": "GET",
                "endpoint": f"{OPENWEB_NINJA_ZILLOW_BASE_URL}/search",
                "detail_endpoint": f"{OPENWEB_NINJA_ZILLOW_BASE_URL}/property-details-address",
                "trigger": "live sync and property detail fallback",
            },
            "rapidapi_cakemls": {
                "configured": rapidapi_ready,
                "enabled": RAPIDAPI_CAKEMLS_ENABLED,
                "method": "POST",
                "endpoint": f"https://{HOST_CAKEMLS}/api/mls/",
                "trigger": "property detail",
            },
            "rapidapi_realtor_search": {
                "configured": rapidapi_ready,
                "enabled": RAPIDAPI_REALTOR_SEARCH_ENABLED,
                "method": "GET",
                "endpoint": f"https://{HOST_REALTOR_SEARCH}/agents/detail-url",
                "trigger": "property detail when an agent profile URL exists",
            },
            "rapidapi_realty_us": {
                "configured": rapidapi_ready,
                "enabled": RAPIDAPI_REALTY_US_ENABLED,
                "method": "GET",
                "endpoint": f"https://{HOST_REALTY_US}/agents/v2/listings",
                "trigger": "property detail when a fulfillmentId exists",
            },
            "rapidapi_us_real_estate_listings": {
                "configured": rapidapi_ready,
                "method": "GET",
                "endpoints": [
                    f"https://{HOST_LISTINGS}/for-sale",
                    f"https://{HOST_LISTINGS}/location-suggest",
                    f"https://{HOST_LISTINGS}/taxHistory",
                ],
                "trigger": "live sync, address search fallback, and tax history",
            },
            "rapidapi_us_real_estate_data1": {
                "configured": rapidapi_ready,
                "method": "GET",
                "endpoints": [
                    f"https://{HOST_LOOKUP}/properties/lookup",
                    f"https://{HOST_LOOKUP}/properties/{{zpid}}",
                ],
                "trigger": "property detail fallback",
            },
        },
        "live_listing_count": total_live,
        "recent_syncs": latest,
        "sync_endpoint": "POST /api/live/sync-fort-worth",
    }


@api_router.get("/properties")
async def list_properties(
    filter: str = Query("live"),
    search: Optional[str] = Query(None),
    limit: int = Query(60, ge=1, le=200),
):
    q: Dict[str, Any] = {}
    q = apply_filter(filter, q)

    if search:
        regex = {"$regex": re.escape(search), "$options": "i"}
        q["$or"] = [
            {"situs_address": regex},
            {"city": regex},
            {"zip": regex},
            {"owner_name": regex},
        ]

    cursor = db.properties.find(q, {"_id": 0}).sort("price", -1).sort("updated_at", -1).limit(limit * 10)
    raw_items = await cursor.to_list(length=limit * 10)
    items = [
        hydrate_listing_record(p)
        for p in raw_items
        if is_user_visible_property(p)
    ][:limit]

    return {
        "count": len(items),
        "items": items,
        "rule": "InvestorFlip V1 shows priced listings first, then off-market distressed targets.",
    }


@api_router.get("/address-suggestions")
async def address_suggestions(
    query: str = Query(..., min_length=5, max_length=160),
    limit: int = Query(6, ge=1, le=10),
):
    """Return cached PropertyReach address suggestions without exposing the API key."""

    cleaned = " ".join(query.strip().split())
    cache_key = cleaned.casefold()
    cached = _address_suggestion_cache.get(cache_key)
    if cached and time.monotonic() - cached[0] < ADDRESS_SUGGESTION_CACHE_SECONDS:
        items = cached[1][:limit]
        return {"count": len(items), "items": items, "cached": True}

    provider_query = cleaned
    if "fort worth" not in cleaned.casefold():
        provider_query = f"{cleaned}, Fort Worth, TX"

    items: List[Dict[str, Any]] = []
    try:
        raw = await _rapid_get(
            HOST_PROPERTY_REACH,
            "/v1/suggestions",
            {"query": provider_query},
        )
        items = normalize_address_suggestions(raw)
    except HTTPException as exc:
        logger.info("PropertyReach suggestions failed: %s", exc.detail)

    if not items:
        try:
            raw = await _rapid_get(
                HOST_LISTINGS,
                "/location-suggest",
                {"query": provider_query},
            )
            items = normalize_address_suggestions(raw)
        except HTTPException as exc:
            logger.info("US Real Estate Listings location suggestions failed: %s", exc.detail)

    _address_suggestion_cache[cache_key] = (time.monotonic(), items)

    if len(_address_suggestion_cache) > 250:
        oldest_key = min(_address_suggestion_cache, key=lambda key: _address_suggestion_cache[key][0])
        _address_suggestion_cache.pop(oldest_key, None)

    return {"count": len(items[:limit]), "items": items[:limit], "cached": False}


@api_router.get("/properties/{property_id}")
async def get_property(property_id: str):
    doc = await db.properties.find_one({"id": property_id}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Property not found")
    if not is_user_visible_property(doc):
        raise HTTPException(404, "Property not available in verified search results")
    return hydrate_listing_record(doc)


@api_router.post("/properties/{property_id}/quill-analysis", response_model=QuillAnalyzeResponse)
async def property_quill_analysis(property_id: str):
    doc = await db.properties.find_one({"id": property_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Property not found")
    if not is_user_visible_property(doc):
        raise HTTPException(status_code=404, detail="Property not available in verified search results")
    return scout_analyze_property(doc)


@api_router.get("/properties/{property_id}/nearby")
async def get_nearby(property_id: str):
    base = await db.properties.find_one({"id": property_id}, {"_id": 0})
    if not base:
        raise HTTPException(404, "Property not found")
    if not is_user_visible_property(base):
        raise HTTPException(404, "Property not available in verified search results")

    zipc = base["zip"]
    near_foreclosures_raw = await db.properties.find(
        {"zip": zipc, "listing_type": {"$in": ["REO", "Foreclosure"]}, "id": {"$ne": property_id}},
        {"_id": 0},
    ).limit(20).to_list(length=20)
    near_investor_raw = await db.properties.find(
        {"zip": zipc, "investor_owned": True, "id": {"$ne": property_id}},
        {"_id": 0},
    ).limit(20).to_list(length=20)

    near_foreclosures = [
        hydrate_listing_record(p)
        for p in near_foreclosures_raw
        if is_user_visible_property(p)
    ][:4]
    near_investor = [
        hydrate_listing_record(p)
        for p in near_investor_raw
        if is_user_visible_property(p)
    ][:4]
    return {"nearby_foreclosures": near_foreclosures, "nearby_investor_purchases": near_investor}


@api_router.post("/properties/{property_id}/ai-analysis", response_model=AIAnalysisResponse)
async def ai_analysis(property_id: str):
    doc = await db.properties.find_one({"id": property_id}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Property not found")
    if not is_user_visible_property(doc):
        raise HTTPException(404, "Property not available in verified search results")

    cached = await db.ai_analysis.find_one({"property_id": property_id}, {"_id": 0})
    if cached and cached.get("narrative"):
        return AIAnalysisResponse(property_id=property_id, narrative=cached["narrative"])

    try:
        from emergentintegrations.llm.chat import LlmChat, UserMessage
        key = os.environ.get("EMERGENT_LLM_KEY")
        if not key:
            raise RuntimeError("EMERGENT_LLM_KEY missing")

        system = (
            "You are Quill, a senior real estate investor analyst. InvestorFlip V1 only analyzes "
            "single-family houses and residential multi-family houses for house flipping. "
            "Given a valid property record, produce a concise investment analysis in 4 short bullet points. "
            "Be specific, reference numbers (equity, taxes, ROI). End with a one-line verdict: "
            "'STRONG BUY', 'BUY', 'WATCH', or 'PASS'. Plain text only, no markdown."
        )
        chat = LlmChat(
            api_key=key,
            session_id=f"prop-{property_id}",
            system_message=system,
        ).with_model("anthropic", "claude-sonnet-4-6")

        payload = (
            f"Address: {doc.get('situs_address')}\n"
            f"Property Type: {get_property_type(doc)}\n"
            f"Data Source: {doc.get('data_source')}\n"
            f"Listing Type: {doc.get('listing_type')}\n"
            f"Owner: {doc.get('owner_name')} ({doc.get('owner_type')})\n"
            f"Asking Price: ${int(doc.get('price') or 0):,}\n"
            f"Market Value: ${int(doc.get('market_value') or 0):,}\n"
            f"Assessed Value: ${int(doc.get('assessed_value') or 0):,}\n"
            f"Annual Taxes: ${int(doc.get('annual_taxes') or 0):,}\n"
            f"Equity Estimate: ${int(doc.get('equity_estimate') or 0):,}\n"
            f"Est ROI: {doc.get('est_roi_pct')}%\n"
            f"Beds/Baths/SqFt: {doc.get('beds')}/{doc.get('baths')}/{doc.get('sqft')}\n"
            f"Year Built: {doc.get('year_built')}\n"
            f"Scores → Investment {doc.get('investment_score')}, Flip {doc.get('flip_score')}, "
            f"Rental {doc.get('rental_score')}, Wholesale {doc.get('wholesale_score')}, Risk {doc.get('risk_score')}"
        )
        msg = UserMessage(text=payload)
        narrative = await chat.send_message(msg)
        narrative = (narrative or "").strip()
        if not narrative:
            raise RuntimeError("Empty LLM response")

        await db.ai_analysis.update_one(
            {"property_id": property_id},
            {"$set": {"property_id": property_id, "narrative": narrative,
                      "created_at": datetime.now(timezone.utc).isoformat()}},
            upsert=True,
        )
        return AIAnalysisResponse(property_id=property_id, narrative=narrative)
    except Exception as e:
        logger.exception("AI analysis failed: %s", e)
        investment_score = doc.get("investment_score") or 0
        verdict = "STRONG BUY" if investment_score >= 75 else (
            "BUY" if investment_score >= 60 else (
                "WATCH" if investment_score >= 45 else "NEEDS DATA"
            )
        )
        narrative = (
            f"• {doc.get('listing_type', 'For Sale')} {doc.get('property_type', 'house')} in {doc.get('city')} with "
            f"${int(doc.get('equity_estimate') or 0):,} of estimated equity ({doc.get('est_roi_pct')}% ROI).\n"
            f"• Data source: {doc.get('data_source')}.\n"
            f"• Asking price ${int(doc.get('price') or 0):,}; estimated market value ${int(doc.get('market_value') or 0):,}.\n"
            f"• Risk score {doc.get('risk_score')}/99 — verify comps, repairs, title, and taxes before offer.\n"
            f"Verdict: {verdict}"
        )
        return AIAnalysisResponse(property_id=property_id, narrative=narrative)


@api_router.get("/saved")
async def list_saved():
    docs = await db.saved.find({}, {"_id": 0}).to_list(length=500)
    ids = [d["property_id"] for d in docs]
    if not ids:
        return {"count": 0, "items": []}
    props_raw = await db.properties.find({"id": {"$in": ids}}, {"_id": 0}).to_list(length=500)
    props = [
        hydrate_listing_record(p)
        for p in props_raw
        if is_user_visible_property(p)
    ]
    return {"count": len(props), "items": props}


@api_router.post("/saved")
async def add_saved(body: SaveRequest):
    exists = await db.properties.find_one({"id": body.property_id}, {"_id": 0})
    if not exists:
        raise HTTPException(404, "Property not found")
    if not is_user_visible_property(exists):
        raise HTTPException(404, "Property not available in verified search results")
    await db.saved.update_one(
        {"property_id": body.property_id},
        {"$set": {"property_id": body.property_id,
                  "saved_at": datetime.now(timezone.utc).isoformat()}},
        upsert=True,
    )
    return {"ok": True, "property_id": body.property_id}


@api_router.delete("/saved/{property_id}")
async def remove_saved(property_id: str):
    await db.saved.delete_one({"property_id": property_id})
    return {"ok": True, "property_id": property_id}


@api_router.get("/saved/ids")
async def saved_ids():
    docs = await db.saved.find({}, {"_id": 0, "property_id": 1}).to_list(length=500)
    return {"ids": [d["property_id"] for d in docs]}


@api_router.get("/owners/classify")
async def classify(name: str):
    return {"name": name, "type": classify_owner(name)}


# ---------- Existing Feed Sync, Upload, Export ----------
@api_router.post("/feeds/sync")
async def feeds_sync(only: Optional[str] = None, limit: int = 50):
    result = await feeds_mod.run_feed_sync(
        db, classify_owner, compute_scores,
        only_feed=only, limit_per_feed=limit,
    )
    return result


@api_router.get("/feeds/status")
async def feeds_status():
    out = []
    for f in feeds_mod.FEEDS:
        cnt = await db.properties.count_documents({"data_source": {"$regex": f.name, "$options": "i"}})
        out.append({"name": f.name, "properties_from_feed": cnt})
    return {"feeds": out}


@api_router.post("/feeds/upload-csv")
async def feeds_upload_csv(
    file: UploadFile = File(...),
    feed_source: str = Form("CSV Upload"),
    listing_type: str = Form("Foreclosure"),
):
    raw = await file.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin1")
    counts = await feeds_mod.ingest_csv_text(
        db, text, feed_source, listing_type, classify_owner, compute_scores,
    )
    return {"ok": True, "feed_source": feed_source, "listing_type": listing_type, **counts}


@api_router.post("/propstream/merge")
async def propstream_merge(
    marketing_file: UploadFile = File(...),
    contacts_file: UploadFile = File(...),
):
    marketing_bytes = await marketing_file.read()
    contacts_bytes = await contacts_file.read()

    marketing_df = pd.read_excel(BytesIO(marketing_bytes))
    contacts_df = pd.read_csv(BytesIO(contacts_bytes))

    match_columns = [
        "Owner 1 First Name",
        "Owner 1 Last Name",
        "Mailing Address",
        "Mailing City",
        "Mailing State",
        "Mailing Zip",
    ]

    contacts_df = contacts_df.drop(columns=["Street Address", "City", "State", "Zip"], errors="ignore")
    contacts_df = contacts_df.rename(columns={
        "First Name": "Owner 1 First Name",
        "Last Name": "Owner 1 Last Name",
        "Mail Street Address": "Mailing Address",
        "Mail City": "Mailing City",
        "Mail State": "Mailing State",
        "Mail Zip": "Mailing Zip",
    })

    missing_marketing = [c for c in match_columns if c not in marketing_df.columns]
    missing_contacts = [c for c in match_columns if c not in contacts_df.columns]

    if missing_marketing or missing_contacts:
        raise HTTPException(
            status_code=400,
            detail={
                "message": "Missing required PropStream matching columns.",
                "missing_marketing_columns": missing_marketing,
                "missing_contacts_columns": missing_contacts,
            }
        )

    merged_df = pd.merge(marketing_df, contacts_df, on=match_columns, how="left")
    output = BytesIO()
    merged_df.to_csv(output, index=False)
    output.seek(0)

    return StreamingResponse(
        output,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=propstream_merged_leads.csv"}
    )


@api_router.get("/export.csv")
async def export_csv(
    filter: str = Query("live"),
    search: Optional[str] = Query(None),
    limit: int = Query(10000, ge=1, le=50000),
):
    q: Dict[str, Any] = {}
    q = apply_filter(filter, q)
    if search:
        rg = {"$regex": re.escape(search), "$options": "i"}
        q["$or"] = [{"situs_address": rg}, {"city": rg}, {"zip": rg}, {"owner_name": rg}]
    docs_raw = await db.properties.find(q, {"_id": 0}).limit(limit).to_list(length=limit)
    docs = [p for p in docs_raw if is_user_visible_property(p)]
    csv_text = feeds_mod.docs_to_csv(docs)
    fname = f"investorflip_{filter}_{datetime.now(timezone.utc).strftime('%Y%m%d')}.csv"
    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@api_router.get("/export.xlsx")
async def export_xlsx(
    filter: str = Query("live"),
    search: Optional[str] = Query(None),
    limit: int = Query(10000, ge=1, le=50000),
):
    q: Dict[str, Any] = {}
    q = apply_filter(filter, q)
    if search:
        rg = {"$regex": re.escape(search), "$options": "i"}
        q["$or"] = [{"situs_address": rg}, {"city": rg}, {"zip": rg}, {"owner_name": rg}]
    docs_raw = await db.properties.find(q, {"_id": 0}).limit(limit).to_list(length=limit)
    docs = [p for p in docs_raw if is_user_visible_property(p)]
    blob = feeds_mod.docs_to_xlsx_bytes(docs)
    fname = f"investorflip_{filter}_{datetime.now(timezone.utc).strftime('%Y%m%d')}.xlsx"
    return Response(
        content=blob,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


# ---------- Property Enrichment / Tax ----------
def _build_address_query(prop: Dict[str, Any]) -> str:
    return build_provider_address_query(prop)


@api_router.post("/properties/{property_id}/enrich")
async def enrich_property(property_id: str):
    prop = await db.properties.find_one({"id": property_id}, {"_id": 0})
    if not prop:
        raise HTTPException(404, "Property not found")
    if not is_user_visible_property(prop):
        raise HTTPException(404, "Property not available in verified search results")
    prop = hydrate_listing_record(prop)

    cached = await db.enrichment.find_one({"property_id": property_id}, {"_id": 0})
    if cached and cached.get("zpid") and cached.get("property_detail_cache_version") == PROPERTY_DETAIL_CACHE_VERSION:
        await _persist_enrichment(property_id, cached, cached.get("photos"))
        return cached

    if cached and cached.get("zpid"):
        enriched = await _add_full_property_details(cached)
        await _persist_enrichment(property_id, enriched, enriched.get("photos"))
        return enriched

    address = _build_address_query(prop)

    if RAPIDAPI_CAKEMLS_ENABLED and RAPIDAPI_KEY:
        try:
            raw = await _rapid_post(HOST_CAKEMLS, "/api/mls/", {"address": address})
            detail = normalize_property_detail(raw)
            useful = any(
                detail.get(key)
                for key in (
                    "beds",
                    "baths",
                    "sqft",
                    "year_built",
                    "list_price",
                    "mls_id",
                    "photos",
                )
            )
            if useful:
                enriched = {
                    "property_id": property_id,
                    "address_queried": address,
                    "source_api": "CakeMLS via RapidAPI",
                    "found": True,
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                    **detail,
                }
                enriched["property_detail_cache_version"] = PROPERTY_DETAIL_CACHE_VERSION
                enriched["property_detail_found"] = True
                enriched = await _add_realtor_agent_details(enriched)
                enriched = await _add_realty_us_agent_listings(enriched, prop)
                photo_urls = enriched.get("photos") if isinstance(enriched.get("photos"), list) else []
                await _persist_enrichment(property_id, enriched, photo_urls)
                return enriched
        except HTTPException as exc:
            logger.info("CakeMLS enrichment failed: %s", exc.detail)

    openweb_detail_providers = [
        (
            "OpenWeb Ninja Real-Time Real Estate Data",
            OPENWEB_NINJA_REAL_ESTATE_API_KEY,
            OPENWEB_NINJA_REAL_ESTATE_BASE_URL,
        ),
        (
            "OpenWeb Ninja Real-Time Zillow Data",
            OPENWEB_NINJA_ZILLOW_API_KEY,
            OPENWEB_NINJA_ZILLOW_BASE_URL,
        ),
    ]
    for provider_name, api_key, base_url in openweb_detail_providers:
        if not api_key:
            continue
        try:
            raw = await _openweb_get(
                "/property-details-address",
                {"address": address},
                api_key=api_key,
                base_url=base_url,
            )
            detail = normalize_property_detail(raw)
            if detail.get("detail_found"):
                enriched = {
                    "property_id": property_id,
                    "address_queried": address,
                    "source_api": provider_name,
                    "found": True,
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                    **detail,
                }
                enriched["property_detail_cache_version"] = PROPERTY_DETAIL_CACHE_VERSION
                enriched["property_detail_found"] = True
                enriched = await _add_realtor_agent_details(enriched)
                enriched = await _add_realty_us_agent_listings(enriched, prop)
                photo_urls = enriched.get("photos") if isinstance(enriched.get("photos"), list) else []
                await _persist_enrichment(property_id, enriched, photo_urls)
                return enriched
        except HTTPException as exc:
            logger.info("%s enrichment failed: %s", provider_name, exc.detail)

    try:
        raw = await _rapid_get(HOST_LOOKUP, "/properties/lookup", {"address": address})
        meta = raw.get("meta") or {}
        data = raw.get("data") or {}
        if meta.get("matched") and data:
            enriched = {
                "property_id": property_id,
                "address_queried": address,
                "source_api": "us-real-estate-data1",
                "found": True,
                "zpid": data.get("zpid"),
                "beds": data.get("beds"),
                "baths": data.get("baths"),
                "sqft": data.get("area_sqft"),
                "year_built": data.get("year_built"),
                "home_type": data.get("home_type"),
                "home_status": data.get("status"),
                "list_price": data.get("price"),
                "zestimate": data.get("zestimate"),
                "rent_zestimate": data.get("rent_zestimate") or data.get("rentZestimate"),
                "tax_assessed_value": data.get("tax_assessed_value"),
                "latitude": data.get("latitude"),
                "longitude": data.get("longitude"),
                "rapidapi_address": data.get("street"),
                "rapidapi_city": data.get("city"),
                "rapidapi_state": data.get("state"),
                "rapidapi_zip": data.get("zipcode"),
                "mls_id": data.get("mls_id") or data.get("mlsId"),
                "parcel_id": data.get("parcel_id") or data.get("parcelId"),
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            }
            photo_values = data.get("photos") if isinstance(data.get("photos"), list) else []
            photo_urls = [url for url in (_photo_url(value) for value in photo_values) if url]
            if photo_urls:
                enriched["photos"] = photo_urls
            enriched = await _add_full_property_details(enriched)
            enriched = await _add_realtor_agent_details(enriched)
            enriched = await _add_realty_us_agent_listings(enriched, prop)
            photo_urls = enriched.get("photos") if isinstance(enriched.get("photos"), list) else photo_urls
            await _persist_enrichment(property_id, enriched, photo_urls)
            return enriched
    except HTTPException as e:
        logger.info("Primary enrichment failed: %s", e.detail)

    return {"property_id": property_id, "address_queried": address, "found": False}


async def _add_full_property_details(enriched: Dict[str, Any]) -> Dict[str, Any]:
    """Fetch and normalize /properties/{zpid}; retain lookup data on failure."""
    result = dict(enriched)
    result["property_detail_cache_version"] = PROPERTY_DETAIL_CACHE_VERSION
    result["property_detail_attempted_at"] = datetime.now(timezone.utc).isoformat()
    zpid = str(result.get("zpid") or "").strip()
    if not zpid or not re.fullmatch(r"[A-Za-z0-9_-]+", zpid):
        result["property_detail_found"] = False
        return result

    try:
        raw = await _rapid_get(HOST_LOOKUP, f"/properties/{zpid}", {})
        detail = normalize_property_detail(raw)
        result.update(detail)
        result["property_detail_found"] = bool(detail.get("detail_found"))
        result["property_detail_endpoint"] = f"/properties/{zpid}"
        result["property_detail_fetched_at"] = datetime.now(timezone.utc).isoformat()
    except HTTPException as exc:
        result["property_detail_found"] = False
        result["property_detail_status"] = exc.status_code
        logger.info("Full property detail failed for %s: %s", zpid, exc.detail)
    return result


async def _persist_enrichment(property_id: str, enriched: Dict[str, Any], photo_urls: Optional[List[str]] = None) -> None:
    existing = await db.properties.find_one({"id": property_id}, {"_id": 0}) or {}
    update: Dict[str, Any] = {}
    if enriched.get("beds"):
        update["beds"] = safe_float(enriched["beds"])
    if enriched.get("baths"):
        update["baths"] = safe_float(enriched["baths"])
    if enriched.get("sqft"):
        update["sqft"] = safe_int(enriched["sqft"])
    if enriched.get("year_built"):
        update["year_built"] = safe_int(enriched["year_built"])
    if enriched.get("lot_size_sqft"):
        update["lot_size_sqft"] = safe_int(enriched["lot_size_sqft"])
    if enriched.get("latitude") and enriched.get("longitude"):
        update["latitude"] = enriched["latitude"]
        update["longitude"] = enriched["longitude"]
    if enriched.get("home_type"):
        update["home_type"] = enriched["home_type"]
        update["property_type"] = enriched["home_type"]
    if enriched.get("zestimate"):
        update["market_value"] = safe_int(enriched["zestimate"])
        update["zestimate"] = safe_int(enriched["zestimate"])
        update["market_value_source"] = "third-party automated estimate"
    if enriched.get("rent_zestimate"):
        update["rent_zestimate"] = safe_int(enriched["rent_zestimate"])
    if enriched.get("tax_assessed_value"):
        update["provider_tax_assessed_value"] = safe_int(enriched["tax_assessed_value"])
    if enriched.get("mls_id"):
        update["mls_id"] = str(enriched["mls_id"])
    if enriched.get("source_mls"):
        update["source_mls"] = str(enriched["source_mls"])
    if enriched.get("parcel_id"):
        update["parcel_id"] = str(enriched["parcel_id"])
    if enriched.get("listing_agent_name"):
        update["listing_agent_name"] = enriched["listing_agent_name"]
    if enriched.get("listing_agent_phone"):
        update["listing_agent_phone"] = enriched["listing_agent_phone"]
    if enriched.get("listing_agent_email"):
        update["listing_agent_email"] = enriched["listing_agent_email"]
    if enriched.get("listing_agent_url"):
        update["listing_agent_url"] = enriched["listing_agent_url"]
    if enriched.get("listing_agent_fulfillment_id"):
        update["listing_agent_fulfillment_id"] = str(enriched["listing_agent_fulfillment_id"])
    if enriched.get("listing_agent_rating"):
        update["listing_agent_rating"] = enriched["listing_agent_rating"]
    if enriched.get("listing_agent_review_count") is not None:
        update["listing_agent_review_count"] = enriched["listing_agent_review_count"]
    if enriched.get("listing_agent_photo_url"):
        update["listing_agent_photo_url"] = enriched["listing_agent_photo_url"]
    if enriched.get("agent_listings"):
        update["agent_listings"] = enriched["agent_listings"]
        update["agent_listings_source"] = enriched.get("agent_listings_source")
    if enriched.get("broker_name"):
        update["broker_name"] = enriched["broker_name"]
    if enriched.get("description"):
        update["listing_description"] = enriched["description"]
    if photo_urls:
        update["image_url"] = photo_urls[0]
    if update:
        combined = {**existing, **update}
        update.update(compute_scores(combined))
        update["updated_at"] = datetime.now(timezone.utc).isoformat()
        await db.properties.update_one({"id": property_id}, {"$set": update})
    await db.enrichment.update_one({"property_id": property_id}, {"$set": enriched}, upsert=True)


@api_router.get("/properties/{property_id}/tax-history")
async def tax_history(property_id: str):
    enr = await db.enrichment.find_one({"property_id": property_id}, {"_id": 0})
    if not enr or not enr.get("zpid"):
        await enrich_property(property_id)
        enr = await db.enrichment.find_one({"property_id": property_id}, {"_id": 0})
    if not enr or not enr.get("zpid"):
        return {"property_id": property_id, "tax_history": [], "available": False}

    provider_history = enr.get("provider_tax_history")
    if isinstance(provider_history, list) and provider_history:
        history = []
        for entry in provider_history:
            if not isinstance(entry, dict):
                continue
            year = safe_int(entry.get("year") or entry.get("time"))
            if not year:
                continue
            assessed = safe_int(
                entry.get("assessed_value")
                or entry.get("assessedValue")
                or entry.get("value")
            )
            market = safe_int(entry.get("market_value") or entry.get("marketValue"))
            normalized = {
                "year": year,
                "tax": safe_int(entry.get("tax") or entry.get("taxPaid"), 0),
            }
            if assessed:
                normalized["assessment"] = {"total": assessed}
            if market:
                normalized["market"] = {"total": market}
            history.append(normalized)
        if history:
            return {
                "property_id": property_id,
                "tax_history": history,
                "available": True,
                "source": "OpenWeb Ninja Real-Time Zillow Data",
            }

    cached = await db.tax_history.find_one({"property_id": property_id}, {"_id": 0})
    if cached and cached.get("tax_history"):
        return {"property_id": property_id, "tax_history": cached["tax_history"], "available": True}

    try:
        raw = await _rapid_get(HOST_LISTINGS, "/taxHistory", {"id": str(enr["zpid"])})
    except HTTPException as e:
        return {"property_id": property_id, "tax_history": [], "available": False, "error": str(e.detail)}

    history = raw.get("tax_history") or []
    await db.tax_history.update_one(
        {"property_id": property_id},
        {"$set": {"property_id": property_id, "zpid": enr["zpid"], "tax_history": history,
                  "fetched_at": datetime.now(timezone.utc).isoformat()}},
        upsert=True,
    )
    return {"property_id": property_id, "tax_history": history, "available": bool(history)}


# ---------- Scout + Quill ----------
@api_router.post("/scout/analyze-deal")
async def analyze_deal():
    return {"message": "Scout analyze deal endpoint is working"}


@api_router.post("/scout/find-opportunities")
async def find_opportunities(limit: int = Query(10, ge=1, le=100)):
    raw_docs = await db.properties.find({"is_live_listing": True}, {"_id": 0}).to_list(length=500)
    docs = [p for p in raw_docs if is_user_visible_property(p)]

    opportunities = []
    for p in docs:
        score = p.get("investment_score") or 0
        if p.get("high_equity"):
            score += 10
        if p.get("tax_delinquent"):
            score += 10
        if p.get("vacant"):
            score += 10
        if p.get("out_of_state_owner"):
            score += 8
        if p.get("listing_type") in ["REO", "Foreclosure", "Cash House"]:
            score += 12
        if p.get("owner_type") in ["Bank", "Trust", "LLC"]:
            score += 6
        score = min(score, 100)

        reason = []
        if p.get("is_live_listing"):
            reason.append("Live for-sale listing")
        if p.get("high_equity"):
            reason.append("Potential equity spread")
        if p.get("listing_type") in ["REO", "Foreclosure", "Cash House"]:
            reason.append(f"{p.get('listing_type')} property")

        opportunities.append({
            "id": p.get("id"),
            "address": p.get("situs_address"),
            "city": p.get("city"),
            "property_type": p.get("property_type") or p.get("home_type"),
            "price": p.get("price"),
            "market_value": p.get("market_value"),
            "equity_estimate": p.get("equity_estimate"),
            "listing_type": p.get("listing_type"),
            "owner_type": p.get("owner_type"),
            "opportunity_score": score,
            "priority": "Call First" if score >= 85 else "Strong Lead" if score >= 70 else "Review",
            "reason": reason or ["General house-flip opportunity"],
        })

    opportunities = sorted(opportunities, key=lambda x: x["opportunity_score"], reverse=True)[:limit]
    return {
        "count": len(opportunities),
        "best_today": opportunities,
        "rule": "Live Fort Worth residential listings only.",
    }


@api_router.post("/admin/backfill-flip-property-types")
async def backfill_flip_property_types():
    docs = await db.properties.find({}, {"_id": 0}).to_list(length=10000)
    updated = 0
    skipped = 0
    examples = []

    for p in docs:
        if get_property_type(p):
            skipped += 1
            continue

        inferred = infer_legacy_property_type(p)
        if not inferred:
            skipped += 1
            continue

        await db.properties.update_one(
            {"id": p.get("id")},
            {"$set": {
                "property_type": inferred,
                "home_type": inferred,
                "property_type_source": "legacy backfill",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }}
        )
        updated += 1
        if len(examples) < 10:
            examples.append({
                "id": p.get("id"),
                "address": p.get("situs_address"),
                "property_type": inferred,
            })

    return {
        "ok": True,
        "updated": updated,
        "skipped": skipped,
        "examples": examples,
    }


@api_router.get("/admin/data-quality")
async def data_quality():
    docs = await db.properties.find({}, {"_id": 0}).to_list(length=10000)
    allowed = [p for p in docs if is_user_visible_property(p)]
    live = [p for p in allowed if p.get("is_live_listing")]
    missing_type = [p for p in docs if not get_property_type(p)]

    return {
        "raw_total": len(docs),
        "synthetic_hidden": sum(is_synthetic_property(p) for p in docs),
        "allowed_flip_houses": len(allowed),
        "live_allowed_flip_houses": len(live),
        "missing_property_type": len(missing_type),
        "sample_live_allowed": [
            {"address": p.get("situs_address"), "property_type": get_property_type(p), "price": p.get("price")}
            for p in live[:10]
        ],
        "rule": "InvestorFlip V1 prioritizes live single-family houses and residential multi-family houses.",
    }


@api_router.delete("/admin/cleanup-demo-properties")
async def cleanup_demo_properties():
    result = await db.properties.delete_many({"data_source": {"$regex": "Demo Seed Data", "$options": "i"}})
    return {"ok": True, "deleted_demo_records": result.deleted_count}


@api_router.delete("/admin/cleanup-non-flip-properties")
async def cleanup_non_flip_properties():
    docs = await db.properties.find({}, {"_id": 0}).to_list(length=5000)
    deleted = 0
    kept = 0
    blocked_examples = []

    for p in docs:
        if is_allowed_flip_house(p):
            kept += 1
            continue
        if len(blocked_examples) < 10:
            blocked_examples.append({
                "id": p.get("id"),
                "address": p.get("situs_address"),
                "property_type": get_property_type(p),
            })
        await db.properties.delete_one({"id": p.get("id")})
        deleted += 1

    return {
        "ok": True,
        "kept": kept,
        "deleted": deleted,
        "blocked_examples": blocked_examples,
        "rule": "Kept only single-family houses and residential multi-family houses.",
    }


@api_router.post("/scout/quill-analysis", response_model=QuillAnalyzeResponse)
async def scout_quill_analysis(body: QuillAnalyzeRequest):
    return analyze_property_with_quill(body)


@api_router.post("/ai/analyze-property", response_model=QuillAnalyzeResponse)
async def quill_analyze_property(body: QuillAnalyzeRequest):
    return analyze_property_with_quill(body)


# Include router
start_background_sync(app)

app.include_router(all_router)  # FREE data sources (violations, foreclosures, OffMarketDeck, SmartPropLeads, etc.)
app.include_router(saved_searches_router)
app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def on_startup():
    await db.connect()
    count = await db.properties.count_documents({})
    seed_demo = os.environ.get("SEED_DEMO_DATA", "false").lower() == "true"

    # Do NOT seed demo data by default anymore. This prevents fake commercial-looking addresses
    # from appearing as houses.
    if count == 0 and seed_demo:
        seeds = generate_seed_properties(36)
        await db.properties.insert_many(seeds)
        logger.info("Seeded %d demo Tarrant County flip-house properties", len(seeds))
    else:
        logger.info("Properties collection has %d docs. Demo seeding disabled by default.", count)


@app.on_event("shutdown")
async def on_shutdown():
    await db.close()
