from address_suggestions import normalize_address_suggestions


def test_normalizes_nested_property_reach_response_and_limits_to_fort_worth():
    payload = {
        "data": {
            "suggestions": [
                {
                    "type": "address",
                    "title": "5520 Birchman Ave, Fort Worth, TX 76107",
                    "streetAddress": "5520 Birchman Ave",
                    "city": "Fort Worth",
                    "state": "TX",
                    "zip": "76107",
                    "county": "Tarrant",
                    "propertyId": 12345,
                },
                {
                    "type": "address",
                    "title": "5520 Birchman Ave, Dallas, TX 75201",
                    "streetAddress": "5520 Birchman Ave",
                    "city": "Dallas",
                    "state": "TX",
                    "zip": "75201",
                },
            ]
        }
    }

    result = normalize_address_suggestions(payload)

    assert result == [
        {
            "type": "address",
            "title": "5520 Birchman Ave, Fort Worth, TX 76107",
            "street_address": "5520 Birchman Ave",
            "city": "Fort Worth",
            "state": "TX",
            "zip": "76107",
            "county": "Tarrant",
            "property_reach_id": 12345,
            "business_id": None,
        }
    ]


def test_accepts_top_level_suggestions_and_deduplicates():
    item = {
        "title": "100 Main St, Fort Worth, Texas 76102",
        "street_address": "100 Main St",
        "locality": "Fort Worth",
        "region": "Texas",
        "postalCode": "76102",
    }

    result = normalize_address_suggestions({"suggestions": [item, item]})

    assert len(result) == 1
    assert result[0]["street_address"] == "100 Main St"
