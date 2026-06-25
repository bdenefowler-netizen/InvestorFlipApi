"""TarrantREI backend regression tests."""
import os
import pytest
import requests

BASE_URL = os.environ.get("EXPO_PUBLIC_BACKEND_URL", "https://cash-house-finder.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api"


@pytest.fixture(scope="session")
def s():
    sess = requests.Session()
    sess.headers.update({"Content-Type": "application/json"})
    return sess


# Health
def test_root(s):
    r = s.get(f"{API}/")
    assert r.status_code == 200
    assert r.json().get("status") == "ok"


# Filters
def test_filters_seventeen(s):
    r = s.get(f"{API}/filters")
    assert r.status_code == 200
    filters = r.json()["filters"]
    assert len(filters) == 17
    keys = [f["key"] for f in filters]
    for k in ["all", "reo", "law_firm", "out_of_state", "bank_owned", "trust", "high_equity"]:
        assert k in keys
    # all should have count 36
    assert next(f for f in filters if f["key"] == "all")["count"] == 36


# Properties listing
def test_properties_all(s):
    r = s.get(f"{API}/properties", params={"filter": "all"})
    assert r.status_code == 200
    data = r.json()
    assert data["count"] == 36
    p = data["items"][0]
    for field in [
        "id", "situs_address", "owner_name", "owner_type",
        "investment_score", "wholesale_score", "flip_score", "rental_score", "risk_score",
        "equity_estimate", "price", "market_value", "listing_type",
        "out_of_state_owner", "high_equity", "image_url",
    ]:
        assert field in p, f"missing {field}"


def test_filter_reo_only(s):
    r = s.get(f"{API}/properties", params={"filter": "reo"})
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) > 0
    assert all(p["listing_type"] == "REO" for p in items)


def test_filter_law_firm(s):
    r = s.get(f"{API}/properties", params={"filter": "law_firm"})
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) > 0
    assert all(p["owner_type"] in ("Law Firm", "Attorney") for p in items)


def test_filter_out_of_state(s):
    r = s.get(f"{API}/properties", params={"filter": "out_of_state"})
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) > 0
    assert all(p["out_of_state_owner"] is True for p in items)


def test_search_fort_worth(s):
    r = s.get(f"{API}/properties", params={"search": "fort worth"})
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) > 0
    # search matches address OR city OR zip OR owner_name
    assert all(
        "Fort Worth" in p["situs_address"]
        or "Fort Worth" in p["city"]
        or "Fort Worth" in p["owner_name"]
        for p in items
    )


# Property detail
def test_property_detail_and_nearby(s):
    items = s.get(f"{API}/properties").json()["items"]
    pid = items[0]["id"]
    r = s.get(f"{API}/properties/{pid}")
    assert r.status_code == 200
    assert r.json()["id"] == pid

    r2 = s.get(f"{API}/properties/{pid}/nearby")
    assert r2.status_code == 200
    j = r2.json()
    assert "nearby_foreclosures" in j and "nearby_investor_purchases" in j


def test_property_not_found(s):
    r = s.get(f"{API}/properties/nonexistent-id-xyz")
    assert r.status_code == 404


# AI analysis
def test_ai_analysis(s):
    items = s.get(f"{API}/properties").json()["items"]
    pid = items[0]["id"]
    r = s.post(f"{API}/properties/{pid}/ai-analysis", timeout=60)
    assert r.status_code == 200
    body = r.json()
    assert body["property_id"] == pid
    assert body["narrative"] and len(body["narrative"]) > 20


# Saved
def test_saved_flow(s):
    items = s.get(f"{API}/properties").json()["items"]
    pid = items[0]["id"]
    # add
    r = s.post(f"{API}/saved", json={"property_id": pid})
    assert r.status_code == 200
    # list
    r2 = s.get(f"{API}/saved")
    assert r2.status_code == 200
    assert any(p["id"] == pid for p in r2.json()["items"])
    # delete
    r3 = s.delete(f"{API}/saved/{pid}")
    assert r3.status_code == 200
    r4 = s.get(f"{API}/saved")
    assert not any(p["id"] == pid for p in r4.json()["items"])


def test_saved_invalid_property(s):
    r = s.post(f"{API}/saved", json={"property_id": "does-not-exist"})
    assert r.status_code == 404


# Owner classification
@pytest.mark.parametrize("name,expected", [
    ("BlueStone Holdings LLC", "LLC"),
    ("Thompson Knight Attorneys PLLC", "Law Firm"),
    ("Wells Fargo Bank NA", "Bank"),
    ("The Henderson Family Trust", "Trust"),
    ("City of Fort Worth", "Government"),
    ("Habitat for Humanity of North Texas", "Nonprofit"),
    ("Republic Title of Texas Inc.", "Corporation"),
    ("John Henderson", "Individual"),
])
def test_classify(s, name, expected):
    r = s.get(f"{API}/owners/classify", params={"name": name})
    assert r.status_code == 200
    assert r.json()["type"] == expected
