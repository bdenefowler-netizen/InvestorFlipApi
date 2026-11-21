import asyncio

from tad_identity import apply_county_identity, has_tad_identity, resolve_tad_identity
from investor_logic import classify_opportunity


def test_has_tad_identity_accepts_account_and_parcel():
    assert has_tad_identity({"account_id": "0012345"}) is True
    assert has_tad_identity({"parcel_id": "12345"}) is True
    assert has_tad_identity({"situs_address": "2401 Kelton St"}) is False


def test_apply_county_identity_copies_authoritative_fields():
    record = {"situs_address": "2401 Kelton St", "import_evidence": [{"source": "lead"}]}
    county = {
        "id": "county-1",
        "account_id": "12345",
        "parcel_id": "0001",
        "legal_description": "LOT 1 BLOCK 2",
        "owner_name": "JOHN DOE",
        "market_value": 200000,
        "sqft": 1500,
    }
    assert apply_county_identity(record, county, "tad_account_exact") is True
    assert record["account_id"] == "12345"
    assert record["legal_description"] == "LOT 1 BLOCK 2"
    assert record["owner_name"] == "JOHN DOE"
    assert record["tad_verified"] is True


def test_uploaded_lead_flags_create_opportunity_signals():
    probate = classify_opportunity({"has_probate": True, "listing_type": "Probate"})
    code = classify_opportunity({"has_uploaded_code_violations": True, "listing_type": "Code Violation"})
    auction = classify_opportunity({"has_uploaded_tax_auction": True, "listing_type": "Tax Auction"})
    assert "distressed" in probate["opportunity_signal_keys"]
    assert "distressed" in code["opportunity_signal_keys"]
    assert "tax_lien" in auction["opportunity_signal_keys"]


def test_resolve_tad_identity_uses_free_lookup_and_saves_county_identity(monkeypatch):
    import tad_identity

    raw = {
        "TAXPIN": "0001-001-0000",
        "ACCOUNT": "0012345",
        "SITUS_ADDR": "2401 KELTON ST",
        "CITY": "FORT WORTH",
        "STATE": "TX",
        "ZIPCODE": "76102",
        "OWNER_NAME": "JOHN DOE",
        "LEGAL_1": "LOT 1 BLOCK 2",
        "TOTAL_VALU": 200000,
    }

    async def fake_search(address):
        assert address == "2401 Kelton St, Fort Worth, TX"
        return [raw]

    async def fake_upsert(db, records):
        return len(records)

    async def fake_fill(db, record):
        record["county_record_id"] = "county-account-12345"
        return "tad_account_exact"

    monkeypatch.setattr(tad_identity, "search_tad_by_address", fake_search)
    monkeypatch.setattr(tad_identity, "upsert_county_records", fake_upsert)
    monkeypatch.setattr("import_matching.fill_county_identity", fake_fill)

    record = {"situs_address": "2401 Kelton St, Fort Worth, TX", "zip": "76102"}
    changed = asyncio.run(resolve_tad_identity(object(), record))
    assert changed is True
    assert record["account_id"] == "0012345"
    assert record["parcel_id"] == "0001-001-0000"
    assert record["tad_resolved_by"] == "tad_address_lookup"


def test_canonical_street_key_drops_city_without_street_comma():
    from address_utils import canonical_street_key

    assert canonical_street_key("5520 Birchman Ave Fort Worth,Tx") == "5520 BIRCHMAN AVE"
    assert canonical_street_key("5520 Birchman Ave, Fort Worth, TX 76111") == "5520 BIRCHMAN AVE"
    assert canonical_street_key("5520 Birchman Ave Apt 2, Fort Worth, TX") == "5520 BIRCHMAN AVE APT 2"


def test_search_tad_by_address_uses_normalized_street_term(monkeypatch):
    import importers.tad_scraper as tad_scraper

    seen = []

    def fake_query(where="1=1", **kwargs):
        seen.append(where)
        if "5520 BIRCHMAN AVE" in where:
            return [{
                "TAXPIN": "6980-87-29",
                "ACCOUNT": "00481688",
                "SITUS_ADDR": "5520 BIRCHMAN AVE",
                "OWNER_NAME": "VAZQUEZ, ARIES",
            }]
        return []

    monkeypatch.setattr(tad_scraper, "_query_tad", fake_query)
    rows = asyncio.run(tad_scraper.search_tad_by_address("5520 Birchman Ave Fort Worth,Tx"))
    assert rows[0]["ACCOUNT"] == "00481688"
    assert "UPPER(SITUS_ADDR) LIKE '%5520 BIRCHMAN AVE%'" in seen


def test_search_tad_by_legal_handles_phase_and_city_noise(monkeypatch):
    import importers.tad_scraper as tad_scraper

    seen = {}

    def fake_query(where="1=1", **kwargs):
        seen["where"] = where
        return [{
            "TAXPIN": "39607-11-15",
            "ACCOUNT": "05904285",
            "LEGAL_1": "SOUTHGATE ADDITION-FT WORTH Block 11 Lot 15",
            "SITUS_ADDR": "8529 CHARLESTON AVE",
        }]

    monkeypatch.setattr(tad_scraper, "_query_tad", fake_query)
    rows = asyncio.run(tad_scraper.search_tad_by_legal(
        "LOT 15 BLOCK 11 SOUTHGATE PHASE I, FORT WORTH"
    ))
    assert rows[0]["ACCOUNT"] == "05904285"
    assert "LOT 15" in seen["where"]
    assert "BLOCK 11" in seen["where"]
    assert "SOUTHGATE" in seen["where"]
    assert "PHASE" not in seen["where"]
