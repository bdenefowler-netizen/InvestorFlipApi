import os

import pytest
from fastapi import HTTPException

os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")

from storage_housekeeping import (  # noqa: E402
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
