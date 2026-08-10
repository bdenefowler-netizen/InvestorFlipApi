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
import json
import html
import uuid
import logging
import asyncio
from dataclasses import dataclass, asdict, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import List, Optional, Dict, Any, Iterable

import httpx
from database import PostgresDatabase
from investor_logic import compute_scores, derive_owner_signals

logger = logging.getLogger("tarrantrei.feeds")
ACTIVE_FORECLOSURE_STATUSES = {
    "active",
    "auction",
    "bank owned",
    "coming soon",
    "for sale",
    "posted",
    "pre-foreclosure",
    "scheduled",
    "scheduled for auction",
    "scheduled for online auction",
}

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


FORECLOSURE_FINDER_HOST = "foreclosure-finder1.p.rapidapi.com"
FORT_WORTH_CENTER_ZIP = "76102"
FCLOSURE_BASE_URL = "https://fclosure.com"
FCLOSURE_FORT_WORTH_URL = f"{FCLOSURE_BASE_URL}/foreclosures/cities/fort-worth"
TARRANT_PUBLIC_SEARCH_URL = "https://tarrant.tx.publicsearch.us/"
LGBS_API_BASE = "https://taxsales.lgbs.com/api/property_sales/"


def _first_dict_list(payload: Any) -> List[Dict[str, Any]]:
    """Return listing-shaped dictionaries from common RapidAPI wrappers."""
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []

    for key in ("listings", "results", "properties", "items", "data"):
        value = payload.get(key)
        if isinstance(value, list):
            items = [item for item in value if isinstance(item, dict)]
            if items:
                return items
        if isinstance(value, dict):
            items = _first_dict_list(value)
            if items:
                return items
    return []


def _nested_text(value: Any, *keys: str) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in keys:
            candidate = value.get(key)
            if candidate not in (None, ""):
                return str(candidate).strip()
    return ""


def _feed_int(value: Any) -> int:
    try:
        return int(float(re.sub(r"[^0-9.-]", "", str(value or "0")) or 0))
    except (TypeError, ValueError):
        return 0


def _feed_float(value: Any) -> float:
    try:
        return float(re.sub(r"[^0-9.-]", "", str(value or "0")) or 0)
    except (TypeError, ValueError):
        return 0.0


