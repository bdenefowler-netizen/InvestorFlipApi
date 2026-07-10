"""Backward-compatible bridge from the retired Scout name to Serenity.

Existing routes can continue importing ``scout_analyze_property`` while every
request is now protected and prepared by Serenity before Quill analyzes it.
"""

from .models import QuillAnalyzeResponse
from .serenity import (
    build_quill_request_from_property,
    serenity_analyze_property,
)


def scout_analyze_property(p: dict) -> QuillAnalyzeResponse:
    """Compatibility alias: Scout now hands the request to Serenity."""
    return serenity_analyze_property(p)
