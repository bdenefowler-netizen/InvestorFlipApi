"""Quill — the deal analysis guru for InvestorFlip.

Takes any property and produces a full deal breakdown:
  - ARV (after repair value) & deal type (Flip / Wholesale / Buy & Hold)
  - Estimated mortgage balance (from public deed/appraisal records)
  - Flood zone check (FEMA NFHL with graceful fallback)
  - Permit/addition history (best-effort from county data)
  - Full P&L: price + repairs + costs vs ARV → net profit & ROI

Personality: encouraging but factual, little goofy. "Hey bud, what
adventure are we gonna get in today?"

All FREE. No API keys required (graceful degradation when sources
are unreachable).
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("quill")

# Try curl_cffi (TLS fingerprinting) then httpx fallback
try:
    from curl_cffi import requests as http_client
    HAS_CURL_CFFI = True
except ImportError:
    import httpx as http_client
    HAS_CURL_CFFI = False

# Bright Data MCP (real Zillow cross-check) — optional, degrades gracefully
try:
    from importers.brightdata_check import cross_check_property as _bd_cross_check
    HAS_BRIGHTDATA = bool(os.environ.get("BRIGHTDATA_TOKEN", "").strip())
except Exception:
    HAS_BRIGHTDATA = False

# ---------- Constants ----------

# Standard cost assumptions (adjustable per deal)
DEFAULT_CLOSING_PCT = 0.03       # buyer closing costs
DEFAULT_COMMISSION_PCT = 0.06    # resale agent commission
DEFAULT_CARRY_MONTHS = 6         # holding period
DEFAULT_CARRY_MONTHLY = 750      # taxes/insurance/utilities
DEFAULT_REPAIR_PER_SQFT = {
    "light": 25,
    "moderate": 45,
    "heavy": 70,
    "full": 95,
}

FLOOD_SOURCES = [
    # FEMA NFHL public REST API (best-effort)
    "https://hazards.fema.gov/gis/nfhl/rest/services/NFHL/MapServer/identify",
    # Tarrant County flood layers (public ArcGIS)
    "https://maps.tarrantcounty.com/arcgis/rest/services/Flood/MapServer/identify",
]



# ---------- Value Cross-Check (ARV Validation) ----------

def cross_check_value(property_data: Dict[str, Any]) -> Dict[str, Any]:
    """Cross-check ALL value signals for a property and produce a
    validated ARV with confidence score.

    Sources used (when available):
      1. county appraised value  (TAD — most objective, sales-based)
      2. feed/market estimate    (Zillow/Realtor-style automated estimate)
      3. tax roll market value   (county tax roll)
      4. wholesaler ARV claim    (InvestorLift / New Western — most biased!)
      5. current ask price       (what the seller wants)

    Logic:
      - County appraised is the anchor (assessor uses actual sales).
      - Market estimate within +/-15% of county = agreement (HIGH confidence).
      - Wholesaler ARV claim > county by 25%+ = inflated flag.
      - Realistic ARV = weighted median of agreeing sources.
    """
    sources = {}

    # County anchor: PREFER the genuine separate tax-roll field (official
    # Tarrant County roll), then TAD/feed-provided appraised/assessed.
    # QA audit 2026-08-02: previously compared ordinary merged fields
    # (market_value/assessed_value that feeds and TAD both overwrite),
    # which silently double-counted the same number as "agreement".
    county = (
        property_data.get("tax_roll_market_value")
        or property_data.get("appraised_value")
        or property_data.get("assessed_value")
    )
    if county:
        sources["county_appraised"] = float(county)

    taxroll = property_data.get("tax_roll_market_value")
    if taxroll and "county_appraised" not in sources:
        sources["tax_roll"] = float(taxroll)

    feed_mv = property_data.get("market_value")
    if feed_mv:
        # market_value is ambiguous — feeds AND TAD both write it. If it's
        # within 1% of the county anchor it's the same source double-counted;
        # skip it so it can't manufacture fake "agreement"/confidence.
        if county and abs(float(feed_mv) - float(county)) / float(county) < 0.01:
            pass
        else:
            sources["market_estimate"] = float(feed_mv)

    arv_claim = property_data.get("arv_estimate")
    if arv_claim:
        sources["wholesaler_arv"] = float(arv_claim)

    # Optional live search estimates are added to the property before the
    # analysis is built. Keeping them in this one function guarantees that
    # the displayed benchmark, deal classification, spread, and P&L all use
    # the same final evidence set.
    live_fields = {
        "live_zillow_value": "live_zillow",
        "live_realtor_value": "live_realtor",
        "live_redfin_value": "live_redfin",
    }
    for field, label in live_fields.items():
        value = property_data.get(field)
        if value and float(value) > 1000:
            sources[label] = float(value)

    if not sources:
        return {
            "available_sources": 0,
            "validated_arv": None,
            "confidence": "none",
            "flags": ["No independent value data — get comps before offering"],
        }

    values = list(sources.values())
    anchor = sources.get("county_appraised")

    # Agreement check: how close are non-county sources to county?
    disagreements = []
    if anchor and len(values) > 1:
        for name, val in sources.items():
            if name == "county_appraised":
                continue
            pct_diff = (val - anchor) / anchor * 100
            if abs(pct_diff) > 25:
                disagreements.append({
                    "source": name,
                    "value": int(val),
                    "vs_county_pct": round(pct_diff, 1),
                    "verdict": "inflated" if pct_diff > 0 else "undervalued",
                })

    # Validated ARV: prefer county, sanity-checked against agreeing sources
    if anchor:
        agreeing = [v for v in values if abs((v - anchor) / anchor * 100) <= 25]
        if agreeing:
            validated = sum(agreeing) / len(agreeing)  # mean of agreeing cluster
        else:
            validated = anchor  # county alone
    else:
        # No county data — median of what we have
        sorted_vals = sorted(values)
        mid = len(sorted_vals) // 2
        validated = sorted_vals[mid] if len(sorted_vals) % 2 else (sorted_vals[mid-1] + sorted_vals[mid]) / 2

    # Confidence score
    if len(sources) >= 3 and not disagreements:
        confidence = "high"
    elif len(sources) >= 2 and len(disagreements) <= 1:
        confidence = "medium"
    elif anchor and disagreements:
        confidence = "low"
    else:
        confidence = "low"

    flags = []
    for d in disagreements:
        flags.append(
            f"⚠️ {d['source']} says ${d['value']:,} — {d['verdict']} vs county ({d['vs_county_pct']:+.0f}%)"
        )
    if not anchor:
        flags.append("ℹ️ No county appraisal — using available estimates")

    return {
        "available_sources": len(sources),
        "sources": {k: int(v) for k, v in sources.items()},
        "validated_arv": int(round(validated / 100) * 100),
        "confidence": confidence,
        "flags": flags,
        "disagreements": disagreements,
    }


def quill_value_take(crosscheck: Dict[str, Any]) -> str:
    """Quill's plain-English take on the value cross-check."""
    if crosscheck["available_sources"] == 0:
        return "No independent value data on this one, bud. Get real comps before you offer a dime."

    arv = crosscheck.get("validated_arv")
    conf = crosscheck.get("confidence")
    flags = crosscheck.get("flags", [])

    if conf == "high":
        return (
            f"Numbers check out, bud. Multiple sources agree around ${arv:,} — "
            "use that as a screening benchmark, then verify with sold comps. 🔒"
        )
    if flags and conf != "high":
        worst = flags[0]
        return (
            f"Hold up, bud. {worst}. "
            f"The screening benchmark is closer to ${arv:,} — don't pay for a dream number. 🚨"
        )
    if conf == "medium":
        return (
            f"Mostly agrees — the screening benchmark lands around ${arv:,}. "
            "One source is off, but verify the cluster with sold comps. 👍"
        )
    return f"Screening estimate: ${arv:,}. Get sold comps before making an offer."