def _foreclosure_finder_listing(item: Dict[str, Any]) -> Optional[FeedListing]:
    """Normalize one auction record and reject anything outside Fort Worth, TX."""
    address_value = item.get("address") or item.get("propertyAddress") or item.get("location")
    full = _nested_text(
        address_value,
        "formattedAddress", "formatted_address", "fullAddress", "full_address", "address",
    )
    address_obj = address_value if isinstance(address_value, dict) else {}
    street = str(
        item.get("streetAddress") or item.get("street_address")
        or address_obj.get("streetAddress") or address_obj.get("street_address")
        or address_obj.get("street") or ""
    ).strip()
    city = str(item.get("city") or address_obj.get("city") or "").strip()
    state = str(item.get("state") or address_obj.get("state") or "").strip().upper()
    zip_code = str(
        item.get("zipcode") or item.get("zip") or item.get("postalCode")
        or item.get("postal_code") or address_obj.get("zipcode") or address_obj.get("zip")
        or address_obj.get("postalCode") or address_obj.get("postal_code") or ""
    ).strip()

    if full:
        parts = [part.strip() for part in full.split(",")]
        street = street or (parts[0] if parts else "")
        city = city or (parts[1] if len(parts) > 1 else "")
        state_match = re.search(r"(?:,|\s)\s*([A-Z]{2})\s+\d{5}(?:-\d{4})?\b", full.upper())
        state = state or (state_match.group(1) if state_match else "")
        zip_match = re.search(r"\b(\d{5})(?:-\d{4})?\b", full)
        zip_code = zip_code or (zip_match.group(1) if zip_match else "")

    location_text = f"{city} {full}".lower().replace("-", " ")
    if "fort worth" not in location_text:
        return None
    if state and state != "TX":
        return None
    if not street:
        return None

    city = "Fort Worth"
    state = "TX"
    zip_code = zip_code[:5]
    full_address = full or f"{street}, {city}, {state} {zip_code}".strip()

    asset_type = str(item.get("assetType") or item.get("asset_type") or "")
    status_label = str(item.get("statusLabel") or item.get("status") or "")
    type_text = f"{asset_type} {status_label}".lower().replace("_", " ")
    listing_type = "REO" if any(term in type_text for term in ("bank owned", "reo")) else "Foreclosure"
    seller = _nested_text(item.get("seller"), "name", "displayName", "display_name")

    image = _nested_text(
        item.get("photoUrl") or item.get("photo_url") or item.get("image") or item.get("primaryPhoto"),
        "href", "url", "src",
    )
    source = _nested_text(item.get("source"), "name", "label")

    return FeedListing(
        feed_source="Foreclosure Finder",
        listing_type=listing_type,
        situs_address=full_address,
        city=city,
        state=state,
        zip=zip_code,
        price=_feed_int(item.get("openingBid") or item.get("opening_bid") or item.get("price")),
        beds=_feed_int(item.get("bedrooms") or item.get("beds")),
        baths=_feed_float(item.get("bathrooms") or item.get("baths")),
        sqft=_feed_int(item.get("squareFootage") or item.get("square_footage") or item.get("sqft")),
        year_built=_feed_int(item.get("yearBuilt") or item.get("year_built")),
        owner_name=seller,
        parcel_id=str(item.get("listingId") or item.get("listing_id") or item.get("id") or ""),
        image_url=image,
        extra={
            "source": source,
            "auction_date": item.get("auctionDate") or item.get("auction_date"),
            "status_label": status_label,
            "property_link": item.get("propertyLink") or item.get("property_link") or item.get("url"),
            "asset_type": asset_type,
            "property_type": item.get("propertyType") or item.get("property_type"),
            "source_endpoint": "/zipcode/auction",
        },
    )


class ForeclosureFinderFeed(FeedSource):
    """Pull Auction.com-style foreclosure listings around Fort Worth via RapidAPI."""
    name = "Foreclosure Finder"

    async def fetch(
        self,
        limit: int = 200,
        zipcode: str = FORT_WORTH_CENTER_ZIP,
        radius: int = 25,
        **params,
    ) -> List[FeedListing]:
        key = os.environ.get("RAPIDAPI_KEY", "").strip()
        if not key:
            logger.info("Foreclosure Finder skipped: RAPIDAPI_KEY is not configured")
            return []

        headers = {
            "x-rapidapi-key": key,
            "x-rapidapi-host": FORECLOSURE_FINDER_HOST,
            "Content-Type": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    f"https://{FORECLOSURE_FINDER_HOST}/zipcode/auction",
                    headers=headers,
                    params={"zipcode": str(zipcode), "radius": str(radius)},
                )
            if response.status_code >= 400:
                logger.info("Foreclosure Finder /zipcode/auction → %s", response.status_code)
                return []
            items = _first_dict_list(response.json())
        except Exception as exc:
            logger.warning("Foreclosure Finder /zipcode/auction error: %s", exc)
            return []

        listings: List[FeedListing] = []
        seen = set()
        for item in items:
            listing = _foreclosure_finder_listing(item)
            if not listing:
                continue
            identity = listing.parcel_id or _normalize_addr(listing.situs_address)
            if identity in seen:
                continue
            seen.add(identity)
            listings.append(listing)
            if len(listings) >= limit:
                break
        return listings


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


