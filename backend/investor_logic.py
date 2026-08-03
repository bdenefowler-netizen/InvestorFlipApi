"""Shared InvestorFlip owner signals, provenance, and screening scores.

The app deliberately distinguishes a screening benchmark from true equity and
ARV.  A county appraisal or third-party estimate can help prioritize research,
but neither proves a resale value or an owner's mortgage balance.
"""

from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any, Dict, Mapping, Optional, Tuple


LAW_FIRM_KEYWORDS = (
    "law office", "law offices", "attorney", "attorneys", "legal",
    "counsel", "litigation", "law firm", "law group", "lawyer",
)
LAW_FIRM_SUFFIXES = ("LLP", "PLLC", "PC", "P.C.", "P.L.L.C.")
KNOWN_LAW_FIRMS = ("Jackson Walker", "Thompson Knight", "Kelly Hart")
BANK_KEYWORDS = (
    "bank", "mortgage", "wells fargo", "chase", "bank of america",
    "citibank", "fannie mae", "freddie mac", "hud", "us bank",
    "deutsche bank", "nationstar", "mr. cooper", "carrington",
)
TRUST_KEYWORDS = ("trust", "trustee", "family trust", "living trust", "revocable")
LLC_KEYWORDS = (" llc", "l.l.c.", "limited liability", " investments")
CORP_KEYWORDS = (
    "inc.", " inc", "incorporated", "corporation", "corp.", "company",
    " co.", "brothers", "holdings", "partners", "properties", "realty", "group",
)
GOV_KEYWORDS = (
    "city of", "county of", "state of texas", "tarrant county", "federal",
    "department of", "housing authority", "isd",
)
NONPROFIT_KEYWORDS = (
    "nonprofit", "non-profit", "foundation", "charity", "habitat for humanity",
    "ministry", "church", "diocese",
)

SYNTHETIC_SOURCE_MARKERS = (
    "demo seed data",
    "seeded sample",
    "synthetic",
)


def classify_owner(owner_name: str) -> str:
    if not owner_name:
        return "Unknown"
    name = owner_name.strip()
    upper = name.upper()
    lower = name.lower()

    if any(firm.lower() in lower for firm in KNOWN_LAW_FIRMS):
        return "Law Firm"
    for keyword in LAW_FIRM_KEYWORDS:
        if keyword in lower:
            return "Attorney" if keyword in {"attorney", "attorneys", "lawyer"} else "Law Firm"
    if any(re.search(rf"\b{re.escape(suffix)}\b", upper) for suffix in LAW_FIRM_SUFFIXES):
        return "Law Firm"
    if any(keyword in lower for keyword in GOV_KEYWORDS):
        return "Government"
    if any(keyword in lower for keyword in NONPROFIT_KEYWORDS):
        return "Nonprofit"
    if any(keyword in lower for keyword in BANK_KEYWORDS):
        return "Bank"
    if any(keyword in lower for keyword in TRUST_KEYWORDS):
        return "Trust"
    if any(keyword in lower for keyword in LLC_KEYWORDS) or upper.endswith(" LLC"):
        return "LLC"
    if any(keyword in lower for keyword in CORP_KEYWORDS):
        return "Corporation"
    return "Individual"


def is_synthetic_property(property_record: Mapping[str, Any]) -> bool:
    if property_record.get("is_synthetic") is True:
        return True
    source = str(property_record.get("data_source") or "").lower()
    # This exact source was produced only by the bundled 20-row fixture. If a
    # genuine provider later matched the same house, do not hide that provider's
    # listing merely because the legacy source label was appended to it.
    legacy_foreclosure = "tarrant county foreclosure records"
    if source.strip() == legacy_foreclosure:
        return True
    return any(marker in source for marker in SYNTHETIC_SOURCE_MARKERS)


def _street(value: str) -> str:
    text = str(value or "").split(",", 1)[0].upper()
    return re.sub(r"[^A-Z0-9]", "", text)


def _mailing_state(value: str) -> Optional[str]:
    matches = re.findall(r"(?:,|\s)\s*([A-Z]{2})\s+\d{5}(?:-?\d{4})?\b", str(value or "").upper())
    return matches[-1] if matches else None


