import asyncio

from importers import foreclosure_finder


def test_bundled_foreclosure_fixture_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("TARRANT_FORECLOSURE_CSV", raising=False)
    assert foreclosure_finder.load_foreclosures_from_csv() == []


def test_bundled_foreclosure_fixture_cannot_be_enabled_as_production(monkeypatch):
    monkeypatch.setenv("TARRANT_FORECLOSURE_CSV", str(foreclosure_finder.FORECLOSURE_CSV))
    assert foreclosure_finder.load_foreclosures_from_csv() == []


def test_import_reports_that_a_verified_file_is_required(monkeypatch):
    monkeypatch.delenv("TARRANT_FORECLOSURE_CSV", raising=False)
    result = asyncio.run(foreclosure_finder.import_foreclosures(object()))
    assert result["skipped"] is True
    assert "verified" in result["reason"].lower()