def _lgbs_listing(item: Dict[str, Any]) -> Optional[FeedListing]:
    street = str(item.get("prop_address_one") or "").strip()
    if item.get("prop_address_two"):
        street = f"{street} {str(item.get('prop_address_two')).strip()}".strip()
    city = str(item.get("prop_city") or "Fort Worth").strip() or "Fort Worth"
    state = str(item.get("prop_state") or item.get("state") or "TX").strip().upper() or "TX"
    zip_code = str(item.get("prop_zipcode") or "").strip()[:5]
    if not street:
        return None

    full_address = f"{street}, {city}, {state} {zip_code}".strip()
    sale_date = item.get("sale_date_only") or item.get("sale_date")
    status = str(item.get("status") or "").strip()
    return FeedListing(
        feed_source="LGBS Tax Sales",
        listing_type="Foreclosure",
        situs_address=full_address,
        city=city,
        state=state,
        zip=zip_code,
        price=_money(item.get("minimum_bid") or "0"),
        market_value=_money(item.get("value") or "0"),
        owner_name="Tax Sale",
        parcel_id=str(item.get("account_nbr") or ""),
        extra={
            "sale_date": sale_date,
            "auction_date": sale_date,
            "status": status,
            "status_label": status,
            "cause_number": item.get("cause_nbr"),
            "sale_number": item.get("sale_nbr"),
            "sale_type": item.get("sale_type"),
            "minimum_bid": item.get("minimum_bid"),
            "appraised_value": item.get("value"),
            "county_sale_list": item.get("county_sale_list"),
            "source_url": LGBS_API_BASE,
            "source_uid": item.get("uid"),
        },
    )


class LGBSTaxSalesFeed(FeedSource):
    """Pull Tarrant County tax-sale rows from the public LGBS tax sales API."""
    name = "LGBS Tax Sales"

    async def fetch(self, limit: int = 200, **params) -> List[FeedListing]:
        out: List[FeedListing] = []
        page_size = min(max(limit, 1), 100)
        offset = 0
        request_params = {
            "state": "TX",
            "county": "TARRANT COUNTY",
            "sale_type": "SALE",
            "ordering": "-sale_date,street_name,address_full,uid",
            "limit": page_size,
        }
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            while len(out) < limit:
                response = await client.get(LGBS_API_BASE, params={**request_params, "offset": offset})
                if response.status_code >= 400:
                    logger.info("LGBS Tax Sales → %s", response.status_code)
                    break
                payload = response.json()
                rows = payload.get("results") or []
                if not rows:
                    break
                for item in rows:
                    listing = _lgbs_listing(item)
                    if listing:
                        out.append(listing)
                    if len(out) >= limit:
                        break
                if not payload.get("next") or len(rows) < page_size:
                    break
                offset += len(rows)
        return out


FCLOSURE_ROW_RE = re.compile(
    r'"href":"(?P<href>/property/[^"]+)".{0,2000}?'
    r'"children":"(?P<address>[^"]+)".{0,500}?'
    r'"children":"(?P<location>Fort Worth,\s*TX,\s*\d{5})".{0,700}?'
    r'"children":"(?P<sale>[A-Z][a-z]{2}\s+\d{1,2})".{0,500}?'
    r'"children":"(?P<beds_baths>(?:\d+(?:\.\d+)?\s*/\s*\d+(?:\.\d+)?|—))".{0,500}?'
    r'"children":"(?P<market>\$?\$?[0-9,]+|—)".{0,500}?'
    r'"children":"(?P<equity>[+\-]?\$?\$?[0-9,]+|—)"',
    re.DOTALL,
)


def _fclosure_page_year(text: str) -> int:
    match = re.search(r'"dateModified":"(\d{4})-\d{2}-\d{2}"', text)
    if match:
        return int(match.group(1))
    match = re.search(r'<time[^>]+dateTime="(\d{4})-\d{2}-\d{2}"', text)
    if match:
        return int(match.group(1))
    return datetime.now(timezone.utc).year


def _fclosure_sale_date(label: str, year: int) -> Optional[date]:
    label = re.sub(r"\s+", " ", str(label or "").strip())
    if not label:
        return None
    try:
        return datetime.strptime(f"{label} {year}", "%b %d %Y").date()
    except ValueError:
        return None


