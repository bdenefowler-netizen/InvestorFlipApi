"""TarrantREI backend - real estate investor tool focused on Tarrant County, TX."""
from fastapi import FastAPI, APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import re
import math
import random
import logging
import asyncio
from pathlib import Path
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
import uuid
from datetime import datetime, timezone

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

    # Law firm detection
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


# ---------- Seed Data ----------
FW_STREETS = [
    "Oak Grove Ln", "Sycamore St", "Magnolia Ave", "Hemphill St", "Camp Bowie Blvd",
    "Hulen St", "Bryant Irvin Rd", "McCart Ave", "Eastchase Pkwy", "Granbury Rd",
    "Mansfield Hwy", "Trail Lake Dr", "Western Center Blvd", "White Settlement Rd",
    "Berry St", "Vickery Blvd", "Lancaster Ave", "Riverside Dr", "Beach St",
    "Meadowbrook Dr", "Forest Park Blvd", "8th Ave", "Park Hill Dr", "Stalcup Rd",
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
    ("HUD", "Government"),
    ("City of Fort Worth", "Government"),
    ("Tarrant County", "Government"),
    ("The Henderson Family Trust", "Trust"),
    ("Patterson Living Trust", "Trust"),
    ("McKinney Revocable Trust", "Trust"),
    ("Habitat for Humanity of North Texas", "Nonprofit"),
    ("Texas Christian Foundation", "Nonprofit"),
    ("Jackson Walker LLP", "Law Firm"),
    ("Thompson Knight Attorneys PLLC", "Law Firm"),
    ("Kelly Hart Law Office", "Law Firm"),
    ("Sanchez & Associates Attorneys", "Attorney"),
    ("Republic Title of Texas Inc.", "Corporation"),
    ("Texas Realty Corp.", "Corporation"),
]

PROPERTY_IMAGES = [
    "https://images.pexels.com/photos/18280830/pexels-photo-18280830.jpeg?auto=compress&cs=tinysrgb&dpr=2&h=650&w=940",
    "https://images.unsplash.com/photo-1649692560786-27c52dd9ac1d?crop=entropy&cs=srgb&fm=jpg&q=80&w=940",
    "https://images.pexels.com/photos/33404981/pexels-photo-33404981.jpeg?auto=compress&cs=tinysrgb&dpr=2&h=650&w=940",
    "https://images.pexels.com/photos/2102587/pexels-photo-2102587.jpeg?auto=compress&cs=tinysrgb&dpr=2&h=650&w=940",
    "https://images.pexels.com/photos/1396122/pexels-photo-1396122.jpeg?auto=compress&cs=tinysrgb&dpr=2&h=650&w=940",
    "https://images.pexels.com/photos/2581922/pexels-photo-2581922.jpeg?auto=compress&cs=tinysrgb&dpr=2&h=650&w=940",
    "https://images.pexels.com/photos/106399/pexels-photo-106399.jpeg?auto=compress&cs=tinysrgb&dpr=2&h=650&w=940",
    "https://images.pexels.com/photos/1370704/pexels-photo-1370704.jpeg?auto=compress&cs=tinysrgb&dpr=2&h=650&w=940",
]

OUT_OF_STATE_STATES = ["CA", "FL", "NY", "NV", "AZ", "CO"]


