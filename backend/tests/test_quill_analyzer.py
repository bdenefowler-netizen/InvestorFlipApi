import asyncio

from importers import quill_analyzer


def test_build_analysis_uses_screening_value_consistently():
    result = quill_analyzer.build_analysis({
        "situs_address": "100 Main St",
        "price": 100_000,
        "tax_roll_market_value": 200_000,
        "live_zillow_value": 210_000,
        "live_realtor_value": 220_000,
        "live_redfin_value": 230_000,
        "sqft": 1_000,
        "year_built": 2000,
    })

    assert result["numbers"]["validated_arv"] == 215_000
    assert result["numbers"]["spread"] == 115_000
    assert result["pnl"]["arv"] == 215_000
    assert "screening" in quill_analyzer.quill_take(result).lower()


def test_live_values_are_collected_before_all_deal_math(monkeypatch):
    async def fake_cross_check(_property):
        return {
            "status": "ok",
            "zestimate": 210_000,
            "cotality": 220_000,
            "redfin_value": 230_000,
            "comps": [],
        }

    monkeypatch.setattr(quill_analyzer, "HAS_BRIGHTDATA", True)
    monkeypatch.setattr(quill_analyzer, "_bd_cross_check", fake_cross_check)
    result = asyncio.run(quill_analyzer.analyze_property({
        "situs_address": "100 Main St",
        "price": 100_000,
        "tax_roll_market_value": 200_000,
        "sqft": 1_000,
        "year_built": 2000,
    }, check_flood=False))

    assert result["value_check"]["sources"]["live_zillow"] == 210_000
    assert result["numbers"]["validated_arv"] == result["pnl"]["arv"] == 215_000
    assert result["analysis_basis"] == "screening"


def test_missing_brightdata_configuration_is_reported_as_skipped(monkeypatch):
    monkeypatch.setattr(quill_analyzer, "HAS_BRIGHTDATA", False)
    result = asyncio.run(quill_analyzer.analyze_property({
        "situs_address": "100 Main St",
        "price": 100_000,
        "market_value": 200_000,
        "sqft": 1_000,
    }, check_flood=False))
    assert result["live_zillow"]["status"] == "skipped"
