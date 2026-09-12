"""
Bulk imports plus the public high-capacity County workbook intake used by ADD.
"""

from __future__ import annotations

import csv
import io
import re
import uuid
import zipfile
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Mapping

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from database import PostgresDatabase

router = APIRouter(prefix="/import/bulk", tags=["buli-import"])

# The mobile ADD workflow must comfortably accept the county files the user
# works with. 300 MiB deliberately exceeds the requested 250 MiB minimum.
MAX_PUBLIC_UPLOAD_BYTES = 300 * 1024 * 1024
MAX_ZIP_EXPANDED_BYTES = 750 * 1024 * 1024


class BulkImportResult(BaseModel):
    batch_id: str
    total: int
    succeeded: int
    failed: int
    errors: List[Dict[str, str]]
    results: List[Dict[str, Any]]


@router.post("/mortgage-lookup", response_model=BulkImportResult)
async def bulk_mortgage_lookup(file: UploadFile = File(...)):
    """Upload a CSV with an address column and pull mortgage estimates."""
    content = await file.read()
    text = content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames or "address" not in reader.fieldnames:
        raise HTTPException(400, "CSV must have 'address' column")

    batch_id = str(uuid.uuid4())
    results: List[Dict[str, Any]] = []
    errors: List[Dict[str, str]] = []
    succeeded = failed = 0

    for row_num, row in enumerate(reader, start=2):
        address = row.get("address", "").strip()
        if not address:
            continue
        try:
            from mortgage_lookup import full_mortgage_report

            report = await full_mortgage_report(address)
            report["row"] = row_num
            report["notes"] = row.get("notes", "")
            results.append(report)
            succeeded += 1
        except Exception as exc:
            errors.append({"row": str(row_num), "address": address, "error": str(exc)})
            failed += 1

    return BulkImportResult(
        batch_id=batch_id,
        total=succeeded + failed,
        succeeded=succeeded,
        failed=failed,
        errors=errors,
        results=results,
    )


@router.post("/properties")
async def bulk_import_properties(
    file: UploadFile = File(...),
    source: str = Form("csv_upload"),
):
    """Legacy simple CSV property importer."""
    content = await file.read()
    text = content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames or "address" not in reader.fieldnames:
        raise HTTPException(400, "CSV must have 'address' column")

    db = PostgresDatabase()
    try:
        await db.connect()
        imported = 0
        errors: List[Dict[str, str]] = []
        for row_num, row in enumerate(reader, start=2):
            address = row.get("address", "").strip()
            if not address:
                continue
            try:
                doc = {
                    "id": str(uuid.uuid4()),
                    "address": address,
                    "city": row.get("city", ""),
                    "state": row.get("state", "TX"),
                    "zip": row.get("zip", ""),
                    "price": float(row["price"]) if row.get("price") else None,
                    "beds": int(float(row["beds"])) if row.get("beds") else None,
                    "baths": float(row["baths"]) if row.get("baths") else None,
                    "sqft": float(row["sqft"]) if row.get("sqft") else None,
                    "notes": row.get("notes", ""),
                    "source": source,
                    "imported_at": datetime.now(timezone.utc).isoformat(),
                    "is_synthetic": False,
                }
                await db.properties.insert_one(doc)
                imported += 1
            except Exception as exc:
                errors.append({"row": str(row_num), "error": str(exc)})
        return {"imported": imported, "errors": errors, "source": source}
    finally:
        await db.close()


def _key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").strip().lower()).strip()


def _row_lookup(row: Mapping[str, Any], *names: str) -> Any:
    wanted = {_key(name) for name in names}
    for column, value in row.items():
        if _key(column) in wanted and value not in (None, ""):
            try:
                if value != value:  # pandas NaN
                    continue
            except Exception:
                pass
            return value
    return None


def _upload_categories(filename: str, rows: List[Dict[str, Any]]) -> List[str]:
    name = _key(filename)
    headers = {_key(k) for row in rows[:20] for k in row.keys()}

    if "probate" in name or (
        any("decedent" in h for h in headers) and any("applicant" in h for h in headers)
    ):
        return ["probate"]

    if (
        "preforeclosure" in name
        or "pre foreclosure" in name
        or "county cause number" in headers
        or "scheduled auction" in headers
        or "foreclosure status" in headers
    ):
        return ["pre_foreclosure"]

    if (
        ("code" in name and "violation" in name)
        or "violation address" in headers
        or "complaint type description" in headers
    ):
        return ["code_violations"]

    if "tad" in name and "taxroll" not in name and "tax roll" not in name:
        return ["tad"]

    tax_signals = {
        "tax account number",
        "current year amount due",
        "prior year amount due",
        "delinquency date",
        "adjusted levy",
        "appraised value",
    }
    if "taxroll" in name or "tax roll" in name or len(headers & tax_signals) >= 2:
        categories = ["tax_roll"]
        if headers & {"current year amount due", "prior year amount due", "amount due", "delinquency date"}:
            categories.append("tax_due")
        return categories

    return ["uploaded"]


def _prepare_row(row: Mapping[str, Any], categories: List[str]) -> Dict[str, Any]:
    """Adapt source-specific headers to the canonical intake shape without losing originals."""
    out = dict(row)
    category = categories[0] if categories else "uploaded"

    if category == "probate":
        # Required project rule: Probate always pulls TAD by DECEDENT property address.
        address = _row_lookup(
            row,
            "Decedent Property Address",
            "Decedent Address",
        )
        if address:
            out["address"] = address
        decedent = _row_lookup(row, "Decedent")
        if decedent and not _row_lookup(row, "owner", "owner name", "current owner"):
            out["owner"] = decedent
        out["listing type"] = "Probate"

    elif category == "code_violations":
        address = _row_lookup(row, "Violation Address", "Property Address", "Address")
        if address:
            out["address"] = address
        out["listing type"] = "Code Violation"

    elif category == "pre_foreclosure":
        address = _row_lookup(
            row,
            "Property Address",
            "Matched Address",
            "Situs Address",
            "Address",
        )
        if address:
            out["address"] = address
        out["listing type"] = "Pre-Foreclosure"

    elif category in {"tax_roll", "tax_due"}:
        address = _row_lookup(
            row,
            "Street Name (Property Location)",
            "Property Location",
            "Property Address",
            "Situs Address",
            "Address",
        )
        if address:
            out["address"] = address

    elif category == "tad":
        address = _row_lookup(
            row,
            "Property Address",
            "Situs Address",
            "Address",
            "Location Address",
        )
        if address:
            out["address"] = address

    # Preserve separate matching identifiers2ʬen though legacy intake still has
