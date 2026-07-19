"""Tests for Fort Worth scoping and official tax-roll link discovery."""

import pytest

from importers.tax_roll import is_fort_worth_texas_property
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