def generate_seed_properties(n: int = 36) -> List[Dict[str, Any]]:
    rng = random.Random(7)
    props: List[Dict[str, Any]] = []
    for i in range(n):
        street_num = rng.randint(100, 9999)
        street = rng.choice(FW_STREETS)
        city, zipc = rng.choice(CITIES)
        situs = f"{street_num} {street}, {city}, TX {zipc}"

        owner_name, owner_type_seed = rng.choice(OWNER_POOL)
        owner_type = classify_owner(owner_name)  # use classifier for truth

        # Mailing address: out-of-state for some investors
        out_of_state = owner_type in ("LLC", "Corporation", "Bank") and rng.random() < 0.45
        if out_of_state:
            st = rng.choice(OUT_OF_STATE_STATES)
            mailing = f"PO Box {rng.randint(1000, 99999)}, {rng.choice(['Los Angeles', 'Miami', 'New York', 'Las Vegas', 'Phoenix', 'Denver'])}, {st} {rng.randint(10000, 99999)}"
        else:
            mailing = situs if owner_type == "Individual" else f"{rng.randint(100, 9999)} Commerce St, Dallas, TX {rng.choice(['75201', '75204', '75219'])}"

        listing_type = rng.choices(
            LISTING_TYPES,
            weights=[2, 3, 3, 2, 2],
        )[0]

        # Force consistency for banks/gov
        if owner_type == "Bank":
            listing_type = rng.choice(["REO", "Foreclosure"])
        if owner_type == "Government":
            listing_type = "As-Is"

        beds = rng.choice([2, 3, 3, 3, 4, 4, 5])
        baths = rng.choice([1, 2, 2, 2.5, 3])
        sqft = rng.randint(900, 3200)
        year_built = rng.randint(1948, 2018)
        lot_size = rng.randint(4500, 12000)

        market_value = rng.randint(120_000, 480_000)
        # Asking price below market for distressed
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
    total = await db.properties.count_documents({})
    for f in INVESTOR_FILTERS:
        q: Dict[str, Any] = {}
        if f["key"] != "all":
            q = apply_filter(f["key"], q)
        count = total if f["key"] == "all" else await db.properties.count_documents(q)
        out.append({**f, "count": count})
    return {"filters": out}


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
    cursor = db.properties.find(q, {"_id": 0}).limit(limit)
    items = await cursor.to_list(length=limit)
    return {"count": len(items), "items": items}


