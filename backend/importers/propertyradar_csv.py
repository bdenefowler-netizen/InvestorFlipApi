"""PropertyRadar CSV importer — flexible column mapping.

PropertyRadar exports vary slightly; we map by fuzzy header matching so
whatever column names come through, we find the gold.
"""
import csv
import io
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Fuzzy header → canonical field mapping
HEADER_MAP = {
    # address
    "property address": "situs_address", "propertyaddress": "situs_address",
    "address": "situs_address", "situs address": "situs_address",
    "situsaddress": "situs_address", "street address": "situs_address",
    "streetaddress": "situs_address", "property street address": "situs_address",
    # city / state / zip
    "property city": "city", "propertycity": "city", "city": "city",
    "property state": "state", "propertystate": "state", "state": "state",
    "property zip": "zip", "propertyzip": "zip", "zip code": "zip",
    "zipcode": "zip", "zip": "zip",
    # owner
    "owner name": "owner_name", "ownername": "owner_name",
    "current owner": "owner_name", "currentowner": "owner_name",
    "first name": "owner_first_name", "firstname": "owner_first_name",
    "last name": "owner_last_name", "lastname": "owner_last_name",
    # owner mailing address
    "mailing address": "mailing_address", "mailingaddress": "mailing_address",
    "owner mailing address": "mailing_address", "ownermailingaddress": "mailing_address",
    "mailing city": "mailing_city", "mailingcity": "mailing_city",
    "mailing state": "mailing_state", "mailingstate": "mailing_state",
    "mailing zip": "mailing_zip", "mailingzip": "mailing_zip",
    # value / equity
    "estimated value": "market_value", "estimatedvalue": "market_value",
    "estimated market value": "market_value", "estimatedmarketvalue": "market_value",
    "avm": "market_value", "total value": "market_value",
    "totalvalue": "market_value", "market value": "market_value",
    "marketvalue": "market_value", "estimated equity": "equity",
    "estimatedequity": "equity", "equity": "equity",
    "open mortgage amount": "open_mortgage", "openmortgageamount": "open_mortgage",
    "mortgage amount": "open_mortgage", "mortgageamount": "open_mortgage",
    # sale info
    "last sale date": "last_sale_date", "lastsaledate": "last_sale_date",
    "sale date": "last_sale_date", "saledate": "last_sale_date",
    "last sale price": "last_sale_price", "lastsaleprice": "last_sale_price",
    "sale price": "last_sale_price", "saleprice": "last_sale_price",
    "last transfer date": "last_sale_date", "lasttransferdate": "last_sale_date",
    "last transfer price": "last_sale_price", "lasttransferprice": "last_sale_price",
    # physical
    "beds": "beds", "bedrooms": "beds", "total bedrooms": "beds",
    "totalbedrooms": "beds",
    "baths": "baths", "bathrooms": "baths", "total bathrooms": "baths",
    "totalbathrooms": "baths",
    "sqft": "sqft", "square feet": "sqft", "squarefeet": "sqft",
    "living area": "sqft", "livingarea": "sqft", "total sqft": "sqft",
    "totalsqft": "sqft", "finished sqft": "sqft", "finishedsqft": "sqft",
    "year built": "year_built", "yearbuilt": "year_built",
    "lot size": "lot_size", "lotsize": "lot_size",
    "property type": "property_type", "propertytype": "property_type",
    "property subtype": "property_type", "propertysubtype": "property_type",
    # distress / leads
    "distress score": "distress_score", "distressscore": "distress_score",
    "foreclosure status": "foreclosure_status", "foreclosurestatus": "foreclosure_status",
    "preforeclosure": "preforeclosure", "preforeclosure status": "foreclosure_status",
    "auction date": "auction_date", "auctiondate": "auction_date",
    "tax delinquent": "tax_delinquent", "taxdelinquent": "tax_delinquent",
    "absentee": "absentee", "owner occupied": "owner_occupied", "owneroccupied": "owner_occupied",
    "vacant": "vacant", "occupancy": "occupancy",
    "phone": "owner_phone", "phone number": "owner_phone", "phonenumber": "owner_phone",
    "email": "owner_email", "email address": "owner_email", "emailaddress": "owner_email",
    # parcel
    "parcel number": "parcel_id", "parcelnumber": "parcel_id",
    "apn": "parcel_id", "tax id": "parcel_id", "taxid": "parcel_id",
    "legal description": "legal_description", "legaldescription": "legal_description",
    "zoning": "zoning",
}

