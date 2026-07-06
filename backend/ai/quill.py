from .models import QuillAnalyzeRequest, QuillAnalyzeResponse
from .calculations import calculate_max_offer, decide_buy_pass_negotiate
from .offer_letters import generate_offer_letter


def analyze_property_with_quill(body: QuillAnalyzeRequest) -> QuillAnalyzeResponse:
    arv = body.arv_estimate or 0
    repairs = body.repair_estimate or 0
    price = body.listing_price or 0
    rent = body.rent_estimate or 0

    max_offer = calculate_max_offer(arv, repairs)
    decision = decide_buy_pass_negotiate(price, max_offer)

    risk_flags = []

    if arv <= 0:
        risk_flags.append("Missing ARV estimate.")

    if repairs <= 0:
        risk_flags.append("Repair estimate should be verified.")

    if not body.mortgage_estimate:
        risk_flags.append("Mortgage balance is unknown.")

    if not body.permits:
        risk_flags.append("Permit history needs review.")

    if not body.comps:
        risk_flags.append("Comparable sales need verification.")

    if not rent:
        risk_flags.append("Rental estimate unavailable.")

    if repairs > 50000:
        risk_flags.append("High repair estimate may reduce flip margin.")

    if body.tax_info:
        if "delinquent" in body.tax_info.lower():
            risk_flags.append("Possible tax delinquency.")

    questions = [
        "Are there any known foundation, roof, plumbing, HVAC, or electrical issues?",
        "Are there any liens, code violations, unpaid taxes, or title issues?",
        "Has the seller received any other cash or as-is offers?",
        "Is the property currently vacant or occupied?",
        "Are permits available for previous renovations?",
        "What is the seller's ideal closing timeline?",
    ]

    if arv > 0:
        arv_explanation = (
            f"Estimated ARV: ${arv:,.0f}. "
            "This estimate should be verified using nearby sold comparable properties."
        )
    else:
        arv_explanation = (
            "No ARV estimate was provided. Comparable sales should be reviewed."
        )

    return QuillAnalyzeResponse(
        decision=decision,
        max_offer=max_offer,
        arv_explanation=arv_explanation,
        repair_estimate=repairs,
        risk_flags=risk_flags,
        offer_letter=generate_offer_letter(body.address, max_offer),
        questions_to_ask_agent=questions,
    )