# ---------- Core Analysis ----------

def estimate_repairs(
    property_data: Dict[str, Any],
    condition: Optional[str] = None,
) -> Dict[str, Any]:
    """Estimate repair costs based on property condition."""
    sqft = property_data.get("sqft") or property_data.get("sq_footage") or 0
    sqft = int(sqft or 0)
    
    # Condition hints from data (violations, distress, age)
    condition = (condition or property_data.get("condition") or "").lower()
    
    violation_count = int(property_data.get("violation_count") or 0)
    distress_score = property_data.get("distress_score") or 0
    year_built = property_data.get("year_built")
    
    # Determine rehab level
    if condition in ("light_rehab", "light rehab"):
        level = "light"
    elif condition in ("moderate", "fixer"):
        level = "moderate"
    elif condition in ("heavy", "gut", "dilapidated"):
        level = "heavy"
    elif condition in ("full", "rehab", "reconstruction"):
        level = "full"
    else:
        # Infer from signals (only if we have a real year_built)
        if year_built:
            age = datetime.now().year - int(year_built)
        else:
            age = 0
        if violation_count >= 5 or distress_score >= 80 or age > 70:
            level = "heavy"
        elif violation_count >= 2 or distress_score >= 50 or age > 45:
            level = "moderate"
        elif age > 60:
            level = "moderate"  # older home without other signals
        else:
            level = "light"
    
    per_sqft = DEFAULT_REPAIR_PER_SQFT[level]
    base = sqft * per_sqft
    
    # Contingency 15%
    contingency = base * 0.15
    
    return {
        "level": level,
        "per_sqft": per_sqft,
        "estimate": int(base),
        "contingency": int(contingency),
        "total": int(base + contingency),
        "sqft": sqft,
    }