def _fclosure_address_parts(address: str, location: str) -> Dict[str, str]:
    address = re.sub(r"\s+", " ", html.unescape(address or "")).strip(" ,")
    location = re.sub(r"\s+", " ", html.unescape(location or "")).strip(" ,")
    zip_match = re.search(r"\b(\d{5})(?:-\d{4})?\b", f"{address} {location}")
    zip_code = zip_match.group(1) if zip_match else ""
    street = re.split(r",?\s*Fort Worth\b", address, maxsplit=1, flags=re.I)[0].strip(" ,")
    full_address = f"{street}, Fort Worth, TX {zip_code}".strip()
    return {
        "street": street,
        "city": "Fort Worth",
        "state": "TX",
        "zip": zip_code,
        "full_address": full_address,
    }


def _fclosure_listing_from_match(match: re.Match[str], year: int) -> Optional[FeedListing]:
    address = html.unescape(match.group("address")).strip()
    location = html.unescape(match.group("location")).strip()
    parts = _fclosure_address_parts(address, location)
    if not parts["street"] or not parts["zip"]:
        return None

    sale_date = _fclosure_sale_date(match.group("sale"), year)
    beds_baths = html.unescape(match.group("beds_baths")).strip()
    beds = 0
    baths = 0.0
    if "/" in beds_baths:
        bed_text, bath_text = [part.strip() for part in beds_baths.split("/", 1)]
        beds = _feed_int(bed_text)
        baths = _feed_float(bath_text)

    href = html.unescape(match.group("href")).strip()
    market_value = _money(match.group("market").replace("$$", "$"))
    equity = html.unescape(match.group("equity")).replace("$$", "$").strip()
    return FeedListing(
        feed_source="Fclosure",
        listing_type="Foreclosure",
        situs_address=parts["full_address"],
        city=parts["city"],
        state=parts["state"],
        zip=parts["zip"],
        market_value=market_value,
        beds=beds,
        baths=baths,
        owner_name="Notice of Trustee's Sale",
        parcel_id="",
        extra={
            "sale_date": sale_date.isoformat() if sale_date else "",
            "auction_date": sale_date.isoformat() if sale_date else "",
            "status": "Scheduled for Auction",
            "status_label": "Scheduled for Auction",
            "source_url": f"{FCLOSURE_BASE_URL}{href}",
            "official_search_url": TARRANT_PUBLIC_SEARCH_URL,
            "source_page": FCLOSURE_FORT_WORTH_URL,
            "sale_date_label": match.group("sale"),
            "market_value": match.group("market").replace("$$", "$"),
            "equity_estimate": equity,
            "beds_baths": beds_baths,
        },
    )


def _fclosure_listings_from_html(
    text: str,
    limit: int = 200,
    sale_date_from: Optional[date] = None,
) -> List[FeedListing]:
    normalized = html.unescape(text or "")
    normalized = normalized.replace(r"\/", "/").replace(r"\"", '"').replace("$$", "$")
    year = _fclosure_page_year(normalized)
    listings: List[FeedListing] = []
    seen = set()
    for match in FCLOSURE_ROW_RE.finditer(normalized):
        listing = _fclosure_listing_from_match(match, year)
        if not listing:
            continue
        identity = _normalize_addr(listing.situs_address)
        if identity in seen:
            continue
        seen.add(identity)
        if sale_date_from:
            sale_date = _listing_sale_date(listing)
            if not sale_date or sale_date < sale_date_from:
                continue
        listings.append(listing)
        if len(listings) >= limit:
            break
    return listings


class FclosureFeed(FeedSource):
    """Pull Fort Worth trustee-sale rows from Fclosure's public city page."""
    name = "Fclosure"

    async def fetch(self, limit: int = 200, **params) -> List[FeedListing]:
        sale_date_from = params.get("sale_date_from")
        headers = {
            "User-Agent": "InvestorFlip/1.0 (+https://fclosure.com/foreclosures/cities/fort-worth)",
            "Accept": "text/html,application/xhtml+xml",
        }
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True, headers=headers) as client:
            response = await client.get(FCLOSURE_FORT_WORTH_URL)
        if response.status_code >= 400:
            logger.info("Fclosure Fort Worth → %s", response.status_code)
            return []
        return _fclosure_listings_from_html(
            response.text,
            limit=limit,
            sale_date_from=sale_date_from if isinstance(sale_date_from, date) else None,
        )