def derive_owner_signals(
    owner_name: str,
    mailing_address: str = "",
    situs_address: str = "",
    property_state: str = "TX",
) -> Dict[str, Any]:
    owner_type = classify_owner(owner_name)
    mailing_state = _mailing_state(mailing_address)
    out_of_state = bool(mailing_state and mailing_state != str(property_state or "TX").upper())
    mailing_street = _street(mailing_address)
    situs_street = _street(situs_address)
    absentee = bool(mailing_street and situs_street and mailing_street != situs_street)
    investor_owned = owner_type in {"LLC", "Corporation", "Trust", "Bank"}

    return {
        "owner_type": owner_type,
        "owner_classification_source": "name heuristic" if owner_name else "missing owner",
        "mailing_state": mailing_state,
        "out_of_state_owner": out_of_state,
        "absentee_owner": absentee,
        "investor_owned": investor_owned,
        # Entity ownership is not proof that the purchase was cash-financed.
        "cash_buyer": bool(False),
        "cash_buyer_status": "unverified",
    }


def _number(value: Any) -> Optional[float]:
    try:
        if value in (None, ""):
            return None
        number = float(value)
        return number if number > 0 else None
    except (TypeError, ValueError):
        return None


def _value_benchmark(property_record: Mapping[str, Any]) -> Tuple[Optional[float], Optional[str], str]:
    arv = _number(property_record.get("arv_estimate"))
    if arv:
        confidence = "high" if property_record.get("arv_verified") else "medium"
        return arv, str(property_record.get("arv_source") or "ARV estimate"), confidence

    provider = _number(property_record.get("zestimate") or property_record.get("provider_estimated_value"))
    if provider:
        return provider, "third-party automated estimate", "medium"

    tax_value = _number(
        property_record.get("tax_roll_market_value")
        or property_record.get("tax_appraised_value")
        or property_record.get("assessed_value")
    )
    if tax_value:
        return tax_value, "Tarrant County appraisal (screening only)", "low"

    market = _number(property_record.get("market_value"))
    market_source = str(property_record.get("market_value_source") or "").lower()
    if market and market_source and "list" not in market_source and "asking" not in market_source:
        return market, str(property_record.get("market_value_source")), "medium"
    return None, None, "insufficient"


