"""External feed ingestion: RealtyInUS, Xome REO, Texas Foreclosure.

Each feed implements `fetch() -> List[FeedListing]` and is run through a common
ingestion pipeline that:
  1. Cross-matches with existing Master.dat properties by (zip, address) → updates
  2. Inserts net-new records with synthesized property doc + owner classification + scoring
  3. Reports counts

Adding a new feed = subclass `FeedSource` and register in FEEDS list.
"""
from __future__ import annotations

import os
import re
import csv
import io
import uuid
import logging
import asyncio
import random
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Dict, Any, Iterable

import httpx
from motor.motor_asyncio import AsyncIOMotorDatabase

logger = logging.getLogger("tarrantrei.feeds")

# ---------- Common record ----------
@dataclass
class FeedListing:
    feed_source: str            # "Xome" | "RealtyInUS" | "TX Foreclosure" | "CSV Upload"
    listing_type: str           # "REO" | "Foreclosure" | "As-Is" | "Investor" | "Cash House"
    situs_address: str          # "1234 W Berry St, Fort Worth, TX 76110"
    city: str
    state: str
    zip: str
    price: int = 0
    market_value: int = 0
    beds: int = 0
    baths: float = 0
    sqft: int = 0
    year_built: int = 0
    owner_name: str = ""
    parcel_id: str = ""
    image_url: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


# ---------- Feed sources ----------
class FeedSource:
    name: str = "Unknown"

    async def fetch(self, limit: int = 50, **params) -> List[FeedListing]:
        raise NotImplementedError


class XomeREOFeed(FeedSource):
    """Best-effort scraper of Xome public REO listings.

    Xome doesn't publish a public API, but lists REOs at xome.com/reo and exposes
    a JSON API endpoint internally on those pages. We try it; on failure we return
    an empty list (the caller is expected to handle that gracefully).
    """
    name = "Xome"

    async def fetch(self, limit: int = 50, state: str = "TX", **params) -> List[FeedListing]:
        url = "https://www.xome.com/api/v1/properties/search"
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
            "Accept": "application/json",
        }
        payload = {"propertyType": ["SFH"], "state": state, "limit": limit, "offset": 0}
        try:
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as c:
                r = await c.post(url, headers=headers, json=payload)
                if r.status_code >= 400:
                    logger.warning("Xome %s → %s", url, r.status_code)
                    return []
                data = r.json()
                items = data.get("properties") or data.get("results") or []
        except Exception as e:
            logger.warning("Xome fetch failed: %s", e)
            return []

        out: List[FeedListing] = []
        for it in items[:limit]:
            try:
                out.append(FeedListing(
                    feed_source="Xome",
                    listing_type="REO",
                    situs_address=it.get("address") or it.get("fullAddress") or "",
                    city=it.get("city") or "",
                    state=it.get("state") or state,
                    zip=it.get("zip") or it.get("zipcode") or "",
                    price=int(it.get("price") or it.get("listPrice") or 0),
                    beds=int(it.get("beds") or 0),
                    baths=float(it.get("baths") or 0),
                    sqft=int(it.get("sqft") or it.get("livingArea") or 0),
                    year_built=int(it.get("yearBuilt") or 0),
                    owner_name=it.get("seller") or "Bank-Owned",
                    image_url=it.get("imageUrl") or it.get("photo") or "",
                    extra={"raw_id": it.get("id")},
                ))
            except Exception:
                continue
        return out