def estimate_mortgage(
    property_data: Dict[str, Any],
) -> Dict[str, Any]:
    """Estimate remaining mortgage from public records.
    
    We don't have exact balances (private) — but we can estimate:
    - Appraised value × assumed LTV at purchase
    - Deed date → years elapsed → amortization factor
    - Refinance records (if we have them) reset the clock
    """
    appraised = property_data.get("appraised_value") or property_data.get("market_value") or 0
    deed_date = property_data.get("deed_date") or property_data.get("sale_date")
    refinance_date = property_data.get("refinance_date")
    
    # If we have a recorded sale price, use that as the loan basis
    loan_basis = property_data.get("sale_price") or appraised or 0
    
    if not loan_basis:
        return {
            "estimated_balance": None,
            "confidence": "unknown",
            "note": "No appraisal/sale data available",
        }
    
    # Assume 80% LTV at origination (typical)
    original_loan = loan_basis * 0.80
    
    # Use most recent mortgage event (refinance resets amortization)
    event_date = refinance_date or deed_date
    years_elapsed = 0
    if event_date:
        try:
            if isinstance(event_date, (int, float)) and event_date > 1000000000:
                # epoch timestamp
                event_dt = datetime.fromtimestamp(event_date, tz=timezone.utc)
            else:
                event_dt = datetime.fromisoformat(str(event_date).replace("Z", "+00:00"))
            years_elapsed = max(0, (datetime.now(timezone.utc) - event_dt).days / 365.25)
        except Exception:
            years_elapsed = 0
    
    # 30-year amortization, 6% APR — remaining balance factor
    # Simple approximation: balance ≈ original × (1 - years/30) adjusted for front-loaded interest
    if years_elapsed >= 30:
        remaining = original_loan * 0.05  # small residual
    else:
        # Rough amortization curve (mortgage balance after n years on 30yr @6%)
        # ~ balance factor table approximations
        amort_factors = {
            0: 1.00, 1: 0.987, 2: 0.973, 3: 0.959, 4: 0.944,
            5: 0.929, 6: 0.913, 7: 0.896, 8: 0.879, 9: 0.861,
            10: 0.842, 12: 0.802, 15: 0.744, 20: 0.619, 25: 0.459,
        }
        yr = int(years_elapsed)
        factor = amort_factors.get(yr)
        if factor is None:
            # interpolate
            keys = sorted(amort_factors.keys())
            if yr < keys[0]:
                factor = amort_factors[keys[0]]
            elif yr > keys[-1]:
                factor = amort_factors[keys[-1]]
            else:
                for i in range(len(keys) - 1):
                    if keys[i] <= yr <= keys[i + 1]:
                        f0, f1 = amort_factors[keys[i]], amort_factors[keys[i + 1]]
                        factor = f0 + (f1 - f0) * (yr - keys[i]) / (keys[i + 1] - keys[i])
                        break
                else:
                    factor = 0.5
        remaining = original_loan * factor
    
    ltv = (remaining / appraised * 100) if appraised else None
    
    return {
        "estimated_balance": int(round(remaining / 100) * 100),
        "original_loan": int(round(original_loan / 100) * 100),
        "assumed_ltv_at_purchase": "80%",
        "years_elapsed": round(years_elapsed, 1),
        "confidence": "medium" if years_elapsed > 0 else "low",
        "note": "Estimate based on public appraisal/deed records. Exact balance is private.",
        "estimated_ltv": round(ltv, 1) if ltv else None,
        "refinanced": bool(refinance_date),
    }


