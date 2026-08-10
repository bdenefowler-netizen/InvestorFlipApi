from datetime import date
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from backend.importers.feeds import (
    FeedListing,
    _fclosure_listings_from_html,
    _is_current_foreclosure_listing,
    _lgbs_listing,
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


def test_current_foreclosure_can_require_october_or_later_sale_date():
    today = date(2026, 8, 9)
    october = date(2026, 10, 1)

    assert _is_current_foreclosure_listing(
        _listing(sale_date="2026-10-06", status="For Sale"),
        today=today,
        sale_date_from=october,
    )
    assert not _is_current_foreclosure_listing(
        _listing(sale_date="2026-09-01", status="For Sale"),
        today=today,
        sale_date_from=october,
    )
    assert not _is_current_foreclosure_listing(
        _listing(status="For Sale"),
        today=today,
        sale_date_from=october,
    )


def test_current_foreclosure_rejects_inactive_status():
    today = date(2026, 8, 9)

    assert not _is_current_foreclosure_listing(_listing(sale_date="2026-09-01", status="Withdrawn"), today=today)
    assert not _is_current_foreclosure_listing(_listing(sale_date="2026-09-01", status_label="Sold"), today=today)


def test_lgbs_listing_maps_tax_sale_fields():
    listing = _lgbs_listing({
        "uid": 1014061572,
        "state": "TX",
        "county": "TARRANT COUNTY",
        "cause_nbr": "096-D39513-23",
        "sale_nbr": 7,
        "sale_date": "2026-10-06T10:00:00",
        "sale_date_only": "2026-10-06",
        "sale_type": "SALE",
        "status": "Scheduled for Auction",
        "account_nbr": "40465160",
        "prop_address_one": "5212 CAROL AVE",
        "prop_city": "FORT WORTH",
        "prop_state": "TX",
        "prop_zipcode": "76105-4561",
        "value": "219510.00",
        "minimum_bid": "4886.88",
    })

    assert listing is not None
    assert listing.feed_source == "LGBS Tax Sales"
    assert listing.parcel_id == "40465160"
    assert listing.situs_address == "5212 CAROL AVE, FORT WORTH, TX 76105"
    assert listing.price == 4886
    assert listing.market_value == 219510
    assert listing.extra["sale_date"] == "2026-10-06"
    assert _is_current_foreclosure_listing(
        listing,
        today=date(2026, 8, 9),
        sale_date_from=date(2026, 10, 1),
    )


def test_fclosure_page_rows_map_and_filter_october_or_later():
    page = (
        '<script type="application/ld+json">{"dateModified":"2026-08-10"}</script>'
        '<script>self.__next_f.push([1,'
        '"10:[\\"$\\",\\"$L9\\",\\"3d0aa6b9\\",{'
        '\\"href\\":\\"/property/2860-stackhouse-st-fort-worth-tx-76244-3d0aa6b9\\",'
        '\\"children\\":[[\\"$\\",\\"div\\",null,{\\"children\\":['
        '[\\"$\\",\\"p\\",null,{\\"children\\":\\"2860 Stackhouse St\\"}],'
        '[\\"$\\",\\"p\\",null,{\\"children\\":\\"Fort Worth, TX, 76244\\"}]]}],'
        '[\\"$\\",\\"span\\",null,{\\"children\\":\\"Oct 6\\"}],'
        '[\\"$\\",\\"span\\",null,{\\"children\\":\\"4 / 4\\"}],'
        '[\\"$\\",\\"span\\",null,{\\"children\\":\\"$$575,000\\"}],'
        '[\\"$\\",\\"span\\",null,{\\"children\\":\\"+$331,800\\"}]]}]\\n"])'
        '</script>'
        '<script>self.__next_f.push([1,'
        '"11:[\\"$\\",\\"$L9\\",\\"9544eb56\\",{'
        '\\"href\\":\\"/property/3532-park-hill-dr-fort-worth-tx-76109-9544eb56\\",'
        '\\"children\\":[[\\"$\\",\\"div\\",null,{\\"children\\":['
        '[\\"$\\",\\"p\\",null,{\\"children\\":\\"3532 Park Hill Dr\\"}],'
        '[\\"$\\",\\"p\\",null,{\\"children\\":\\"Fort Worth, TX, 76109\\"}]]}],'
        '[\\"$\\",\\"span\\",null,{\\"children\\":\\"Sep 1\\"}],'
        '[\\"$\\",\\"span\\",null,{\\"children\\":\\"3 / 3\\"}],'
        '[\\"$\\",\\"span\\",null,{\\"children\\":\\"$$696,000\\"}],'
        '[\\"$\\",\\"span\\",null,{\\"children\\":\\"+$444,500\\"}]]}]\\n"])'
        '</script>'
    )

    listings = _fclosure_listings_from_html(page, sale_date_from=date(2026, 10, 1))

    assert len(listings) == 1
    assert listings[0].feed_source == "Fclosure"
    assert listings[0].situs_address == "2860 Stackhouse St, Fort Worth, TX 76244"
    assert listings[0].market_value == 575000
    assert listings[0].beds == 4
    assert listings[0].baths == 4
    assert listings[0].extra["sale_date"] == "2026-10-06"
    assert listings[0].extra["source_url"].endswith("/property/2860-stackhouse-st-fort-worth-tx-76244-3d0aa6b9")
