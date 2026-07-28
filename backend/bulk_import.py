"""
Bulk CSV Import - Upload address lists and pull property data + mortgage estimates.
"""

import csv
import io
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Query
from pydantic import BaseModel

from database import PostgresDatabase

router = APIRouter(prefix="/api/import/bulk", tags=["bulk-import"])


class BulkImportResult(BaseModel):
    batch_id: str
    total: int
    succeeded: int
    failed: int
    errors: List[Dict[str, str]]
    results: List[Dict[str, Any]]


@router.post("/mortgage-lookup", response_model=BulkImportResult)
async def bulk_mortgage_lookup(file: UploadFile = File(...)):
    """
    Upload a CSV file with addresses to bulk pull mortgage estimates.
    
    CSV should have headers: address (required), notes (optional)
    Example:
        address,notes
        "3425 Cloer Dr, Fort Worth, TX 76109",
        "4701 El Campo Ave, Fort Worth, TX 76107,bought 2022"
    """
    # Read CSV
    content = await file.read()
    text = content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    
    if not reader.fieldnames or "address" not in reader.fieldnames:
        raise HTTPException(400, "CSV must have 'address' column")
    
    batch_id = str(uuid.uuid4())
    results = []
    errors = []
    succeeded = 0
    failed = 0
    
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
        except Exception as e:
            errors.append({"row": str(row_num), "address": address, "error": str(e)})
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
    """
    Upload a CSV file with property addresses to import into the database.
    
    CSV headers: address (required), price (optional), beds (optional), 
    baths (optional), sqft (optional), notes (optional)
    """
    content = await file.read()
    text = content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    
    if not reader.fieldnames or "address" not in reader.fieldnames:
        raise HTTPException(400, "CSV must have 'address' column")
    
    db = PostgresDatabase()
    try:
        await db.connect()
        imported = 0
        errors = []
        
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
            except Exception as e:
                errors.append({"row": str(row_num), "error": str(e)})
        
        return {
            "imported": imported,
            "errors": errors,
            "source": source,
        }
    finally:
        await db.close()
