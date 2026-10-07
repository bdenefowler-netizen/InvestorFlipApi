from datetime import date, timedelta

from importers.feeds import _append_source, _is_current_foreclosure_listing, _lgbs_listing


def test_lgbs_tax_sale_maps_to_tax_lien_evidence():
    listing = _lgbs_listing({
        "prop_address_one": "100 Main St",
        "prop_city": "Fort Worth",
        "prop_state": "TX",
        "prop_zipcode": "76102",
        "account_nbr": "000123456",
        "minimum_bid": "$42,000",
        "value": "$180,000",
        "status": "Scheduled",
        "sale_date_only": (date.today() + timedelta(days=14)).isoformat(),
    })

    assert listing is not None
    assert listing.listing_type == "Tax Lien"
    assert listing.extra["tax_delinquent"] is True
    assert listing.price == 42000
    assert listing.market_value == 180000
    assert _is_current_foreclosure_listing(listing) is True


def test_expired_tax_sale_is_not_current():
    listing = _lgbs_listing({
        "prop_address_one": "100 Main St",
        "prop_city": "Fort Worth",
        "prop_state": "TX",
        "prop_zipcode": "76102",
        "status": "Scheduled",
        "sale_date_only": (date.today() - timedelta(days=1)).isoformat(),
    })

    assert listing is not None
    assert _is_current_foreclosure_listing(listing) is False


def test_feed_source_provenance_does_not_repeat_every_sync():
    source = "User upload [tad]: base.xlsx + LGBS Tax Sales"
    assert _append_source(source, "LGBS Tax Sales") == source