VALUE_MAP = {
    "property_type": {
        "single family": "single_family", "single family residence": "single_family",
        "sfr": "single_family", "residential": "single_family",
        "condo": "condo", "condominium": "condo",
        "townhouse": "townhouse", "townhome": "townhouse",
        "multi family": "multi_family", "multifamily": "multi_family",
        "duplex": "multi_family", "triplex": "multi_family",
        "quadplex": "multi_family", "apartment": "multi_family",
        "manufactured": "manufactured", "mobile home": "manufactured",
        "vacant land": "land", "land": "land", "lot": "land",
    }
}


def _clean_header(h: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (h or "").strip().lower())


def _parse_money(v: str) -> Optional[float]:
    if v is None:
        return None
    s = str(v).strip()
    if not s or s.lower() in ("n/a", "na", "null", "none", "-", "--", ""):
        return None
    s = re.sub(r"[^\d.-]", "", s)
    try:
        return float(s)
    except ValueError:
        return None


def _parse_int(v: str) -> Optional[int]:
    f = _parse_money(v)
    return int(f) if f is not None else None


def _parse_bool(v: str) -> Optional[bool]:
    if v is None:
        return None
    s = str(v).strip().lower()
    if s in ("y", "yes", "true", "1", "t"):
        return True
    if s in ("n", "no", "false", "0", "f"):
        return False
    return None


def normalize_property(row: Dict[str, str]) -> Dict[str, Any]:
    """Map one PropertyRadar CSV row to our property schema."""
    # Build cleaned header lookup
    mapped = {}
    for k, v in row.items():
        canon = HEADER_MAP.get(_clean_header(k))
        if canon and v is not None and str(v).strip() not in ("", "n/a", "N/A"):
            mapped[canon] = str(v).strip()

    # Rebuild full address if needed
    address = mapped.get("situs_address")
    city = mapped.get("city")
    state = mapped.get("state")
    zip5 = (mapped.get("zip") or "")[:5]

    # Normalize values
    prop: Dict[str, Any] = {
        "situs_address": address,
        "city": city,
        "state": state,
        "zip": zip5,
        "owner_name": mapped.get("owner_name"),
        "mailing_address": mapped.get("mailing_address"),
        "mailing_city": mapped.get("mailing_city"),
        "mailing_state": mapped.get("mailing_state"),
        "mailing_zip": mapped.get("mailing_zip"),
        "market_value": _parse_money(mapped.get("market_value")),
        "equity": _parse_money(mapped.get("equity")),
        "open_mortgage": _parse_money(mapped.get("open_mortgage")),
        "last_sale_date": mapped.get("last_sale_date"),
        "last_sale_price": _parse_money(mapped.get("last_sale_price")),
        "beds": _parse_int(mapped.get("beds")),
        "baths": _parse_float_or_int(mapped.get("baths")),
        "sqft": _parse_int(mapped.get("sqft")),
        "year_built": _parse_int(mapped.get("year_built")),
        "lot_size": mapped.get("lot_size"),
        "property_type": VALUE_MAP["property_type"].get(
            (mapped.get("property_type") or "").lower(), mapped.get("property_type")
        ),
        "distress_score": _parse_int(mapped.get("distress_score")),
        "foreclosure_status": mapped.get("foreclosure_status"),
        "auction_date": mapped.get("auction_date"),
        "tax_delinquent": _parse_bool(mapped.get("tax_delinquent")),
        "absentee": _parse_bool(mapped.get("absentee")),
        "owner_occupied": _parse_bool(mapped.get("owner_occupied")),
        "vacant": _parse_bool(mapped.get("vacant")),
        "owner_phone": mapped.get("owner_phone"),
        "owner_email": mapped.get("owner_email"),
        "parcel_id": mapped.get("parcel_id"),
        "legal_description": mapped.get("legal_description"),
        "zoning": mapped.get("zoning"),
        "data_source": "PropertyRadar",
    }

    # Derive price for analysis (last sale price if no list price)
    if prop["last_sale_price"] and not prop.get("price"):
        prop["price"] = prop["last_sale_price"]

    return {k: v for k, v in prop.items() if v is not None}


def _parse_float_or_int(v: str) -> Optional[float]:
    f = _parse_money(v)
    return f


def parse_csv(csv_text: str) -> List[Dict[str, Any]]:
    """Parse PropertyRadar CSV export into normalized property dicts."""
    reader = csv.DictReader(io.StringIO(csv_text))
    props = []
    for row in reader:
        if not row or not any(v for v in row.values()):
            continue
        p = normalize_property(row)
        if p.get("situs_address"):
            props.append(p)
    return props


def parse_csv_file(path: str) -> List[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8-sig", errors="ignore") as f:
        return parse_csv(f.read())


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python propertyradar_csv.py <file.csv>")
        sys.exit(1)
    props = parse_csv_file(sys.argv[1])
    print(f"Parsed {len(props)} properties from {sys.argv[1]}")
    if props:
        print(f"Sample: {json.dumps(props[0], indent=2)[:800]}")
