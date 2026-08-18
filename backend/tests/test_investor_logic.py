from investor_logic import (
    classify_opportunity,
    classify_owner,
    compute_scores,
    derive_owner_signals,
    is_synthetic_property,
    merge_live_refresh,
)


def test_owner_signals_reclassify_llc_and_absentee_owner():
    signals = derive_owner_signals(
        "M&C LEGACY LLC",
        "PO BOX 4090, SCOTTSDALE, AZ 85261",
        "6113 Whitman Ave, Fort Worth, TX 76133",
        "TX",
    )
    assert signals["owner_type"] == "LLC"
    assert signals["investor_owned"] is True
    assert signals["out_of_state_owner"] is True
    assert signals["absentee_owner"] is True
    assert signals["cash_buyer"] is False
    assert signals["cash_buyer_status"] == "unverified"


def test_bank_classifier_precedes_corporation_classifier():
    assert classify_owner("FREEDOM MORTGAGE CORPORATION") == "Bank"


def test_wholesale_property_is_target_opportunity():
    result = classify_opportunity({
        "listing_type": "Wholesale",
        "wholesale": True,
    })
    assert result["is_target_opportunity"] is True
    assert "investor_special" in result["opportunity_signal_keys"]


def test_synthetic_sources_are_detected_without_hiding_real_tax_matches():
    assert is_synthetic_property({"data_source": "Tarrant County Tax Roll - seeded sample"})
    assert is_synthetic_property({"data_source": "Demo Seed Data - NOT LIVE"})
    assert is_synthetic_property({"data_source": "Tarrant County Foreclosure Records"})
    assert not is_synthetic_property({
        "data_source": "OpenWeb Ninja + Tarrant County Foreclosure Records",
    })
    assert not is_synthetic_property({"data_source": "RapidAPI listings", "tax_roll_source": "Tarrant County"})


def test_scores_do_not_call_asking_price_equity_or_roi():
    result = compute_scores({
        "price": 250_000,
        "tax_roll_market_value": 300_000,
        "year_built": 1970,
        "owner_type": "LLC",
    })
    assert result["value_spread"] == 50_000
    assert result["discount_to_benchmark_pct"] == 16.7
    assert result["value_benchmark_source"].startswith("Tarrant County")
    assert result["equity_estimate"] is None
    assert result["est_roi_pct"] is None
    assert result["score_confidence"] == "low"
    assert result["rental_score"] is None


def test_live_refresh_keeps_tax_owner_and_discards_unsourced_fake_market_value():
    existing = {
        "id": "p1",
        "created_at": "old",
        "situs_address": "6113 Whitman Ave, Fort Worth, TX 76133",
        "state": "TX",
        "price": 250000,
        "market_value": 250000,
        "owner_name": "M&C LEGACY LLC",
        "owner_mailing_address": "PO BOX 4090, SCOTTSDALE, AZ 85261",
        "tax_roll_market_value": 300000,
        "tax_roll_source": "Tarrant County",
        "annual_taxes": 6000,
        "tax_delinquent": False,
    }
    incoming = {
        "id": "p1",
        "situs_address": existing["situs_address"],
        "state": "TX",
        "price": 255000,
        "market_value": None,
        "market_value_source": None,
        "owner_name": "",
        "owner_mailing_address": "",
        "annual_taxes": 0,
        "tax_delinquent": False,
        "data_provenance": {"listing": "RapidAPI"},
    }

    result = merge_live_refresh(existing, incoming)

    assert result["owner_name"] == "M&C LEGACY LLC"
    assert result["owner_type"] == "LLC"
    assert result["out_of_state_owner"] is True
    assert result["annual_taxes"] == 6000
    assert result["tax_roll_market_value"] == 300000
    assert result["market_value"] is None
    assert result["value_spread"] == 45000