async def check_flood_zone(lat: float, lng: float) -> Dict[str, Any]:
    """Check flood zone via FEMA NFHL (best-effort).
    
    Returns:
        {"in_flood_zone": bool|None, "zone": str|None, "source": str, "note": str}
    """
    if not lat or not lng:
        return {
            "in_flood_zone": None,
            "zone": None,
            "source": "none",
            "note": "No coordinates available",
        }
    
    for base in FLOOD_SOURCES:
        try:
            params = {
                "f": "json",
                "geometryType": "esriGeometryPoint",
                "geometry": f"{lng},{lat}",
                "tolerance": "50",
                "mapExtent": f"{lng-0.02},{lat-0.02},{lng+0.02},{lat+0.02}",
                "imageDisplay": "1000,1000,96",
                "layers": "all",
            }
            if HAS_CURL_CFFI:
                resp = http_client.get(base, params=params, impersonate="chrome124", timeout=15)
            else:
                async with http_client.AsyncClient(timeout=15) as client:
                    resp = await client.get(base, params=params)
            
            if resp.status_code != 200:
                continue
            
            data = resp.json()
            results = data.get("results") or []
            if not results:
                continue
            
            # Find flood zone attribute
            for r in results:
                attrs = r.get("attributes") or {}
                zone = (
                    attrs.get("FLD_ZONE")
                    or attrs.get("zone")
                    or attrs.get("ZONE")
                    or attrs.get("FLOOD_ZONE")
                )
                if zone:
                    # FEMA zones: A, AE, AH, AO, VE, V = special flood hazard (SFHA)
                    # X, X500 = minimal risk
                    in_flood = zone.upper().startswith(("A", "V"))
                    return {
                        "in_flood_zone": in_flood,
                        "zone": zone,
                        "source": base.split("/")[2],
                        "note": "FEMA SFHA zone" if in_flood else "Minimal flood risk",
                    }
        except Exception as e:
            logger.debug(f"Flood source {base} failed: {e}")
            continue
    
    return {
        "in_flood_zone": None,
        "zone": None,
        "source": "none",
        "note": "Flood data unavailable — check FEMA map manually",
    }


def determine_deal_type(
    price: float,
    arv: float,
    repairs: Dict[str, Any],
) -> Dict[str, Any]:
    """Classify deal as Flip, Wholesale, or Buy & Hold."""
    if not price or not arv:
        return {"type": "unknown", "confidence": 0.0, "reason": "Missing price/ARV"}
    
    spread = arv - price
    total_cost = price + repairs["total"]
    margin = spread - total_cost + repairs["total"]  # gross profit before selling costs
    
    # Wholesale: deals assigned for a fee — typically priced far below ARV
    # with buyer (flipper) still making money after repairs
    flipper_profit = arv - (price + repairs["total"]) * 1.10  # 10% buffer for flipper
    
    if flipper_profit <= 0:
        return {
            "type": "buy_and_hold",
            "confidence": 0.5,
            "reason": "Not enough spread for a flip after repairs",
        }
    
    # If the deal is priced so low that the profit is mostly assignment fee → wholesale
    # Typical wholesale: deal must leave 10-15% of ARV as flipper profit AND
    # price is < 70% of ARV minus repairs
    price_to_arv = price / arv if arv else 1
    
    if price_to_arv <= 0.65:
        return {
            "type": "wholesale",
            "confidence": 0.7,
            "reason": f"Priced at {price_to_arv*100:.0f}% of ARV — room for assignment + flip",
        }
    
    return {
        "type": "fix_and_flip",
        "confidence": 0.8,
        "reason": "Classic flip spread after repairs",
    }


