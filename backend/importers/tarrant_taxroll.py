"""Tarrant County Tax Roll importer (Master.dat + Rec.DAT, fixed-width 742-byte records).

Field positions reverse-engineered from sample records:
- 0..11    account_id (11 digit)
- 30..35   account_code (e.g. 00002, 00201, 00301)
- 35..155  legal_description (two 60-char lines concatenated)
- 195..240 situs_address (street name area)
- 326..382 owner_name (50 chars)
- 384..444 mailing_street (60 chars)
- 444..474 mailing_city (30 chars)
- 476..480 mailing_state (2 chars padded)
- 496..510 mailing_zip (9 chars or zip+4)
- 615..634 market_value
- 634..650 assessed_value
- 650..666 taxable_value

These positions are heuristic; we trim/validate every field.
"""
import os
import re
import asyncio
from pathlib import Path
from typing import Dict, Any, Iterator, Optional
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DATA = ROOT / "data"
MASTER = DATA / "big_b"   # Master.dat
REC = DATA / "big_a"      # Rec.DAT
RECORD_LEN = 742          # bytes per record (incl trailing \r)

# Import the classifier from server (relative import workaround)
import sys
sys.path.insert(0, str(ROOT))
from server import classify_owner, compute_scores  # type: ignore

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


def _s(b: bytes, start: int, length: int) -> str:
    return b[start:start + length].decode("latin1", errors="replace").strip()


def _money(raw: str) -> int:
    raw = re.sub(r"[^0-9.]", "", raw)
    if not raw:
        return 0
    try:
        return int(float(raw))
    except ValueError:
        return 0


def parse_master_record(line: bytes) -> Optional[Dict[str, Any]]:
    if len(line) < 700:
        return None
    acct = _s(line, 0, 11)
    if not acct.isdigit():
        return None
    legal1 = _s(line, 35, 60)
    legal2 = _s(line, 95, 60)
    legal = (legal1 + " " + legal2).strip()

    # Situs: house number at byte 185 (5-char field), street name at byte 198 (30-char field).
    house_num = _s(line, 185, 5)
    street_raw = _s(line, 198, 30)
    street = re.sub(r"\s+\d{4,}.*$", "", street_raw).strip()
    if house_num and house_num.isdigit() and street:
        situs_street = f"{int(house_num)} {street}"
    else:
        situs_street = street or ""

    owner = _s(line, 326, 56)
    mail_street = _s(line, 384, 60)
    mail_city = _s(line, 444, 30)
    mail_state = _s(line, 476, 2)
    mail_zip_raw = _s(line, 496, 9)
    mail_zip = (mail_zip_raw[:5] + "-" + mail_zip_raw[5:]) if len(mail_zip_raw) == 9 and mail_zip_raw.isdigit() else mail_zip_raw[:5]

    # Money fields at tail (3 concatenated "%020.2f" amounts)
    tail = _s(line, 615, 60)
    money_pat = re.findall(r"(\d{1,12}\.\d{2})", tail)
    def _clamp(v: str) -> int:
        try:
            n = int(float(v))
        except Exception:
            return 0
        if n < 0 or n > 999_999_999:
            return 0
        return n
    market_value = _clamp(money_pat[0]) if len(money_pat) > 0 else 0
    assessed_value = _clamp(money_pat[1]) if len(money_pat) > 1 else 0
    taxable_value = _clamp(money_pat[2]) if len(money_pat) > 2 else 0

    return {
        "account_id": acct,
        "owner_name": owner,
        "owner_mailing_address_line": mail_street,
        "mailing_city": mail_city,
        "mailing_state": mail_state,
        "mailing_zip": mail_zip,
        "situs_address": situs_street,
        "legal_description": legal,
        "market_value": market_value,
        "assessed_value": assessed_value,
        "taxable_value": taxable_value,
    }


def iter_master(limit: Optional[int] = None) -> Iterator[Dict[str, Any]]:
    count = 0
    with open(MASTER, "rb") as f:
        for raw in f:
            rec = parse_master_record(raw.rstrip(b"\n"))
            if rec:
                yield rec
                count += 1
                if limit and count >= limit:
                    return


def iter_rec_account_ids(limit: Optional[int] = None) -> Iterator[str]:
    """Yield account_ids that have outstanding receivables (tax_delinquent flag)."""
    seen = set()
    count = 0
    with open(REC, "rb") as f:
        for raw in f:
            if len(raw) < 12:
                continue
            acct = raw[:11].decode("latin1", "replace").strip()
            if acct.isdigit() and acct not in seen:
                seen.add(acct)
                yield acct
                count += 1
                if limit and count >= limit:
                    return


LISTING_TYPES = ["REO", "As-Is", "Investor", "Cash House", "Foreclosure"]

OUT_OF_STATE_LIKELY = {"CA", "FL", "NY", "NV", "AZ", "CO", "WA", "GA", "IL", "MA", "OR", "MI"}


