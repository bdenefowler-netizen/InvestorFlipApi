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
from motor.motor_asyncio import AsyncIOMotorClient
from ai.models import QuillAnalyzeRequest, QuillAnalyzeResponse
from ai.quill import analyze_property_with_quill
from ai.scout import scout_analyze_property
import os
import re
import random
import logging
import pandas as pd
from io import BytesIO
from pathlib import Path
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
import uuid
from datetime import datetime, timezone
import httpx
from importers import feeds as feeds_mod

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

# MongoDB
mongo_url = os.environ["MONGO_URL"]
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ["DB_NAME"]]

app = FastAPI(title="TarrantREI / InvestorFlip API")
api_router = APIRouter(prefix="/api")

logger = logging.getLogger("tarrantrei")
logging.basicConfig(level=logging.INFO)


# ---------- Owner Classifier ----------
LAW_FIRM_KEYWORDS = [
    "law office", "law offices", "attorney", "attorneys", "legal",
    "counsel", "litigation", "law firm", "law group", "lawyer",
]
LAW_FIRM_SUFFIXES = ["LLP", "PLLC", "PC", "P.C.", "P.L.L.C."]
KNOWN_LAW_FIRMS = ["Jackson Walker", "Thompson Knight", "Kelly Hart"]

BANK_KEYWORDS = [
    "bank", "mortgage", "wells fargo", "chase", "bank of america",
    "citibank", "fannie mae", "freddie mac", "hud", "us bank",
    "deutsche bank", "nationstar", "mr. cooper", "carrington",
]
TRUST_KEYWORDS = ["trust", "trustee", "family trust", "living trust", "revocable"]
LLC_KEYWORDS = [" llc", "l.l.c.", "limited liability", " ll", " investments"]
CORP_KEYWORDS = [
    "inc.", " inc", "incorporated", "corporation", "corp.", "company",
    " co.", "brothers", "holdings", "partners", "properties", "realty", "group"
]
GOV_KEYWORDS = [
    "city of", "county of", "state of texas", "tarrant county", "federal",
    "department of", "housing authority", "isd",
]
NONPROFIT_KEYWORDS = [
    "nonprofit", "non-profit", "foundation", "charity", "habitat for humanity",
    "ministry", "church", "diocese",
]


def classify_owner(owner_name: str) -> str:
    if not owner_name:
        return "Individual"
    name = owner_name.strip()
    upper = name.upper()
    lower = name.lower()

    for firm in KNOWN_LAW_FIRMS:
        if firm.lower() in lower:
            return "Law Firm"
    for kw in LAW_FIRM_KEYWORDS:
        if kw in lower:
            return "Attorney" if "attorney" in kw or "lawyer" in kw else "Law Firm"
    if any(re.search(rf"\b{re.escape(suf)}\b", upper) for suf in LAW_FIRM_SUFFIXES):
        return "Law Firm"

    if any(k in lower for k in GOV_KEYWORDS):
        return "Government"
    if any(k in lower for k in NONPROFIT_KEYWORDS):
        return "Nonprofit"
    if any(k in lower for k in BANK_KEYWORDS):
        return "Bank"
    if any(k in lower for k in TRUST_KEYWORDS):
        return "Trust"
    if any(k in lower for k in LLC_KEYWORDS):
        return "LLC"
    if any(k in lower for k in CORP_KEYWORDS):
        return "Corporation"
    return "Individual"