def build_analysis(property_data: Dict[str, Any]) -> Dict[str, Any]:
    """Build the full Quill analysis for a property."""
    price = property_data.get("price") or 0
    arv = (
        property_data.get("arv_estimate")
        or property_data.get("market_value")
        or property_data.get("appraised_value")
        or 0
    )
    sqft = property_data.get("sqft") or property_data.get("sq_footage") or 0
    
    repairs = estimate_repairs(property_data)
    mortgage = estimate_mortgage(property_data)
    
    # Value cross-check — validate ARV against all sources
    crosscheck = cross_check_value(property_data)
    validated_arv = crosscheck.get("validated_arv") or float(arv or 0)
    
    has_price = float(price or 0) > 0
    deal = determine_deal_type(float(price or 0), float(validated_arv or 0), repairs)
    
    # P&L (use validated ARV) — NEVER fabricate a profit when the purchase
    # price is unknown. price=0 previously produced phantom $100K+ profits
    # and 200%+ ROI that could push a user into a bad decision.
    eff_arv = float(validated_arv or 0)
    closing_costs = float(price or 0) * DEFAULT_CLOSING_PCT
    carry_costs = DEFAULT_CARRY_MONTHS * DEFAULT_CARRY_MONTHLY
    total_investment = float(price or 0) + repairs["total"] + closing_costs + carry_costs
    commission = eff_arv * DEFAULT_COMMISSION_PCT
    if has_price:
        net_profit = eff_arv - total_investment - commission
        roi = (net_profit / total_investment * 100) if total_investment else 0.0
    else:
        net_profit = None
        roi = None
    
    return {
        "property": {
            "address": property_data.get("situs_address"),
            "city": property_data.get("city"),
            "state": property_data.get("state"),
            "zip": property_data.get("zip"),
            "beds": property_data.get("beds"),
            "baths": property_data.get("baths"),
            "sqft": sqft,
            "year_built": property_data.get("year_built"),
        },
        "numbers": {
            "price": int(float(price or 0)),
            "arv": int(float(arv or 0)),
            "validated_arv": int(float(validated_arv or 0)),
            "spread": int(float(validated_arv or 0) - float(price or 0)),
            "repairs": repairs,
            "mortgage": mortgage,
            "price_missing": not has_price,
            "deal_type": deal["type"],
            "deal_confidence": deal["confidence"],
            "deal_reason": deal["reason"],
        },
        "value_check": crosscheck,
        "value_take": quill_value_take(crosscheck),
        "pnl": {
            "purchase_price": int(float(price or 0)),
            "estimated_repairs": repairs["total"],
            "closing_costs": int(closing_costs),
            "carry_costs": carry_costs,
            "total_investment": int(total_investment),
            "arv": int(eff_arv),
            "commission": int(commission),
            "net_profit": int(net_profit) if net_profit is not None else None,
            "roi_pct": round(roi, 1) if roi is not None else None,
        },
        "flags": _build_flags(property_data, deal, net_profit),
    }


def _build_flags(
    property_data: Dict[str, Any],
    deal: Dict[str, Any],
    net_profit: float,
) -> List[Dict[str, str]]:
    """Generate quick-glance flags for the deal."""
    flags = []
    
    if net_profit is None:
        flags.append({"type": "warn", "label": "⚠️ No purchase price — profit unknown"})
    elif net_profit > 50000:
        flags.append({"type": "hot", "label": f"🔥 +${int(net_profit):,} potential"})
    elif net_profit > 20000:
        flags.append({"type": "good", "label": f"✅ +${int(net_profit):,} potential"})
    elif net_profit > 0:
        flags.append({"type": "ok", "label": f"💰 +${int(net_profit):,} (thin margin)"})
    else:
        flags.append({"type": "warn", "label": "⚠️ Negative margin at ask"})
    
    if property_data.get("violation_count"):
        flags.append({"type": "warn", "label": f"⚠️ {property_data['violation_count']} code violations"})
    if property_data.get("foreclosure"):
        flags.append({"type": "danger", "label": "🏛️ Foreclosure"})
    if property_data.get("absentee_owner"):
        flags.append({"type": "info", "label": "👤 Absentee owner"})
    if property_data.get("days_on_market") and int(property_data["days_on_market"]) > 90:
        flags.append({"type": "info", "label": f"⏳ {property_data['days_on_market']} DOM (motivated?)"})
    
    return flags[:5]


