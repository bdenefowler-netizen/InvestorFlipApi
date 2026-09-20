"""Bright Data deal-finder compatibility layer.

Bright Data credentials are read from environment variables only.  This module
keeps the older deal-finder routes working while routing discovery through the
MCP scraper used by InvestorFlip's current Bright Data integration.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("brightdata_deals")


def _token() -> str:
    """Return the configured Bright Data token without ever embedding a fallback."""
    return (
        os.environ.get("BRIGHT_DATA_API_TOKEN", "").strip()
        or os.environ.get("BRIGHT_DATA_TOKEN", "").strip()
        or os.environ.get("BRIGHTDATA_TOKEN", "").strip()
    )


def _require_token() -> None:
    if not _token():
        raise RuntimeError(
            "Bright Data is not configured. Set BRIGHT_DATA_API_TOKEN in Railway."
        )


async def fetch_all_pre_foreclosure_leads(
    days_back: int = 30,
    include_fsbo: bool = True,
    include_hubzu: bool = True,
) -> list[dict[str, Any]]:
    """Preview Bright Data-discovered Fort Worth/Tarrant-area leads.

    ``days_back`` is retained for backwards compatibility with the existing API
    route.  The MCP sources are live marketplace/public-web sources and therefore
    are not silently relabeled as county pre-foreclosure records.
    """
    _require_token()

    # Import lazily so app startup stays healthy even if Bright Data is not used.
    from importers.brightdata_mcp_scraper import fetch_all_leads

    leads = await fetch_all_leads(
        include_offmarket=True,
        include_fsbo=include_fsbo,
        include_hubzu=include_hubzu,
        max_pages=1,
    )

    now = datetime.now(timezone.utc).isoformat()
    clean: list[dict[str, Any]] = []
    for lead in leads:
        if not isinstance(lead, dict):
            continue
        row = dict(lead)
        row.setdefault("source_platform", "Bright Data")
        row.setdefault("data_source", "Bright Data MCP")
        row["brightdata_discovered_at"] = now
        row["brightdata_days_back_requested"] = int(days_back or 30)
        clean.append(row)

    return clean


async def import_brightdata_deals(
    db,
    days_back: int = 30,
) -> dict[str, Any]:
    """Discover Bright Data leads and merge them into InvestorFlip.

    The actual database write remains delegated to the existing MCP importer,
    which preserves the current normalization/deduplication behavior.
    """
    _require_token()
    from importers.brightdata_mcp_scraper import import_leads_to_db

    leads = await fetch_all_pre_foreclosure_leads(days_back=days_back)
    if not leads:
        return {
            "imported": 0,
            "updated": 0,
            "skipped": 0,
            "errors": 0,
            "total": 0,
            "source": "brightdata_mcp",
            "status": "no_leads_found",
            "days_back": days_back,
        }

    result = await import_leads_to_db(db, leads)
    if not isinstance(result, dict):
        result = {"imported": 0, "result": result}

    return {
        **result,
        "total": len(leads),
        "source": "brightdata_mcp",
        "status": "success",
        "days_back": days_back,
    }
