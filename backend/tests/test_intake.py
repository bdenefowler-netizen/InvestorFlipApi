from intake import infer_listing_type, normalize_import_row, property_link_seed


def test_spreadsheet_aliases_create_a_stable_visible_house():
    row = normalize_import_row(
        {
            "Property Address": "123 Main Street",
            "Property City": "Fort Worth",
            "ZIP Code": "76102",
            "List Price": "$175,000",
            "Bedrooms": 3,
            "Bathrooms": 2,
            "Tags": "motivated seller, as is",
        },
        "User upload: leads.xlsx",
        2,
    )
    assert row is not None
    assert row["situs_address"] == "123 Main Street, Fort Worth, TX 76102"
    assert row["price"] == 175000
    assert row["listing_type"] == "As-Is"
    assert row["motivation_score"] == 70
    assert row["is_live_listing"] is True


def test_spreadsheet_rejects_blank_address_instead_of_creating_blank_card():
    assert normalize_import_row({"Owner": "Somebody", "Price": 1}, "upload.csv", 9) is None


def test_motivated_keywords_are_classified():
    assert infer_listing_type("bank owned REO")["listing_type"] == "REO"
    assert infer_listing_type("Investor special wholesale")["wholesale"] is True
    assert infer_listing_type("tax lein")["motivation_score"] == 70


def test_zillow_and_realtor_links_extract_address_without_fetching_web_page():
    zillow = property_link_seed(
        "https://www.zillow.com/homedetails/123-Main-St-Fort-Worth-TX-76102/123456_zpid/"
    )
    assert zillow["zpid"] == "123456"
    assert zillow["address"] == "123 Main St, Fort Worth, TX 76102"

    realtor = property_link_seed(
        "https://www.realtor.com/realestateandhomes-detail/456-Oak-Ave_Fort-Worth_TX_76104_M12345"
    )
    assert realtor["address"] == "456 Oak Ave, Fort Worth, TX 76104"


def test_property_link_rejects_arbitrary_hosts():
    try:
        property_link_seed("https://example.com/property/123-main-st")
    except ValueError as exc:
        assert "Zillow" in str(exc)
    else:
        raise AssertionError("arbitrary host should be rejected")
