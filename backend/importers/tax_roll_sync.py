"""Download and match the official Tarrant County tax roll.

The ZIP is streamed to temporary storage, validated, passed to the existing
address-matching importer, and deleted automatically afterward.

Safe Railway test from /app or the backend directory:

    python -m importers.tax_roll_sync --dry-run

Apply matched enrichment only after reviewing the dry-run report:

    python -m importers.tax_roll_sync --apply
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Dict
from urllib.parse import urlparse

import httpx
from database import PostgresDatabase

from .tax_roll import ensure_indexes, import_matches, load_layout

DEFAULT_TAX_ROLL_URL = (
    "https://www.tarrantcountytx.gov/content/dam/main/tax/tax-rolls/2026/"
    "TaxRoll20260710.zip"
)
DEFAULT_LAYOUT = Path(__file__).resolve().parent.parent / "data" / "tarrant_tax_roll_layout.json"
REQUIRED_MEMBERS = {"Master.dat", "Rec.DAT", "Stats.txt"}


async def download_zip(url: str, destination: Path) -> Dict[str, Any]:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.netloc.lower() != "www.tarrantcountytx.gov":
        raise ValueError("Tax-roll URL must be an HTTPS URL hosted by www.tarrantcountytx.gov")

    bytes_written = 0
    async with httpx.AsyncClient(follow_redirects=True, timeout=httpx.Timeout(300.0)) as client:
        async with client.stream("GET", url) as response:
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            with destination.open("wb") as output:
                async for chunk in response.aiter_bytes(1024 * 1024):
                    if chunk:
                        output.write(chunk)
                        bytes_written += len(chunk)

    if bytes_written == 0:
        raise RuntimeError("Downloaded tax-roll ZIP is empty")
    return {"downloaded_bytes": bytes_written, "content_type": content_type}


def validate_archive(path: Path, layout: Dict[str, Any]) -> Dict[str, Any]:
    if not zipfile.is_zipfile(path):
        raise RuntimeError("Downloaded file is not a valid ZIP archive")

    with zipfile.ZipFile(path, "r") as archive:
        names = set(archive.namelist())
        missing = REQUIRED_MEMBERS - names
        if missing:
            raise RuntimeError(f"Tax-roll ZIP is missing: {', '.join(sorted(missing))}")

        master_name = layout["master"].get("member", "Master.dat")
        expected_size = int(layout["master"].get("record_size", 741))
        with archive.open(master_name, "r") as master:
            first = master.readline().decode("utf-8", errors="replace").rstrip("\r\n")
        if len(first) != expected_size:
            raise RuntimeError(
                f"MASTER.DAT record length is {len(first)}, expected {expected_size}; refusing to import"
            )

        stats_text = archive.read("Stats.txt").decode("utf-8", errors="replace").strip()
        return {
            "members": sorted(names),
            "master_record_size": len(first),
            "stats_preview": stats_text[:500],
        }


async def run(args: argparse.Namespace) -> None:
    layout_path = Path(args.layout).expanduser().resolve()
    if not layout_path.exists():
        raise FileNotFoundError(layout_path)
    layout = load_layout(layout_path)

    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("DATABASE_URL is required")

    with tempfile.TemporaryDirectory(prefix="investorflip-taxroll-") as temp_dir:
        zip_path = Path(temp_dir) / "tarrant-tax-roll.zip"
        download_result = await download_zip(args.url, zip_path)
        archive_result = validate_archive(zip_path, layout)

        db = PostgresDatabase(database_url)
        try:
            await ensure_indexes(db)
            match_result = await import_matches(
                db=db,
                zip_path=zip_path,
                layout=layout,
                dry_run=not args.apply,
                max_records=args.max_records,
            )
        finally:
            await db.close()

        print(json.dumps({
            "ok": True,
            "source_url": args.url,
            "mode": "apply" if args.apply else "dry-run",
            "temporary_file_deleted_after_run": True,
            "download": download_result,
            "archive": archive_result,
            "matches": match_result,
        }, indent=2, default=str))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Download and match the official Tarrant County tax roll")
    parser.add_argument("--url", default=DEFAULT_TAX_ROLL_URL)
    parser.add_argument("--layout", default=str(DEFAULT_LAYOUT))
    parser.add_argument("--max-records", type=int, default=None, help="Optional scan limit for diagnostics")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="Write matched tax data to PostgreSQL")
    mode.add_argument("--dry-run", action="store_true", help="Report matches only; this is the default")
    return parser


if __name__ == "__main__":
    asyncio.run(run(build_parser().parse_args()))
