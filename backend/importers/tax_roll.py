"""Streaming Tarrant County tax-roll importer for InvestorFlip.

The county export uses fixed-width records. Field positions are deliberately
loaded from a JSON layout file rather than guessed in code.

Example Railway/local usage from the backend directory:

    python -m importers.tax_roll \
        --zip /data/TaxRoll20260618.zip \
        --layout data/tarrant_tax_roll_layout.json \
        --city "Fort Worth"

Records are written to the ``tax_roll`` MongoDB collection. Live listings stay
in ``properties`` and can later be enriched by parcel ID or normalized address.
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
from typing import Any, Dict, Iterable, Iterator, Mapping, Optional

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo import ASCENDING, UpdateOne


REQUIRED_MASTER_FIELDS = ("account_id", "situs_address", "situs_city")


@dataclass(frozen=True)
class FieldSpec:
    """One zero-based, end-exclusive fixed-width field."""

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
        return cls(
            start=start,
            end=end,
            kind=str(value.get("kind", "text")).lower(),
            scale=int(value.get("scale", 0)),
        )


def load_layout(path: Path) -> Dict[str, Any]:
    layout = json.loads(path.read_text(encoding="utf-8"))
    if "master" not in layout or "fields" not in layout["master"]:
        raise ValueError("Layout must define master.fields")

    missing = [name for name in REQUIRED_MASTER_FIELDS if name not in layout["master"]["fields"]]
    if missing:
        raise ValueError(f"Layout is missing required master fields: {', '.join(missing)}")
    return layout


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip())


def _parse_value(raw: str, spec: FieldSpec) -> Any:
    value = raw[spec.start : spec.end]
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
    parsed: Dict[str, Any] = {}
    for name, config in fields.items():
        parsed[name] = _parse_value(line, FieldSpec.from_mapping(config))
    return parsed


_SUFFIXES = {
    "STREET": "ST",
    "AVENUE": "AVE",
    "BOULEVARD": "BLVD",
    "ROAD": "RD",
    "DRIVE": "DR",
    "LANE": "LN",
    "COURT": "CT",
    "CIRCLE": "CIR",
    "PARKWAY": "PKWY",
    "PLACE": "PL",
    "TRAIL": "TRL",
    "HIGHWAY": "HWY",
}


def normalize_address(value: str) -> str:
    """Create a conservative matching key without inventing address data."""
    text = _clean_text(value or "").upper()
    text = re.sub(r"[^A-Z0-9 ]", " ", text)
    parts = [_SUFFIXES.get(part, part) for part in text.split()]
    return " ".join(parts)


def _master_document(record: Dict[str, Any], source_name: str) -> Dict[str, Any]:
    account_id = str(record.get("account_id") or "").strip()
    situs_address = str(record.get("situs_address") or "").strip()
    city = str(record.get("situs_city") or "").strip()
    state = str(record.get("situs_state") or "TX").strip() or "TX"
    zip_code = str(record.get("situs_zip") or "").strip()[:5]

    mailing_parts = [
        record.get("mailing_address"),
        record.get("mailing_city"),
        record.get("mailing_state"),
        record.get("mailing_zip"),
    ]
    mailing_address = _clean_text(" ".join(str(v) for v in mailing_parts if v))

    now = datetime.now(timezone.utc).isoformat()
    document = {
        "account_id": account_id,
        "parcel_id": account_id,
        "situs_address": situs_address,
        "normalized_situs_address": normalize_address(situs_address),
        "city": city,
        "state": state,
        "zip": zip_code,
        "owner_name": record.get("owner_name") or "",
        "owner_mailing_address": mailing_address,
        "mailing_city": record.get("mailing_city") or "",
        "mailing_state": record.get("mailing_state") or "",
        "mailing_zip": str(record.get("mailing_zip") or "")[:5],
        "market_value": record.get("market_value"),
        "assessed_value": record.get("assessed_value"),
        "land_value": record.get("land_value"),
        "improvement_value": record.get("improvement_value"),
        "year_built": record.get("year_built"),
        "legal_description": record.get("legal_description") or "",
        "homestead": record.get("homestead"),
        "data_source": source_name,
        "tax_roll_updated_at": now,
    }
    return {key: value for key, value in document.items() if value not in (None, "")}


def iter_member_lines(archive: zipfile.ZipFile, member: str) -> Iterator[str]:
    with archive.open(member, "r") as stream:
        for raw in stream:
            yield raw.decode("utf-8", errors="replace").rstrip("\r\n")


async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    await db.tax_roll.create_index([("account_id", ASCENDING)], unique=True)
    await db.tax_roll.create_index([("normalized_situs_address", ASCENDING), ("zip", ASCENDING)])
    await db.tax_roll.create_index([("city", ASCENDING)])


async def import_master(
    db: AsyncIOMotorDatabase,
    zip_path: Path,
    layout: Mapping[str, Any],
    city: str = "Fort Worth",
    batch_size: int = 1000,
    dry_run: bool = False,
    max_records: Optional[int] = None,
) -> Dict[str, int]:
    master_config = layout["master"]
    member = master_config.get("member", "Master.dat")
    fields = master_config["fields"]
    target_city = city.casefold().strip()
    source_name = f"Tarrant County Tax Roll ({zip_path.name})"

    scanned = matched_city = valid = upserted = skipped = 0
    operations = []

    async def flush() -> None:
        nonlocal upserted, operations
        if not operations:
            return
        if dry_run:
            upserted += len(operations)
        else:
            result = await db.tax_roll.bulk_write(operations, ordered=False)
            upserted += result.upserted_count + result.modified_count
        operations = []

    with zipfile.ZipFile(zip_path, "r") as archive:
        if member not in archive.namelist():
            raise FileNotFoundError(f"{member!r} is not present in {zip_path.name}")

        for line in iter_member_lines(archive, member):
            scanned += 1
            record = parse_fixed_width(line, fields)
            if str(record.get("situs_city") or "").casefold().strip() != target_city:
                continue
            matched_city += 1

            document = _master_document(record, source_name)
            account_id = document.get("account_id")
            normalized_address = document.get("normalized_situs_address")
            if not account_id or not normalized_address:
                skipped += 1
                continue

            valid += 1
            operations.append(
                UpdateOne(
                    {"account_id": account_id},
                    {"$set": document, "$setOnInsert": {"created_at": datetime.now(timezone.utc).isoformat()}},
                    upsert=True,
                )
            )
            if len(operations) >= batch_size:
                await flush()

            if max_records and scanned >= max_records:
                break

    await flush()
    return {
        "scanned": scanned,
        "matched_city": matched_city,
        "valid": valid,
        "upserted_or_updated": upserted,
        "skipped": skipped,
    }


async def run(args: argparse.Namespace) -> None:
    zip_path = Path(args.zip).expanduser().resolve()
    layout_path = Path(args.layout).expanduser().resolve()
    if not zip_path.exists():
        raise FileNotFoundError(zip_path)
    if not layout_path.exists():
        raise FileNotFoundError(layout_path)

    layout = load_layout(layout_path)
    mongo_url = os.environ.get("MONGO_URL", "").strip()
    db_name = os.environ.get("DB_NAME", "").strip()
    if not args.dry_run and (not mongo_url or not db_name):
        raise RuntimeError("MONGO_URL and DB_NAME are required unless --dry-run is used")

    client = AsyncIOMotorClient(mongo_url) if mongo_url else None
    try:
        db = client[db_name] if client else None
        if db is not None:
            await ensure_indexes(db)
        result = await import_master(
            db=db,
            zip_path=zip_path,
            layout=layout,
            city=args.city,
            batch_size=args.batch_size,
            dry_run=args.dry_run,
            max_records=args.max_records,
        )
        print(json.dumps({"ok": True, "city": args.city, "dry_run": args.dry_run, **result}, indent=2))
    finally:
        if client:
            client.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Import Fort Worth records from a Tarrant County fixed-width tax roll")
    parser.add_argument("--zip", required=True, help="Path to TaxRoll ZIP archive")
    parser.add_argument("--layout", required=True, help="Path to fixed-width JSON field layout")
    parser.add_argument("--city", default="Fort Worth")
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument("--max-records", type=int, default=None, help="Optional scan limit for testing")
    parser.add_argument("--dry-run", action="store_true", help="Parse and count without writing to MongoDB")
    return parser


if __name__ == "__main__":
    asyncio.run(run(build_parser().parse_args()))
