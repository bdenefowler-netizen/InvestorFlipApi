from investor_logic import classify_opportunity, is_target_opportunity


def test_plain_retail_and_fsbo_are_not_mislabeled_as_motivated():
    property_record = {
        "listing_type": "For Sale",
        "listing_description": "Move-in ready home with updated kitchen.",
        "raw_source_excerpt": {
            "listing_sub_type": {
                "is_foreclosure": False,
                "is_bankOwned": False,
                "is_FSBO": True,
            }
        },
    }

    result = classify_opportunity(property_record)

    assert result["is_target_opportunity"] is False
    assert result["opportunity_signal_keys"] == []


def test_provider_foreclosure_and_bank_owned_flags_are_structured_signals():
    foreclosure = classify_opportunity({
        "listing_type": "For Sale",
        "raw_source_excerpt": {"listingSubType": {"isForeclosure": True}},
    })
    reo = classify_opportunity({
        "listing_type": "For Sale",
        "raw_source_excerpt": {"listing_sub_type": {"is_bankOwned": True}},
    })

    assert foreclosure["opportunity_signal_keys"] == ["foreclosure"]
    assert reo["opportunity_signal_keys"] == ["reo"]


def test_tax_balance_and_listing_language_can_create_multiple_signals():
    result = classify_opportunity({
        "listing_type": "For Sale",
        "tax_delinquent": True,
        "listing_description": (
            "Motivated seller. Investor special sold as-is; cash offers only. "
            "Property needs major renovation."
        ),
    })

    assert result["is_target_opportunity"] is True
    assert set(result["opportunity_signal_keys"]) == {
        "motivated_seller",
        "distressed",
        "tax_lien",
        "cash_offer",
        "investor_special",
        "as_is",
    }
    assert len(result["opportunity_evidence"]) == 6
    assert is_target_opportunity({"listing_type": "REO"}) is True


def test_fsbo_preforeclosure_and_new_keywords_are_target_signals():
    result = classify_opportunity({
        "listing_type": "For Sale By Owner",
        "pre_foreclosure": True,
        "listing_description": "Contractor special. Needs TLC and full renovation.",
    })

    assert result["is_target_opportunity"] is True
    assert set(result["opportunity_signal_keys"]) >= {"fsbo", "pre_foreclosure", "distressed"}