# ---------- Scoring ----------
def compute_scores(p: Dict[str, Any]) -> Dict[str, int]:
    mv = max(1, int(p.get("market_value") or p.get("estimated_value") or p.get("price") or 1))
    asking = max(1, int(p.get("price") or mv))
    equity_pct = max(0.0, (mv - asking) / mv)
    annual_taxes = int(p.get("annual_taxes") or 0)
    tax_burden = annual_taxes / mv if mv else 0
    owner_type = p.get("owner_type", "Individual")
    listing_type = p.get("listing_type", "For Sale")
    year_built = int(p.get("year_built") or 1990)
    age = max(0, 2026 - year_built)

    investor_friendly = owner_type in ("Bank", "Government", "Trust")
    distress = listing_type in ("REO", "Foreclosure")

    investment = 50 + int(equity_pct * 80) + (15 if distress else 0) + (5 if investor_friendly else 0)
    wholesale = 40 + int(equity_pct * 100) + (20 if distress else 0)
    flip = 35 + int(equity_pct * 70) + (20 if age > 25 else 5) + (10 if distress else 0)
    rental = 60 + int((1 - tax_burden * 30) * 20) - (8 if age > 50 else 0)
    risk = 30 + (25 if distress else 0) + (15 if owner_type == "Bank" else 0) + (10 if age > 40 else 0)

    def clamp(v):
        return max(1, min(99, int(v)))

    return {
        "investment_score": clamp(investment),
        "wholesale_score": clamp(wholesale),
        "flip_score": clamp(flip),
        "rental_score": clamp(rental),
        "risk_score": clamp(risk),
    }


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
    {"key": "out_of_state", "label": "Out-of-State Owner"},
    {"key": "vacant", "label": "Vacant"},
    {"key": "corporate", "label": "Corporate Owner"},
    {"key": "trust", "label": "Trust-Owned"},
    {"key": "bank_owned", "label": "Bank-Owned"},
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
    return query


# ---------- Models ----------
class AIAnalysisResponse(BaseModel):
    property_id: str
    narrative: str


class SaveRequest(BaseModel):
    property_id: str


# ---------- RapidAPI Helpers ----------
RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY", "")
OPENWEB_NINJA_API_KEY = os.environ.get("OPENWEB_NINJA_API_KEY", "")
HOST_LOOKUP = "us-real-estate-data1.p.rapidapi.com"
HOST_LISTINGS = "us-real-estate-listings.p.rapidapi.com"
HOST_REALTIME = "real-time-real-estate-data.p.rapidapi.com"


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


def _extract_address(item: Dict[str, Any]) -> Dict[str, str]:
    raw_address = item.get("address")
    address_obj = raw_address if isinstance(raw_address, dict) else {}
    location_obj = item.get("location") if isinstance(item.get("location"), dict) else {}
    location_address = location_obj.get("address") if isinstance(location_obj.get("address"), dict) else {}

    street = (
        item.get("streetAddress") or item.get("street_address") or item.get("street")
        or item.get("address1") or item.get("addressLine") or item.get("address_line_1")
        or address_obj.get("streetAddress") or address_obj.get("street_address")
        or address_obj.get("street") or address_obj.get("line") or address_obj.get("address1")
        or location_obj.get("streetAddress") or location_obj.get("street")
        or location_address.get("streetAddress") or location_address.get("street")
        or location_address.get("line") or ""
    )
    city = (
        item.get("city") or item.get("addressCity") or item.get("locality")
        or address_obj.get("city") or address_obj.get("locality")
        or location_obj.get("city") or location_obj.get("locality")
        or location_address.get("city") or "Fort Worth"
    )
    state = (
        item.get("state") or item.get("addressState") or item.get("region")
        or address_obj.get("state") or address_obj.get("region")
        or location_obj.get("state") or location_obj.get("region")
        or location_address.get("state") or "TX"
    )
    zipc = (
        item.get("zipcode") or item.get("zip") or item.get("postal_code") or item.get("postalCode")
        or address_obj.get("zipcode") or address_obj.get("zip")
        or address_obj.get("postal_code") or address_obj.get("postalCode")
        or location_obj.get("postal_code") or location_obj.get("postalCode")
        or location_address.get("postal_code") or location_address.get("postalCode") or ""
    )

    full = (
        (raw_address if isinstance(raw_address, str) else "")
        or item.get("full_address") or item.get("fullAddress")
        or item.get("formattedAddress") or item.get("formatted_address")
        or item.get("address_line") or item.get("addressLine")
        or address_obj.get("formattedAddress") or address_obj.get("formatted_address")
        or location_obj.get("formattedAddress") or location_obj.get("formatted_address")
        or (f"{street}, {city}, {state} {zipc}".strip(", ") if street else "")
    )

    return {
        "street": str(street).strip(),
        "city": str(city).strip(),
        "state": str(state).strip() or "TX",
        "zip": str(zipc).strip(),
        "full": str(full).strip(),
    }


