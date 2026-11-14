"""Conservative identity and distress-evidence rules for county importers.

Pure functions; no network calls, database access, or side effects.
Unknown or ambiguous identity never becomes a verified property match.
"""
from __future__ import annotations

import re
from typing import Any, Mapping, Sequence


def account_key(value: Any) -> str:
    """Normalize Tarrant account identifiers; preserve alphabetic prefixes."""
    if value is None:
        return ""
    key = re.sub(r"[^A-Z0-9]", "", str(value).strip().upper())
    if not key:
        return ""
    return key.lstrip("0") or "0" if key.isdigit() else key

def tax_evidence(record: Mapping[str, Any]) -> dict[str, Any]:
    """Keep current due, prior due, and verified delinquency separate.

    An ordinary current balance does not prove delinquency or a filed lien.
    """
    def amount(name: str) -> float:
        try:
            return max(0.0, float(record.get(name) or 0))
      except (TypeError, ValueError):
            return 0.0

    current = amount("current_amount_due")
    prior = amount("prior_amount_due")
    verified_date = str(record.get("delinquency_date") or "").strip()
    return {
        "current_amount_due": current,
        "prior_amount_due": prior,
        "has_balance_due": current > 0 or prior > 0,
        "has_prior_balance_due": prior > 0,
        "tax_delinquent": bool(verified_date),
        "delinquency_date": verified_date,
        "tax_lien_verified": False,  # requires a separate recorded lien document
    }


def match_by_account(
    evidence: Mapping[str, Any],
    properties: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Only return a verified property if one exact normalized account matches.

    An address is never sufficient on its own. Duplicate matches are refused.
    """
    key = account_key(evidence.get("account_id") or evidence.get("tad_account"))
    if not key:
        return {"status": "unmatched", "reason": "missing_account", "property_id": None}
    candidates = [
        item for item in properties
        if account_key(item.get("account_id") or item.get("tad_account")) == key
    ]
    if len(candidates) != 1:
        return {
            "status": "ambiguous" if candidates else "unmatched",
            "reason": "duplicate_account" if candidates else "account_not_found",
            "property_id": None,
        }
    item = candidates[0]
    if not item.get("id"):
        return {"status": "unmatched", "reason": "missing_property_id", "property_id": None}
    # Never silently link across conflicting jurisdictions.
    for field in ("county", "state"):
        left, right = str(evidence.get(field) or "").strip().upper(), str(item.get(field) or "").strip().upper()
        if left and right and left != right:
            return {"status": "conflict", "reason": f"{field}_mismatch", "property_id": None}
    return {"status": "verified_account", "reason": "unique_account", "property_id": str(item["id"])}
