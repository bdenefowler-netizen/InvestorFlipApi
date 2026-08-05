"""Tests for Fort Worth scoping and official tax-roll link discovery."""

import asyncio
import zipfile

import pytest

from importers.tax_roll import import_matches, is_fort_worth_texas_property, property_enrichment
from importers.tax_roll_sync import select_latest_tax_roll_url, validate_tax_roll_url


def test_fort_worth_filter_requires_texas():
    assert is_fort_worth_texas_property({"city": "Fort Worth", "state": "TX"})
    assert is_fort_worth_texas_property({"situs_address": "100 Main St, Fort Worth, Texas 76102"})
    assert not is_fort_worth_texas_property({"city": "Fort Worth", "state": "CO"})
    assert not is_fort_worth_texas_property({"city": "Arlington", "state": "TX"})


def test_latest_official_tax_roll_link_is_selected():
    html = """
        <a href="/content/dam/main/tax/tax-rolls/2026/TaxRoll20260710.zip">older</a>
        <a href="/content/dam/main/tax/tax-rolls/2026/TaxRoll20260717.zip">newest</a>
    """
    assert select_latest_tax_roll_url(html).endswith("/TaxRoll20260717.zip")


def test_non_county_or_unversioned_urls_are_rejected():
    with pytest.raises(ValueError):
        validate_tax_roll_url("https://example.com/TaxRoll20260717.zip")
    with pytest.raises(ValueError):
        validate_tax_roll_url("https://www.tarrantcountytx.gov/latest.zip")


def test_tax_enrichment_reclassifies_owner_and_recomputes_screening():
    existing = {
        "id": "p1",
        "situs_address": "6113 Whitman Ave, Fort Worth, TX 76133",
        "state": "TX",
        "price": 250000,
        "owner_type": "Individual",
        "investment_score": 50,
    }
    tax_record = {
        "account_id": "123",
        "owner_name": "M&C LEGACY LLC",
        "owner_mailing_address": "PO BOX 4090, SCOTTSDALE, AZ 85261",
        "market_value": 300000,
        "land_value": 50000,
        "improvement_value": 250000,
        "annual_taxes": 6000,
        "current_amount_due": 0,
        "prior_amount_due": 0,
        "tax_delinquent": False,
        "data_source": "Tarrant County Tax Roll (TaxRoll20260717.zip)",
    }

    result = property_enrichment(tax_record, existing)

    assert result["owner_type"] == "LLC"
    assert result["investor_owned"] is True
    assert result["out_of_state_owner"] is True
    assert result["value_spread"] == 50000
    assert result["equity_estimate"] is None
    assert result["score_confidence"] == "low"


def test_tax_roll_import_window_resumes_without_replaying_rows(tmp_path):
    class EmptyCursor:
        def __aiter__(self):
            async def values():
                if False:
                    yield None
            return values()

    class EmptyProperties:
        def find(self, *_args, **_kwargs):
            return EmptyCursor()

    class FakeDatabase:
        properties = EmptyProperties()

    archive_path = tmp_path / "tax.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("Master.dat", "001100MAIN\n002200OAK \n003300ELM \n")
    layout = {
        "master": {
            "member": "Master.dat",
            "record_size": 10,
            "fields": {
                "account_id": {"start": 0, "end": 3},
                "street_number": {"start": 3, "end": 6},
                "street_name": {"start": 6, "end": 10},
            },
        }
    }

    first = asyncio.run(import_matches(
        FakeDatabase(), archive_path, layout, dry_run=True,
        start_record=0, max_records=1,
    ))
    second = asyncio.run(import_matches(
        FakeDatabase(), archive_path, layout, dry_run=True,
        start_record=first["next_record"], max_records=1,
    ))

    assert first["start_record"] == 0
    assert first["next_record"] == 1
    assert first["snapshot_complete"] is False
    assert second["start_record"] == 1
    assert second["next_record"] == 2
    assert second["snapshot_complete"] is False