def quill_take(
    analysis: Dict[str, Any],
    personality: str = "default",
) -> str:
    """Generate Quill's plain-English take on the deal."""
    n = analysis["numbers"]
    p = analysis["pnl"]
    deal_type = n["deal_type"].replace("_", " ").title()
    
    if personality == "encouraging":
        greeting = "Hey bud! 👋"
    else:
        greeting = "Alright, here's the scoop:"
    
    if p["net_profit"] is None:
        return (
            f"{greeting} We don't have a purchase price for this one yet, so "
            "I can't run the numbers. 🤷 Once we know the ask, I'll sniff out "
            "the profit and ROI."
        )
    if p["net_profit"] > 50000:
        vibe = f"This one's got some real juice in it. 🧃 We're looking at roughly ${p['net_profit']:,} in it after everything shakes out — that's a solid {p['roi_pct']}% return on your money."
    elif p["net_profit"] > 20000:
        vibe = f"Not a monster, but a legit deal. 💪 About ${p['net_profit']:,} net ({p['roi_pct']}% ROI). Worth a serious look."
    elif p["net_profit"] > 0:
        vibe = f"It's a workable deal — ${p['net_profit']:,} net ({p['roi_pct']}% ROI). Thin-ish, so negotiate hard."
    else:
        vibe = f"At ask, this one's underwater ({p['net_profit']:,}). BUT — that's the starting number. Sellers don't always mean what they ask."
    
    mortgage_note = ""
    if n["mortgage"].get("estimated_balance"):
        mortgage_note = f" They likely owe ~${n['mortgage']['estimated_balance']:,} (est.), so there's room to negotiate."
    
    return (
        f"{greeting} {deal_type} alert: ${n['price']:,} ask → ${n['validated_arv']:,} screening value = "
        f"${n['spread']:,} spread before costs. {vibe}{mortgage_note}"
    )


# ---------- Async Orchestration ----------

async def analyze_property(
    property_data: Dict[str, Any],
    check_flood: bool = True,
) -> Dict[str, Any]:
    """Full async analysis — includes flood zone lookup."""
    enriched = dict(property_data)
    live_result: Dict[str, Any] = {"status": "skipped"}

    # Gather live evidence first. The full analysis is deliberately built
    # only after this finishes so the benchmark, spread, deal type, and P&L
    # cannot disagree with the live-source panel shown to the user.
    if HAS_BRIGHTDATA:
        try:
            live_result = await _bd_cross_check(enriched)
            if live_result.get("status") == "ok":
                enriched["live_zillow_value"] = live_result.get("zestimate")
                enriched["live_realtor_value"] = live_result.get("cotality")
                enriched["live_redfin_value"] = live_result.get("redfin_value")
        except Exception as e:
            logger.debug(f"Bright Data check failed: {e}")
            live_result = {"status": "error", "error": str(e)}

    analysis = build_analysis(enriched)
    analysis["live_zillow"] = live_result
    if live_result.get("comps"):
        analysis["comps"] = live_result["comps"]
    
    if check_flood and property_data.get("latitude"):
        flood = await check_flood_zone(
            property_data.get("latitude"),
            property_data.get("longitude"),
        )
        analysis["flood"] = flood
    else:
        analysis["flood"] = {
            "in_flood_zone": None,
            "zone": None,
            "source": "none",
            "note": "Flood check skipped",
        }
    
    # Permits / additions — placeholder for county permit data
    analysis["permits"] = {
        "additions_found": None,
        "permits": property_data.get("permits") or [],
        "note": "Permit lookup available where county data is reachable",
    }

    analysis["take"] = quill_take(analysis, personality="encouraging")
    analysis["analysis_basis"] = "screening"
    analysis["disclaimer"] = (
        "Automated screening estimate only. Verify sold comps, title, taxes, "
        "condition, repair scope, and financing before making an offer."
    )
    analysis["generated_at"] = datetime.now(timezone.utc).isoformat()
    
    return analysis