def normalize_live_listing(item: Dict[str, Any], source_name: str) -> Optional[Dict[str, Any]]:
    addr = _extract_address(item)

    if not addr["street"] and not addr["full"]:
        return None

    raw_type = (
        item.get("homeType") or item.get("home_type") or item.get("propertyType")
        or item.get("property_type") or item.get("propertySubType")
        or item.get("property_sub_type") or item.get("propertyTypeText")
        or item.get("property_type_name") or item.get("style") or item.get("type")
    )
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

    price = safe_int(
        item.get("price") or item.get("listPrice") or item.get("list_price") or item.get("asking_price") or item.get("unformattedPrice"),
        0,
    )
    zestimate = safe_int(item.get("zestimate") or item.get("estimate") or item.get("estimated_value"), None)
    market_value = zestimate or price or 0
    assessed_value = safe_int(item.get("taxAssessedValue") or item.get("tax_assessed_value"), market_value)
    annual_taxes = safe_int(item.get("annualTaxAmount") or item.get("annual_taxes") or item.get("taxAnnualAmount"), 0)

    beds = safe_float(item.get("beds") or item.get("bedrooms") or item.get("bedroom_count"), None)
    baths = safe_float(item.get("baths") or item.get("bathrooms") or item.get("bathroom_count") or item.get("bathroomsFloat"), None)
    sqft = safe_int(item.get("livingArea") or item.get("living_area") or item.get("square_feet") or item.get("building_size") or item.get("area") or item.get("area_sqft"), None)
    year_built = safe_int(item.get("yearBuilt") or item.get("year_built"), None)

    photos = []
    for k in ["imgSrc", "image", "image_url", "photo_url", "primary_photo", "hiResImageLink"]:
        if item.get(k):
            photos.append(item[k])
    for arr_key in ["photos", "originalPhotos", "responsivePhotos"]:
        arr = item.get(arr_key)
        if isinstance(arr, list):
            for p in arr[:5]:
                if isinstance(p, str):
                    photos.append(p)
                elif isinstance(p, dict):
                    if p.get("url"):
                        photos.append(p["url"])
                    mixed = p.get("mixedSources") if isinstance(p.get("mixedSources"), dict) else {}
                    jpeg = mixed.get("jpeg") if isinstance(mixed.get("jpeg"), list) else []
                    if jpeg and isinstance(jpeg[-1], dict) and jpeg[-1].get("url"):
                        photos.append(jpeg[-1]["url"])

    listing_status = str(item.get("homeStatus") or item.get("status") or item.get("listingStatus") or "For Sale")
    listing_type = "For Sale"
    if "foreclosure" in listing_status.lower():
        listing_type = "Foreclosure"
    elif "reo" in listing_status.lower():
        listing_type = "REO"

    equity_estimate = max(0, int(market_value or 0) - int(price or 0)) if market_value and price else 0
    est_roi_pct = round((equity_estimate / max(price or 1, 1)) * 100, 1) if price else 0

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
        "lot_size_sqft": safe_int(item.get("lotSize") or item.get("lot_size") or item.get("lotAreaValue"), None),
        "image_url": photos[0] if photos else None,
        "photos": photos,
        "price": price,
        "market_value": market_value,
        "assessed_value": assessed_value,
        "annual_taxes": annual_taxes,
        "equity_estimate": equity_estimate,
        "est_roi_pct": est_roi_pct,
        "legal_description": "",
        "listing_type": listing_type,
        "listing_status": listing_status,
        "owner_name": item.get("owner_name") or "",
        "owner_type": classify_owner(item.get("owner_name") or ""),
        "owner_mailing_address": "",
        "out_of_state_owner": False,
        "tax_delinquent": False,
        "vacant": False,
        "high_equity": equity_estimate > 0 and market_value and equity_estimate / max(market_value, 1) >= 0.20,
        "cash_buyer": False,
        "investor_owned": False,
        "latitude": item.get("latitude") or item.get("lat"),
        "longitude": item.get("longitude") or item.get("lng") or item.get("lon"),
        "zpid": item.get("zpid"),
        "mls_id": item.get("mlsId") or item.get("mls_id"),
        "listing_agent_name": item.get("listing_agent_name") or item.get("agentName"),
        "listing_agent_phone": item.get("listing_agent_phone") or item.get("agentPhone"),
        "broker_name": item.get("broker_name") or item.get("brokerName"),
        "detail_url": item.get("detailUrl") or item.get("detail_url") or item.get("href") or item.get("url"),
        "is_live_listing": True,
        "data_source": source_name,
        "raw_source_excerpt": item,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    prop.update(compute_scores(prop))
    return prop


