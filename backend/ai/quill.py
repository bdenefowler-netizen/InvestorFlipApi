from .models import QuillAnalyzeRequest, QuillAnalyzeResponse
from .calculations import calculate_max_offer, decide_buy_pass_negotiate
from .offer_letters import generate_offer_letter


def analyze_property_with_quill(body: QuillAnalyzeRequest) -> QuillAnalyzeResponse:
    arv = body.arv_estimate or 0
    repairs = body.repair_estimate or 0
    price = body.listing_price or 0

    max_offer = calculate_max_offer(arv, repairs)
    decision = decide_buy_pass_negotiate(price, max_offer)

    risk_flags = []

    if not body.mortgage_estimate:
        risk_flags.append("Mortgage balance is unknown.")

    if not body.permits:
        risk_flags.append("Permit history needs review.")

    if not body.comps:
        risk_flags.append("Comparable sales need verification.")

    if repairs > 50000:
        risk_flags.append("High repair estimate may reduce flip margin.")

    questions = [
        "Are there any known foundation, roof, plumbing, or electrical issues?",
        "Are there existing liens, code violations, or unpaid taxes?",
        "Has the seller received any other cash or as-is offers?",
        "Is the property vacant or occupied?",
        "Are there permits available for prior renovations?",
        "What is the seller's ideal closing timeline?",
    ]

    return QuillAnalyzeResponse(
        decision=decision,
        max_offer=max_offer,
        arv_explanation=f"ARV estimate used: ${arv:,.0f}. Verify this against nearby sold comps.",
        repair_estimate=f"Repair estimate used: ${repairs:,.0f}. Confirm with walkthrough/photos.",
        risk_flags=risk_flags,
        offer_letter=generate_offer_letter(body.address, max_offer),
        questions_to_ask_agent=questions,
    )
