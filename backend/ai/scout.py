# backend/app/ai/scout.py

from app.models.quill import QuillAnalyzeRequest, QuillAnalyzeResponse


def analyze_property_with_quill(body: QuillAnalyzeRequest) -> QuillAnalyzeResponse:
    listing_price = body.listing_price or 0
    arv = body.arv_estimate or 0
    repairs = body.repair_estimate or 0

    max_offer = round((arv * 0.70) - repairs, 2) if arv else None

    risk_flags = []

    if not arv:
        risk_flags.append("Missing ARV estimate")

    if not repairs:
        risk_flags.append("Repair estimate needs verification")

    if body.mortgage_estimate and listing_price:
        if body.mortgage_estimate >= listing_price * 0.9:
            risk_flags.append("Seller may have limited equity")

    if body.tax_info:
        if body.tax_info.get("delinquent"):
            risk_flags.append("Tax delinquency detected")

    if body.permits:
        risk_flags.append("Permit history should be reviewed")

    if max_offer and listing_price:
        if listing_price <= max_offer:
            recommendation = "BUY"
        elif listing_price <= max_offer * 1.15:
            recommendation = "NEGOTIATE"
        else:
            recommendation = "PASS"
    else:
        recommendation = "NEGOTIATE"

    offer_letter = f"""
Hi,

I’m interested in the property at {body.address}. Based on the current condition, repair estimate, and comparable values, I would be able to make a cash/as-is offer around ${max_offer:,.0f}.

This would be a simple as-is purchase with no repair requests.

Please let me know if the seller would consider an offer in that range.

Thank you.
""".strip() if max_offer else "Offer amount needs ARV and repair estimate before generating."

    questions = [
        "Are there any known foundation, roof, plumbing, or electrical issues?",
        "Are there any existing offers on the property?",
        "How did the seller arrive at the asking price?",
        "Are there any liens, unpaid taxes, or title issues?",
        "Would the seller consider an as-is cash offer?",
        "Are there inspection reports, seller disclosures, or repair estimates available?",
    ]

    return QuillAnalyzeResponse(
        recommendation=recommendation,
        max_offer=max_offer,
        arv_explanation=f"ARV is estimated at ${arv:,.0f}. Max offer uses the 70% rule: ARV x 70% minus repairs.",
        repair_estimate=repairs,
        risk_flags=risk_flags,
        offer_letter=offer_letter,
        questions_to_ask_agent=questions,
    )
