from datetime import date
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from backend.importers.feeds import (
    FeedListing,
    _is_current_foreclosure_listing,
    _parse_date,
)


def _listing(**extra):
    return FeedListing(
        feed_source="Test",
        listing_type="Foreclosure",
        situs_address="123 Main St, Fort Worth, TX 76102",
        city="Fort Worth",
        state="TX",
        zip="76102",
        extra=extra,
    )


def test_parse_common_sale_date_formats():
    assert _parse_date("2026-09-01") == date(2026, 9, 1)
    assert _parse_date("09/01/2026") == date(2026, 9, 1)
    assert _parse_date("September 1, 2026") == date(2026, 9, 1)


def test_current_foreclosure_requires_nonexpired_sale_date():
    today = date(2026, 8, 9)

    assert _is_current_foreclosure_listing(_listing(sale_date="2026-09-01", status="For Sale"), today=today)
    assert not _is_current_foreclosure_listing(_listing(sale_date="2026-08-04", status="For Sale"), today=today)


def test_current_foreclosure_rejects_inactive_status():
    today = date(2026, 8, 9)

    assert not _is_current_foreclosure_listing(_listing(sale_date="2026-09-01", status="Withdrawn"), today=today)
    assert not _is_current_foreclosure_listing(_listing(sale_date="2026-09-01", status_label="Sold"), today=today)