class RealtyInUSFeed(FeedSource):
    """Pulls residential SFH listings via the RapidAPI Real-Time Real Estate Data API
    (the one we already pay for). Uses the propertyByZip endpoint when available.
    """
    name = "RealtyInUS"

    async def fetch(self, limit: int = 25, zip_codes: Optional[List[str]] = None, **params) -> List[FeedListing]:
        zips = zip_codes or ["76104", "76110", "76119"]
        key = os.environ.get("RAPIDAPI_KEY", "")
        if not key:
            return []
        out: List[FeedListing] = []
        headers = {"x-rapidapi-key": key, "x-rapidapi-host": "real-time-real-estate-data.p.rapidapi.com"}
        # The Real-Time Real Estate Data API exposes /search-property-by-zipcode
        async with httpx.AsyncClient(timeout=20.0) as c:
            for z in zips:
                if len(out) >= limit:
                    break
                try:
                    r = await c.get(
                        "https://real-time-real-estate-data.p.rapidapi.com/search-properties",
                        headers=headers,
                        params={"location": f"Fort Worth, TX {z}", "sort": "newest", "page": 1, "limit": 10},
                    )
                    if r.status_code >= 400:
                        logger.info("RealtyInUS zip=%s → %s: %s", z, r.status_code, r.text[:150])
                        continue
                    data = r.json().get("data") or {}
                    for it in (data.get("results") or [])[:10]:
                        if len(out) >= limit:
                            break
                        addr = it.get("address") or {}
                        out.append(FeedListing(
                            feed_source="RealtyInUS",
                            listing_type="Investor" if (it.get("price") or 0) < 200_000 else "As-Is",
                            situs_address=addr.get("streetAddress") or "",
                            city=addr.get("city") or "Fort Worth",
                            state=addr.get("state") or "TX",
                            zip=addr.get("zipcode") or z,
                            price=int(it.get("price") or 0),
                            beds=int(it.get("bedrooms") or 0),
                            baths=float(it.get("bathrooms") or 0),
                            sqft=int(it.get("livingArea") or 0),
                            year_built=int(it.get("yearBuilt") or 0),
                            image_url=it.get("imgSrc") or "",
                            extra={"zpid": it.get("zpid")},
                        ))
                except Exception as e:
                    logger.warning("RealtyInUS fetch zip=%s error: %s", z, e)
        return out


