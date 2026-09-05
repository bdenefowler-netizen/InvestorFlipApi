"""Pure route classification used by the API's admin-key middleware."""

from __future__ import annotations

import re


def requires_admin_key(path: str, method: str = "GET") -> bool:
    """Return whether a route can mutate data or consume paid-provider credit."""
    if path.startswith("/api/") and method.upper() not in {"GET", "HEAD", "OPTIONS"}:
        return True
    protected_prefixes = (
        "/api/import/",
        "/api/admin/",
        "/api/intake/",
        "/api/quill/analyze",
        "/api/quill/offer-letter",
        "/api/quill/negotiate",
        "/api/brightdata/check",
    )
    protected_exact = {
        "/api/feeds/sync",
        "/api/feeds/upload-csv",
        "/api/live/sync-fort-worth",
        "/api/ai/analyze-property",
        "/api/scout/quill-analysis",
    }
    # Read-only endpoints — should NOT require admin key (no mutation, no credit cost)
    READ_ONLY_ENDPOINTS = {
        "/api/analyze/quick",
        "/api/analyze/deal",
    }
    protected_property_operation = bool(re.match(
        r"^/api/properties/[^/]+/(?:enrich|ai-analysis|quill-analysis|tax-history)$",
        path,
    ))
    if path in READ_ONLY_ENDPOINTS:
        return False
    return (
        path.startswith(protected_prefixes)
        or path in protected_exact
        or protected_property_operation
    )
