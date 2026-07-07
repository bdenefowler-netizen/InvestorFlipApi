"""TarrantREI backend - real estate investor tool focused on Tarrant County, TX.

UPDATED InvestorFlip V1 rule:
Only show/analyze house-flip targets:
- Single-family houses
- Residential multi-family houses

Blocks commercial, land, apartments, condos, townhomes, duplex, triplex, fourplex, etc.
No Fort Worth streets/corridors are blocked. If it is a house, it can show.
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

app = FastAPI(title="TarrantREI API")
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
CORP_KEYWORDS = ["inc.", " inc", "incorporated", "corporation", "corp.", "company", " co.", "brothers", "holdings", "partners", "properties", "realty", "group"]
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
    mv = max(1, p.get("market_value", 0))
    asking = max(1, p.get("price", mv))
    equity_pct = max(0.0, (mv - asking) / mv)
    annual_taxes = p.get("annual_taxes", 0)
    tax_burden = annual_taxes / mv if mv else 0
    owner_type = p.get("owner_type", "Individual")
    listing_type = p.get("listing_type", "As-Is")
    year_built = p.get("year_built", 1990)
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
    "townhome",
    "townhouse",
    "duplex",
    "triplex",
    "fourplex",
    "quadplex",
    "apartment",
]


def get_property_type(p: Dict[str, Any]) -> str:
    return str(
        p.get("property_type")
        or p.get("home_type")
        or p.get("land_use")
        or p.get("use_code")
        or p.get("property_class")
        or ""
    ).lower().strip()


def has_basic_house_facts(p: Dict[str, Any]) -> bool:
    """Legacy safety net for older records created before property_type existed.

    This prevents the frontend from returning zero items after the V1 validator was added.
    It does not block any Fort Worth streets/corridors; it only checks house-like facts.
    """
    try:
        beds = float(p.get("beds") or 0)
        baths = float(p.get("baths") or 0)
        sqft = float(p.get("sqft") or 0)
        year_built = int(p.get("year_built") or 0)
    except Exception:
        return False

    return beds >= 1 and baths >= 1 and 500 <= sqft <= 8000 and 1800 <= year_built <= 2035


def infer_legacy_property_type(p: Dict[str, Any]) -> Optional[str]:
    """Infer a safe residential type for old/demo records missing property_type.

    Real imported records should eventually use county CAD fields like property_type,
    land_use, use_code, or property_class. This only keeps old records from vanishing.
    No Fort Worth streets or corridors are blocked here; the rule is property-type based.
    """
    if get_property_type(p):
        return None

    if has_basic_house_facts(p):
        return "Single Family Residential"

    return None


def is_allowed_flip_house(p: Dict[str, Any]) -> bool:
    t = get_property_type(p)

    # Legacy fallback: old database records may not have property_type/home_type yet.
    # If they look like a house by beds/baths/sqft/year_built, allow them.
    if not t:
        return infer_legacy_property_type(p) is not None

    if any(blocked in t for blocked in BLOCKED_FLIP_TYPES):
        return False

    return any(allowed in t for allowed in ALLOWED_FLIP_TYPES)


# ---------- Seed Data ----------
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
            "data_source": "Tarrant County Tax Roll (Master.dat / Rec.DAT - seeded sample)",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        prop.update(compute_scores(prop))
        props.append(prop)
    return props


# ---------- Filter Definitions ----------
INVESTOR_FILTERS = [
    {"key": "all", "label": "All"},
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
    if f == "reo":
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


# ---------- Routes ----------
@api_router.get("/")
async def root():
    return {"name": "TarrantREI API", "status": "ok"}


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
        "rule": "InvestorFlip V1 only shows single-family houses and residential multi-family houses.",
    }


@api_router.get("/properties")
async def list_properties(
    filter: str = Query("all"),
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

    cursor = db.properties.find(q, {"_id": 0}).limit(limit * 5)
    raw_items = await cursor.to_list(length=limit * 5)
    items = [p for p in raw_items if is_allowed_flip_house(p)][:limit]

    return {
        "count": len(items),
        "items": items,
        "rule": "InvestorFlip V1 only shows single-family houses and residential multi-family houses.",
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
        {"_id": 0, "id": 1, "situs_address": 1, "price": 1, "listing_type": 1, "image_url": 1, "property_type": 1, "home_type": 1},
    ).limit(20).to_list(length=20)
    near_investor_raw = await db.properties.find(
        {"zip": zipc, "investor_owned": True, "id": {"$ne": property_id}},
        {"_id": 0, "id": 1, "situs_address": 1, "price": 1, "owner_type": 1, "image_url": 1, "property_type": 1, "home_type": 1},
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
            "You are a senior real estate investor analyst. InvestorFlip V1 only analyzes "
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
            f"Address: {doc['situs_address']}\n"
            f"Property Type: {get_property_type(doc)}\n"
            f"Listing Type: {doc['listing_type']}\n"
            f"Owner: {doc['owner_name']} ({doc['owner_type']})\n"
            f"Out-of-State Owner: {doc['out_of_state_owner']}\n"
            f"Asking Price: ${doc['price']:,}\n"
            f"Market Value: ${doc['market_value']:,}\n"
            f"Assessed Value: ${doc['assessed_value']:,}\n"
            f"Annual Taxes: ${doc['annual_taxes']:,}\n"
            f"Equity Estimate: ${doc['equity_estimate']:,}\n"
            f"Est ROI: {doc['est_roi_pct']}%\n"
            f"Beds/Baths/SqFt: {doc['beds']}/{doc['baths']}/{doc['sqft']}\n"
            f"Year Built: {doc['year_built']}\n"
            f"Tax Delinquent: {doc['tax_delinquent']} | Vacant: {doc['vacant']}\n"
            f"Scores → Investment {doc['investment_score']}, Flip {doc['flip_score']}, "
            f"Rental {doc['rental_score']}, Wholesale {doc['wholesale_score']}, Risk {doc['risk_score']}"
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
        verdict = "STRONG BUY" if doc["investment_score"] >= 75 else (
            "BUY" if doc["investment_score"] >= 60 else (
                "WATCH" if doc["investment_score"] >= 45 else "PASS"
            )
        )
        narrative = (
            f"• {doc['listing_type']} {doc.get('property_type', 'house')} in {doc['city']} with ${doc['equity_estimate']:,} "
            f"of estimated equity ({doc['est_roi_pct']}% ROI).\n"
            f"• Owned by {doc['owner_name']} ({doc['owner_type']})"
            f"{' — out-of-state, may motivate quick sale.' if doc['out_of_state_owner'] else '.'}\n"
            f"• Annual taxes ${doc['annual_taxes']:,} against ${doc['assessed_value']:,} assessed value "
            f"({round(doc['annual_taxes']/max(doc['assessed_value'],1)*100,2)}% effective rate).\n"
            f"• Risk score {doc['risk_score']}/99 — "
            f"{'distressed asset, expect repairs.' if doc['listing_type'] in ('REO','Foreclosure') else 'standard underwriting.'}\n"
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


# ---------- Feed Sync, Upload, Export ----------
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
    filter: str = Query("all"),
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
    fname = f"tarrant_rei_{filter}_{datetime.now(timezone.utc).strftime('%Y%m%d')}.csv"
    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@api_router.get("/export.xlsx")
async def export_xlsx(
    filter: str = Query("all"),
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
    fname = f"tarrant_rei_{filter}_{datetime.now(timezone.utc).strftime('%Y%m%d')}.xlsx"
    return Response(
        content=blob,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


# ---------- RapidAPI Enrichment ----------
RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY", "")
HOST_LOOKUP = "us-real-estate-data1.p.rapidapi.com"
HOST_LISTINGS = "us-real-estate-listings.p.rapidapi.com"


async def _rapid_get(host: str, path: str, params: Dict[str, Any]) -> Dict[str, Any]:
    if not RAPIDAPI_KEY:
        raise HTTPException(503, "RAPIDAPI_KEY not configured")
    headers = {
        "x-rapidapi-key": RAPIDAPI_KEY,
        "x-rapidapi-host": host,
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.get(f"https://{host}{path}", headers=headers, params=params)
        if r.status_code >= 400:
            raise HTTPException(r.status_code, f"RapidAPI error: {r.text[:200]}")
        return r.json()


def _build_address_query(prop: Dict[str, Any]) -> str:
    situs = (prop.get("situs_address") or "").strip()
    base = re.sub(r",?\s*Tarrant County,?\s*(TX)?\.?\s*$", "", situs, flags=re.I).strip().rstrip(",")
    if re.search(r"\bTX\s*\d{5}\b", base, flags=re.I):
        return base
    mailing_city = (prop.get("city") or "").title().strip()
    mailing_state = (prop.get("state") or "TX").upper()
    TARRANT_CITIES = {
        "Fort Worth", "Arlington", "Mansfield", "Bedford", "Euless", "Hurst",
        "North Richland Hills", "Grapevine", "Keller", "Southlake", "Colleyville",
        "Watauga", "Haltom City", "White Settlement", "Saginaw", "Forest Hill",
        "Crowley", "Burleson", "Kennedale", "Benbrook", "Richland Hills",
    }
    city = mailing_city if mailing_state == "TX" and mailing_city in TARRANT_CITIES else "Fort Worth"
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
                "lot_size": f"{data.get('lot_area_value')} {data.get('lot_area_unit')}" if data.get("lot_area_value") else None,
                "home_type": data.get("home_type"),
                "home_status": data.get("status"),
                "list_price": data.get("price"),
                "zestimate": data.get("zestimate"),
                "rent_zestimate": data.get("rent_zestimate"),
                "tax_assessed_value": data.get("tax_assessed_value"),
                "latitude": data.get("latitude"),
                "longitude": data.get("longitude"),
                "rapidapi_address": data.get("street"),
                "rapidapi_city": data.get("city"),
                "rapidapi_state": data.get("state"),
                "rapidapi_zip": data.get("zipcode"),
                "is_foreclosure": data.get("is_foreclosure"),
                "mls_id": data.get("mls_id"),
                "listing_agent_name": data.get("listing_agent_name"),
                "listing_agent_phone": data.get("listing_agent_phone"),
                "broker_name": data.get("broker_name"),
                "photos": [],
                "hi_res_image": None,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            }
            await _persist_enrichment(property_id, enriched)
            return enriched
    except HTTPException as e:
        logger.info("Primary enrichment failed: %s", e.detail)

    try:
        raw = await _rapid_get(
            "real-time-real-estate-data.p.rapidapi.com",
            "/property-details-address",
            {"address": address},
        )
        data = raw.get("data") or {}
        if not data:
            return {"property_id": property_id, "address_queried": address, "found": False}

        reso = data.get("resoFacts") or {}
        photos = data.get("originalPhotos") or data.get("responsivePhotos") or []
        photo_urls: List[str] = []
        for p in photos[:6]:
            if isinstance(p, dict):
                mixed = p.get("mixedSources") or {}
                jpg = mixed.get("jpeg") or []
                if jpg:
                    photo_urls.append(jpg[-1].get("url") if isinstance(jpg[-1], dict) else None)
                elif p.get("url"):
                    photo_urls.append(p["url"])
        photo_urls = [u for u in photo_urls if u]

        def _safe_int_local(v: Any) -> Optional[int]:
            try:
                return int(re.sub(r"[^0-9]", "", str(v))) if v else None
            except Exception:
                return None

        def _aag(label: str) -> Optional[str]:
            for f in reso.get("atAGlanceFacts") or []:
                if isinstance(f, dict) and f.get("factLabel") == label:
                    return f.get("factValue")
            return None

        enriched = {
            "property_id": property_id,
            "address_queried": address,
            "source_api": "real-time-real-estate-data",
            "found": True,
            "zpid": data.get("zpid"),
            "beds": reso.get("bedrooms") or data.get("bedrooms"),
            "baths": reso.get("bathroomsFloat") or reso.get("bathrooms") or data.get("bathrooms"),
            "sqft": data.get("livingAreaValue") or data.get("livingArea"),
            "year_built": reso.get("yearBuilt") or _safe_int_local(_aag("Year Built")),
            "lot_size": _aag("Lot"),
            "home_type": data.get("homeType") or _aag("Type"),
            "home_status": data.get("homeStatus"),
            "list_price": data.get("price"),
            "rapidapi_address": data.get("streetAddress"),
            "rapidapi_city": data.get("city"),
            "rapidapi_state": data.get("state"),
            "rapidapi_zip": data.get("zipcode"),
            "appliances": reso.get("appliances") or [],
            "cooling": reso.get("cooling") or [],
            "heating": reso.get("heating") or [],
            "parcel_id": data.get("parcelId"),
            "photos": photo_urls,
            "hi_res_image": data.get("hiResImageLink"),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }
        await _persist_enrichment(property_id, enriched, photo_urls=photo_urls)
        return enriched
    except HTTPException as e:
        return {"property_id": property_id, "address_queried": address, "error": str(e.detail), "found": False}


async def _persist_enrichment(property_id: str, enriched: Dict[str, Any], photo_urls: Optional[List[str]] = None) -> None:
    update: Dict[str, Any] = {}
    if enriched.get("beds"):
        update["beds"] = int(enriched["beds"])
    if enriched.get("baths"):
        update["baths"] = float(enriched["baths"])
    if enriched.get("sqft"):
        update["sqft"] = int(enriched["sqft"])
    if enriched.get("year_built"):
        update["year_built"] = int(enriched["year_built"])
    if enriched.get("latitude") and enriched.get("longitude"):
        update["latitude"] = enriched["latitude"]
        update["longitude"] = enriched["longitude"]
    if enriched.get("home_type"):
        update["home_type"] = enriched["home_type"]
        update["property_type"] = enriched["home_type"]
    if photo_urls:
        update["image_url"] = photo_urls[0]
    if update:
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


@api_router.post("/scout/analyze-deal")
async def analyze_deal():
    return {"message": "Scout analyze deal endpoint is working"}


@api_router.post("/scout/find-opportunities")
async def find_opportunities(limit: int = 10):
    raw_docs = await db.properties.find({}, {"_id": 0}).to_list(length=500)
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
        if p.get("high_equity"):
            reason.append("High equity")
        if p.get("tax_delinquent"):
            reason.append("Tax delinquent")
        if p.get("vacant"):
            reason.append("Vacant")
        if p.get("out_of_state_owner"):
            reason.append("Out-of-state owner")
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
        "rule": "InvestorFlip V1 only shows single-family houses and residential multi-family houses.",
    }


@api_router.post("/admin/backfill-flip-property-types")
async def backfill_flip_property_types():
    """Add property_type to older records that look like houses.

    Run this once after deploying if /api/properties returns 0 because old records
    were created before property_type/home_type existed.
    """
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
        "rule": "Backfilled only legacy records that look like single-family houses.",
    }


@api_router.get("/admin/data-quality")
async def data_quality():
    docs = await db.properties.find({}, {"_id": 0}).to_list(length=10000)
    allowed = [p for p in docs if is_allowed_flip_house(p)]
    missing_type = [p for p in docs if not get_property_type(p)]

    return {
        "raw_total": len(docs),
        "allowed_flip_houses": len(allowed),
        "missing_property_type": len(missing_type),
        "sample_allowed": [
            {"address": p.get("situs_address"), "property_type": get_property_type(p) or infer_legacy_property_type(p)}
            for p in allowed[:10]
        ],
        "rule": "InvestorFlip V1 only shows single-family houses and residential multi-family houses.",
    }


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


# ---------- Scout + Quill ----------
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
    if count == 0:
        seeds = generate_seed_properties(36)
        await db.properties.insert_many(seeds)
        logger.info("Seeded %d demo Tarrant County flip-house properties", len(seeds))
    else:
        # Auto-backfill old records that existed before property_type/home_type was added.
        # This prevents /api/properties from returning 0 immediately after deploying V1 validation.
        docs = await db.properties.find({}, {"_id": 0}).to_list(length=10000)
        backfilled = 0
        for p in docs:
            if get_property_type(p):
                continue
            inferred = infer_legacy_property_type(p)
            if not inferred:
                continue
            await db.properties.update_one(
                {"id": p.get("id")},
                {"$set": {
                    "property_type": inferred,
                    "home_type": inferred,
                    "property_type_source": "startup legacy backfill",
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }}
            )
            backfilled += 1

        real_count = await db.properties.count_documents({"data_source": {"$regex": "Master.dat"}})
        if real_count > 0:
            logger.info("Real Tarrant County tax roll loaded: %d properties", real_count)
        else:
            logger.info("Properties collection already has %d docs", count)
        if backfilled:
            logger.info("Backfilled %d legacy records with Single Family Residential property_type", backfilled)


@app.on_event("shutdown")
async def on_shutdown():
    client.close()
