"""Run all FREE data imports: Fort Worth violations, foreclosures, TAD, SmartPropLeads, and more.

ALL SOURCES ARE FREE - No subscriptions required.

Usage:
    python run_all_imports.py [--limit N]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from database import PostgresDatabase


async def run_all(limit: int = 2000):
    """Run all free imports and print results."""
    db = PostgresDatabase()
    results = {}
    
    try:
        await db.connect()
        
        # 1. Import Fort Worth Code Violations (FREE)
        print("\n[1/8] Importing Fort Worth Code Violations (FREE)...")
        try:
            from importers.fort_worth_violations import import_fort_worth_violations
            results["fort_worth_violations"] = await import_fort_worth_violations(db, limit=limit)
            print(f"    ✓ {results['fort_worth_violations']}")
        except Exception as e:
            results["fort_worth_violations"] = {"error": str(e)}
            print(f"    ✗ Error: {e}")
        
        # 2. Import Foreclosures (FREE)
        print("\n[2/8] Importing Tarrant County Foreclosures (FREE)...")
        try:
            from importers.foreclosure_finder import import_foreclosures
            results["foreclosures"] = await import_foreclosures(db)
            print(f"    ✓ {results['foreclosures']}")
        except Exception as e:
            results["foreclosures"] = {"error": str(e)}
            print(f"    ✗ Error: {e}")
        
        # 3. Import ForeclosureListingsUSA (FREE)
        print("\n[3/8] Importing ForeclosureListingsUSA (FREE)...")
        try:
            from importers.foreclosure_listings_scraper import import_foreclosure_listings
            results["foreclosure_listings"] = await import_foreclosure_listings(db, pages=3)
            print(f"    ✓ {results['foreclosure_listings']}")
        except Exception as e:
            results["foreclosure_listings"] = {"error": str(e)}
            print(f"    ✗ Error: {e}")
        
        # 4. Import OffMarketDeck (FREE)
        print("\n[4/8] Importing OffMarketDeck (FREE)...")
        try:
            from importers.offmarketdeck_scraper import import_offmarket_deals
            results["offmarketdeck"] = await import_offmarket_deals(db, pages=2)
            print(f"    ✓ {results['offmarketdeck']}")
        except Exception as e:
            results["offmarketdeck"] = {"error": str(e)}
            print(f"    ✗ Error: {e}")
        
        # 5. Import TAD (FREE)
        print("\n[5/8] Importing TAD Property Data (FREE)...")
        try:
            from importers.tad_scraper import import_tad_properties
            results["tad"] = await import_tad_properties(db, limit=500)
            print(f"    ✓ {results['tad']}")
        except Exception as e:
            results["tad"] = {"error": str(e)}
            print(f"    ✗ Error: {e}")
        
        # 6. Import New Western (FREE scraping)
        print("\n[6/8] Importing New Western Marketplace (FREE scraping)...")
        try:
            from importers.new_western_scraper import import_new_western
            results["new_western"] = await import_new_western(db, limit=100)
            print(f"    ✓ {results['new_western']}")
        except Exception as e:
            results["new_western"] = {"error": str(e)}
            print(f"    ✗ Error: {e}")
        
        # 7. Import Stessa (FREE scraping)
        print("\n[7/8] Importing Stessa Marketplace (FREE scraping)...")
        try:
            from importers.stessa_scraper import import_stessa
            results["stessa"] = await import_stessa(db, limit=100)
            print(f"    ✓ {results['stessa']}")
        except Exception as e:
            results["stessa"] = {"error": str(e)}
            print(f"    ✗ Error: {e}")
        
        # 8. Import SmartPropLeads (FREE to browse)
        print("\n[8/8] Importing SmartPropLeads (FREE to browse)...")
        try:
            from importers.smartpropleads_scraper import import_smartpropleads
            results["smartpropleads"] = await import_smartpropleads(db, limit=100)
            print(f"    ✓ {results['smartpropleads']}")
        except Exception as e:
            results["smartpropleads"] = {"error": str(e)}
            print(f"    ✗ Error: {e}")
        
        # Print summary
        print("\n" + "=" * 60)
        print("FREE IMPORT SUMMARY - NO SUBSCRIPTIONS REQUIRED")
        print("=" * 60)
        
        total_inserted = 0
        total_matched = 0
        
        for source, data in results.items():
            if "error" in data:
                print(f"  {source}: ERROR - {data['error']}")
            else:
                inserted = data.get("inserted", 0)
                matched = data.get("matched", 0)
                total_inserted += inserted
                total_matched += matched
                print(f"  {source}: {inserted} inserted, {matched} matched")
        
        print(f"\n  TOTAL: {total_inserted} inserted, {total_matched} matched")
        print("=" * 60)
        
    finally:
        await db.close()
    
    return results


def main():
    parser = argparse.ArgumentParser(description="Run all FREE data imports")
    parser.add_argument("--limit", type=int, default=2000, help="Max records to import")
    args = parser.parse_args()
    
    results = asyncio.run(run_all(args.limit))
    
    # Exit with error if any import failed
    if any("error" in v for v in results.values()):
        sys.exit(1)


if __name__ == "__main__":
    main()
