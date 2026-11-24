"""Foreclosure Finder response normalization and Fort Worth scoping."""

from datetime import date, timedelta

from importers.feeds import _first_dict_list, _foreclosure_finder_listing
from importers.foreclosure_finder import _is_upcoming_foreclosure


def test_foreclosure_finder_parses_nested_listing_response():
    payload = {
        "data": {
            "listings": [
                {
                    "listingId": "auction-123",
                    "address": "1200 Main St, Fort Worth, TX 76102, Tarrant County",
                    "openingBid": "$125,000",
                    "bedrooms": 3,
                    "bathrooms": 2.5,
                    "squareFootage": "1,640",
                    "yearBuilt": 1965,
                    "auctionDate": "2026-08-04",
                    "assetType": "FORECLOSURE",
                    "photoUrl": "https://example.com/home.jpg",
                    "source": "Auction.com",
                }
            ]
        }
    }

    items = _first_dict_list(payload)
    assert len(items) == 1

    listing = _foreclosure_finder_listing(items[0])
    assert listing is not None
    assert listing.city == "Fort Worth"
    assert listing.state == "TX"
    assert listing.zip == "76102"
    assert listing.price == 125000
    assert listing.baths == 2.5
    assert listing.listing_type == "Foreclosure"
    assert listing.extra["source_endpoint"] == "/zipcode/auction"


def test_foreclosure_finder_rejects_nearby_non_fort_worth_results():
    listing = _foreclosure_finder_listing({
        "id": "arlington-1",
        "address": "100 Center St, Arlington, TX 76010, Tarrant County",
        "openingBid": 90000,
    })
    assert listing is None


def test_foreclosure_finder_accepts_nested_address_and_recognizes_reo():
    listing = _foreclosure_finder_listing({
        "id": "reo-1",
        "address": {
            "streetAddress": "500 W 7th St",
            "city": "Fort Worth",
            "state": "TX",
            "postalCode": "76102",
        },
        "price": 210000,
        "asset_type": "BANK_OWNED",
        "seller": {"name": "Example Bank"},
        "primaryPhoto": {"href": "https://example.com/reo.jpg"},
    })

    assert listing is not None
    assert listing.listing_type == "REO"
    assert listing.owner_name == "Example Bank"
    assert listing.image_url == "https://example.com/reo.jpg"


def test_only_foreclosures_at_least_one_month_out_are_kept():
    future = (date.today() + timedelta(days=60)).strftime("%m/%d/%Y")
    soon = (date.today() + timedelta(days=7)).strftime("%m/%d/%Y")
    past = (date.today() - timedelta(days=1)).strftime("%m/%d/%Y")

    assert _is_upcoming_foreclosure({"sale_date": future})
    assert not _is_upcoming_foreclosure({"sale_date": soon})
    assert not _is_upcoming_foreclosure({"sale_date": past})
    assert not _is_upcoming_foreclosure({"sale_date": ""})
