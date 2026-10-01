"""Bzip2-compressed county file intake for InvestorFlip.

Extends the stable upload runtime without rewriting source files.
Compressed .bz2 files are accepted, streamed to the raw-intake area,
validated by streaming decompression, hashed, and returned immediately as
received work. Parsing/matching is deliberately deferred to the import worker.
"""

from __future__ import annotations

import bz2
import hashlib
import os
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Dict

import upload_runtime as base
from fastapi import HTTPException, UploadFile

_RECEIVED_ROOT = Path(
    os.environ.get(
        "INVESTORFLIP_RAW_UPLOAD_DIR",
        str(Path(tempfile.gettempdir()) / "investorflip-raw-intake"),
    )
)
_RECEIVED_ROOT.mkdir(parents=True, exist_ok=True)

_MAX_DECOMPRESSED_BYTES = int(
    os.environ.get(
        "INVESTORFLIP_MAX_BZ2_DECOMPRESSED_BYTES",
        str(1024 * 1024 * 1024),
    )
)
_STREAM_BYTES = 1024 * 1024

_original_safe_filename = base._safe_filename
_original_stage_uploaded_file = base._stage_uploaded_file


def _safe_filename(value: str) -> str:
    name = Path(value or "county-import.bz2").name
    if name.lower().endswith(".bz2"):
        if len(name) > 255:
            raise HTTPException(400, "Filename is too long")
        return name
    return _original_safe_filename(name)


def _detect_category(filename: str) -> list[str]:
    lower = filename.lower()
    if lower.startswith("tad__"):
        return ["tad"]
    if lower.startswith("taxroll__"):
        return ["tax"]
    if lower.startswith("preforeclosure__"):
        return ["pre_foreclosure"]
    if lower.startswith("probate__"):
        return ["probate"]
    if lower.startswith("code_violation__"):
        return ["code_violations"]
    if lower.startswith("owner__"):
        return ["owner"]
    return []


async def _receive_bz2(file: UploadFile) -> Dict[str, Any]:
    started = time.perf_counter()
    filename = _safe_filename(file.filename or "county-import.bz2")
    receipt_id = str(uuid.uuid4())

    destination = _RECEIVED_ROOT / f"{receipt_id}__{filename}"
    temporary = destination.with_suffix(destination.suffix + ".tmp")

    digest = hashlib.sha256()
    compressed_bytes = 0

    try:
        await file.seek(0)
        with temporary.open("wb") as output:
            while True:
                chunk = await file.read(_STREAM_BYTES)
                if not chunk:
                    break
                compressed_bytes += len(chunk)
                if compressed_bytes > base.bulk.MAX_PUBLIC_UPLOAD_BYTES:
                    raise HTTPException(
                        413,
                        "The compressed upload is larger than 300 MiB",
                    )
                digest.update(chunk)
                output.write(chunk)

        temporary.replace(destination)

        decompressed_bytes = 0
        line_count = 0
        try:
            with bz2.open(destination, "rb") as source:
                while True:
                    chunk = source.read(_STREAM_BYTES)
                    if not chunk:
                        break
                    decompressed_bytes += len(chunk)
                    if decompressed_bytes > _MAX_DECOMPRESSED_BYTEN:
                        raise HTTPException(
                            413,
                            "BZ2 payload expands beyond the 1 GiB safety limit",
                        )
                    line_count += chunk.count(b"\n")
        except HTTPException:
            raise
        except (OSError, EOFError, ValueError) as exc:
            raise HTTPException(
                400,
                f"Invalid BZ2 file: {str(exc)[:180]}",
            ) from exc

        return {
            "ok": True,
            "batch_id": receipt_id,
            "filename": filename,
            "categories": _detect_category(filename),
            "files": [
                {
                    "file": filename,
                    "status": "received",
                    "rows": 0,
                    "accepted": 0,
                    "rejected": 0,
                    "inserted": 0,
                    "updated": 0,
                    "staged": 0,
                    "reason": "BZ2 compressed text accepted; parsing is deferred",
                }
            ],
            "rows_read": 0,
            "accepted": 0,
            "rejected": 0,
            "inserted": 0,
            "updated": 0,
            "staged": 0,
            "property_ids": [],
            "processing": {
                "status": "received",
                "parsing": "deferred",
                "matching": "deferred",
                "enrichment": "deferred",
            },
            "enrichment": {
                "county": {
                    "live_checked": 0,
                    "enriched": 0,
                    "tad_lookups": 0,
                    "missing": 0,
                    "deferred": True,
                }
            },
            "transport": {
                "mode": "bz2",
                "bytes_received": compressed_bytes,
                "decompressed_bytes": decompressed_bytes,
                "line_count_estimate": line_count,
                "sha256": digest.hexdigest(),
                "receipt_id": receipt_id,
                "storage": "raw-intake",
            },
            "timings": {
                "total_seconds": round(time.perf_counter() - started, 3),
            },
        }
    except Exception:
        temporary.unlink(missing_ok=True)
        destination.unlink(missing_ok=True)
        raise


async def _stage_uploaded_file(file: UploadFile) -> Dict[str, Any]:
    filename = _safe_filename(file.filename or "county-import.bz2")
    if filename.lower().endswith(".bz2"):
        return await _receive_bz2(file)
    return await _original_stage_uploaded_file(file)


# Extend the stable runtime globals that the already-registered route handlers
# resolve at request time.
base._safe_filename = _safe_filename
base._stage_uploaded_file = _stage_uploaded_file


def main() -> None:
    base.main()


if __name__ == "__main__":
    main()
