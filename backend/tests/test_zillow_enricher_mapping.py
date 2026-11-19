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


def test_brightdata_token_helpers_accept_every_supported_env_name(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost/db")
    from server import _brightdata_token_configured
    from zillow_enrich_routes import _brightdata_token_set

    names = ("BRIGHT_DATA_API_TOKEN", "BRIGHT_DATA_TOKEN", "BRIGHTDATA_TOKEN")
    for name in names:
        monkeypatch.delenv(name, raising=False)
    assert not _brightdata_token_configured()
    assert not _brightdata_token_set()

    for name in names:
        monkeypatch.setenv(name, "token")
        assert _brightdata_token_configured()
        assert _brightdata_token_set()
        monkeypatch.delenv(name)
