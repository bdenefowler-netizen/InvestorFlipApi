from importers.apify_import import is_allowed_actor_id, is_allowed_run, normalize_record


def test_apify_run_requires_explicit_actor_or_task_allowlist(monkeypatch):
    monkeypatch.delenv("APIFY_IMPORT_ALL_RUNS", raising=False)
    monkeypatch.setenv("APIFY_ALLOWED_ACTOR_IDS", "actor-good")
    monkeypatch.setenv("APIFY_ALLOWED_TASK_IDS", "task-good")
    assert is_allowed_run({"actorId": "actor-good"})
    assert is_allowed_run({"actorTaskId": "task-good"})
    assert not is_allowed_run({"actorId": "unrelated"})


def test_direct_apify_actor_requires_reviewed_or_configured_id(monkeypatch):
    monkeypatch.setenv("APIFY_ALLOWED_ACTOR_IDS", "actor-configured")
    assert is_allowed_actor_id("actor-configured")
    assert is_allowed_actor_id("actor-built-in", {"actor-built-in"})
    assert not is_allowed_actor_id("actor-unknown", {"actor-built-in"})


def test_apify_price_per_sqft_is_not_treated_as_square_footage():
    item = normalize_record({
        "address": "100 Main St",
        "city": "Fort Worth",
        "state": "TX",
        "zip": "76102",
        "pricePerSqft": 175,
    })
    assert item is not None
    assert item["sqft"] is None


def test_apify_normalizes_nested_realtor_address_shape():
    item = normalize_record({
        "list_price": 299900,
        "href": "https://www.realtor.com/example",
        "primary_photo": {"href": "https://example.test/photo.jpg"},
        "location": {
            "county": {"name": "Tarrant"},
            "address": {
                "line": "1700 Weiler Blvd",
                "city": "Fort Worth",
                "state_code": "TX",
                "postal_code": "76112",
                "coordinate": {"lat": 32.754317, "lon": -97.233399},
            },
        },
        "description": {
            "beds": 5,
            "baths": 2,
            "sqft": 2628,
            "lot_sqft": 22041,
            "type": "single_family",
            "year_built": 1952,
        },
        "source": {"listing_id": "21357998"},
    })

    assert item is not None
    assert item["situs_address"] == "1700 Weiler Blvd, Fort Worth, TX 76112"
    assert item["county"] == "Tarrant"
    assert item["price"] == 299900
    assert item["beds"] == 5
    assert item["sqft"] == 2628
    assert item["mls_number"] == "21357998"
    assert item["listing_url"] == "https://www.realtor.com/example"


def test_apify_normalizes_object_address_shape():
    item = normalize_record({
        "address": {
            "streetAddress": "1941 6th Ave",
            "city": "Fort Worth",
            "state": "TX",
            "postalCode": "76110",
        },
        "listPrice": "$250,000",
        "bedrooms": "3",
        "bathrooms": "2",
        "livingArea": "1,710",
        "homeType": "single_family",
    })

    assert item is not None
    assert item["situs_address"] == "1941 6th Ave, Fort Worth, TX 76110"
    assert item["price"] == 250000
    assert item["beds"] == 3.0
    assert item["sqft"] == 1710
