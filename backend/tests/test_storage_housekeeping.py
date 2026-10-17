import os

import pytest
from fastapi import HTTPException

os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")

from storage_housekeeping import (  # noqa: E402
    CLEAN_RESET_CONFIRMATION_PHRASE,
    CLEAN_RESET_PRESERVED_TABLES,
    CLEAN_RESET_TABLES,
    CONFIRMATION_PHRASE,
    CleanupRequest,
    require_cleanup_confirmation,
)


def test_cleanup_defaults_to_dry_run():
    request = CleanupRequest()
    assert request.dry_run is True
    assert request.action == "safe_housekeeping"


def test_destructive_cleanup_requires_exact_confirmation():
    request = CleanupRequest(dry_run=False, confirmation="yes")
    with pytest.raises(HTTPException) as exc:
        require_cleanup_confirmation(request)
    assert exc.value.status_code == 409


def test_exact_confirmation_allows_safe_cleanup():
    request = CleanupRequest(dry_run=False, confirmation=CONFIRMATION_PHRASE)
    require_cleanup_confirmation(request)


def test_batch_cleanup_requires_exact_batch_specific_confirmation():
    batch_id = "6d0fc63e-5cb0-43a6-85ca-016d5a900d6b"
    request = CleanupRequest(
        action="staging_batch",
        batch_id=batch_id,
        dry_run=False,
        confirmation=CONFIRMATION_PHRASE,
    )
    with pytest.raises(HTTPException) as exc:
        require_cleanup_confirmation(request)
    assert exc.value.status_code == 409

    request.confirmation = f"DELETE STAGING BATCH {batch_id}"
    require_cleanup_confirmation(request)


def test_clean_reset_requires_its_own_explicit_confirmation():
    request = CleanupRequest(
        action="clean_reset",
        dry_run=False,
        confirmation=CONFIRMATION_PHRASE,
    )
    with pytest.raises(HTTPException) as exc:
        require_cleanup_confirmation(request)
    assert exc.value.status_code == 409

    request.confirmation = CLEAN_RESET_CONFIRMATION_PHRASE
    require_cleanup_confirmation(request)


def test_clean_reset_preserves_schema_and_saved_searches_only():
    assert "properties" in CLEAN_RESET_TABLES
    assert "import_staging" in CLEAN_RESET_TABLES
    assert "saved" in CLEAN_RESET_TABLES
    assert "saved_searches" not in CLEAN_RESET_TABLES
    assert CLEAN_RESET_PRESERVED_TABLES == ("saved_searches",)