def _money(v: str) -> int:
    v = re.sub(r"[^0-9.]", "", str(v) or "")
    if not v:
        return 0
    try:
        return int(float(v))
    except Exception:
        return 0


def _parse_date(value: Any) -> Optional[date]:
    text = str(value or "").strip()
    if not text:
        return None
    if re.fullmatch(r"\d{10,13}", text):
        timestamp = int(text)
        if timestamp > 9_999_999_999:
            timestamp = timestamp // 1000
        return datetime.fromtimestamp(timestamp, tz=timezone.utc).date()
    text = re.sub(r"\s+", " ", text)
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _listing_sale_date(listing: FeedListing) -> Optional[date]:
    for key in ("sale_date", "auction_date", "tax_sale_date"):
        parsed = _parse_date(listing.extra.get(key))
        if parsed:
            return parsed
    return None


def _is_current_foreclosure_listing(
    listing: FeedListing,
    today: Optional[date] = None,
    sale_date_from: Optional[date] = None,
) -> bool:
    if listing.listing_type not in {"Foreclosure", "REO"}:
        return True
    today = today or datetime.now(timezone.utc).date()
    sale_date = _listing_sale_date(listing)
    if sale_date and sale_date < today:
        return False
    if sale_date_from and (not sale_date or sale_date < sale_date_from):
        return False
    status = str(listing.extra.get("status") or listing.extra.get("status_label") or "").strip().lower()
    if status and status not in ACTIVE_FORECLOSURE_STATUSES:
        return False
    return True


# Registry
FEEDS: List[FeedSource] = [
    ForeclosureFinderFeed(),
    FclosureFeed(),
    LGBSTaxSalesFeed(),
    TexasForeclosureFeed(),
]


