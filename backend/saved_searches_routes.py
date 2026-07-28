"""
Saved Searches — save, list, delete, and re-run property searches.
Uses the same PostgreSQL JSONB document store as the rest of InvestorFlip.
"""

import uuid
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from database import PostgresDatabase

router = APIRouter(tags=["saved-searches"])


# ─── Models ──────────────────────────────────────────────

class SavedSearchCreate(BaseModel):
    name: str
    query: str
    filter_type: str = "all"
    ai_query: Optional[str] = None
    notes: Optional[str] = None
    notify_on_new: bool = False


class SavedSearchResponse(BaseModel):
    id: str
    name: str
    query: str
    filter_type: str
    ai_query: Optional[str] = None
    notes: Optional[str] = None
    notify_on_new: bool = False
    created_at: str
    last_run_at: Optional[str] = None
    result_count: Optional[int] = None


class SavedSearchRunResult(BaseModel):
    search: SavedSearchResponse
    results: List[Dict[str, Any]]
    count: int


# ─── Helper ──────────────────────────────────────────────

def _build_filter_query(filter_type: str) -> Dict[str, Any]:
    """Mirrors the filter logic from get_distressed_properties in add_all_routes.py."""
    query: Dict[str, Any] = {}
    if filter_type in ("violations", "distressed"):
        query["violation_count"] = {"$gt": 0}
    elif filter_type == "foreclosure":
        query["$or"] = [
            {"listing_type": "Foreclosure"},
            {"foreclosure": True},
        ]
    elif filter_type == "vacant":
        query["vacant"] = True
    elif filter_type == "wholesale":
        query["$or"] = [
            {"listing_type": "Wholesale"},
            {"wholesale": True},
        ]
    elif filter_type == "tax-delinquent":
        query["tax_delinquent"] = True
    elif filter_type == "pre-foreclosure":
        query["$or"] = [
            {"pre_foreclosure": True},
            {"listing_type": "Pre-Foreclosure"},
        ]
    elif filter_type == "absentee":
        query["absentee_owner"] = True
    elif filter_type == "fixer-upper":
        query["$or"] = [
            {"fixer_upper": True},
            {"listing_type": "Fix & Flip"},
        ]
    elif filter_type == "off-market":
        query["$or"] = [
            {"off_market": True},
            {"wholesale": True},
        ]
    return query


async def _run_search(doc: Dict[str, Any], limit: int = 20) -> List[Dict[str, Any]]:
    db = PostgresDatabase()
    try:
        await db.connect()
        base_query = _build_filter_query(doc.get("filter_type", "all"))
        search_text = doc.get("query", "").strip()
        if search_text:
            text_clause = {
                "$or": [
                    {"address": {"$regex": re.escape(search_text), "$options": "i"}},
                    {"city": {"$regex": re.escape(search_text), "$options": "i"}},
                    {"zip": {"$regex": re.escape(search_text), "$options": "i"}},
                ]
            }
            base_query = {"$and": [base_query, text_clause]} if base_query else text_clause
        base_query["is_synthetic"] = {"$ne": True}
        cursor = db.properties.find(base_query)
        cursor = cursor.sort("investment_score", -1).sort("distress_score", -1)
        return await cursor.to_list(length=limit)
    finally:
        await db.close()


def _to_response(doc: Dict[str, Any]) -> SavedSearchResponse:
    return SavedSearchResponse(
        id=doc["id"],
        name=doc["name"],
        query=doc["query"],
        filter_type=doc.get("filter_type", "all"),
        ai_query=doc.get("ai_query"),
        notes=doc.get("notes"),
        notify_on_new=doc.get("notify_on_new", False),
        created_at=doc["created_at"],
        last_run_at=doc.get("last_run_at"),
        result_count=doc.get("result_count"),
    )


# ─── Endpoints ───────────────────────────────────────────

@router.get("/api/saved-searches", response_model=List[SavedSearchResponse])
async def list_saved_searches(limit: int = Query(50, ge=1, le=200)):
    """List all saved searches, newest first."""
    db = PostgresDatabase()
    try:
        await db.connect()
        docs = await db.saved_searches.find({}).sort("created_at", -1).to_list(length=limit)
        return [_to_response(d) for d in docs]
    finally:
        await db.close()


@router.post("/api/saved-searches", response_model=SavedSearchResponse, status_code=201)
async def save_search(body: SavedSearchCreate):
    """Save a search for later reuse."""
    db = PostgresDatabase()
    try:
        await db.connect()
        doc = {
            "id": str(uuid.uuid4()),
            "name": body.name.strip(),
            "query": body.query.strip(),
            "filter_type": body.filter_type,
            "ai_query": body.ai_query.strip() if body.ai_query else None,
            "notes": body.notes.strip() if body.notes else None,
            "notify_on_new": body.notify_on_new,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "last_run_at": None,
            "result_count": None,
        }
        await db.saved_searches.insert_one(doc)
        return _to_response(doc)
    finally:
        await db.close()


@router.get("/api/saved-searches/{search_id}", response_model=SavedSearchResponse)
async def get_saved_search(search_id: str):
    db = PostgresDatabase()
    try:
        await db.connect()
        doc = await db.saved_searches.find_one({"id": search_id})
        if not doc:
            raise HTTPException(404, "Saved search not found")
        return _to_response(doc)
    finally:
        await db.close()


@router.patch("/api/saved-searches/{search_id}", response_model=SavedSearchResponse)
async def update_saved_search(search_id: str, body: SavedSearchCreate):
    db = PostgresDatabase()
    try:
        await db.connect()
        doc = await db.saved_searches.find_one({"id": search_id})
        if not doc:
            raise HTTPException(404, "Saved search not found")
        doc.update({
            "name": body.name.strip(),
            "query": body.query.strip(),
            "filter_type": body.filter_type,
            "ai_query": body.ai_query.strip() if body.ai_query else None,
            "notes": body.notes.strip() if body.notes else None,
            "notify_on_new": body.notify_on_new,
        })
        await db.saved_searches.update_one({"id": search_id}, doc)
        return _to_response(doc)
    finally:
        await db.close()


@router.delete("/api/saved-searches/{search_id}", status_code=204)
async def delete_saved_search(search_id: str):
    db = PostgresDatabase()
    try:
        await db.connect()
        ok = await db.saved_searches.delete_one({"id": search_id})
        if not ok:
            raise HTTPException(404, "Saved search not found")
    finally:
        await db.close()


@router.post("/api/saved-searches/{search_id}/run", response_model=SavedSearchRunResult)
async def run_saved_search(search_id: str, limit: int = Query(20, ge=1, le=100)):
    """Re‑run a saved search and return matching properties."""
    db = PostgresDatabase()
    try:
        await db.connect()
        doc = await db.saved_searches.find_one({"id": search_id})
        if not doc:
            raise HTTPException(404, "Saved search not found")
        results = await _run_search(doc, limit)
        doc["last_run_at"] = datetime.now(timezone.utc).isoformat()
        doc["result_count"] = len(results)
        await db.saved_searches.update_one({"id": search_id}, doc)
        return SavedSearchRunResult(
            search=_to_response(doc),
            results=results,
            count=len(results),
        )
    finally:
        await db.close()
