"""Canonical street-key normalization so every importer can match addresses
regardless of format (bare street, full 'ST, CITY, ST ZIP', casing, suffixes).

'2401 Kelton Street, Fort Worth, TX 76112' -> '2401 KELTON ST'
'2401 KELTON ST'                            -> '2401 KELTON ST'
"""
import re
from typing import Any, Optional

_DIRECTIONS = {
    "N": "N", "NORTH": "N",
    "S": "S", "SOUTH": "S",
    "E": "E", "EAST": "E",
    "W": "W", "WEST": "W",
    "NE": "NE", "NW": "NW", "SE": "SE", "SW": "SW",
}

_STREET_SUFFIX = {
    "ST": "ST", "STREET": "ST",
    "AVE": "AVE", "AVENUE": "AVE",
    "BLVD": "BLVD", "BOULEVARD": "BLVD",
    "DR": "DR", "DRIVE": "DR",
    "RD": "RD", "ROAD": "RD",
    "LN": "LN", "LANE": "LN",
    "CT": "CT", "COURT": "CT",
    "CIR": "CIR", "CIRCLE": "CIR",
    "PKWY": "PKWY", "PARKWAY": "PKWY",
    "PL": "PL", "PLACE": "PL",
    "TRL": "TRL", "TRAIL": "TRL",
    "HWY": "HWY", "HIGHWAY": "HWY",
    "TER": "TER", "TERRACE": "TER",
    "CV": "CV", "COVE": "CV",
    "BND": "BND", "BEND": "BEND",
    "HTS": "HTS", "HEIGHTS": "HTS",
    "PATH": "PATH", "PASS": "PASS",
    "ROW": "ROW", "RUN": "RUN",
    "VW": "VW", "VIEW": "VW",
}


def _norm(value: Any) -> str:
    return str(value or "").strip()


def canonical_street_key(raw: Any) -> str:
    """Return a stable, uppercased street key like '2401 KELTON ST'.

    Drops city/state/zip (anything after the first comma), normalizes
    directionals + street suffixes, collapses whitespace.
    """
    text = _norm(raw)
    if not text:
        return ""
    # take the street part only (before any comma)
    text = text.split(",", 1)[0].strip().upper()
    if not text:
        return ""
    tokens = text.split()
    out: list[str] = []
    for tok in tokens:
        clean = tok.strip(".#-")
        if clean in _DIRECTIONS:
            out.append(_DIRECTIONS[clean])
        elif clean in _STREET_SUFFIX:
            out.append(_STREET_SUFFIX[clean])
        else:
            out.append(clean)
    key = " ".join(out)
    return re.sub(r"\s+", " ", key).strip()


def street_prefix_regex(raw: Any) -> Optional[str]:
    """Regex that matches any address format starting with this street key:
    '^2401 KELTON ST' (matches bare, full, or apt-suffixed variants)."""
    key = canonical_street_key(raw)
    if not key:
        return None
    return "^" + re.escape(key) + r"(,|\s|$)"