# ---------- Ingestion pipeline ----------
def _normalize_addr(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").upper().strip())


async def cross_match_tax_roll(db: PostgresDatabase, listing: FeedListing) -> Optional[Dict[str, Any]]:
    """Try to find this listing's parcel in our Master.dat data by address+zip."""
    if listing.parcel_id:
        match = await db.properties.find_one({"account_id": listing.parcel_id}, {"_id": 0})
        if match:
            return match
        trimmed = listing.parcel_id.lstrip("0")
        if trimmed and trimmed != listing.parcel_id:
            match = await db.properties.find_one({"account_id": trimmed}, {"_id": 0})
            if match:
                return match

    if not listing.situs_address:
        return None

    addr_re = re.escape(listing.situs_address.split(",")[0].strip())
    q = {"$and": [
        {"situs_address": {"$regex": f"^{addr_re}", "$options": "i"}},
    ]}
    if listing.zip:
        q["$and"].append({"$or": [{"zip": listing.zip}, {"mailing_zip": listing.zip}]})
    return await db.properties.find_one(q, {"_id": 0})


async def ingest_listings(
    db: PostgresDatabase,
    listings: Iterable[FeedListing],
    classify_owner_fn,
    compute_scores_fn,
    sale_date_from: Optional[date] = None,
) -> Dict[str, Any]:
    inserted = 0
    matched = 0
    skipped = 0
    property_ids: List[str] = []
    new_docs: List[Dict[str, Any]] = []
    for L in listings:
        if not _is_current_foreclosure_listing(L, sale_date_from=sale_date_from):
            skipped += 1
            continue

        match = await cross_match_tax_roll(db, L)
        if match:
            # Update existing tax-roll record with listing info
            updates: Dict[str, Any] = {
                "listing_type": L.listing_type,
                "price": L.price or match.get("price", 0),
                "data_source": f"{match.get('data_source', '')} + {L.feed_source}",
                "is_live_listing": True,
                "listing_last_seen_at": datetime.now(timezone.utc).isoformat(),
                "missed_syncs": 0,
                "feed_extra": L.extra,
            }
            if L.beds: updates["beds"] = L.beds
            if L.baths: updates["baths"] = L.baths
            if L.sqft: updates["sqft"] = L.sqft
            if L.year_built: updates["year_built"] = L.year_built
            if L.image_url: updates["image_url"] = L.image_url
            updates["last_feed_sync"] = datetime.now(timezone.utc).isoformat()
            combined = {**match, **updates}
            updates.update(compute_scores(combined))
            await db.properties.update_one({"id": match["id"]}, {"$set": updates})
            property_ids.append(match["id"])
            matched += 1
            continue

        if not L.situs_address:
            skipped += 1
            continue

        # Net-new property doc
        owner_name = (L.owner_name or "").strip()
        owner_type = classify_owner_fn(owner_name)
        price = L.price or 0
        mv = L.market_value or None

        prop = {
            "id": str(uuid.uuid4()),
            "account_id": L.parcel_id or "",
            "situs_address": L.situs_address + (f", {L.city}, {L.state} {L.zip}" if "," not in L.situs_address else ""),
            "city": L.city or "Fort Worth",
            "state": L.state or "TX",
            "zip": (L.zip or "")[:5],
            "county": "Tarrant",
            "property_type": L.extra.get("property_type"),
            "home_type": L.extra.get("property_type"),
            "beds": L.beds or 0,
            "baths": L.baths or 0,
            "sqft": L.sqft or 0,
            "year_built": L.year_built or 0,
            "lot_size_sqft": 0,
            "image_url": L.image_url or None,
            "price": price,
            "market_value": mv,
            "market_value_source": "feed-provided estimate" if mv else None,
            "assessed_value": None,
            "annual_taxes": None,
            "equity_estimate": None,
            "equity_status": "unknown - mortgage balance required",
            "est_roi_pct": None,
            "roi_status": "unknown - ARV, repairs, holding, and selling costs required",
            "legal_description": L.extra.get("legal", ""),
            "listing_type": L.listing_type,
            "listing_status": L.extra.get("status_label") or L.listing_type,
            "is_live_listing": True,
            "listing_last_seen_at": datetime.now(timezone.utc).isoformat(),
            "missed_syncs": 0,
            "owner_name": owner_name,
            "owner_type": owner_type,
            "owner_mailing_address": "",
            "out_of_state_owner": False,
            "tax_delinquent": False,
            "distress_status": L.listing_type,
            "vacant": False,
            "high_equity": False,
            "cash_buyer": False,
            "investor_owned": owner_type in ("LLC", "Corporation", "Trust", "Bank"),
            "data_source": f"{L.feed_source} feed",
            "feed_extra": L.extra,
            "ingested_at": datetime.now(timezone.utc).isoformat(),
        }
        prop.update(derive_owner_signals(
            prop.get("owner_name") or "",
            prop.get("owner_mailing_address") or "",
            prop.get("situs_address") or "",
            prop.get("state") or "TX",
        ))
        prop.update(compute_scores(prop))
        new_docs.append(prop)
        property_ids.append(prop["id"])
        inserted += 1

    if new_docs:
        await db.properties.insert_many(new_docs)
    return {
        "inserted": inserted,
        "matched": matched,
        "skipped": skipped,
        "property_ids": property_ids,
    }


async def cleanup_expired_feed_records(db: PostgresDatabase, feed_name: str) -> int:
    today = datetime.now(timezone.utc).date()
    deleted_ids: List[str] = []
    cursor = db.properties.find(
        {
            "data_source": {"$regex": re.escape(feed_name), "$options": "i"},
            "ingested_at": {"$exists": True},
            "feed_extra": {"$exists": True},
        },
        {"_id": 0, "id": 1, "feed_extra": 1},
    )
    async for doc in cursor:
        extra = doc.get("feed_extra") or {}
        sale_date = None
        for key in ("sale_date", "auction_date", "tax_sale_date"):
            sale_date = _parse_date(extra.get(key))
            if sale_date:
                break
        if sale_date and sale_date < today:
            deleted_ids.append(doc["id"])
    if not deleted_ids:
        return 0
    result = await db.properties.delete_many({"id": {"$in": deleted_ids}})
    return int(result.deleted_count)


async def run_feed_sync(
    db: PostgresDatabase,
    classify_owner_fn,
    compute_scores_fn,
    only_feed: Optional[str] = None,
    limit_per_feed: int = 50,
    sale_date_from: Optional[date] = None,
) -> Dict[str, Any]:
    results: Dict[str, Any] = {
        "by_feed": {},
        "totals": {"inserted": 0, "matched": 0, "skipped": 0, "deleted_expired": 0},
        "property_ids": [],
    }
    for feed in FEEDS:
        if only_feed and feed.name.lower() != only_feed.lower():
            continue
        try:
            listings = await feed.fetch(limit=limit_per_feed, sale_date_from=sale_date_from)
        except Exception as e:
            logger.exception("Feed %s fetch error", feed.name)
            results["by_feed"][feed.name] = {"error": str(e), "inserted": 0, "matched": 0, "skipped": 0, "fetched": 0}
            continue
        deleted_expired = await cleanup_expired_feed_records(db, feed.name)
        counts = await ingest_listings(
            db,
            listings,
            classify_owner_fn,
            compute_scores_fn,
            sale_date_from=sale_date_from,
        )
        counts["fetched"] = len(listings)
        counts["deleted_expired"] = deleted_expired
        results["by_feed"][feed.name] = counts
        results["property_ids"].extend(counts.get("property_ids") or [])
        for k in ("inserted", "matched", "skipped"):
            results["totals"][k] += counts[k]
        results["totals"]["deleted_expired"] += deleted_expired
    return results


# ---------- CSV upload (Texas Foreclosure or any feed) ----------
async def ingest_csv_text(
    db: PostgresDatabase,
    csv_text: str,
    feed_source: str,
    listing_type: str,
    classify_owner_fn,
    compute_scores_fn,
    sale_date_from: Optional[date] = None,
) -> Dict[str, Any]:
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
    return await ingest_listings(
        db,
        listings,
        classify_owner_fn,
        compute_scores_fn,
        sale_date_from=sale_date_from,
    )


# ---------- Export ----------
EXPORT_COLUMNS = [
    "id", "account_id", "situs_address", "city", "state", "zip", "county",
    "listing_type", "data_source",
    "owner_name", "owner_type", "owner_mailing_address", "out_of_state_owner",
    "absentee_owner", "investor_owned", "cash_buyer", "cash_buyer_status",
    "tax_delinquent", "vacant", "high_equity",
    "price", "market_value", "market_value_source", "tax_roll_market_value",
    "assessed_value", "annual_taxes", "value_benchmark", "value_benchmark_source",
    "value_spread", "discount_to_benchmark_pct", "equity_estimate", "equity_status",
    "est_roi_pct", "roi_status",
    "beds", "baths", "sqft", "year_built",
    "investment_score", "wholesale_score", "flip_score", "rental_score", "risk_score",
    "score_confidence", "score_kind", "score_missing_inputs",
    "legal_description",
]


def _export_value(value: Any) -> Any:
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, separators=(",", ":"), default=str)
    return value


def docs_to_csv(docs: Iterable[Dict[str, Any]]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=EXPORT_COLUMNS, extrasaction="ignore")
    w.writeheader()
    for d in docs:
        w.writerow({k: _export_value(d.get(k, "")) for k in EXPORT_COLUMNS})
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
        ws.append([_export_value(d.get(k, "")) for k in EXPORT_COLUMNS])
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()
