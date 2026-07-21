from listing_normalization import extract_listing_fields


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
