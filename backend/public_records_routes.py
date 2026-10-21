"""Read-only API for official county/city property records."""

from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query

from address_utils import canonical_street_key
from database import PostgresDatabase
from importers.arcgis_client import ArcGISQueryError
from importers.fort_worth_code_violations import search_code_violations_by_address
from importers.fort_worth_permits import search_permits_by_address, summarize_permits
from importers.socrata_catalog import search_catalog
from importers.tad_scraper import search_tad_by_address, search_tad_by_owner

router = APIRouter(prefix="/api/public-records", tags=["Public records"])


def _bounded_address(value: str) -> str:
    address = " ".join(str(value or "").split()).strip()
    if len(address) < 5:
        raise HTTPException(status_code=400, detail="Enter a complete street address")
    return address[:180]


async def _county_records_by_address(
    address: str, limit: int = 25
) -> list[Dict[str, Any]]:
    """Search the locally synced official TAD/tax-roll snapshot by street key."""
    key = canonical_street_key(address)
    if not key:
        return []
    db = PostgresDatabase()
    try:
        await db.connect()
        return await (
            db.county_records.find({"address_key": key}, {"_id": 0})
            .sort("updated_at", -1)
            .limit(limit)
            .to_list(length=limit)
        )
    finally:
        await db.close()


async def _tarrant_property_candidates(
    address: str,
) -> tuple[list[Dict[str, Any]], list[Dict[str, Any]]]:
    """Return the durable snapshot and current full ArcGIS parcel matches."""
    synced, live = await asyncio.gather(
        _county_records_by_address(address),
        search_tad_by_address(address),
    )
    return synced, live


@router.get("/sources")
async def public_record_sources() -> Dict[str, Any]:
    return {
        "sources": [
            {
                "key": "tarrant_tax_roll",
                "authority": "Tarrant County / TAD official bulk roll",
                "cost": "free",
                "coverage": "full imported snapshot",
            },
            {
                "key": "tarrant_parcels_live",
                "authority": "Tarrant County ArcGIS Dynamic/TADParcels",
                "cost": "free",
                "coverage": "full live parcel layer (758,000+ records)",
            },
            {
                "key": "fort_worth_code_violations",
                "authority": "City of Fort Worth",
                "cost": "free",
            },
            {
                "key": "fort_worth_permits",
                "authority": "City of Fort Worth",
                "cost": "free",
            },
            {
                "key": "socrata_catalog",
                "authority": "Socrata Open Data Network",
                "cost": "free",
            },
        ]
    }


@router.get("/tarrant/property")
async def tarrant_property(
    address: str = Query(..., min_length=5, max_length=180),
) -> Dict[str, Any]:
    try:
        snapshot, live = await _tarrant_property_candidates(_bounded_address(address))
        items = snapshot or live
        return {
            "query": address,
            "count": len(items),
            "items": items[:25],
            "live_arcgis_matches": live[:25],
            "coverage": "official bulk-roll snapshot plus full live TAD parcel layer",
        }
    except (ArcGISQueryError, RuntimeError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/tarrant/owner")
async def tarrant_owner(
    name: str = Query(..., min_length=2, max_length=120),
    limit: int = Query(25, ge=1, le=100),
) -> Dict[str, Any]:
    try:
        items = await search_tad_by_owner(name, limit=limit)
        return {
            "query": name,
            "count": len(items),
            "items": items,
            "source": "live official Tarrant County/TAD parcel layer",
        }
    except ArcGISQueryError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/fort-worth/code-violations")
async def fort_worth_code_violations(
    address: str = Query(..., min_length=5, max_length=180),
    limit: int = Query(100, ge=1, le=500),
) -> Dict[str, Any]:
    try:
        return await search_code_violations_by_address(
            _bounded_address(address), limit=limit
        )
    except ArcGISQueryError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/fort-worth/permits")
async def fort_worth_permits(
    address: str = Query(..., min_length=5, max_length=180),
    limit: int = Query(100, ge=1, le=500),
) -> Dict[str, Any]:
    try:
        rows = await search_permits_by_address(_bounded_address(address), limit=limit)
        return {"query": address, **summarize_permits(rows)}
    except ArcGISQueryError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/fort-worth/property-report")
async def fort_worth_property_report(
    address: str = Query(..., min_length=5, max_length=180),
    limit: int = Query(100, ge=1, le=250),
) -> Dict[str, Any]:
    clean = _bounded_address(address)
    results = await asyncio.gather(
        _tarrant_property_candidates(clean),
        search_code_violations_by_address(clean, limit=limit),
        search_permits_by_address(clean, limit=limit),
        return_exceptions=True,
    )
    property_result, violations, permits = results
    errors: Dict[str, str] = {}
    if isinstance(property_result, Exception):
        errors["tarrant_property"] = str(property_result)
        property_rows, live_property_rows = [], []
    else:
        property_rows, live_property_rows = property_result
        if not property_rows:
            property_rows = live_property_rows
    if isinstance(violations, Exception):
        errors["code_violations"] = str(violations)
        violations = {"violation_count": 0, "open_violation_count": 0, "violations": []}
    if isinstance(permits, Exception):
        errors["permits"] = str(permits)
        permits = []

    return {
        "query": address,
        "property": property_rows[0] if property_rows else None,
        "property_candidates": property_rows[:10],
        "live_arcgis_matches": live_property_rows[:10],
        "code_violations": violations,
        "permits": summarize_permits(permits),
        "partial": bool(errors),
        "errors": errors,
    }


@router.get("/catalog/search")
async def socrata_catalog_search(
    q: str = Query(..., min_length=2, max_length=160),
    domain: Optional[str] = Query(None, max_length=120),
    limit: int = Query(10, ge=1, le=50),
) -> Dict[str, Any]:
    try:
        items = await search_catalog(q, domain=domain, limit=limit)
        return {"query": q, "count": len(items), "items": items}
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"Socrata catalog lookup failed: {exc}"
        ) from exc
