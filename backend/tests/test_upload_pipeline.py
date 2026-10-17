from county_records_routes import _display_staged, _row_identity
from bulk_import import _prepare_row, _upload_categories
from import_worker import _code_records
from intake_v3 import _ident, _merge, normalize_import_row


def test_pre_foreclosure_legal_description_alias_is_canonicalized():
    identity = _ident({
        "Property Address": "123 Main St, Fort Worth, TX 76102",
        "Legal Property Description": "LOT 1 BLK 2 TEST ADDITION",
    })

    assert identity["address_key"]
    assert identity["legal"] == "LOT 1 BLK 2 TEST ADDITION"


def test_probate_decedent_address_is_a_property_identity():
    row = normalize_import_row(
        {
            "Decedent Address": "3704 Clark Ave, Fort Worth, TX 76107",
            "Decedent": "Webb, Guy Frederick",
        },
        "User upload [probate]: probate.xlsx",
        2,
    )

    assert row["situs_address"].startswith("3704 Clark Ave")
    assert row["has_probate"] is True


def test_probate_missing_address_stays_in_case_workspace_not_property_master():
    identity = _ident({"Decedent Address": "(none listed)"})

    assert identity["address"] == ""
    assert identity["address_key"] == ""


def test_code_violation_upload_aggregates_for_county_workspace():
    records = _code_records([
        {
            "Violation_Address": "100 Main St",
            "City": "Fort Worth",
            "State": "TX",
            "Complaint_Type_Description": "Property Maintenance",
            "Violation_Current_Status": "Open",
            "Case_ID": "CASE-1",
            "Violation_ID": "V-1",
        },
        {
            "Violation_Address": "100 Main St",
            "City": "Fort Worth",
            "State": "TX",
            "Complaint_Type_Description": "High Grass and Weeds",
            "Violation_Current_Status": "Closed",
            "Case_ID": "CASE-2",
            "Violation_ID": "V-2",
        },
    ])

    assert len(records) == 1
    assert records[0]["has_code_violations"] is True
    assert records[0]["code_violation_count"] == 2
    assert records[0]["open_code_violation_count"] == 1


def test_probate_staging_row_keeps_addressless_case_visible():
    item = _display_staged({
        "id": "stage-1",
        "status": "processed",
        "source_file": "probate.xlsx",
        "payload": {
            "Case Number": "2026-PR00001-1",
            "Decedent": "Example, Person",
            "Decedent Address": "(none listed)",
        },
    })

    assert item["id"] == "stage-1"
    assert item["raw_import_row"]["Case Number"] == "2026-PR00001-1"
    assert item["situs_address"] == "(none listed)"


def test_workbook_merge_recognizes_violation_and_decedent_addresses():
    assert _row_identity({"Violation_Address": "100 Main St"}).startswith("address:")
    assert _row_identity({"Decedent Address": "200 Oak Ave"}).startswith("address:")


def test_tad_upload_does_not_invent_listing_or_numeric_city():
    row = normalize_import_row(
        {
            "Situs Address": "4711 Anchorage Dr",
            "CITY": 24.0,
            "Account ID": "12345678",
            "Legal Description": "LOT 1 BLOCK 2 TEST ADDITION",
        },
        "User upload [tad]: tad__TAD_RESIDENTIAL_BASE.xlsx",
        2,
    )

    assert row["city"] == ""
    assert row["is_live_listing"] is False
    assert row["listing_type"] is None
    assert row["listing_status"] is None


def test_tad_enrichment_cannot_erase_a_real_listing():
    existing = {
        "id": "listing-1",
        "data_source": "RapidAPI Realtor Search",
        "is_live_listing": True,
        "listing_type": "For Sale",
        "listing_status": "for_sale",
        "price": 250000,
        "city": "Fort Worth",
        "owner_name": "Example Owner",
    }
    incoming = normalize_import_row(
        {
            "Situs Address": "4711 Anchorage Dr",
            "CITY": 24.0,
            "Account ID": "12345678",
            "Assessed Value": 180000,
        },
        "User upload [tad]: tad.xlsx",
        2,
    )

    merged = _merge(existing, incoming, "address_exact", 95)

    assert merged["is_live_listing"] is True
    assert merged["listing_type"] == "For Sale"
    assert merged["listing_status"] == "for_sale"
    assert merged["price"] == 250000
    assert merged["city"] == "Fort Worth"
    assert merged["assessed_value"] == 180000


def test_tax_lien_upload_is_detected_and_matches_by_tad_account():
    raw = {
        "cause_number": "D37745-23",
        "tad_account_number": "862274",
        "status": "For Sale",
    }
    categories = _upload_categories("tax_lien__tarrant.csv", [raw])
    prepared = _prepare_row(raw, categories)
    row = normalize_import_row(prepared, "User upload [tax_lien]: tarrant.csv", 2)

    assert categories == ["tax_lien"]
    assert row["account_id"] == "862274"
    assert row["cause_number"] == "D37745-23"
    assert row["sale_status"] == "For Sale"
    assert row["tax_lien_active"] is True
    assert row["tax_delinquent"] is True
    assert row["listing_type"] == "Tax Lien"


def test_tax_lien_royalty_and_withdrawn_rows_do_not_become_house_leads():
    royalty = normalize_import_row(
        {
            "cause_number": "D40000-24",
            "tad_account_number": "123",
            "status": "For Sale\u00a0Royalty\u00a0\n Interest only",
        },
        "User upload [tax_lien]: tarrant.csv",
        2,
    )
    withdrawn = normalize_import_row(
        {
            "cause_number": "D40001-24",
            "tad_account_number": "456",
            "status": "Withdrawn Prior to DC",
        },
        "User upload [tax_lien]: tarrant.csv",
        3,
    )

    assert royalty["sale_status"] == "For Sale - Royalty Interest Only"
    assert royalty["tax_lien_royalty_interest_only"] is True
    assert royalty["tax_lien_active"] is False
    assert royalty["tax_delinquent"] is False
    assert royalty["listing_type"] is None
    assert withdrawn["tax_lien_withdrawn"] is True
    assert withdrawn["tax_lien_active"] is False
    assert withdrawn["tax_delinquent"] is False
    assert withdrawn["listing_type"] is None
