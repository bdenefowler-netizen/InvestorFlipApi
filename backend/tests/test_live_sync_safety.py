import os

os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")

from server import _direct_listing_provider_state, sanitize_property_semantics


def test_feed_success_cannot_authorize_direct_listing_retirement():
    direct_reports = [
        {"provider": "OpenWeb Ninja", "status": "error", "accepted": 0},
        {"provider": "RapidAPI Realtor", "status": "error", "accepted": 0},
    ]
    combined_reports = list(direct_reports)
    combined_reports.append(
        {"provider": "LGBS Tax Sales", "status": "success", "accepted": 27}
    )

    succeeded, capped = _direct_listing_provider_state(
        direct_reports, merged_total=0, returned_count=0, limit=50
    )

    assert any(report["status"] == "success" for report in combined_reports)
    assert succeeded is False
    assert capped is False


def test_legacy_tad_parcel_is_not_presented_as_live_for_sale():
    cleaned = sanitize_property_semantics({
        "data_source": "User upload [tad]: tad__TAD_RESIDENTIAL_BASE.xlsx",
        "is_live_listing": True,
        "listing_type": "For Sale",
        "listing_status": "For Sale",
        "city": "24.0",
        "tax_delinquent": True,
    })

    assert cleaned["is_live_listing"] is False
    assert cleaned["listing_type"] is None
    assert cleaned["listing_status"] is None
    assert cleaned["city"] == ""
    assert cleaned["tax_delinquent"] is True
