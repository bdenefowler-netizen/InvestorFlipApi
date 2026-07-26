from listing_normalization import (
    build_provider_address_query,
    extract_listing_fields,
    hydrate_listing_record,
)


def test_nested_realtor_listing_fields_are_normalized():
    raw = {
        "property_id": "7728551747",
        "href": "https://www.realtor.com/realestateandhomes-detail/example",
        "status": "for_sale",
        "list_price": 425000,
        "primary_photo": {"href": "http://ap.rdcpix.com/front.jpg"},
        "photos": [{"href": "http://ap.rdcpix.com/kitchen.jpg"}],
        "description": {
            "type": "single_family",
            "beds": 4,
            "baths": 2,
            "sqft": 2775,
            "lot_sqft": 7710,
            "year_built": 2001,
        },
        "location": {
            "address": {
                "line": "5541 Cranberry Dr",
                "city": "Fort Worth",
                "state": "Texas",
                "postal_code": "76137",
                "coordinate": {"lat": 32.886428, "lon": -97.264713},
            }
        },
        "source": {
            "name": "NTREIS",
            "listing_id": "21326679",
            "agents": [{"agent_name": "Melissa Clark", "office_name": "Texas Ally"}],
            "disclaimer": {"text": "MLS data"},
        },
    }

    result = extract_listing_fields(raw)

    assert result["address"]["full"] == "5541 Cranberry Dr, Fort Worth, Texas 76137"
    assert result["property_type"] == "single family"
    assert result["beds"] == 4
    assert result["baths"] == 2
    assert result["sqft"] == 2775
    assert result["year_built"] == 2001
    assert result["lot_size_sqft"] == 7710
    assert result["latitude"] == 32.886428
    assert result["longitude"] == -97.264713
    assert result["photos"] == [
        "https://ap.rdcpix.com/front.jpg",
        "https://ap.rdcpix.com/kitchen.jpg",
    ]
    assert result["source"]["name"] == "NTREIS"
    assert result["source"]["listing_id"] == "21326679"
    assert result["zestimate"] is None


def test_legacy_record_is_hydrated_from_raw_realtor_payload():
    record = {
        "id": "example",
        "beds": None,
        "baths": None,
        "sqft": None,
        "year_built": None,
        "image_url": {"href": "https://example.com/front.jpg"},
        "raw_source_excerpt": {
            "href": "https://www.realtor.com/example",
            "photos": [
                {"href": "http://example.com/front.jpg"},
                {"href": "http://example.com/kitchen.jpg"},
            ],
            "description": {
                "type": "single_family",
                "beds": 4,
                "baths": 2,
                "sqft": 2775,
                "lot_sqft": 7710,
                "year_built": 2001,
                "text": "Move-in ready.",
            },
            "location": {
                "address": {
                    "line": "5541 Cranberry Dr",
                    "city": "Fort Worth",
                    "state": "Texas",
                    "postal_code": "76137",
                    "coordinate": {"lat": 32.886428, "lon": -97.264713},
                }
            },
            "source": {
                "name": "NTREIS",
                "listing_id": "21326679",
                "agents": [{"agent_name": "Melissa Clark", "office_name": "Texas Ally"}],
            },
        },
    }

    result = hydrate_listing_record(record)

    assert result["beds"] == 4
    assert result["baths"] == 2
    assert result["sqft"] == 2775
    assert result["year_built"] == 2001
    assert result["lot_size_sqft"] == 7710
    assert result["listing_description"] == "Move-in ready."
    assert result["listing_agent_name"] == "Melissa Clark"
    assert result["broker_name"] == "Texas Ally"
    assert result["source_mls"] == "NTREIS"
    assert len(result["photos"]) == 2


def test_provider_address_query_does_not_duplicate_fort_worth():
    record = {
        "situs_address": "5541 Cranberry Dr, Fort Worth, Texas 76137",
        "city": "Fort Worth",
        "zip": "76137",
        "raw_source_excerpt": {
            "location": {
                "address": {
                    "line": "5541 Cranberry Dr",
                    "city": "Fort Worth",
                    "state": "Texas",
                    "postal_code": "76137",
                }
            }
        },
    }

    assert build_provider_address_query(record) == "5541 Cranberry Dr, Fort Worth, TX 76137"
