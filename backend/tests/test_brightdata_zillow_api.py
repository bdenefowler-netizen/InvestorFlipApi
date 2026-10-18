import asyncio

import httpx

from importers.brightdata_zillow_api import (
    compact_zillow_record,
    fetch_fort_worth_zillow_listings,
)


def test_compact_zillow_record_keeps_useful_fields_and_bounds_payload():
    raw = {
        "zpid": 29279268,
        "address": {"streetAddress": "5024 Prairie Creek Trl", "city": "Fort Worth", "state": "TX", "zipcode": "76179"},
        "homeStatus": "FOR_SALE",
        "homeType": "SINGLE_FAMILY",
        "price": 277700,
        "zestimate": 273200,
        "rentZestimate": 1871,
        "taxAssessedValue": 248214,
        "parcelId": "06603874",
        "taxHistory": [{"time": 2025, "value": 248214}, {"time": 2020, "taxPaid": 4096.14}],
        "priceHistory": [{"date": "2026-10-12", "event": "Listed for sale", "price": 277700}],
        "photos": [{"mixedSources": {"jpeg": [{"url": "http://small"}, {"url": "https://large"}]}}],
        "nearbyHomes": [{"huge": "discard me"}],
        "url": "https://www.zillow.com/homedetails/example/29279268_zpid/",
    }
    compact = compact_zillow_record(raw)
    assert compact["parcel_id"] == "06603874"
    assert compact["annualTaxAmount"] == 4096.14
    assert compact["photos"] == ["https://large"]
    assert compact["price_history"][0]["price"] == 277700
    assert "nearbyHomes" not in compact


def test_fetch_zillow_listings_runs_async_snapshot(monkeypatch):
    monkeypatch.setenv("BRIGHTDATA_TOKEN", "test-token")
    progress_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal progress_calls
        assert request.headers["authorization"] == "Bearer test-token"
        if request.url.path.endswith("/trigger"):
            assert request.url.params["discover_by"] == "input_filters"
            return httpx.Response(200, json={"snapshot_id": "sd_test"})
        if "/progress/" in request.url.path:
            progress_calls += 1
            return httpx.Response(200, json={"status": "ready" if progress_calls > 1 else "running"})
        if "/snapshot/" in request.url.path:
            return httpx.Response(200, json=[{
                "zpid": 1,
                "address": {"streetAddress": "100 Main St", "city": "Fort Worth", "state": "TX", "zipcode": "76102"},
            }])
        return httpx.Response(404)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await fetch_fort_worth_zillow_listings(
                1, client=client, poll_interval=0, timeout_seconds=5,
            )

    rows = asyncio.run(run())
    assert rows[0]["zpid"] == 1
    assert progress_calls == 2
