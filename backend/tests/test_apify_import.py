import asyncio

from importers.apify_import import (
    import_apify_runs,
    is_allowed_actor_id,
    is_allowed_run,
    normalize_record,
)


def test_retired_apify_cannot_be_reenabled_by_environment(monkeypatch):
    monkeypatch.setenv("APIFY_IMPORT_ALL_RUNS", "true")
    monkeypatch.setenv("APIFY_ALLOWED_ACTOR_IDS", "actor-good")
    monkeypatch.setenv("APIFY_ALLOWED_TASK_IDS", "task-good")

    assert is_allowed_run({"actorId": "actor-good"}) is False
    assert is_allowed_run({"actorTaskId": "task-good"}) is False
    assert is_allowed_actor_id("actor-good") is False


def test_retired_apify_normalizer_is_fail_closed():
    assert normalize_record({
        "address": "100 Main St",
        "city": "Fort Worth",
        "state": "TX",
        "zip": "76102",
        "price": 250000,
    }) is None


def test_retired_apify_import_reports_noop():
    result = asyncio.run(import_apify_runs(object()))

    assert result["ok"] is True
    assert result["skipped"] is True
    assert result["status"] == "RETIRED"
    assert result["records_imported"] == 0
