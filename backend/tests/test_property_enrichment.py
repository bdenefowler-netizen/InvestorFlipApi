"""Normalization tests for the full us-real-estate-data1 property endpoint."""

from property_enrichment import normalize_property_detail


def test_full_property_details_are_normalized_without_raw_payload():
    payload = {
        "meta": {"status": 200},
        "data": {
            "property": {
                "zpid": 26040715,
                "streetAddress": "5541 Cranberry Dr",
                "city": "Fort Worth",
                "state": "TX",
                "zipcode": "76137",
                "bedrooms": 4,
                "bathrooms": 2.5,
                "livingArea": 2775,
                "lotSize": 7710,
                "yearBuilt": 2001,
                "homeType": "SINGLE_FAMILY",
                "homeStatus": "FOR_SALE",
                "price": 425000,
                "zestimate": 440000,
                "rentZestimate": 2800,
                "taxAssessedValue": 350000,
                "latitude": 32.88,
                "longitude": -97.26,
                "parcelId": "123-456",
                "photos": [{"url": "http://example.com/front.jpg"}],
                "attributionInfo": {
                    "mlsId": "NTREIS-123",
                    "mlsName": "NTREIS",
                    "agentName": "Pat Agent",
                    "agentPhoneNumber": "817-555-0100",
                    "brokerName": "Example Realty",
                },
                "priceHistory": [{"date": "2026-07-01", "price": 425000}],
                "taxHistory": [{"time": 2025, "taxPaid": 7200}],
            }
        },
    }

    result = normalize_property_detail(payload)

    assert result["detail_found"] is True
    assert result["zpid"] == 26040715
    assert result["beds"] == 4
    assert result["baths"] == 2.5
    assert result["sqft"] == 2775
    assert result["lot_size_sqft"] == 7710
    assert result["rent_zestimate"] == 2800
    assert result["parcel_id"] == "123-456"
    assert result["mls_id"] == "NTREIS-123"
    assert result["source_mls"] == "NTREIS"
    assert result["photos"] == ["https://example.com/front.jpg"]
    assert result["price_history"][0]["price"] == 425000
    assert result["provider_tax_history"][0]["taxPaid"] == 7200


def test_cakemls_reso_style_listing_is_normalized():
    payload = {
        "data": {
            "listing": {
                "ListPrice": 425000,
                "BedroomsTotal": 4,
                "BathroomsTotalDecimal": 2.5,
                "LivingArea": 2775,
                "LotSizeSquareFeet": 7710,
                "YearBuilt": 2001,
                "PropertyType": "Residential",
                "mlsNumber": "21326679",
                "ListAgentEmail": "agent@example.com",
                "ListAgentURL": "https://www.realtor.com/realestateagents/example",
            }
        }
    }

    result = normalize_property_detail(payload)

    assert result["list_price"] == 425000
    assert result["beds"] == 4
    assert result["baths"] == 2.5
    assert result["sqft"] == 2775
    assert result["lot_size_sqft"] == 7710
    assert result["year_built"] == 2001
    assert result["mls_id"] == "21326679"
    assert result["listing_agent_email"] == "agent@example.com"
    assert result["listing_agent_url"].startswith("https://www.realtor.com/")


def test_empty_detail_response_is_not_treated_as_found():
    assert normalize_property_detail({"data": {}}) == {"detail_found": False}
