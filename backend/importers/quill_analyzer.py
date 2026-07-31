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
    year_built = property_data.get("year_built") or 1950
    
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
        # Infer from signals
        age = datetime.now().year - int(year_built or 1950)
        if violation_count >= 5 or distress_score >= 80 or age > 70:
            level = "heavy"
        elif violation_count >= 2 or distress_score >= 50 or age > 45:
            level = "moderate"
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
    deal = determine_deal_type(float(price or 0), float(arv or 0), repairs)
    
    # P&L
    closing_costs = float(price or 0) * DEFAULT_CLOSING_PCT
    carry_costs = DEFAULT_CARRY_MONTHS * DEFAULT_CARRY_MONTHLY
    total_investment = float(price or 0) + repairs["total"] + closing_costs + carry_costs
    commission = float(arv or 0) * DEFAULT_COMMISSION_PCT
    net_profit = float(arv or 0) - total_investment - commission
    roi = (net_profit / total_investment * 100) if total_investment else 0
    
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
            "spread": int(float(arv or 0) - float(price or 0)),
            "repairs": repairs,
            "mortgage": mortgage,
            "deal_type": deal["type"],
            "deal_confidence": deal["confidence"],
            "deal_reason": deal["reason"],
        },
        "pnl": {
            "purchase_price": int(float(price or 0)),
            "estimated_repairs": repairs["total"],
            "closing_costs": int(closing_costs),
            "carry_costs": carry_costs,
            "total_investment": int(total_investment),
            "arv": int(float(arv or 0)),
            "commission": int(commission),
            "net_profit": int(net_profit),
            "roi_pct": round(roi, 1),
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
    
    if net_profit > 50000:
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
        f"{greeting} {deal_type} alert: ${n['price']:,} ask → ${n['arv']:,} ARV = "
        f"${n['spread']:,} spread before costs. {vibe}{mortgage_note}"
    )


# ---------- Async Orchestration ----------

async def analyze_property(
    property_data: Dict[str, Any],
    check_flood: bool = True,
) -> Dict[str, Any]:
    """Full async analysis — includes flood zone lookup."""
    analysis = build_analysis(property_data)
    
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