async def fetch_live_fort_worth_residential_listings(limit: int = 50) -> List[Dict[str, Any]]:
    """Try multiple RapidAPI listing endpoints and normalize live Fort Worth residential listings.

    The endpoint names vary between RapidAPI providers/plans, so this function tries
    several common patterns and uses the first successful responses.
    """
    attempts = [
        {
            "host": HOST_REALTIME,
            "path": "/search",
            "params": {"location": "Fort Worth, TX", "status_type": "ForSale", "home_type": "Houses", "sort": "NEWEST", "limit": limit},
            "source": "RapidAPI real-time-real-estate-data /search",
        },
        {
            "host": HOST_REALTIME,
            "path": "/search-by-location",
            "params": {"location": "Fort Worth, TX", "status_type": "ForSale", "home_type": "Houses", "sort": "NEWEST", "limit": limit},
            "source": "RapidAPI real-time-real-estate-data /search-by-location",
        },
        {
            "host": HOST_REALTIME,
            "path": "/propertyExtendedSearch",
            "params": {"location": "Fort Worth, TX", "status_type": "ForSale", "home_type": "Houses", "sort": "NEWEST", "limit": limit},
            "source": "RapidAPI real-time-real-estate-data /propertyExtendedSearch",
        },
        {
            "host": HOST_REALTIME,
            "path": "/properties/list",
            "params": {"location": "Fort Worth, TX", "status_type": "ForSale", "home_type": "Houses", "limit": limit},
            "source": "RapidAPI real-time-real-estate-data /properties/list",
        },
        {
            "host": HOST_LISTINGS,
            "path": "/for-sale",
            "params": {"location": "Fort Worth, TX", "property_type": "single_family", "limit": limit},
            "source": "RapidAPI us-real-estate-listings /for-sale",
        },
    ]

    normalized: List[Dict[str, Any]] = []
    errors: List[str] = []

    for attempt in attempts:
        try:
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
    flip_ids = [p.get("id") for p in all_docs if is_allowed_flip_house(p)]
    total = len(flip_ids)

    for f in INVESTOR_FILTERS:
        q: Dict[str, Any] = {}
        if f["key"] != "all":
            q = apply_filter(f["key"], q)
        if flip_ids:
            q["id"] = {"$in": flip_ids}
        count = total if f["key"] == "all" else await db.properties.count_documents(q)
        out.append({**f, "count": count})

    return {
        "filters": out,
        "raw_total_before_flip_filter": raw_total,
        "rule": "InvestorFlip V1 only shows live/verified single-family houses and residential multi-family houses.",
    }


