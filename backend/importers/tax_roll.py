"""Match Tarrant County tax-roll records to live InvestorFlip listings.

The official MASTER.DAT layout does not contain a property-city field. To avoid
misclassifying addresses, this importer loads the live Fort Worth listings from
PostgreSQL, scans MASTER.DAT once, and imports only exact normalized street-address
matches. Matched tax facts are stored in ``tax_roll`` and copied onto the live
property records for Serenity and Quill.

Railway usage from the backend directory:

    python -m importers.tax_roll \
        --zip /data/TaxRoll20260710.zip \
        --layout data/tarrant_tax_roll_layout.json \
        --dry-run

Remove ``--dry-run`` after the match report looks correct.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Mapping, Optional

from database import PostgresDatabase

REQUIRED_MASTER_FIELDS = ("account_id", "street_name", "street_number")


@dataclass(frozen=True)
class FieldSpec:
    start: int
    end: int
    kind: str = "text"
    scale: int = 0

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "FieldSpec":
        start = int(value["start"])
        end = int(value["end"])
        if start < 0 or end <= start:
            raise ValueError(f"Invalid fixed-width slice: start={start}, end={end}")
        return cls(start, end, str(value.get("kind", "text")).lower(), int(value.get("scale", 0)))


def load_layout(path: Path) -> Dict[str, Any]:
    layout = json.loads(path.read_text(encoding="utf-8"))
    fields = layout.get("master", {}).get("fields", {})
    missing = [name for name in REQUIRED_MASTER_FIELDS if name not in fields]
    if missing:
        raise ValueError(f"Layout missing required master fields: {', '.join(missing)}")
    return layout


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip())


def _parse_value(raw: str, spec: FieldSpec) -> Any:
    value = raw[spec.start:spec.end]
    if spec.kind == "text":
        return _clean_text(value)

    cleaned = re.sub(r"[^0-9.-]", "", value)
    if not cleaned or cleaned in {"-", ".", "-."}:
        return None
    if spec.kind == "int":
        number = int(cleaned)
        return int(number / (10 ** spec.scale)) if spec.scale else number
    if spec.kind in {"decimal", "money", "float"}:
        number = float(cleaned)
        return number / (10 ** spec.scale) if spec.scale else number
    if spec.kind == "bool":
        return value.strip().upper() in {"1", "Y", "YES", "T", "TRUE"}
    raise ValueError(f"Unsupported field kind: {spec.kind}")


def parse_fixed_width(line: str, fields: Mapping[str, Mapping[str, Any]]) -> Dict[str, Any]:
    return {name: _parse_value(line, FieldSpec.from_mapping(config)) for name, config in fields.items()}


_SUFFIXES = {
    "STREET": "ST", "AVENUE": "AVE", "BOULEVARD": "BLVD", "ROAD": "RD",
    "DRIVE": "DR", "LANE": "LN", "COURT": "CT", "CIRCLE": "CIR",
    "PARKWAY": "PKWY", "PLACE": "PL", "TRAIL": "TRL", "HIGHWAY": "HWY",
    "TERRACE": "TER", "TURNPIKE": "TPKE",
}
_DIRECTIONS = {"NORTH": "N", "SOUTH": "S", "EAST": "E", "WEST": "W"}


def normalize_address(value: str) -> str:
    """Normalize only the street portion for conservative exact matching."""
    text = _clean_text((value or "").split(",")[0]).upper()
    text = re.sub(r"[^A-Z0-9 ]", " ", text)
    parts = []
    for part in text.split():
        part = _DIRECTIONS.get(part, part)
        part = _SUFFIXES.get(part, part)
        if part.isdigit():
            part = str(int(part))
        parts.append(part)
    return " ".join(parts)


def build_situs_address(record: Mapping[str, Any]) -> str:
    number = str(record.get("street_number") or "").strip()
    if number.isdigit():
        number = str(int(number))
    street = _clean_text(str(record.get("street_name") or ""))
    return _clean_text(f"{number} {street}")


def iter_member_lines(archive: zipfile.ZipFile, member: str) -> Iterator[str]:
    with archive.open(member, "r") as stream:
        for raw in stream:
            yield raw.decode("utf-8", errors="replace").rstrip("\r\n")


async def ensure_indexes(db: PostgresDatabase) -> None:
    await db.connect()


async def load_live_targets(db: PostgresDatabase) -> Dict[str, List[Dict[str, str]]]:
    query = {
        "is_live_listing": True,
        "$or": [
            {"city": {"$regex": "^Fort Worth$", "$options": "i"}},
            {"situs_address": {"$regex": "Fort Worth", "$options": "i"}},
        ],
    }
    targets: Dict[str, List[Dict[str, str]]] = {}
    async for prop in db.properties.find(query, {"_id": 0, "id": 1, "situs_address": 1}):
        key = normalize_address(str(prop.get("situs_address") or ""))
        if key and prop.get("id"):
            targets.setdefault(key, []).append({"id": str(prop["id"]), "address": str(prop.get("situs_address") or "")})
    return targets


def master_document(record: Dict[str, Any], property_ids: List[str], source_name: str) -> Dict[str, Any]:
    situs_address = build_situs_address(record)
    owner_name = _clean_text(f"{record.get('owner_name_1') or ''} {record.get('owner_name_2') or ''}")
    mailing_street = _clean_text(f"{record.get('owner_address_1') or ''} {record.get('owner_address_2') or ''}")
    mailing_address = _clean_text(
        f"{mailing_street}, {record.get('owner_city') or ''}, {record.get('owner_state') or ''} {record.get('owner_zip') or ''}"
    ).strip(", ")
    land = float(record.get("land_value") or 0)
    improvement = float(record.get("improvement_value") or 0)
    current_due = float(record.get("current_amount_due") or 0)
    prior_due = float(record.get("prior_amount_due") or 0)

    document = {
        "account_id": str(record.get("account_id") or "").strip(),
        "parcel_id": str(record.get("account_id") or "").strip(),
        "situs_address": situs_address,
        "normalized_situs_address": normalize_address(situs_address),
        "matched_property_ids": property_ids,
        "owner_name": owner_name,
        "owner_mailing_address": mailing_address,
        "mailing_city": record.get("owner_city") or "",
        "mailing_state": record.get("owner_state") or "",
        "mailing_zip": str(record.get("owner_zip") or "")[:5],
        "sqft": record.get("sqft"),
        "lot_size_sqft": record.get("lot_size_sqft"),
        "year_built": record.get("year_built"),
        "acres": record.get("acres"),
        "land_value": land,
        "improvement_value": improvement,
        "market_value": land + improvement,
        "annual_taxes": record.get("adjusted_levy"),
        "current_amount_due": current_due,
        "prior_amount_due": prior_due,
        "tax_delinquent": prior_due > 0 or current_due > 0,
        "delinquency_date": record.get("delinquency_date") or "",
        "owner_exemption_codes": record.get("owner_exemption_codes") or "",
        "legal_description": record.get("legal_description") or "",
        "roll_code": record.get("roll_code") or "",
        "account_status_codes": record.get("account_status_codes") or "",
        "data_source": source_name,
        "tax_roll_updated_at": datetime.now(timezone.utc).isoformat(),
    }
    return {key: value for key, value in document.items() if value not in (None, "")}


def property_enrichment(document: Mapping[str, Any]) -> Dict[str, Any]:
    updates = {
        "account_id": document.get("account_id"),
        "parcel_id": document.get("parcel_id"),
        "owner_name": document.get("owner_name"),
        "owner_mailing_address": document.get("owner_mailing_address"),
        "tax_roll_market_value": document.get("market_value"),
        "tax_roll_land_value": document.get("land_value"),
        "tax_roll_improvement_value": document.get("improvement_value"),
        "annual_taxes": document.get("annual_taxes"),
        "current_tax_amount_due": document.get("current_amount_due"),
        "prior_tax_amount_due": document.get("prior_amount_due"),
        "tax_delinquent": document.get("tax_delinquent", False),
        "tax_roll_source": document.get("data_source"),
        "tax_roll_matched_at": datetime.now(timezone.utc).isoformat(),
    }
    for key in ("sqft", "lot_size_sqft", "year_built", "legal_description"):
        if document.get(key) not in (None, "", 0):
            updates[key] = document[key]
    return {key: value for key, value in updates.items() if value not in (None, "")}


async def import_matches(
    db: PostgresDatabase,
    zip_path: Path,
    layout: Mapping[str, Any],
    dry_run: bool = False,
    max_records: Optional[int] = None,
) -> Dict[str, int]:
    targets = await load_live_targets(db)
    if not targets:
        raise RuntimeError("No live Fort Worth listings found in PostgreSQL. Sync live listings first.")

    master = layout["master"]
    member = master.get("member", "Master.dat")
    fields = master["fields"]
    expected_size = int(master.get("record_size", 741))
    source_name = f"Tarrant County Tax Roll ({zip_path.name})"

    scanned = malformed = matched_records = matched_properties = 0
    tax_records: List[Dict[str, Any]] = []
    property_updates: List[tuple[str, Dict[str, Any]]] = []

    with zipfile.ZipFile(zip_path, "r") as archive:
        if member not in archive.namelist():
            raise FileNotFoundError(f"{member!r} is not present in {zip_path.name}")
        for line in iter_member_lines(archive, member):
            scanned += 1
            if len(line) != expected_size:
                malformed += 1
                continue
            record = parse_fixed_width(line, fields)
            key = normalize_address(build_situs_address(record))
            matches = targets.get(key)
            if not matches:
                if max_records and scanned >= max_records:
                    break
                continue

            property_ids = [item["id"] for item in matches]
            document = master_document(record, property_ids, source_name)
            if not document.get("account_id"):
                continue

            matched_records += 1
            matched_properties += len(property_ids)
            if not dry_run:
                document["created_at"] = datetime.now(timezone.utc).isoformat()
                tax_records.append(document)
                enrichment = property_enrichment(document)
                for property_id in property_ids:
                    property_updates.append((property_id, enrichment))

            if max_records and scanned >= max_records:
                break

    tax_written = properties_enriched = 0
    if not dry_run:
        for document in tax_records:
            result = await db.tax_roll.update_one(
                {"account_id": document["account_id"]}, {"$set": document}, upsert=True
            )
            tax_written += result.upserted_count + result.modified_count
        for property_id, enrichment in property_updates:
            result = await db.properties.update_one({"id": property_id}, {"$set": enrichment})
            properties_enriched += result.modified_count

    return {
        "live_address_keys": len(targets),
        "scanned_master_records": scanned,
        "malformed_records": malformed,
        "matched_tax_records": matched_records,
        "matched_live_properties": matched_properties,
        "tax_records_written": tax_written,
        "properties_enriched": properties_enriched,
    }


async def run(args: argparse.Namespace) -> None:
    zip_path = Path(args.zip).expanduser().resolve()
    layout_path = Path(args.layout).expanduser().resolve()
    if not zip_path.exists():
        raise FileNotFoundError(zip_path)
    if not layout_path.exists():
        raise FileNotFoundError(layout_path)

    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("DATABASE_URL is required")

    db = PostgresDatabase(database_url)
    try:
        await ensure_indexes(db)
        result = await import_matches(
            db=db,
            zip_path=zip_path,
            layout=load_layout(layout_path),
            dry_run=args.dry_run,
            max_records=args.max_records,
        )
        print(json.dumps({"ok": True, "dry_run": args.dry_run, **result}, indent=2))
    finally:
        await db.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Match Tarrant tax-roll records to live Fort Worth listings")
    parser.add_argument("--zip", required=True, help="Path to the Tarrant County TaxRoll ZIP")
    parser.add_argument("--layout", default="data/tarrant_tax_roll_layout.json")
    parser.add_argument("--max-records", type=int, default=None, help="Optional scan limit for testing")
    parser.add_argument("--dry-run", action="store_true", help="Report matches without writing to PostgreSQL")
    return parser


if __name__ == "__main__":
    asyncio.run(run(build_parser().parse_args()))
