"""Protected RapidAPI provider routes."""
from __future__ import annotations
from typing import Any, Dict, Optional
import httpx
from fastapi import APIRouter, Body, HTTPException, Query
from importers.rapidapi_real_estate_extras import (
    ai_property_details, ai_property_market_search, email_hunter_email, email_hunter_find,
    foreclosed_properties, fsbo_owner_lookup, fsbo_property_details, provider_status,
    realtor_autocomplete, us_property_lookup, us_property_search, zillw_full_property_bundle,
)
router=APIRouter(prefix="/api/rapidapi",tags=["RapidAPI"])

def _provider_error(exc:Exception)->HTTPException:
    if isinstance(exc,RuntimeError): return HTTPException(status_code=400,detail=str(exc))
    if isinstance(exc,httpx.HTTPStatusError):
        response=exc.response
        detail=response.text[:500] if response is not None else str(exc)
        status=response.status_code if response is not None else 502
        return HTTPException(status_code=502,detail=f"Provider returned {status}: {detail}")
    return HTTPException(status_code=502,detail=f"Provider request failed: {exc}")

@router.get("/status")
async def rapidapi_provider_status()->Dict[str,Any]: return provider_status()

@router.get("/fsbo-owner")
async def rapidapi_fsbo_owner(address:str=Query(...,min_length=3),request_timeout_secs:int=Query(30,ge=1,le=120))->Any:
    try:return await fsbo_owner_lookup(address,request_timeout_secs)
    except Exception as exc: raise _provider_error(exc) from exc

@router.get("/fsbo-details")
async def rapidapi_fsbo_details(property_id:str=Query(...,min_length=1))->Any:
    try:return await fsbo_property_details(property_id)
    except Exception as exc: raise _provider_error(exc) from exc

@router.get("/realtor-autocomplete")
async def rapidapi_realtor_autocomplete(query:str=Query(...,min_length=3))->Any:
    try:return await realtor_autocomplete(query)
    except Exception as exc: raise _provider_error(exc) from exc

@router.get("/foreclosures")
async def rapidapi_foreclosures(page:int=Query(1,ge=1),city:str=Query("Fort Worth",min_length=1),no_hoa_fee:Optional[bool]=Query(None))->Any:
    try:return await foreclosed_properties(page=page,city=city,no_hoa_fee=no_hoa_fee)
    except Exception as exc: raise _provider_error(exc) from exc

@router.post("/quill-market-search")
async def rapidapi_quill_market_search(payload:Dict[str,Any]=Body(...),noqueue:bool=Query(True))->Any:
    try:return await ai_property_market_search(payload,noqueue=noqueue)
    except Exception as exc: raise _provider_error(exc) from exc

@router.post("/quill-property-details")
async def rapidapi_quill_property_details(payload:Dict[str,Any]=Body(...),noqueue:bool=Query(True))->Any:
    try:return await ai_property_details(payload,noqueue=noqueue)
    except Exception as exc: raise _provider_error(exc) from exc

@router.get("/email-hunter/find")
async def rapidapi_email_hunter_find(url:str=Query(...,min_length=4))->Any:
    try:return await email_hunter_find(url)
    except Exception as exc: raise _provider_error(exc) from exc

@router.get("/email-hunter/email")
async def rapidapi_email_hunter_email(url:str=Query(...,min_length=4))->Any:
    try:return await email_hunter_email(url)
    except Exception as exc: raise _provider_error(exc) from exc

@router.get("/us-properties")
async def rapidapi_us_properties(city:str=Query(...,min_length=1),state:str=Query(...,min_length=2,max_length=2),page_size:int=Query(25,ge=1,le=100),page:int=Query(1,ge=1))->Any:
    try:return await us_property_search(city=city,state=state,page_size=page_size,page=page)
    except Exception as exc: raise _provider_error(exc) from exc

@router.get("/us-properties/lookup")
async def rapidapi_us_property_lookup(address:str=Query(...,min_length=5))->Any:
    try:return await us_property_lookup(address)
    except Exception as exc: raise _provider_error(exc) from exc

@router.post("/zillw/full-property-bundle")
async def rapidapi_zillw_full_property_bundle(payload:Dict[str,Any]=Body(...))->Any:
    try:return await zillw_full_property_bundle(payload)
    except Exception as exc: raise _provider_error(exc) from exc
