from admin_auth import requires_admin_key


def test_paid_and_mutating_routes_require_admin_key():
    assert requires_admin_key("/api/live/sync-fort-worth", "POST")
    assert requires_admin_key("/api/intake/upload", "POST")
    assert requires_admin_key("/api/admin/county-records/sync", "POST")
    assert requires_admin_key("/api/properties/abc/enrich", "POST")
    assert requires_admin_key("/api/properties/abc/quill-analysis", "POST")
    assert requires_admin_key("/api/quill/analyze/abc")
    assert requires_admin_key("/api/saved", "POST")
    assert requires_admin_key("/api/saved/abc", "DELETE")
    assert requires_admin_key("/api/saved-searches/abc", "PATCH")
    assert requires_admin_key("/api/brightdata/check-batch", "POST")
    assert requires_admin_key("/api/brightdata/check/abc", "GET")


def test_read_only_routes_remain_available():
    assert not requires_admin_key("/api/properties", "GET")
    assert not requires_admin_key("/api/county-records", "GET")
    assert not requires_admin_key("/api/live/status", "GET")
    assert not requires_admin_key("/api/quill/hello", "GET")
