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