def compute_scores(property_record: Mapping[str, Any]) -> Dict[str, Any]:
    """Return transparent preliminary scores without inventing ARV or equity."""
    asking = _number(property_record.get("price") or property_record.get("listing_price"))
    benchmark, benchmark_source, benchmark_confidence = _value_benchmark(property_record)
    repairs = _number(property_record.get("repair_estimate"))
    rent = _number(property_record.get("rent_estimate") or property_record.get("rent_zestimate"))
    mortgage = _number(property_record.get("mortgage_estimate"))
    year_built = int(_number(property_record.get("year_built")) or 0)
    age = max(0, datetime.now(timezone.utc).year - year_built) if year_built else 0
    listing_type = str(property_record.get("listing_type") or "For Sale")
    owner_type = str(property_record.get("owner_type") or "Unknown")
    distress = listing_type in {"REO", "Foreclosure"} or bool(property_record.get("tax_delinquent"))

    value_spread = benchmark - asking if benchmark and asking else None
    discount_pct = (value_spread / benchmark * 100) if value_spread is not None and benchmark else None

    def clamp(value: float) -> int:
        return max(1, min(99, round(value)))

    flip_score: Optional[int] = None
    wholesale_score: Optional[int] = None
    if benchmark and asking:
        spread_points = max(-25.0, min(35.0, (discount_pct or 0) * 1.2))
        condition_points = 8 if age >= 30 else 3 if age else 0
        distress_points = 8 if distress else 0
        repair_penalty = min(25.0, repairs / benchmark * 100) if repairs else 8
        flip_score = clamp(45 + spread_points + condition_points + distress_points - repair_penalty)
        wholesale_score = clamp(42 + spread_points * 1.25 + distress_points)

    rental_score: Optional[int] = None
    if rent and asking:
        gross_yield = rent * 12 / asking * 100
        rental_score = clamp(20 + gross_yield * 6)

    available = [score for score in (flip_score, wholesale_score, rental_score) if score is not None]
    investment_score = clamp(sum(available) / len(available)) if available else None

    missing = []
    if benchmark_confidence != "high":
        missing.append("verified sold-comps ARV")
    if repairs is None:
        missing.append("repair estimate")
    if rent is None:
        missing.append("rent estimate")
    if mortgage is None:
        missing.append("mortgage balance")
    if not property_record.get("comps_verified"):
        missing.append("verified comparable sales")

    risk = 20 + min(50, len(missing) * 9)
    if distress:
        risk += 8
    if owner_type == "Bank":
        risk += 4
    if age >= 50:
        risk += 5

    score_confidence = benchmark_confidence
    if benchmark_confidence == "medium" and not repairs:
        score_confidence = "low"
    if benchmark_confidence == "low":
        score_confidence = "low"

    return {
        "investment_score": investment_score,
        "wholesale_score": wholesale_score,
        "flip_score": flip_score,
        "rental_score": rental_score,
        "risk_score": clamp(risk),
        "score_confidence": score_confidence,
        "score_kind": "preliminary screening",
        "score_missing_inputs": missing,
        "value_benchmark": round(benchmark, 2) if benchmark else None,
        "value_benchmark_source": benchmark_source,
        "value_spread": round(value_spread, 2) if value_spread is not None else None,
        "discount_to_benchmark_pct": round(discount_pct, 1) if discount_pct is not None else None,
        # True owner equity requires debt data; asking-price discount is not equity.
        "equity_estimate": round(benchmark - mortgage, 2) if benchmark and mortgage else None,
        "equity_status": "estimated" if benchmark and mortgage else "unknown - mortgage balance required",
        "est_roi_pct": None,
        "roi_status": "unknown - ARV, repairs, holding, and selling costs required",
    }


TAX_ROLL_PRESERVED_FIELDS = (
    "account_id", "parcel_id", "owner_name", "owner_mailing_address",
    "tax_roll_market_value", "tax_roll_land_value", "tax_roll_improvement_value",
    "annual_taxes", "current_tax_amount_due", "prior_tax_amount_due",
    "tax_delinquent", "tax_roll_source", "tax_roll_matched_at", "legal_description",
)


def merge_live_refresh(
    existing_property: Mapping[str, Any],
    incoming_listing: Mapping[str, Any],
) -> Dict[str, Any]:
    """Merge a listing refresh without erasing more-trusted county enrichment."""
    existing = dict(existing_property or {})
    merged = {**existing, **dict(incoming_listing)}

    if existing.get("id"):
        merged["id"] = existing["id"]
    if existing.get("created_at"):
        merged["created_at"] = existing["created_at"]

    if existing.get("tax_roll_source"):
        for field in TAX_ROLL_PRESERVED_FIELDS:
            if field in existing:
                merged[field] = existing[field]

    incoming_market_value = _number(incoming_listing.get("market_value"))
    existing_market_source = str(existing.get("market_value_source") or "")
    if not incoming_market_value and existing_market_source:
        merged["market_value"] = existing.get("market_value")
        merged["market_value_source"] = existing_market_source
        if existing.get("zestimate"):
            merged["zestimate"] = existing["zestimate"]
    elif not incoming_market_value:
        # Old rows used asking price as market value without a source. Do not
        # carry that fabricated value into a truthful refresh.
        merged["market_value"] = None
        merged["market_value_source"] = None

    old_provenance = existing.get("data_provenance") if isinstance(existing.get("data_provenance"), dict) else {}
    new_provenance = incoming_listing.get("data_provenance") if isinstance(incoming_listing.get("data_provenance"), dict) else {}
    merged["data_provenance"] = {**old_provenance, **new_provenance}

    merged.update(derive_owner_signals(
        str(merged.get("owner_name") or ""),
        str(merged.get("owner_mailing_address") or ""),
        str(merged.get("situs_address") or ""),
        str(merged.get("state") or "TX"),
    ))
    merged.update(compute_scores(merged))
    return merged
