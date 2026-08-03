from importers.tax_roll import select_tax_roll_matches


def _candidate(account_id):
    return {
        "id": "listing-1",
        "address": "100 Main St, Fort Worth, TX 76102",
        "property": {"account_id": account_id},
    }


def test_tax_roll_direct_enrichment_requires_same_account():
    row = {"account_id": "00007209703"}
    matches = select_tax_roll_matches(row, [
        _candidate("7209703"),
        _candidate("different-account"),
        _candidate(None),
    ])
    assert len(matches) == 1
    assert matches[0]["property"]["account_id"] == "7209703"


def test_tax_roll_never_uses_street_only_when_account_is_missing():
    assert select_tax_roll_matches({"account_id": ""}, [_candidate(None)]) == []
    assert select_tax_roll_matches(
        {"account_id": "tax-account"}, [_candidate("listing-account")],
    ) == []