class TexasForeclosureFeed(FeedSource):
    """Texas county Notice of Trustee Sale CSV.

    There's no free public API, so this feed reads from a CSV file at
    /app/backend/data/tx_foreclosures.csv (user can upload via API). Columns
    expected (case-insensitive): address, city, zip, sale_date, opening_bid,
    trustee, parcel_id.
    """
    name = "TX Foreclosure"

    CSV_PATH = Path(__file__).resolve().parent.parent / "data" / "tx_foreclosures.csv"

    async def fetch(self, limit: int = 200, **params) -> List[FeedListing]:
        if not self.CSV_PATH.exists():
            return []
        out: List[FeedListing] = []
        try:
            with self.CSV_PATH.open("r", encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    row = {(k or "").lower().strip(): (v or "").strip() for k, v in row.items()}
                    if not row.get("address"):
                        continue
                    out.append(FeedListing(
                        feed_source="TX Foreclosure",
                        listing_type="Foreclosure",
                        situs_address=row.get("address", ""),
                        city=row.get("city", "Fort Worth"),
                        state=row.get("state", "TX"),
                        zip=row.get("zip", ""),
                        price=_money(row.get("opening_bid", "0")),
                        owner_name=row.get("trustee") or row.get("owner") or "Trustee",
                        parcel_id=row.get("parcel_id", ""),
                        extra={"sale_date": row.get("sale_date", "")},
                    ))
                    if len(out) >= limit:
                        break
        except Exception as e:
            logger.warning("TX Foreclosure CSV read failed: %s", e)
        return out


def _money(v: str) -> int:
    v = re.sub(r"[^0-9.]", "", str(v) or "")
    if not v:
        return 0
    try:
        return int(float(v))
    except Exception:
        return 0


# Registry
FEEDS: List[FeedSource] = [
    RealtyInUSFeed(),
    XomeREOFeed(),
    TexasForeclosureFeed(),
]


# ---------- Ingestion pipeline ----------
PROPERTY_IMAGES = [
    "https://images.pexels.com/photos/18280830/pexels-photo-18280830.jpeg?auto=compress&cs=tinysrgb&dpr=2&h=650&w=940",
    "https://images.pexels.com/photos/33404981/pexels-photo-33404981.jpeg?auto=compress&cs=tinysrgb&dpr=2&h=650&w=940",
    "https://images.pexels.com/photos/2102587/pexels-photo-2102587.jpeg?auto=compress&cs=tinysrgb&dpr=2&h=650&w=940",
    "https://images.pexels.com/photos/1396122/pexels-photo-1396122.jpeg?auto=compress&cs=tinysrgb&dpr=2&h=650&w=940",
]


def _normalize_addr(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").upper().strip())


async def cross_match_tax_roll(db: AsyncIOMotorDatabase, listing: FeedListing) -> Optional[Dict[str, Any]]:
    """Try to find this listing's parcel in our Master.dat data by address+zip."""
    addr_re = re.escape(listing.situs_address.split(",")[0].strip())
    q = {"$and": [
        {"situs_address": {"$regex": f"^{addr_re}", "$options": "i"}},
    ]}
    if listing.zip:
        q["$and"].append({"$or": [{"zip": listing.zip}, {"mailing_zip": listing.zip}]})
    return await db.properties.find_one(q, {"_id": 0})


async def ingest_listings(
    db: AsyncIOMotorDatabase,
    listings: Iterable[FeedListing],
    classify_owner_fn,
    compute_scores_fn,
) -> Dict[str, int]:
    inserted = 0
    matched = 0
    skipped = 0
    new_docs: List[Dict[str, Any]] = []
    for L in listings:
        if not L.situs_address:
            skipped += 1
            continue

        match = await cross_match_tax_roll(db, L)
        if match:
            # Update existing tax-roll record with listing info
            updates: Dict[str, Any] = {
                "listing_type": L.listing_type,
                "price": L.price or match.get("price", 0),
                "data_source": f"{match.get('data_source', '')} + {L.feed_source}",
            }
            if L.beds: updates["beds"] = L.beds
            if L.baths: updates["baths"] = L.baths
            if L.sqft: updates["sqft"] = L.sqft
            if L.year_built: updates["year_built"] = L.year_built
            if L.image_url: updates["image_url"] = L.image_url
            updates["last_feed_sync"] = datetime.now(timezone.utc).isoformat()
            await db.properties.update_one({"id": match["id"]}, {"$set": updates})
            matched += 1
            continue

        # Net-new property doc
        owner_type = classify_owner_fn(L.owner_name or L.feed_source)
        price = L.price or 0
        mv = L.market_value or int(price * (1.05 if L.listing_type != "REO" else 1.25))
        equity = max(0, mv - price)
        roi = round((equity / max(price, 1)) * 100, 1) if price else 0.0
        annual_taxes = int(mv * 0.025) if mv else 0

        prop = {
            "id": str(uuid.uuid4()),
            "account_id": L.parcel_id or "",
            "situs_address": L.situs_address + (f", {L.city}, {L.state} {L.zip}" if "," not in L.situs_address else ""),
            "city": L.city or "Fort Worth",
            "state": L.state or "TX",
            "zip": (L.zip or "")[:5],
            "county": "Tarrant",
            "beds": L.beds or 0,
            "baths": L.baths or 0,
            "sqft": L.sqft or 0,
            "year_built": L.year_built or 0,
            "lot_size_sqft": 0,
            "image_url": L.image_url or random.choice(PROPERTY_IMAGES),
            "price": price,
            "market_value": mv,
            "assessed_value": int(mv * 0.88) if mv else 0,
            "annual_taxes": annual_taxes,
            "equity_estimate": equity,
            "est_roi_pct": roi,
            "legal_description": L.extra.get("legal", ""),
            "listing_type": L.listing_type,
            "owner_name": L.owner_name or L.feed_source,
            "owner_type": owner_type,
            "owner_mailing_address": "",
            "out_of_state_owner": False,
            "tax_delinquent": L.listing_type == "Foreclosure",
            "vacant": False,
            "high_equity": (equity / max(mv, 1)) >= 0.20 if mv else False,
            "cash_buyer": owner_type in ("LLC", "Corporation"),
            "investor_owned": owner_type in ("LLC", "Corporation", "Trust"),
            "data_source": f"{L.feed_source} feed",
            "feed_extra": L.extra,
            "ingested_at": datetime.now(timezone.utc).isoformat(),
        }
        prop.update(compute_scores_fn(prop))
        new_docs.append(prop)
        inserted += 1

    if new_docs:
        await db.properties.insert_many(new_docs)
    return {"inserted": inserted, "matched": matched, "skipped": skipped}


async def run_feed_sync(
    db: AsyncIOMotorDatabase,
    classify_owner_fn,
    compute_scores_fn,
    only_feed: Optional[str] = None,
    limit_per_feed: int = 50,
) -> Dict[str, Any]:
    results: Dict[str, Any] = {"by_feed": {}, "totals": {"inserted": 0, "matched": 0, "skipped": 0}}
    for feed in FEEDS:
        if only_feed and feed.name.lower() != only_feed.lower():
            continue
        try:
            listings = await feed.fetch(limit=limit_per_feed)
        except Exception as e:
            logger.exception("Feed %s fetch error", feed.name)
            results["by_feed"][feed.name] = {"error": str(e), "inserted": 0, "matched": 0, "skipped": 0, "fetched": 0}
            continue
        counts = await ingest_listings(db, listings, classify_owner_fn, compute_scores_fn)
        counts["fetched"] = len(listings)
        results["by_feed"][feed.name] = counts
        for k in ("inserted", "matched", "skipped"):
            results["totals"][k] += counts[k]
    return results


# ---------- CSV upload (Texas Foreclosure or any feed) ----------
async def ingest_csv_text(
    db: AsyncIOMotorDatabase,
    csv_text: str,
    feed_source: str,
    listing_type: str,
    classify_owner_fn,
    compute_scores_fn,
) -> Dict[str, int]:
    reader = csv.DictReader(io.StringIO(csv_text))
    listings: List[FeedListing] = []
    for row in reader:
        row = {(k or "").lower().strip(): (v or "").strip() for k, v in row.items()}
        if not row.get("address"):
            continue
        listings.append(FeedListing(
            feed_source=feed_source,
            listing_type=listing_type,
            situs_address=row.get("address", ""),
            city=row.get("city", "Fort Worth"),
            state=row.get("state", "TX"),
            zip=row.get("zip", ""),
            price=_money(row.get("price") or row.get("opening_bid", "0")),
            market_value=_money(row.get("market_value", "0")),
            beds=int(_money(row.get("beds", "0"))),
            baths=float(_money(row.get("baths", "0"))),
            sqft=int(_money(row.get("sqft", "0"))),
            year_built=int(_money(row.get("year_built", "0"))),
            owner_name=row.get("owner") or row.get("trustee") or feed_source,
            parcel_id=row.get("parcel_id") or row.get("account_id", ""),
            extra={k: v for k, v in row.items() if k not in {"address", "city", "state", "zip"}},
        ))
    return await ingest_listings(db, listings, classify_owner_fn, compute_scores_fn)


# ---------- Export ----------
EXPORT_COLUMNS = [
    "id", "account_id", "situs_address", "city", "state", "zip", "county",
    "listing_type", "data_source",
    "owner_name", "owner_type", "owner_mailing_address", "out_of_state_owner",
    "investor_owned", "cash_buyer", "tax_delinquent", "vacant", "high_equity",
    "price", "market_value", "assessed_value", "annual_taxes",
    "equity_estimate", "est_roi_pct",
    "beds", "baths", "sqft", "year_built",
    "investment_score", "wholesale_score", "flip_score", "rental_score", "risk_score",
    "legal_description",
]


def docs_to_csv(docs: Iterable[Dict[str, Any]]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=EXPORT_COLUMNS, extrasaction="ignore")
    w.writeheader()
    for d in docs:
        w.writerow({k: d.get(k, "") for k in EXPORT_COLUMNS})
    return buf.getvalue()


def docs_to_xlsx_bytes(docs: List[Dict[str, Any]]) -> bytes:
    try:
        from openpyxl import Workbook  # type: ignore
    except ImportError:
        raise RuntimeError("openpyxl not installed")
    wb = Workbook()
    ws = wb.active
    ws.title = "TarrantREI Deals"
    ws.append(EXPORT_COLUMNS)
    for d in docs:
        ws.append([d.get(k, "") for k in EXPORT_COLUMNS])
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()
