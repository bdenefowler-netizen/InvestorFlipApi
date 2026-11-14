"""Read-only TAD / Tax Roll / live-listing identity audit.

Run from backend: python -m diagnostics.property_link_audit
Requires DATABASE_URL. This script NEVER creates tables, modifies rows, or calls providers.
Report contains counts only, no owner details or credentials.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
from collections import Counter
from typing import Any, Mapping

def account_key(value: Any) -> str:
    """Match the county_records normalize_account_id convention."""
    value = re.sub(r"[^A-Z0-9]", "", str(value or "").strip().upper())
    return (value.lstrip("0") or "0") if value.isdigit() else value

def flag(record: Mapping[str, Any], name: str) -> bool:
    return record.get(name) is True

def audit_rows(county: list[dict], properties: list[dict], tax: list[dict]) -> dict:
    """Audit bounded samples; account identity only, never street-only joins."""
    county_by_account: dict[str, list[dict]] = {}
    for item in county:
        key = account_key(item.get("normalized_account_id") or item.get("account_id"))
        if key:
            county_by_account.setdefault(key, []).append(item)
    tax_accounts = {account_key(x.get("account_id")) for x in tax}
    tax_accounts.discard("")
    report = Counter()
    report["county_rows_scanned"] = len(county)
    report["property_rows_scanned"] = len(properties)
    report["tax_roll_rows_scanned"] = len(tax)
    report["county_with_tad"] = sum(flag(c, "has_tad") for c in county)
    report["county_with_tax_roll"] = sum(flag(c, "has_tax_roll") for c in county)
    report["county_with_both"] = sum(flag(c, "has_tad") and flag(c, "has_tax_roll") for c in county)
    report["county_tax_account_in_tax_collection"] = sum(
        flag(c, "has_tax_roll") and account_key(c.get("account_id")) in tax_accounts for c in county
    )
    for prop in properties:
        report["live_listings"] += bool(prop.get("is_live_listing"))
        key = account_key(prop.get("account_id"))
        if not key:
            report["properties_without_account"] += 1
            continue
        candidates = county_by_account.get(key, [])
        if len(candidates) == 1:
            report["property_exact_account_county_match"] += 1
            report["property_exact_account_tax_evidence"] += bool(candidates[0].get("has_tax_roll"))
        elif len(candidates) > 1:
            report["property_ambiguous_account"] += 1
        else:
            report["property_account_unmatched_in_sample"] += 1
    return dict(report)

async def collect(database_url: str, limit: int) -> dict:
    """Read-only transaction avoids PostgresDatabase.connect() schema initialization."""
    import asyncpg
    connection = await asyncpg.connect(database_url)
    try:
        async with connection.transaction(readonly=True):
            result = {}
            tables = (
                ("county_records", "county"),
                ("properties", "properties"),
                ("tax_roll", "tax"),
            )
            snapshots = {}
            for table, label in tables:
                result[f"{label}_total"] = await connection.fetchval(f"SELECT count(*) FROM {table}")
                rows = await connection.fetch(
                    f"SELECT data FROM {table} ORDER BY document_key LIMIT $1", limit
                )
                snapshots[label] = [json.loads(r["data"]) if isinstance(r["data"], str)
                                    else dict(r["data"]) for r in rows]
            result["sample"] = audit_rows(
                snapshots["county"], snapshots["properties"], snapshots["tax"]
            )
              result["wample_limit_per_table"] = limit
            result["warning"] = (
                "Samples are independently limited by document_key; overlap counts are "
                  "diagnostic only, NOT county-wide match rates. A zero overlap in this "
                  "sample does not establish a broken join."
            )
            return result
    finally:
        await connection.close()

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=500, help="Rows per table, max 5000")
    args = parse.parse_args()
    if not 1 <= args.limit <= 5000:
        parser.error("--limit must be between 1 and 5000")
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        parser.error("DATABASE_URL must be configured; no credentials are printed")
    result = asyncio.run(collect(url, args.limit))
    print(json.dumps(result, indent=2, sort_keys=True)

if __name__ == "__main__":
    main()
