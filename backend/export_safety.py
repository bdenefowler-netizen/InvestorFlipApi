"""Helpers for exporting untrusted public/provider text to spreadsheets."""

from __future__ import annotations

from typing import Any


def spreadsheet_safe(value: Any) -> Any:
    """Neutralize text that spreadsheet programs could evaluate as a formula."""
    if not isinstance(value, str):
        return value
    stripped = value.lstrip(" \t\r\n")
    if stripped.startswith(("=", "+", "-", "@")):
        return "'" + value
    return value
