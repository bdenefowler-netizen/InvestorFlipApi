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
from html.parser import HTMLParser
import json
import os
import re
import tempfile
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urljoin, urlparse

import httpx
from database import PostgresDatabase

from .tax_roll import ensure_indexes, import_matches, load_layout

TAX_ROLL_PAGE_URL = (
    "https://www.tarrantcountytx.gov/content/main/en/tax/property-tax/"
    "tarrant-county-tax-roll.html"
)
TAX_ROLL_LINK_PATTERN = re.compile(
    r"^/content/dam/main/tax/tax-rolls/\d{4}/TaxRoll[_-]?(\d{8})\.zip$",
    re.IGNORECASE,
)
DEFAULT_LAYOUT = Path(__file__).resolve().parent.parent / "data" / "tarrant_tax_roll_layout.json"
REQUIRED_MEMBERS = {"Master.dat", "Rec.DAT", "Stats.txt"}


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        if tag.lower() != "a":
            return
        values = dict(attrs)
        if values.get("href"):
            self.links.append(str(values["href"]))


def validate_tax_roll_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.netloc.lower() != "www.tarrantcountytx.gov":
        raise ValueError("Tax-roll URL must be HTTPS and hosted by www.tarrantcountytx.gov")
    if not TAX_ROLL_LINK_PATTERN.fullmatch(parsed.path):
        raise ValueError("Tax-roll URL does not match the official dated ZIP path")
    return url


def select_latest_tax_roll_url(html: str, page_url: str = TAX_ROLL_PAGE_URL) -> str:
    parser = _LinkParser()
    parser.feed(html)
    candidates = []
    for href in parser.links:
        absolute = urljoin(page_url, href)
        parsed = urlparse(absolute)
        match = TAX_ROLL_LINK_PATTERN.fullmatch(parsed.path)
        if parsed.netloc.lower() == "www.tarrantcountytx.gov" and match:
            candidates.append((match.group(1), absolute))
    if not candidates:
        raise RuntimeError("The official Tarrant County page did not contain a dated TaxRoll ZIP")
    return validate_tax_roll_url(max(candidates)[1])


async def discover_latest_tax_roll_url(page_url: str = TAX_ROLL_PAGE_URL) -> str:
    async with httpx.AsyncClient(follow_redirects=True, timeout=httpx.Timeout(60.0)) as client:
        response = await client.get(page_url)
        response.raise_for_status()
    return select_latest_tax_roll_url(response.text, page_url)


async def resolve_tax_roll_url(explicit_url: Optional[str]) -> str:
    configured = explicit_url or os.environ.get("TARRANT_TAX_ROLL_URL", "").strip()
    if configured:
        return validate_tax_roll_url(configured)
    return await discover_latest_tax_roll_url()


async def download_zip(url: str, destination: Path) -> Dict[str, Any]:
    validate_tax_roll_url(url)

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
    layout_path = Path(args.layout).expanduser().resolve() if getattr(args, "layout", None) else DEFAULT_LAYOUT
    if not layout_path.exists():
        raise FileNotFoundError(layout_path)
    layout = load_layout(layout_path)

    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("DATABASE_URL is required")

    source_url = await resolve_tax_roll_url(args.url)
    db = PostgresDatabase(database_url)
    try:
        await ensure_indexes(db)
        previous = await db.live_sync_log.find_one({
            "sync_type": "tax_roll",
            "source_url": source_url,
            "status": "success",
        })
        if previous and not args.force:
            result = {
                "ok": True,
                "skipped": True,
                "reason": "This official tax-roll ZIP was already applied",
                "source_url": source_url,
            }
            print(json.dumps(result, indent=2))
            return result

        with tempfile.TemporaryDirectory(prefix="investorflip-taxroll-") as temp_dir:
            zip_path = Path(temp_dir) / "tarrant-tax-roll.zip"
            download_result = await download_zip(source_url, zip_path)
            archive_result = validate_archive(zip_path, layout)
            match_result = await import_matches(
                db=db,
                zip_path=zip_path,
                layout=layout,
                dry_run=not args.apply,
                max_records=args.max_records,
            )
            if args.apply:
                await db.live_sync_log.insert_one({
                    "id": str(uuid.uuid4()),
                    "sync_type": "tax_roll",
                    "source": "Tarrant County Tax Roll",
                    "source_url": source_url,
                    "source_file": Path(urlparse(source_url).path).name,
                    "status": "success",
                    "matched_tax_records": match_result["matched_tax_records"],
                    "properties_enriched": match_result["properties_enriched"],
                    "created_at": datetime.now(timezone.utc).isoformat(),
                })

            result = {
                "ok": True,
                "source_url": source_url,
                "mode": "apply" if args.apply else "dry-run",
                "temporary_file_deleted_after_run": True,
                "download": download_result,
                "archive": archive_result,
                "matches": match_result,
            }
            print(json.dumps(result, indent=2, default=str))
            return result
    finally:
        await db.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Download and match the official Tarrant County tax roll")
    parser.add_argument("--url", default=None, help="Optional official ZIP override; newest link is discovered by default")
    parser.add_argument("--layout", default=str(DEFAULT_LAYOUT))
    parser.add_argument("--max-records", type=int, default=None, help="Optional scan limit for diagnostics")
    parser.add_argument("--force", action="store_true", help="Reapply a ZIP that was already logged successfully")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="Write matched tax data to PostgreSQL")
    mode.add_argument("--dry-run", action="store_true", help="Report matches only; this is the default")
    return parser


if __name__ == "__main__":
    asyncio.run(run(build_parser().parse_args()))
