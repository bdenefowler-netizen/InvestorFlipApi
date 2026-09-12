"""Protected RapidAPI provider routes.

Every route is under /api/rapidapi so admin_auth requires the InvestorFlip admin
key before any paid-provider credit can be consumed.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import httpx
from fastapi import APIRouter, Body, HTTPException, Query

from importers.rapidapi_real_estate_extras import (
    ai_property_market_search,
    foreclosed_properties,
    fsbo_owner_lookup,
    fsbo_property_details,
    provider_status,
    realtor_autocomplete,
)

router = APIRouter(prefix="/api/rapidapi", tags=["RapidAPI"])


def _provider_error(exc: Exception) -> HTTPException:
    if isinstance(exc, RuntimeError):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, httpx.HTTPStatusError):
        response = exc.response
        detail = response.text[:500] if response is not None else str(exc)
        status = response.status_code if response is not None else 502
        return HTTPException(status_code=502, detail=f"Provider returned {status}: {detail}")
    return HTTPException(status_code=502, detail=f"Provider request failed: {exc}")


@router.get("/status")
async def rapidapi_provider_status() -> Dict[str, Any]:
    """Show which optional RapidAPI integrations are configured/enabled."""
    return provider_status()


@router.get("/fsbo-owner")
async def rapidapi_fsbo_owner(
    address: str = Query(..., min_length=3),
    request_timeout_secs: int = Query(30, ge=1, le=120),
) -> Any:
    try:
        return await fsbo_owner_lookup(address, request_timeout_secs)
    except Exception as exc:
        raise _provider_error(exc) from exc


@router.get("/fsbo-details")
async def rapidapi_fsbo_details(property_id: str = Query(..., min_length=1)) -> Any:
    try:
        return await fsbo_property_details(property_id)
    except Exception as exc:
        raise _provider_error(exc) from exc


@router.get("/realtor-autocomplete")
async def rapidapi_realtor_autocomplete(query: str = Query(..., min_length=3)) -> Any:
    try:
        return await realtor_autocomplete(query)
    except Exception as exc:
        raise _provider_error(exc) from exc


@router.get("/foreclosures")
async def rapidapi_foreclosures(
    page: int = Query(1, ge=1),
    city: str = Query("Fort Worth", min_length=1),
    no_hoa_fee: Optional[bool] = Query(None),
) -> Any:
    """Search the nationwide foreclosure feed; default view is Fort Worth."""
    try:
        return await foreclosed_properties(
            page=page,
            city=city,
            no_hoa_fee=no_hoa_fee,
        )
    except Exception as exc:
        raise _provider_error(exc) from exc


@router.post("/quill-market-search")
async def rapidapi_quill_market_search(
    payload: Dict[str, Any] = Body(...),
    noqueue: bool = Query(True),
) -> Any:
    """Return provider AI valuation/market/rental signals for Quill to evaluate."""
    try:
        return await ai_property_market_search(payload, noqueue=noqueue)
    except Exception as exc:
        raise _provider_error(exc) from exc
