"""Unit tests for PostgreSQL query and projection behavior."""

from database import _compile_query, _project


def compile_query(query):
    params = []
    sql = _compile_query(query, params)
    return sql, params


def test_empty_query_matches_all():
    assert compile_query({}) == ("TRUE", [])


def test_scalar_and_boolean_use_json_containment():
    sql, params = compile_query({"listing_type": "REO", "is_live_listing": True})
    assert sql == "data @> $1::jsonb AND data @> $2::jsonb"
    assert params == [{"listing_type": "REO"}, {"is_live_listing": True}]


def test_in_and_not_equal_queries():
    sql, params = compile_query({"listing_type": {"$in": ["REO", "Foreclosure"]}, "id": {"$ne": "p1"}})
    assert "data @> $1::jsonb OR data @> $2::jsonb" in sql
    assert "NOT (data @> $3::jsonb)" in sql
    assert params == [
        {"listing_type": "REO"},
        {"listing_type": "Foreclosure"},
        {"id": "p1"},
    ]


def test_nested_search_query_uses_case_insensitive_regex():
    sql, params = compile_query({
        "$and": [
            {"situs_address": {"$regex": "^100 Main", "$options": "i"}},
            {"$or": [{"zip": "76104"}, {"mailing_zip": "76104"}]},
        ]
    })
    assert "data ->> 'situs_address'" in sql
    assert "~* $1" in sql
    assert " OR " in sql
    assert params == ["^100 Main", {"zip": "76104"}, {"mailing_zip": "76104"}]


def test_projection_supports_include_and_exclude_modes():
    document = {"id": "p1", "address": "100 Main", "owner": "A"}
    assert _project(document, {"_id": 0}) == document
    assert _project(document, {"_id": 0, "id": 1}) == {"id": "p1"}
    assert _project(document, {"owner": 0}) == {"id": "p1", "address": "100 Main"}


def test_county_collections_are_queryable():
    from database import COLLECTION_KEYS

    assert COLLECTION_KEYS["county_records"] == "id"
    assert COLLECTION_KEYS["county_sync_log"] == "id"
