"""Import PropertyRadar CSV(s) into the InvestorFlip database.

Usage:
  python import_propertyradar.py <file1.csv> [file2.csv ...] [--dry-run]
"""
import argparse
import asyncio
import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from propertyradar_csv import parse_csv_file  # noqa: E402

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def import_props(props, dry_run=False):
    from database import PostgresDatabase

    db = PostgresDatabase()
    try:
        await db.connect()
        inserted = 0
        updated = 0
        skipped = 0
        for p in props:
            addr = (p.get("situs_address") or "").strip()
            city = (p.get("city") or "").strip()
            zip5 = (p.get("zip") or "")[:5]
            key_addr = addr.split(",")[0].strip()
            if not key_addr:
                skipped += 1
                continue

            # Look for existing property by address+zip
            existing = None
            try:
                existing = await db.properties.find_one({
                    "situs_address": {"$regex": f"^{re.escape(key_addr)}(,|$)", "$options": "i"},
                    "zip": zip5,
                })
            except Exception:
                existing = None

            if existing:
                update_fields = {k: v for k, v in p.items() if k not in ("situs_address", "city", "state", "zip")}
                update_fields["data_source"] = existing.get("data_source", "") + " + PropertyRadar"
                update_fields["updated_at"] = datetime.now(timezone.utc).isoformat()
                if not dry_run:
                    await db.properties.update_one({"id": existing["id"]}, {"$set": update_fields})
                updated += 1
            else:
                new_prop = dict(p)
                new_prop["id"] = existing_id() if False else None  # generate below
                new_prop["created_at"] = datetime.now(timezone.utc).isoformat()
                new_prop["updated_at"] = new_prop["created_at"]
                if not dry_run:
                    await db.properties.insert_one(new_prop)
                inserted += 1

        logger.info(f"Inserted: {inserted} | Updated: {updated} | Skipped: {skipped}")
        return {"inserted": inserted, "updated": updated, "skipped": skipped}
    finally:
        await db.close()


def existing_id():
    import uuid
    return str(uuid.uuid4())


import re  # noqa: E402
from datetime import datetime, timezone  # noqa: E402


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("files", nargs="+")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    all_props = []
    for f in args.files:
        props = parse_csv_file(f)
        logger.info(f"{f}: {len(props)} properties")
        all_props.extend(props)

    result = await import_props(all_props, dry_run=args.dry_run)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