@api_router.get("/properties/{property_id}")
async def get_property(property_id: str):
    doc = await db.properties.find_one({"id": property_id}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Property not found")
    return doc


@api_router.get("/properties/{property_id}/nearby")
async def get_nearby(property_id: str):
    base = await db.properties.find_one({"id": property_id}, {"_id": 0})
    if not base:
        raise HTTPException(404, "Property not found")
    zipc = base["zip"]
    near_foreclosures = await db.properties.find(
        {"zip": zipc, "listing_type": {"$in": ["REO", "Foreclosure"]}, "id": {"$ne": property_id}},
        {"_id": 0, "id": 1, "situs_address": 1, "price": 1, "listing_type": 1, "image_url": 1},
    ).limit(4).to_list(length=4)
    near_investor = await db.properties.find(
        {"zip": zipc, "investor_owned": True, "id": {"$ne": property_id}},
        {"_id": 0, "id": 1, "situs_address": 1, "price": 1, "owner_type": 1, "image_url": 1},
    ).limit(4).to_list(length=4)
    return {"nearby_foreclosures": near_foreclosures, "nearby_investor_purchases": near_investor}


@api_router.post("/properties/{property_id}/ai-analysis", response_model=AIAnalysisResponse)
async def ai_analysis(property_id: str):
    doc = await db.properties.find_one({"id": property_id}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Property not found")

    # Cache
    cached = await db.ai_analysis.find_one({"property_id": property_id}, {"_id": 0})
    if cached and cached.get("narrative"):
        return AIAnalysisResponse(property_id=property_id, narrative=cached["narrative"])

    try:
        from emergentintegrations.llm.chat import LlmChat, UserMessage
        key = os.environ.get("EMERGENT_LLM_KEY")
        if not key:
            raise RuntimeError("EMERGENT_LLM_KEY missing")

        system = (
            "You are a senior real estate investor analyst. Given a property record, "
            "produce a concise investment analysis in 4 short bullet points. "
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
        # Fallback deterministic narrative
        verdict = "STRONG BUY" if doc["investment_score"] >= 75 else (
            "BUY" if doc["investment_score"] >= 60 else (
                "WATCH" if doc["investment_score"] >= 45 else "PASS"
            )
        )
        narrative = (
            f"• {doc['listing_type']} property in {doc['city']} with ${doc['equity_estimate']:,} "
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
    props = await db.properties.find({"id": {"$in": ids}}, {"_id": 0}).to_list(length=500)
    return {"count": len(props), "items": props}


@api_router.post("/saved")
async def add_saved(body: SaveRequest):
    exists = await db.properties.find_one({"id": body.property_id}, {"_id": 0, "id": 1})
    if not exists:
        raise HTTPException(404, "Property not found")
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
from fastapi import UploadFile, File, Form
from fastapi.responses import StreamingResponse, Response
from importers import feeds as feeds_mod


@api_router.post("/feeds/sync")
async def feeds_sync(only: Optional[str] = None, limit: int = 50):
    """Pull from all registered feeds (RealtyInUS, Xome, TX Foreclosure)
    and insert/update properties in the DB. Auto-classifies + scores."""
    result = await feeds_mod.run_feed_sync(
        db, classify_owner, compute_scores,
        only_feed=only, limit_per_feed=limit,
    )
    return result


@api_router.get("/feeds/status")
async def feeds_status():
    """List all registered feeds and last-sync counts."""
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
    """Upload a CSV with columns: address, city, state, zip, price, owner, parcel_id,
    beds, baths, sqft, year_built, market_value. Records are ingested via the
    same pipeline (auto-classified + scored, cross-matched against Master.dat).
    """
    raw = await file.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin1")
    counts = await feeds_mod.ingest_csv_text(
        db, text, feed_source, listing_type, classify_owner, compute_scores,
    )
    return {"ok": True, "feed_source": feed_source, "listing_type": listing_type, **counts}


@api_router.get("/export.csv")
async def export_csv(
    filter: str = Query("all"),
    search: Optional[str] = Query(None),
    limit: int = Query(10000, ge=1, le=50000),
):
    """Export filtered properties to CSV (Excel-compatible)."""
    q: Dict[str, Any] = {}
    q = apply_filter(filter, q)
    if search:
        rg = {"$regex": re.escape(search), "$options": "i"}
        q["$or"] = [{"situs_address": rg}, {"city": rg}, {"zip": rg}, {"owner_name": rg}]
    docs = await db.properties.find(q, {"_id": 0}).limit(limit).to_list(length=limit)
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
    """Export filtered properties to Excel (.xlsx)."""
    q: Dict[str, Any] = {}
    q = apply_filter(filter, q)
    if search:
        rg = {"$regex": re.escape(search), "$options": "i"}
        q["$or"] = [{"situs_address": rg}, {"city": rg}, {"zip": rg}, {"owner_name": rg}]
    docs = await db.properties.find(q, {"_id": 0}).limit(limit).to_list(length=limit)
    blob = feeds_mod.docs_to_xlsx_bytes(docs)
    fname = f"tarrant_rei_{filter}_{datetime.now(timezone.utc).strftime('%Y%m%d')}.xlsx"
    return Response(
        content=blob,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


# ---------- RapidAPI Enrichment ----------
import httpx

RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY", "")
HOST_DETAILS = "real-time-real-estate-data.p.rapidapi.com"
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
    """Build a normalized address string for the RapidAPI lookup.

    Master.dat carries the owner's *mailing* city/zip, not the *situs* city/zip.
    The situs is almost always inside Tarrant County, so we try Fort Worth first;
    if the owner mailing state is TX and the city is a known Tarrant suburb, we
    use that. RapidAPI's matcher handles the zip fuzzily.
    """
    situs = (prop.get("situs_address") or "").strip()
    # Strip our ", Tarrant County, TX" suffix
    base = re.sub(r",?\s*Tarrant County,\s*TX.*$", "", situs, flags=re.I).strip()
    mailing_city = (prop.get("city") or "").title().strip()
    mailing_state = (prop.get("state") or "TX").upper()
    # If mailing is in TX and a Tarrant city, use it as situs city; otherwise default to Fort Worth.
    TARRANT_CITIES = {
        "Fort Worth", "Arlington", "Mansfield", "Bedford", "Euless", "Hurst",
        "North Richland Hills", "Grapevine", "Keller", "Southlake", "Colleyville",
        "Watauga", "Haltom City", "White Settlement", "Saginaw", "Forest Hill",
        "Crowley", "Burleson", "Kennedale", "Benbrook", "Richland Hills",
    }
    if mailing_state == "TX" and mailing_city in TARRANT_CITIES:
        city = mailing_city
    else:
        city = "Fort Worth"
    return f"{base}, {city}, TX"


@api_router.post("/properties/{property_id}/enrich")
async def enrich_property(property_id: str):
    """Pull beds/baths/sqft/year_built/photos from Real-Time Real Estate Data API."""
    prop = await db.properties.find_one({"id": property_id}, {"_id": 0})
    if not prop:
        raise HTTPException(404, "Property not found")

    cached = await db.enrichment.find_one({"property_id": property_id}, {"_id": 0})
    if cached and cached.get("zpid"):
        return cached

    address = _build_address_query(prop)
    try:
        raw = await _rapid_get(HOST_DETAILS, "/property-details-address", {"address": address})
    except HTTPException as e:
        return {"property_id": property_id, "address_queried": address, "error": str(e.detail), "found": False}

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

    enriched = {
        "property_id": property_id,
        "address_queried": address,
        "found": True,
        "zpid": data.get("zpid"),
        "beds": reso.get("bedrooms") or data.get("bedrooms"),
        "baths": reso.get("bathroomsFloat") or reso.get("bathrooms") or data.get("bathrooms"),
        "sqft": data.get("livingAreaValue") or data.get("livingArea"),
        "year_built": reso.get("yearBuilt") or _safe_int(_get_at_a_glance(reso, "Year Built")),
        "lot_size": _get_at_a_glance(reso, "Lot"),
        "home_type": data.get("homeType") or _get_at_a_glance(reso, "Type"),
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

    # Mirror enriched primary stats back onto the property doc
    update: Dict[str, Any] = {}
    if enriched["beds"]:
        update["beds"] = int(enriched["beds"])
    if enriched["baths"]:
        update["baths"] = float(enriched["baths"])
    if enriched["sqft"]:
        update["sqft"] = int(enriched["sqft"])
    if enriched["year_built"]:
        update["year_built"] = int(enriched["year_built"])
    if photo_urls:
        update["image_url"] = photo_urls[0]
    if update:
        await db.properties.update_one({"id": property_id}, {"$set": update})

    await db.enrichment.update_one(
        {"property_id": property_id}, {"$set": enriched}, upsert=True
    )
    return enriched


def _safe_int(v: Any) -> Optional[int]:
    try:
        return int(re.sub(r"[^0-9]", "", str(v))) if v else None
    except Exception:
        return None


def _get_at_a_glance(reso: Dict[str, Any], label: str) -> Optional[str]:
    facts = reso.get("atAGlanceFacts") or []
    for f in facts:
        if isinstance(f, dict) and f.get("factLabel") == label:
            return f.get("factValue")
    return None


@api_router.get("/properties/{property_id}/tax-history")
async def tax_history(property_id: str):
    """Return tax history via US Real Estate Listings API (needs zpid from enrichment)."""
    enr = await db.enrichment.find_one({"property_id": property_id}, {"_id": 0})
    if not enr or not enr.get("zpid"):
        # Auto-enrich first
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
    # Seed properties if collection empty (demo fallback only)
    count = await db.properties.count_documents({})
    if count == 0:
        seeds = generate_seed_properties(36)
        await db.properties.insert_many(seeds)
        logger.info("Seeded %d demo Tarrant County properties", len(seeds))
    else:
        real_count = await db.properties.count_documents({"data_source": {"$regex": "Master.dat"}})
        if real_count > 0:
            logger.info("Real Tarrant County tax roll loaded: %d properties", real_count)
        else:
            logger.info("Properties collection already has %d docs", count)


@app.on_event("shutdown")
async def on_shutdown():
    client.close()
