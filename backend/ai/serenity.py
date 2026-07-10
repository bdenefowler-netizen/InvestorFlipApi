"""Serenity - Protector of Your Deals.

Serenity is the InvestorFlip V2 data guardian.
She protects your time and money by finding, filtering, and enriching deals
before Quill performs investment analysis.

Dedicated to Serenity:
The good girl who frequented the Naughty List, but was never actually on it.
"""

from .models import QuillAnalyzeRequest, QuillAnalyzeResponse
from .openweb_ninja import enrich_property, openweb_ninja_status
from .quill import analyze_property_with_quill


SERENITY_DEDICATION = (
    "Serenity — Protector of Your Deals. "
    "The good girl who frequented the Naughty List, but was never actually on it."
)


def build_quill_request_from_property(p: dict) -> QuillAnalyzeRequest:
    """Build the analysis request that Serenity hands to Quill."""
    return QuillAnalyzeRequest(
        address=p.get("situs_address", "Unknown address"),
        owner_info=f"{p.get('owner_name', '')} ({p.get('owner_type', '')})",
        listing_price=p.get("price"),
        beds=p.get("beds"),
        baths=p.get("baths"),
        sqft=p.get("sqft"),
        arv_estimate=p.get("market_value"),
        repair_estimate=None,
        rent_estimate=None,
        mortgage_estimate=None,
        photos=[p.get("image_url")] if p.get("image_url") else [],
        tax_info="Delinquent" if p.get("tax_delinquent") else "Current",
        permits="Unknown",
        comps="Needs verification",
        notes=(
            "Serenity protected this search and found a possible deal for Quill. "
            f"Listing type: {p.get('listing_type')}. "
            f"Equity estimate: {p.get('equity_estimate')}. "
            f"Investment score: {p.get('investment_score')}. "
            f"Owner type: {p.get('owner_type')}. "
            f"Data sources: {p.get('serenity_sources', [])}."
        ),
    )


def serenity_status() -> dict:
    """Return Serenity and provider status without exposing API secrets."""
    return {
        "name": "Serenity",
        "role": "Protector of Your Deals",
        "dedication": SERENITY_DEDICATION,
        "openweb_ninja": openweb_ninja_status(),
    }


def serenity_analyze_property(p: dict) -> QuillAnalyzeResponse:
    """Serenity enriches and prepares the deal; Quill analyzes it."""
    protected_property = enrich_property(p)
    request = build_quill_request_from_property(protected_property)
    return analyze_property_with_quill(request)
