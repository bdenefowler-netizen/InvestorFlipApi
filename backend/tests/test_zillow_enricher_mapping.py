import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient

from importers.zillow_enricher import (
    ZillowRedfinEnricher,
    _is_address_match,
    extract_photo_urls,
    parse_redfin,
    parse_zillow,
)


def test_zillow_does_not_call_the_largest_dollar_amount_a_zestimate():
    parsed = parse_zillow(
        "$250,000 - 123 Main St",
        "Sold for $300,000. Annual tax amount: $4,500. 3 beds 2 baths.",
    )

    assert parsed["zillow_estimate"] is None


def test_explicit_estimates_are_mapped():
    assert parse_zillow("123 Main St", "Zestimate: $271,947")["zillow_estimate"] == 271947
    assert parse_redfin("123 Main St", "Redfin Estimate: $265,000")["redfin_estimate"] == 265000


def test_search_result_must_match_house_number_and_street():
    assert _is_address_match(
        "3915 Meadowbrook Dr",
        "3915 Meadowbrook Drive, Fort Worth, TX | Zillow",
    )
    assert not _is_address_match(
        "3915 Meadowbrook Dr",
        "4400 Meadowbrook Drive, Fort Worth, TX | Zillow",
    )
    assert not _is_address_match(
        "3915 Meadowbrook Dr",
        "3915 Oak Street, Fort Worth, TX | Zillow",
    )


def test_brightdata_token_helpers_accept_every_supported_env_name(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost/db")
    from server import _brightdata_token_configured
    from zillow_enrich_routes import _brightdata_token_set

    names = ("BRIGHT_DATA_API_TOKEN", "BRIGHT_DATA_TOKEN", "BRIGHTDATA_TOKEN")
    for name in names:
        monkeypatch.delenv(name, raising=False)
    assert not _brightdata_token_configured()
    assert not _brightdata_token_set()

    for name in names:
        monkeypatch.setenv(name, "token")
        assert _brightdata_token_configured()
        assert _brightdata_token_set()
        monkeypatch.delenv(name)


def test_extract_photo_urls_prefers_zillow_static_images():
    fragment = """
        <meta property="og:image" content="https://photos.zillowstatic.com/fp/primary.jpg">
        <img src="https://photos.zillowstatic.com/fp/second.jpg">
        <img src="https://photos.zillowstatic.com/logo.svg">
        <img src="https://example.com/unrelated.jpg">
    """

    urls = extract_photo_urls(fragment)

    assert urls[0] == "https://photos.zillowstatic.com/fp/primary.jpg"
    assert "https://photos.zillowstatic.com/fp/second.jpg" in urls
    assert not any("logo" in url or "example.com" in url for url in urls)


def test_merge_includes_photos_and_image_url():
    merged = ZillowRedfinEnricher()._merge(
        {"zillow_estimate": 250000, "photos": ["https://photos.zillowstatic.com/fp/one.jpg"]},
        {},
    )

    assert merged["photos"] == ["https://photos.zillowstatic.com/fp/one.jpg"]
    assert merged["image_url"] == "https://photos.zillowstatic.com/fp/one.jpg"
    assert merged["photo_count"] == 1


def test_zillow_dataset_compaction_keeps_multiple_photos_without_duplicates():
    from importers.brightdata_zillow_api import compact_zillow_record

    record = compact_zillow_record({
        "photos": [
            "http://photos.zillowstatic.com/fp/one.jpg",
            "http://photos.zillowstatic.com/fp/one.jpg",
            {"mixedSources": {"jpeg": [
                {"url": "http://photos.zillowstatic.com/fp/two.jpg"},
                {"url": "http://photos.zillowstatic.com/fp/three.jpg"},
            ]}},
        ]
    })

    assert record["photo_count"] == 2
    assert record["source_photo_count"] == 3
    assert "https://photos.zillowstatic.com/fp/two.jpg" not in record["photos"]
    assert "https://photos.zillowstatic.com/fp/three.jpg" in record["photos"]
    assert record["image_url"] == "https://photos.zillowstatic.com/fp/one.jpg"


def test_zillow_dataset_address_lookup_filters_by_street(monkeypatch):
    from importers import brightdata_zillow_api as api

    async def fake_fetch(limit, **kwargs):
        return [
            {"address": {"streetAddress": "8529 CHARLESTON AVE", "city": "Fort Worth"}},
            {"address": {"streetAddress": "123 OTHER ST", "city": "Fort Worth"}},
        ]

    monkeypatch.setattr(api, "fetch_fort_worth_zillow_listings", fake_fetch)
    matches = asyncio.run(api.find_zillow_listing_by_address("8529 Charleston Ave, Fort Worth, TX"))

    assert len(matches) == 1
    assert matches[0]["address"]["streetAddress"] == "8529 CHARLESTON AVE"


def test_county_clerk_unlocker_uses_valid_endpoint_and_env_token(monkeypatch):
    from importers import county_clerk_scraper as clerk

    for name in ("BRIGHT_DATA_API_TOKEN", "BRIGHT_DATA_TOKEN", "BRIGHTDATA_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("BRIGHT_DATA_API_TOKEN", "test-token")

    assert clerk.brightdata_token() == "test-token"
    url = clerk._build_unlocker_url("https://tarrant.tx.publicsearch.us/")
    assert url.startswith("https://api.brightdata.com/gdpr?")
    assert "api_key=test-token" in url


def test_single_property_enrich_does_not_require_admin_key(monkeypatch):
    import zillow_enrich_routes as routes
    from importers.zillow_enricher import ZillowRedfinEnricher

    class FakeDb:
        async def connect(self):
            return None

        async def close(self):
            return None

    async def fake_enrich(self, db, address, city="Fort Worth", state="TX", **kwargs):
        return {
            "ok": True,
            "address": address,
            "dry_run": kwargs.get("write_to_db") is False,
            "include_photos": kwargs.get("include_photos") is True,
        }

    monkeypatch.setattr(routes, "_brightdata_token_set", lambda: True)
    monkeypatch.setattr(routes, "get_db", lambda: FakeDb())
    monkeypatch.setattr(ZillowRedfinEnricher, "enrich", fake_enrich)

    app = FastAPI()
    app.include_router(routes.router)
    response = TestClient(app).post(
        "/api/enrich/property",
        params={
            "address": "8529 CHARLESTON AVE",
            "city": "Fort Worth",
            "state": "TX",
            "dry_run": "true",
            "include_photos": "false",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "address": "8529 CHARLESTON AVE",
        "dry_run": True,
        "include_photos": False,
    }


def test_enrich_health_reports_configuration_without_exposing_secrets(monkeypatch):
    from zillow_enrich_routes import router

    monkeypatch.setenv("DATABASE_URL", "postgresql://user:secret@localhost/db")
    monkeypatch.setenv("INVESTORFLIP_ADMIN_KEY", "admin-secret")
    for name in ("BRIGHT_DATA_API_TOKEN", "BRIGHT_DATA_TOKEN", "BRIGHTDATA_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("BRIGHTDATA_TOKEN", "provider-secret")

    app = FastAPI()
    app.include_router(router)
    response = TestClient(app).get("/api/enrich/test")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["brightdata_token_set"] is True
    assert body["database_configured"] is True
    assert body["admin_key_configured"] is True
    assert body["capabilities"]["zillow_redfin_search"] is True
    assert "provider-secret" not in str(body)
    assert "admin-secret" not in str(body)
