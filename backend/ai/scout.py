from .models import QuillAnalyzeRequest, QuillAnalyzeResponse
from .quill import analyze_property_with_quill


def build_quill_request_from_property(p: dict) -> QuillAnalyzeRequest:
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
            f"Listing type: {p.get('listing_type')}. "
            f"Equity estimate: {p.get('equity_estimate')}. "
            f"Investment score: {p.get('investment_score')}. "
            f"Owner type: {p.get('owner_type')}."
        ),
    )


def scout_analyze_property(p: dict) -> QuillAnalyzeResponse:
    request = build_quill_request_from_property(p)
    return analyze_property_with_quill(request)