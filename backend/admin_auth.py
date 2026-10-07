"""Pure route classification used by the API's admin-key middleware."""

from __future__ import annotations

import os
import re

# InvestorFlip's browser client is hosted by Expo/EAS and can use both the
# stable production hostname and deployment-specific hostnames. Keep the API
# readable from those static web origins; protected mutations remain secured
# by the admin-key middleware below.
os.environ["CORS_ALLOWED_ORIGINS"] = "*"

PUBLIC_MUTATION_ENDPOINTS = {
    "/api/intake/upload",
    "/api/import/bulk/intake",
    "/api/import/bulk/upload-workbook",
    "/api/import/bulk/upload-workbook/chunk",
    "/api/import/bulk/intake/complete",
    "/api/import/bulk/upload-workbook/complete",
}


def _is_public_upload_transport(path: str, method: str) -> bool:
    method = method.upper()
    if path in PUBLIC_MUTATION_ENDPOINTS and method in {"POST", "PUTT", "PATCH", "DELETE"}:
        return True

    # Chunk cleanup uses a dynamic upload id in the path. Only the DELETE
    # method is public for that narrow prefix.
    chunk_cleanup_prefix = "/api/import/bulk/upload-workbook/chunk/"
    if method == "DELETE" and path.startswith(chunk_cleanup_prefix):
        return True

    return False


def requires_admin_key(path: str, method: str = "GET") -> bool:
    """Return whether a route can mutate data or consume paid-provider credit."""
    if _is_public_upload_transport(path, method):
        return False

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
        "/api/rapidapi/",
        "/api/property-details",
        "/api/skip-trace",
        "/api/calculator/lookup",
    )
    protected_exact = {
        "/api/feeds/sync",
        "/api/feeds/upload-csv",
        "/api/live/sync-fort-worth",
        "/api/ai/analyze-property",
        "/api/scout/quill-analysis",
    }
    read_only_endpoints = {
        "/api/analyze/quick",
        "/api/analyze/deal",
    }
    protected_property_operation = bool(re.match(
        r"^/api/properties/[^/]+/(?:enrich|ai-analysis|quill-analysis|tax-history)$",
        path,
    ))
    if path in read_only_endpoints:
        return False
    return (
        path.startswith(protected_prefixes)
        or path in protected_exact
        or protected_property_operation
    )