def enrich(rec: Dict[str, Any], idx: int, delinquent: bool) -> Dict[str, Any]:
    import uuid
    owner_type = classify_owner(rec["owner_name"])
    state = rec["mailing_state"].strip().upper()
    out_of_state = state and state != "TX" and state != ""
    investor_owned = owner_type in ("LLC", "Corporation", "Trust")

    # Derive listing_type heuristically (real data has no listing label)
    if owner_type == "Bank":
        listing_type = "REO"
    elif delinquent:
        listing_type = "Foreclosure"
    elif owner_type in ("LLC", "Corporation"):
        listing_type = "Investor"
    elif owner_type == "Government":
        listing_type = "As-Is"
    else:
        listing_type = LISTING_TYPES[idx % len(LISTING_TYPES)]

    mv = rec["market_value"] or rec["assessed_value"] or 0
    # Asking price: distressed → market discount
    discount = 0.25 if listing_type in ("REO", "Foreclosure") else (0.10 if listing_type == "Cash House" else 0.0)
    price = int(mv * (1 - discount)) if mv else 0
    annual_taxes = int(rec["assessed_value"] * 0.025) if rec["assessed_value"] else 0
    equity = mv - price
    est_roi = round((equity / max(price, 1)) * 100, 1) if price else 0.0
    high_equity = mv and (equity / max(mv, 1)) >= 0.20

    situs_addr = rec["situs_address"]
    if not situs_addr or len(situs_addr) < 3:
        situs_addr = f"Account {rec['account_id']}, Tarrant County, TX"
    else:
        situs_addr = f"{situs_addr}, Tarrant County, TX"

    full_mailing = f"{rec['owner_mailing_address_line']}, {rec['mailing_city']}, {rec['mailing_state']} {rec['mailing_zip']}".strip()

    prop = {
        "id": str(uuid.uuid4()),
        "account_id": rec["account_id"],
        "situs_address": situs_addr,
        "city": rec["mailing_city"] if not out_of_state else "Fort Worth",
        "state": "TX",
        "zip": (rec["mailing_zip"][:5] if not out_of_state else "76104"),
        "county": "Tarrant",
        "beds": 0,
        "baths": 0,
        "sqft": 0,
        "year_built": 0,
        "lot_size_sqft": 0,
        "image_url": PROPERTY_IMAGES[idx % len(PROPERTY_IMAGES)],
        "price": price,
        "market_value": mv,
        "assessed_value": rec["assessed_value"],
        "annual_taxes": annual_taxes,
        "equity_estimate": equity,
        "est_roi_pct": est_roi,
        "legal_description": rec["legal_description"],
        "listing_type": listing_type,
        "owner_name": rec["owner_name"],
        "owner_type": owner_type,
        "owner_mailing_address": full_mailing,
        "out_of_state_owner": bool(out_of_state),
        "tax_delinquent": delinquent,
        "vacant": False,
        "high_equity": bool(high_equity),
        "cash_buyer": owner_type in ("LLC", "Corporation"),
        "investor_owned": investor_owned,
        "data_source": "Tarrant County Tax Roll (Master.dat / Rec.DAT)",
    }
    prop.update(compute_scores(prop))
    return prop


async def run_import(limit_master: int = 25000, limit_rec: int = 200000) -> None:
    mongo_url = os.environ["MONGO_URL"]
    client = AsyncIOMotorClient(mongo_url)
    db = client[os.environ["DB_NAME"]]

    print(f"[1/3] Building delinquent-account set from Rec.DAT (cap {limit_rec})…")
    delinquent_set = set()
    for i, acct in enumerate(iter_rec_account_ids(limit=limit_rec)):
        delinquent_set.add(acct)
        if i and i % 50000 == 0:
            print(f"  scanned {i} rec accounts…")
    print(f"  total unique delinquent accounts: {len(delinquent_set)}")

    print(f"[2/3] Parsing Master.dat (cap {limit_master}) and enriching…")
    batch = []
    BATCH = 1000
    valid = 0

    # Wipe demo seed and any prior import
    await db.properties.delete_many({})
    print("  cleared existing properties collection")

    for idx, rec in enumerate(iter_master(limit=limit_master)):
        # Skip parcels with no owner or no value (exempt/parent records)
        if not rec["owner_name"] or len(rec["owner_name"]) < 3:
            continue
        if rec["market_value"] == 0 and rec["assessed_value"] == 0:
            continue
        delinquent = rec["account_id"] in delinquent_set
        prop = enrich(rec, idx, delinquent)
        batch.append(prop)
        valid += 1
        if len(batch) >= BATCH:
            await db.properties.insert_many(batch)
            batch = []
            if valid % 5000 == 0:
                print(f"  inserted {valid} properties…")
    if batch:
        await db.properties.insert_many(batch)
    print(f"  inserted {valid} valid properties")

    print("[3/3] Building indexes…")
    await db.properties.create_index("id", unique=True)
    await db.properties.create_index("account_id")
    await db.properties.create_index("owner_type")
    await db.properties.create_index("listing_type")
    await db.properties.create_index("zip")
    await db.properties.create_index([("situs_address", "text"), ("owner_name", "text"), ("city", "text")])
    print("  done.")

    total = await db.properties.count_documents({})
    print(f"\n✓ Import complete. Total real Tarrant County properties in DB: {total}")
    client.close()


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit-master", type=int, default=25000)
    ap.add_argument("--limit-rec", type=int, default=200000)
    args = ap.parse_args()
    asyncio.run(run_import(args.limit_master, args.limit_rec))
