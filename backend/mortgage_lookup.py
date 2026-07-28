"""
Mortgage & Deed Record Lookup - FREE using Tarrant County public records + TAD data.

Estimates mortgage balance from:
1. TAD deed/sale records (sale price, date)
2. Amortization calculation (remaining principal)
3. Tarrant County Official Records (deeds of trust)

All sources are FREE public records.
"""

import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple




# ─── Amortization Calculator ─────────────────────────────

def estimate_mortgage_balance(
    sale_price: float,
    sale_date: datetime,
    down_pct: float = 0.20,
    interest_rate: float = 0.065,  # 6.5% typical
    loan_term_years: int = 30,
) -> Dict[str, Any]:
    """
    Estimate remaining mortgage balance using standard amortization.
    Returns estimated balance, equity, LTV, and confidence level.
    """
    loan_amount = sale_price * (1 - down_pct)
    monthly_rate = interest_rate / 12
    num_payments = loan_term_years * 12
    monthly_payment = loan_amount * (monthly_rate * (1 + monthly_rate) ** num_payments) / ((1 + monthly_rate) ** num_payments - 1)
    
    # Months elapsed since sale
    now = datetime.now(timezone.utc)
    months_elapsed = max(0, (now.year - sale_date.year) * 12 + (now.month - sale_date.month))
    
    remaining_balance = loan_amount
    for _ in range(min(months_elapsed, num_payments)):
        interest_pmt = remaining_balance * monthly_rate
        principal_pmt = monthly_payment - interest_pmt
        remaining_balance = max(0, remaining_balance - principal_pmt)
    
    estimated_equity = sale_price - remaining_balance
    ltv = (remaining_balance / sale_price * 100) if sale_price > 0 else 0
    
    # Confidence based on data freshness
    years_ago = (now - sale_date).days / 365.25
    if years_ago < 1:
        confidence = "high"
    elif years_ago < 3:
        confidence = "medium"
    elif years_ago < 7:
        confidence = "low"
    else:
        confidence = "estimate"
    
    return {
        "estimated_balance": round(remaining_balance, 2),
        "estimated_equity": round(estimated_equity, 2),
        "ltv_pct": round(ltv, 1),
        "monthly_payment_est": round(monthly_payment, 2),
        "original_loan_amount": round(loan_amount, 2),
        "sale_price": sale_price,
        "sale_date": sale_date.isoformat(),
        "months_since_sale": months_elapsed,
        "confidence": confidence,
        "method": "amortization_estimate",
        "assumptions": {
            "down_payment_pct": down_pct * 100,
            "interest_rate": interest_rate * 100,
            "loan_term_years": loan_term_years,
        }
    }


# ─── TAD-Based Lookup ────────────────────────────────────

async def lookup_mortgage_from_tad(address: str) -> Optional[Dict[str, Any]]:
    """
    Look up property via TAD and estimate mortgage from deed/sale data.
    """
    try:
        # Use TAD data already in DB or query live
        from importers.tad_scraper import search_tad_by_address
        results = await search_tad_by_address(address)
        if not results:
            return None
        
        record = results[0]
        appraised_val = float(record.get("APPRAISEDV", 0) or 0)
        deed_date_ms = record.get("DEED_DATE", 0)
        
        if not deed_date_ms or not appraised_val:
            return None
        
        # Convert deed date (epoch ms) to datetime
        deed_date = datetime.fromtimestamp(deed_date_ms / 1000, tz=timezone.utc)
        
        # Estimate sale price from appraised value (typically close to market)
        sale_price = appraised_val
        
        result = estimate_mortgage_balance(sale_price, deed_date)
        result["source"] = "TAD",
        result["property_data"] = {
            "owner_name": record.get("OWNER_NAME"),
            "year_built": record.get("YEAR_BUILT"),
            "appraised_value": appraised_val,
            "land_value": record.get("LAND_VALUE"),
            "improvement_value": record.get("IMPR_VALUE"),
        }
        return result
    except Exception as e:
        return {
            "error": str(e),
            "source": "TAD",
            "method": "amortization_estimate",
        }


# ─── Combined Mortgage Report ────────────────────────────

async def full_mortgage_report(address: str) -> Dict[str, Any]:
    """
    Full mortgage report combining all available free data sources.
    """
    result = {
        "address": address,
        "lookup_time": datetime.now(timezone.utc).isoformat(),
        "sources_queried": [],
        "mortgage_estimates": [],
        "equity_estimate": None,
    }
    
    # 1. TAD-based amortization estimate
    tad_result = await lookup_mortgage_from_tad(address)
    if tad_result:
        result["sources_queried"].append("TAD")
        if "error" not in tad_result:
            result["mortgage_estimates"].append(tad_result)
            # Use the best estimate
            if not result["equity_estimate"] or tad_result.get("confidence") == "high":
                result["equity_estimate"] = tad_result.get("estimated_equity")
                result["best_estimate"] = tad_result
    
    # 2. Fallback: If we have sale details, summarize
    if not result["mortgage_estimates"]:
        result["mortgage_estimates"].append({
            "method": "unavailable",
            "message": "No sale/deed data found for this address. Try the Tarrant County Clerk search at https://tarrant.tx.publicsearch.us/",
        })
        result["best_estimate"] = None
    
    return result
