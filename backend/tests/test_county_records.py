from importers.county_records import (
    choose_county_candidate,
    completeness,
    county_candidate_matches,
    county_record_from_tad,
    county_record_from_tax_roll,
    county_record_id,
    normalize_account_id,
)
from export_safety import spreadsheet_safe


def test_account_ids_merge_even_when_one_source_has_leading_zeroes():
    assert normalize_account_id("000-07209703") == "7209703"
    assert county_record_id("07209703", "2401 Kelton St") == county_record_id(
        "00007209703", "2401 KELTON STREET"
    )


def test_tad_record_keeps_raw_fields_and_rejects_blank_addresses():
    raw = {
        "TAXPIN": "39545-22-14",
        "ACCOUNT": "07209703",
        "OWNER_NAME": "MEANS, JASMYN",
        "OWNER_ADDR": "2401 KELTON ST",
        "OWNER_CITY": "FORT WORTH, TX",
        "OWNER_ZIP": "76133",
        "SITUS_ADDR": "2401 KELTON ST",
        "CITY": "FORT WORTH",
        "STATE": "TX",
        "YEAR_BUILT": 2000,
        "LIVING_ARE": 1816,
        "APPRAISEDV": 347891,
        "CUSTOM_SOURCE_FIELD": "preserved",
    }
    record = county_record_from_tad(raw)
    assert record is not None
    assert record["situs_address"].startswith("2401 KELTON ST")
    assert record["year_built"] == 2000
    assert record["tad_raw"]["CUSTOM_SOURCE_FIELD"] == "preserved"
    assert county_record_from_tad({"ACCOUNT": "1", "SITUS_ADDR": ""}) is None


def test_tax_roll_record_preserves_debt_and_full_raw_record():
    raw = {
        "account_id": "00007209703",
        "street_number": "2401",
        "street_name": "KELTON ST",
        "owner_name_1": "MEANS",
        "owner_name_2": "JASMYN",
        "owner_address_1": "PO BOX 10",
        "owner_city": "FORT WORTH",
        "owner_state": "TX",
        "owner_zip": "76133",
        "land_value": 30000,
        "improvement_value": 317891,
        "current_amount_due": 100,
        "prior_amount_due": 250,
        "legal_description": "LOT 14",
    }
    record = county_record_from_tax_roll(raw, "Tarrant County Tax Roll (test.zip)")
    assert record is not None
    assert record["tax_delinquent"] is True
    assert record["tax_roll_market_value"] == 347891
    assert record["prior_tax_amount_due"] == 250
    assert record["tax_roll_raw"]["legal_description"] == "LOT 14"


def test_completeness_reports_missing_fields_instead_of_blank_cells():
    result = completeness({"situs_address": "100 Main", "account_id": "10"})
    assert 0 < result["completeness_score"] < 100
    assert "owner_name" in result["missing_fields"]


def test_county_match_prefers_exact_account_and_rejects_a_different_account():
    prop = {"account_id": "00007209703", "situs_address": "2401 Kelton St"}
    assert county_candidate_matches(prop, {
        "account_id": "7209703",
        "situs_address": "A completely different display address",
    })
    assert not county_candidate_matches(prop, {
        "account_id": "7209704",
        "situs_address": "2401 Kelton St",
    })


def test_county_address_match_requires_zip_or_city_and_rejects_ambiguity():
    prop = {"situs_address": "100 Main Street", "city": "Fort Worth", "zip": "76102"}
    correct = {"situs_address": "100 MAIN ST", "city": "FORT WORTH", "zip": "76102"}
    wrong_zip = {"situs_address": "100 MAIN ST", "city": "FORT WORTH", "zip": "76010"}
    assert county_candidate_matches(prop, correct)
    assert not county_candidate_matches(prop, wrong_zip)
    assert choose_county_candidate(prop, [correct, dict(correct)]) is None


def test_county_address_without_zip_requires_matching_city():
    prop = {"situs_address": "100 Main Street", "city": "Fort Worth"}
    assert county_candidate_matches(prop, {
        "situs_address": "100 MAIN ST", "city": "FORT WORTH",
    })
    assert not county_candidate_matches(prop, {
        "situs_address": "100 MAIN ST", "city": "ARLINGTON",
    })


def test_county_address_can_use_city_when_tad_zip_is_missing():
    prop = {"situs_address": "100 Main Street", "city": "Fort Worth", "zip": "76102"}
    assert county_candidate_matches(prop, {
        "situs_address": "100 MAIN ST", "city": "FORT WORTH", "zip": "",
    })


def test_csv_export_neutralizes_spreadsheet_formulas():
    assert spreadsheet_safe("=HYPERLINK(\"bad\")").startswith("'=")
    assert spreadsheet_safe("  +1+1").startswith("'  +")
    assert spreadsheet_safe("@SUM(A1:A2)").startswith("'@")
    assert spreadsheet_safe("Normal owner") == "Normal owner"
    assert spreadsheet_safe(123) == 123
