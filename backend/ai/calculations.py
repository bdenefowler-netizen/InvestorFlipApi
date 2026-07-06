def calculate_max_offer(arv: float, repairs: float, rule: float = 0.70) -> float:
    if not arv:
        return 0
    return max(0, (arv * rule) - (repairs or 0))


def decide_buy_pass_negotiate(listing_price: float, max_offer: float) -> str:
    if not max_offer or not listing_price:
        return "NEGOTIATE"
    if listing_price <= max_offer:
        return "BUY"
    if listing_price <= max_offer * 1.10:
        return "NEGOTIATE"
    return "PASS"
