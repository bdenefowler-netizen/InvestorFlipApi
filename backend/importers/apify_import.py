"""Retired provider compatibility shim.

Apify was removed from InvestorFlip. This module intentionally performs no
network requests and exists only so older routes/startup code fail closed while
the remaining legacy route labels are removed safely.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

DEFAULT_LOOKBACK_DAYS = 7
APIFY_BASE = ""
TARRANT_CITIES = {
    "fort worth", "arlington", "north richland hills", "haltom city",
    "keller", "southlake", "colleyville", "grapevine", "bedford",
    "euless", "hurst", "benbrook", "white settlement", "saginaw",
    "watauga", "river oaks", "forest hill", "crowley", "burleson",
    "mansfield", "azle", "lake worth", "sansome park", "westworth village",
    "haslet", "eagle mountain", "blue mound", "pelican bay", "kennedale",
    "everman", "dalworthington gardens", "pantego",
}

_RETIRED_REASON = (
    "Apify retired from InvestorFlip. Use native scrapers, Bright Data, "
    "RapidAPI, or OpenWeb Ninja instead."
)


def is_fort_worth_area(city: str) -> bool:
    c = (city or "").strip().lower()
    return bool(c and (c in TARRANT_CITIES or c.startswith("fort worth") or "fort worth" in c))


def get_api_key() -> str:
    """Apify credentials are intentionally ignored."""
    return ""


def is_allowed_actor_id(actor_id: str, built_in_ids: Optional[set[str]] = None) -> bool:
    """Fail closed so legacy /import/apify cannot start a paid actor."""
    return False


def is_allowed_run(run: Dict[str, Any]) -> bool:
    return False


async def fetch_recent_runs(*args: Any, **kwargs: Any) -> List[Dict[str, Any]]:
    return []


async def fetch_dataset(*args: Any, **kwargs: Any) -> List[Dict[str, Any]]:
    return []


def normalize_record(record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    return None


async def import_apify_runs(db: Any, lookback_days: int = DEFAULT_LOOKBACK_DAYS) -> Dict[str, Any]:
    """Compatibility no-op. Never performs an external request."""
    return {
        "ok": True,
        "skipped": True,
        "status": "RETIRED",
        "reason": _RETIRED_REASON,
        "records_imported": 0,
        "property_ids": [],
    }
