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

        # 🐾 Chef Deal Sniffer Score
    deal_sniffer_score = 0
    if max_offer and price:
        ratio = price / max_offer
        if decision == "BUY":
            deal_sniffer_score = min(100, int(80 + (1 - ratio) * 50))
        elif decision == "NEGOTIATE":
            deal_sniffer_score = min(100, int(50 + (1 - ratio) * 30))
        else:
            deal_sniffer_score = max(0, int(30 - ratio * 20))
    
    chef_verdicts = {
        "BUY": "I've sniffed this one from corner to corner. Solid ARV, great equity potential. This deal smells like victory! 🏆🐾",
        "NEGOTIATE": "Hmm... interesting scent. Could be a good deal with some negotiation. Let me sniff around a bit more. 👃🤔",
        "PASS": "My nose says pass on this one. Something doesn't smell right. Trust the sniffer! 🚫🐾",
    }
    
    # 🐾 Build the math breakdown
    # 70% rule:  max_offer = (ARV * 0.70) - repairs
    arv_x_70 = arv * 0.70
    max_offer_70 = arv_x_70 - repairs
    # 75% rule:  max_offer = (ARV * 0.75) - repairs
    arv_x_75 = arv * 0.75
    max_offer_75 = arv_x_75 - repairs

    # Build plain-English reason for the decision
    if decision == "BUY":
        if price and arv:
            spread = arv - price
            spread_pct = (spread / arv) * 100
            deal_reason = (
                f"This one's a BUY! 🏆 The asking price (${price:,.0f}) is {spread_pct:.0f}% below "
                f"the ARV (${arv:,.0f}). My max offer is ${max_offer:,.0f} using the 70% rule "
                f"(ARV × 0.70 - repairs = ${arv_x_70:,.0f} - ${repairs:,.0f}). "
                f"Estimated profit after repairs: ${(spread - repairs):,.0f}."
            )
        else:
            deal_reason = "This deal looks like a BUY based on the numbers I have."
    elif decision == "NEGOTIATE":
        if price and arv:
            spread = arv - price
            deal_reason = (
                f"Room to negotiate. Asking ${price:,.0f} vs ARV ${arv:,.0f} "
                f"(${spread:,.0f} spread). My max offer is ${max_offer:,.0f} "
                f"(70% rule: ${arv_x_70:,.0f} - ${repairs:,.0f} repairs). "
                f"Offer ~${(max_offer - 10000):,.0f} to leave room for counters."
            )
        else:
            deal_reason = "There's room to negotiate but I need more data to nail down the offer."
    else:  # PASS
        if price and arv:
            spread = arv - price
            spread_pct = (spread / arv) * 100
            deal_reason = (
                f"PASS. The asking price (${price:,.0f}) is too close to or above the ARV (${arv:,.0f}) — "
                f"only {spread_pct:.0f}% spread. With ${repairs:,.0f} repairs, "
                f"the max offer drops to ${max_offer:,.0f}. Walk away or wait for a price drop."
            )
        else:
            deal_reason = "PASS. The numbers don't work — too little spread to make a profit after repairs."

    math_breakdown = {
        "arv": arv,
        "arv_x_70_pct": round(arv_x_70, 2),
        "repairs": repairs,
        "max_offer_70_rule": round(max_offer_70, 2),
        "max_offer_75_rule": round(max_offer_75, 2),
        "max_offer_used": max_offer,
        "rule_used": "70%",
        "asking_price": price,
        "spread": (arv - price) if (arv and price) else None,
        "spread_pct": round(((arv - price) / arv) * 100, 1) if (arv and price and arv > 0) else None,
    }

    return QuillAnalyzeResponse(
        analyst="Quill AI 🐾",
        deal_sniffer_score=deal_sniffer_score,
        chef_verdict=chef_verdicts.get(decision, "Sniff sniff... 🐕"),
        decision=decision,
        max_offer=max_offer,
        arv_explanation=arv_explanation,
        repair_estimate=repairs,
        risk_flags=risk_flags,
        offer_letter=generate_offer_letter(body.address, max_offer),
        questions_to_ask_agent=questions,
        math_breakdown=math_breakdown,
        deal_reason=deal_reason,
    )
