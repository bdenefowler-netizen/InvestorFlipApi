from importers.zillow_enricher import _is_address_match, parse_redfin, parse_zillow


def test_zillow_does_not_call_the_largest_dollar_amount_a_zestimate():
    parsed = parse_zillow(
        "$250,000 - 123 Main St",
        "Sold for $300,000. Annual tax amount: $4,500. 3 beds 2 baths.",
    )

    assert parsed["zillow_estimate"] is None


def test_explicit_estimates_are_mapped():
    assert parse_zillow("123 Main St", "Zestimate: $271,947")["zillow_estimate"] == 271947
    assert parse_redfin("123 Main St", "Redfin Estimate: $265,000")["redfin_estimate"] == 265000


def test_search_result_must_match_house_number_and_street():
    assert _is_address_match(
        "3915 Meadowbrook Dr",
        "3915 Meadowbrook Drive, Fort Worth, TX | Zillow",
    )
    assert not _is_address_match(
        "3915 Meadowbrook Dr",
        "4400 Meadowbrook Drive, Fort Worth, TX | Zillow",
    )
    assert not _is_address_match(
        "3915 Meadowbrook Dr",
        "3915 Oak Street, Fort Worth, TX | Zillow",
    )