@api_router.post("/live/sync-fort-worth")
async def sync_live_fort_worth(limit: int = Query(50, ge=1, le=100)):
    listings = await fetch_live_fort_worth_residential_listings(limit=limit)

    upserted = 0
    for p in listings:
        await db.properties.update_one(
            {"id": p["id"]},
            {"$set": p},
            upsert=True,
        )
        upserted += 1

    await db.live_sync_log.insert_one({
        "source": "live Fort Worth residential listings",
        "count": upserted,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })

    return {
        "ok": True,
        "upserted": upserted,
        "items": listings,
        "rule": "Synced live Fort Worth for-sale houses only.",
        "note": "If upserted is 0, verify RAPIDAPI_KEY and that your RapidAPI plan supports one of the listing endpoints.",
    }


@api_router.get("/live/fort-worth-listings")
async def live_fort_worth_listings(limit: int = Query(50, ge=1, le=100)):
    docs = await db.properties.find(
        {"is_live_listing": True},
        {"_id": 0},
    ).sort("updated_at", -1).limit(limit * 3).to_list(length=limit * 3)

    items = [p for p in docs if is_fort_worth_property(p) and is_allowed_flip_house(p)][:limit]

    return {
        "count": len(items),
        "items": items,
        "rule": "Live Fort Worth residential for-sale listings only.",
    }


