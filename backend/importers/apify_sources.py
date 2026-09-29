"""Retired provider compatibility shim.

InvestorFlip no longer uses Apify. Every function in this module is a fail-closed
no-op so legacy imports cannot create paid actor runs or fetch paid datasets.
Preferred sources are native scrapers, Bright Data, RapidAPI, and OpenWeb Ninja.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

APIFY_API_KEY = ""
APIFY_BASE = ""
ACTORS: Dict[str, str] = {}
EXISTING_DATASETS: Dict[str, str] = {}
APIFY_REPLACEMENTS: Dict[str, str] = {}

_RETIRED_REASON = (
    "Apify retired from InvestorFlip. Use native scrapers, Bright Data, "
    "RapidAPI, or OpenWeb Ninja instead."
)


def _retired(source: str) -> Dict[str, Any]:
    return {
        "ok": True,
        "skipped": True,
        "status": "RETIRED",
        "source": source,
        "reason": _RETIRED_REASON,
        "fetched": 0,
        "inserted": 0,
        "matched": 0,
    }


def _ready() -> bool:
    return False


async def _get_dataset_items(*args: Any, **kwargs: Any) -> List[Dict[str, Any]]:
    return []


async def _run_actor(*args: Any, **kwargs: Any) -> Optional[str]:
    return None


async def import_investorlift(db: Any, limit: int = 500, city: Optional[str] = None) -> Dict[str, Any]:
    return _retired("investorlift")


async def import_motivated_sellers(db: Any, limit: int = 500, min_score: int = 0) -> Dict[str, Any]:
    return _retired("motivated_sellers")


async def import_skip_trace_apify(db: Any, limit: int = 100) -> Dict[str, Any]:
    return _retired("skip_trace")


async def import_us_listings(db: Any, limit: int = 500, city: Optional[str] = None) -> Dict[str, Any]:
    return _retired("us_listings")


async def run_real_estate_aggregator(
    location: str = "Fort Worth, TX",
    sources: Optional[List[str]] = None,
) -> Dict[str, Any]:
    return _retired("real_estate_aggregator")


async def apify_status() -> Dict[str, Any]:
    return {
        "configured": False,
        "retired": True,
        "status": "RETIRED",
        "reason": _RETIRED_REASON,
        "actors": {},
    }
