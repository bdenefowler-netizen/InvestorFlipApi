def generate_offer_letter(address: str, max_offer: float) -> str:
    return (
        f"Hello,\n\n"
        f"I am interested in the property at {address}. "
        f"Based on the current condition, estimated repairs, and comparable market data, "
        f"I would like to discuss a possible as-is cash offer around ${max_offer:,.0f}. "
        f"This offer is subject to verification of property condition, title, taxes, and access.\n\n"
        f"Thank you."
    )