@api_router.get("/live/status")
async def live_status():
    total_live = await db.properties.count_documents({"is_live_listing": True})
    latest = await db.live_sync_log.find({}, {"_id": 0}).sort("created_at", -1).limit(5).to_list(length=5)
    return {
        "rapidapi_configured": bool(RAPIDAPI_KEY),
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

    cursor = db.properties.find(q, {"_id": 0}).sort("updated_at", -1).limit(limit * 5)
    raw_items = await cursor.to_list(length=limit * 5)
    items = [p for p in raw_items if is_allowed_flip_house(p)][:limit]

    return {
        "count": len(items),
        "items": items,
        "rule": "InvestorFlip V1 prioritizes live single-family and residential multi-family listings.",
    }


@api_router.get("/properties/{property_id}")
async def get_property(property_id: str):
    doc = await db.properties.find_one({"id": property_id}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Property not found")
    if not is_allowed_flip_house(doc):
        raise HTTPException(400, "Property blocked: not a single-family or residential multi-family flip target")
    return doc


@api_router.post("/properties/{property_id}/quill-analysis", response_model=QuillAnalyzeResponse)
async def property_quill_analysis(property_id: str):
    doc = await db.properties.find_one({"id": property_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Property not found")
    if not is_allowed_flip_house(doc):
        raise HTTPException(status_code=400, detail="Property blocked: not a single-family or residential multi-family flip target")
    return scout_analyze_property(doc)


@api_router.get("/properties/{property_id}/nearby")
async def get_nearby(property_id: str):
    base = await db.properties.find_one({"id": property_id}, {"_id": 0})
    if not base:
        raise HTTPException(404, "Property not found")
    if not is_allowed_flip_house(base):
        raise HTTPException(400, "Property blocked: not a single-family or residential multi-family flip target")

    zipc = base["zip"]
    near_foreclosures_raw = await db.properties.find(
        {"zip": zipc, "listing_type": {"$in": ["REO", "Foreclosure"]}, "id": {"$ne": property_id}},
        {"_id": 0},
    ).limit(20).to_list(length=20)
    near_investor_raw = await db.properties.find(
        {"zip": zipc, "investor_owned": True, "id": {"$ne": property_id}},
        {"_id": 0},
    ).limit(20).to_list(length=20)

    near_foreclosures = [p for p in near_foreclosures_raw if is_allowed_flip_house(p)][:4]
    near_investor = [p for p in near_investor_raw if is_allowed_flip_house(p)][:4]
    return {"nearby_foreclosures": near_foreclosures, "nearby_investor_purchases": near_investor}


@api_router.post("/properties/{property_id}/ai-analysis", response_model=AIAnalysisResponse)
async def ai_analysis(property_id: str):
    doc = await db.properties.find_one({"id": property_id}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Property not found")
    if not is_allowed_flip_house(doc):
        raise HTTPException(400, "Property blocked: not a single-family or residential multi-family flip target")

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
        verdict = "STRONG BUY" if doc.get("investment_score", 0) >= 75 else (
            "BUY" if doc.get("investment_score", 0) >= 60 else (
                "WATCH" if doc.get("investment_score", 0) >= 45 else "PASS"
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
    props = [p for p in props_raw if is_allowed_flip_house(p)]
    return {"count": len(props), "items": props}


@api_router.post("/saved")
async def add_saved(body: SaveRequest):
    exists = await db.properties.find_one({"id": body.property_id}, {"_id": 0})
    if not exists:
        raise HTTPException(404, "Property not found")
    if not is_allowed_flip_house(exists):
        raise HTTPException(400, "Property blocked: not a single-family or residential multi-family flip target")
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
    docs = [p for p in docs_raw if is_allowed_flip_house(p)]
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
    docs = [p for p in docs_raw if is_allowed_flip_house(p)]
    blob = feeds_mod.docs_to_xlsx_bytes(docs)
    fname = f"investorflip_{filter}_{datetime.now(timezone.utc).strftime('%Y%m%d')}.xlsx"
    return Response(
        content=blob,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


# ---------- Property Enrichment / Tax ----------
def _build_address_query(prop: Dict[str, Any]) -> str:
    situs = (prop.get("situs_address") or "").strip()
    base = re.sub(r",?\s*Tarrant County,?\s*(TX)?\.?\s*$", "", situs, flags=re.I).strip().rstrip(",")
    if re.search(r"\bTX\s*\d{5}\b", base, flags=re.I):
        return base
    city = (prop.get("city") or "Fort Worth").title().strip()
    return f"{base}, {city}, TX"


@api_router.post("/properties/{property_id}/enrich")
async def enrich_property(property_id: str):
    prop = await db.properties.find_one({"id": property_id}, {"_id": 0})
    if not prop:
        raise HTTPException(404, "Property not found")

    cached = await db.enrichment.find_one({"property_id": property_id}, {"_id": 0})
    if cached and cached.get("zpid"):
        return cached

    address = _build_address_query(prop)

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
                "tax_assessed_value": data.get("tax_assessed_value"),
                "latitude": data.get("latitude"),
                "longitude": data.get("longitude"),
                "rapidapi_address": data.get("street"),
                "rapidapi_city": data.get("city"),
                "rapidapi_state": data.get("state"),
                "rapidapi_zip": data.get("zipcode"),
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            }
            await _persist_enrichment(property_id, enriched)
            return enriched
    except HTTPException as e:
        logger.info("Primary enrichment failed: %s", e.detail)

    return {"property_id": property_id, "address_queried": address, "found": False}


async def _persist_enrichment(property_id: str, enriched: Dict[str, Any], photo_urls: Optional[List[str]] = None) -> None:
    update: Dict[str, Any] = {}
    if enriched.get("beds"):
        update["beds"] = safe_float(enriched["beds"])
    if enriched.get("baths"):
        update["baths"] = safe_float(enriched["baths"])
    if enriched.get("sqft"):
        update["sqft"] = safe_int(enriched["sqft"])
    if enriched.get("year_built"):
        update["year_built"] = safe_int(enriched["year_built"])
    if enriched.get("latitude") and enriched.get("longitude"):
        update["latitude"] = enriched["latitude"]
        update["longitude"] = enriched["longitude"]
    if enriched.get("home_type"):
        update["home_type"] = enriched["home_type"]
        update["property_type"] = enriched["home_type"]
    if enriched.get("zestimate"):
        update["market_value"] = safe_int(enriched["zestimate"])
    if photo_urls:
        update["image_url"] = photo_urls[0]
    if update:
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
    docs = [p for p in raw_docs if is_allowed_flip_house(p)]

    opportunities = []
    for p in docs:
        score = p.get("investment_score", 50)
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
    allowed = [p for p in docs if is_allowed_flip_house(p)]
    live = [p for p in allowed if p.get("is_live_listing")]
    missing_type = [p for p in docs if not get_property_type(p)]

    return {
        "raw_total": len(docs),
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
    client.close()