# ---------- CLI Test ----------

if __name__ == "__main__":
    import asyncio
    
    sample = {
        "situs_address": "2617 Concho St",
        "city": "FORT WORTH",
        "state": "TX",
        "zip": "76104",
        "price": 50000,
        "market_value": 194988,
        "sqft": 1096,
        "beds": 3,
        "baths": 1,
        "year_built": 1954,
        "latitude": 32.7157,
        "longitude": -97.3000,
        "violation_count": 4,
        "days_on_market": 276,
    }
    
    async def main():
        result = await analyze_property(sample, check_flood=False)
        print(json.dumps(result, indent=2, default=str))
    
    asyncio.run(main())


# ---------- Offer Letter Generator ----------

def generate_offer_letter(
    analysis: Dict[str, Any],
    buyer_name: str = "[Buyer Name]",
    offer_price: Optional[int] = None,
    earnest_money: int = 1000,
    closing_days: int = 30,
    financing: str = "Cash",
) -> str:
    """Generate a professional offer letter for a property."""
    n = analysis["numbers"]
    p = analysis["pnl"]
    prop = analysis["property"]
    address = prop.get("address") or "the property"
    city = prop.get("city") or ""
    state = prop.get("state") or ""
    
    if offer_price is None:
        # Default: start at 70% of ARV minus repairs (classic flip formula)
        offer_price = int(max(0, (n["arv"] * 0.70) - n["repairs"]["total"]))
    
    return f"""RE: Offer to Purchase — {address}, {city} {state}

Dear Property Owner,

I am writing to present my offer to purchase the property located at {address}, {city}, {state}. I understand the property needs some attention, and I am prepared to handle the repairs and improvements necessary to bring it back to its full potential.

My offer terms are as follows:

  • Purchase Price:  ${offer_price:,} (cash)
  • Earnest Money:   ${earnest_money:,}
  • Closing:         {closing_days} days from mutual acceptance
  • Financing:       {financing}
  • Inspection:      For informational purposes only

My goal is a quick, smooth closing with no contingencies to slow us down. I have funds available and am ready to move as soon as you are ready.

Why my offer works for you:
  • No realtor commissions taken from your side
  • No repairs required from you — I take it as-is
  • Fast closing — cash in hand, no lender delays
  • No cleaning, staging, or showings to manage

I would love the opportunity to make this easy for you. Please call me anytime to discuss.

Sincerely,
{buyer_name}

P.S. — Every situation is different. If my number doesn't work for you, let's talk — I'm flexible and I want this to work for both of us."""


def negotiation_advice(analysis: Dict[str, Any]) -> str:
    """Quill's negotiation advice for a deal."""
    n = analysis["numbers"]
    p = analysis["pnl"]
    deal_type = n["deal_type"].replace("_", " ").title()
    spread = n["spread"]
    dom = analysis["property"].get("days_on_market")
    mortgage = n["mortgage"]
    
    # Determine leverage points
    advice = []
    
    # Days on market = leverage
    if dom and int(dom) > 90:
        advice.append(f"⏳ {dom} days on market — that's your leverage. Sellers get tired.")
    elif dom and int(dom) > 30:
        advice.append(f"⏳ {dom} days on market — some room to negotiate.")
    
    # Mortgage balance knowledge = leverage
    if mortgage.get("estimated_balance"):
        bal = mortgage["estimated_balance"]
        advice.append(
            f"🏦 They likely owe ~${bal:,}. If the spread covers their payoff + your margin, "
            "you're in the driver's seat."
        )
    
    # Deal-specific
    if deal_type == "Wholesale":
        advice.append("📋 This smells like a wholesale — negotiate the assignment fee, not the whole spread.")
    elif deal_type == "Fix And Flip":
        advice.append("🛠️ Flip math: hold firm on your max — repairs always run over. Add 10% buffer.")
    
    # Generic closer
    advice.append("🤝 Start low, be kind, close fast. Cash talks.")
    
    return "\n".join(advice)
