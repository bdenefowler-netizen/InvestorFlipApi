"""Stable county-upload runtime for InvestorFlip.

Registers the public staging and chunked upload routes in memory before the
FastAPI application is imported. This replaces the old Railway startup scripts
that rewrote bulk_import.py on disk.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import time
import uuid
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List

import bulk_import as bulk
from database import PostgresDatabase
from fastapi import File, Form, HTTPException, UploadFile
from import_staging import stage_import_rows
from tad_targeted_stream import stage_targeted_tad_xlsx

_CHUNK_ROOT = Path(tempfile.gettempdir()) / "investorflip-chunk-uploads"
_CHUNK_ROOT.mkdir(parents=True, exist_ok=True)

_MAX_CHUNK_BYTES = 10 * 1024 * 1024
_MAX_CHUNKS = 2_000
_ALLOWED_SUFFIXES = {".csv", ".xls", ".xlsx", ".zip"}


def _remove_route(path: str, methods: set[str]) -> None:
    """Remove one existing route from bulk.router before replacing it."""
    bulk.router.routes[:] = [
        route
        for route in bulk.router.routes
        if not (
            getattr(route, "path", None) == path
            and methods.issubset(set(getattr(route, "methods", set()) or set()))
        )
    ]


def _safe_upload_id(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "", str(value or ""))
    if not cleaned or len(cleaned) > 120:
        raise HTTPException(400, "Invalid upload id")
    return cleaned


def _safe_filename(value: str) -> str:
    name = Path(value or "county-import.xlsx").name
    if Path(name).suffix.lower() not in _ALLOWED_SUFFIXES:
        raise HTTPException(400, "Upload .csv, .xls, .xlsx, or .zip")
    return name


def _validate_transport(total_chunks: int, total_size: int) -> None:
    if total_chunks < 1 or total_chunks > _MAX_CHUNKS:
        raise HTTPException(400, "Invalid total chunk count")
    if total_size < 1 or total_size > bulk.MAX_PUBLIC_UPLOAD_BYTES:
        raise HTTPException(413, "The upload is larger than 300 MiB")


def _upload_dir(upload_id: str) -> Path:
    return _CHUNK_ROOT / _safe_upload_id(upload_id)


async def _stage_payload(
    db: PostgresDatabase,
    *,
    batch_id: str,
    name: str,
    payload: bytes,
    ext: str,
) -> tuple[Dict[str, Any], List[str]]:
    from county_records_routes import _read_upload_rows

    parse_started = time.perf_counter()
    rows, sheets = _read_upload_rows(payload, ext, name)
    categories = bulk._upload_categories(name, rows)
    prepared = [bulk._prepare_row(row, categories) for row in rows]
    parse_seconds = time.perf_counter() - parse_started

    store_started = time.perf_counter()
    staged = await stage_import_rows(
        db,
        batch_id=batch_id,
        source_file=name,
        categories=categories,
        rows=prepared,
    )
    store_seconds = time.perf_counter() - store_started

    return (
        {
            "file": name,
            "status": "staged",
            "categories": categories,
            "rows": len(rows),
            "accepted": staged,
            "rejected": max(0, len(rows) - staged),
            "inserted": staged,
            "updated": 0,
            "staged": staged,
            "sheets": sheets,
            "timings": {
                "parse_seconds": round(parse_seconds, 3),
                "store_seconds": round(store_seconds, 3),
            },
        },
        categories,
    )


async def _stage_uploaded_file(file: UploadFile) -> Dict[str, Any]:
    request_started = time.perf_counter()
    batch_id = str(uuid.uuid4())
    filename = _safe_filename(file.filename or "upload.xlsx")
    suffix = Path(filename).suffix.lower()

    receive_started = time.perf_counter()
    raw = await file.read(bulk.MAX_PUBLIC_UPLOAD_BYTES + 1)
    receive_seconds = time.perf_counter() - receive_started

    if not raw:
        raise HTTPException(400, "The uploaded file is empty")
    if len(raw) > bulk.MAX_PUBLIC_UPLOAD_BYTES:
        raise HTTPException(413, "The upload is larger than 300 MiB")

    db = PostgresDatabase()
    await db.connect()
    try:
        reports: List[Dict[str, Any]] = []
        categories_seen: List[str] = []
        totals = {"accepted": 0, "rejected": 0, "inserted": 0, "updated": 0, "staged": 0}
        parse_seconds_total = 0.0
        store_seconds_total = 0.0

        async def import_one(name: str, payload: bytes, ext: str) -> None:
            nonlocal parse_seconds_total, store_seconds_total
            report, categories = await _stage_payload(
                db,
                batch_id=batch_id,
                name=name,
                payload=payload,
                ext=ext,
            )
            reports.append(report)
            for category in categories:
                if category not in categories_seen:
                    categories_seen.append(category)
            totals["accepted"] += int(report["accepted"])
            totals["rejected"] += int(report["rejected"])
            totals["inserted"] += int(report["inserted"])
            totals["staged"] += int(report["staged"])
            parse_seconds_total += float(report["timings"]["parse_seconds"])
            store_seconds_total += float(report["timings"]["store_seconds"])

        if suffix == ".zip":
            try:
                with zipfile.ZipFile(BytesIO(raw)) as archive:
                    members = [
                        member
                        for member in archive.infolist()
                        if not member.is_dir()
                        and Path(member.filename).suffix.lower() in {".csv", ".xls", ".xlsx"}
                    ]
                    if not members:
                        raise HTTPException(400, "ZIP contains no CSV or Excel files")
                    expanded = sum(max(0, member.file_size) for member in members)
                    if expanded > bulk.MAX_ZIP_EXPANDED_BYTES:
                        raise HTTPException(413, "ZIP expands beyond the 750 MB safety limit")
                    for member in members:
                        try:
                            await import_one(
                                member.filename,
                                archive.read(member),
                                Path(member.filename).suffix.lower(),
                            )
                        except Exception as exc:
                            reports.append(
                                {
                                    "file": member.filename,
                                    "status": "error",
                                    "reason": str(exc)[:300],
                                }
                            )
            except HTTPException:
                raise
            except Exception as exc:
                raise HTTPException(400, f"Could not read ZIP: {str(exc)[:220]}") from exc
        else:
            try:
                await import_one(filename, raw, suffix)
            except Exception as exc:
                raise HTTPException(
                    400,
                    f"Could not stage workbook: {str(exc)[:220]}",
                ) from exc

        total_seconds = time.perf_counter() - request_started
        return {
            "ok": bool(totals["staged"]),
            "batch_id": batch_id,
            "filename": filename,
            "categories": categories_seen,
            "files": reports,
            "rows_read": totals["accepted"] + totals["rejected"],
            **totals,
            "property_ids": [],
            "processing": {
                "status": "staged",
                "matching": "deferred",
                "owner_classification": "deferred",
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
            "timings": {
                "receive_seconds": round(receive_seconds, 3),
                "parse_seconds": round(parse_seconds_total, 3),
                "store_seconds": round(store_seconds_total, 3),
                "total_seconds": round(total_seconds, 3),
                "bytes_received": len(raw),
            },
        }
    finally:
        await db.close()


# Replace the legacy workbook route with the staging-only hot path.
_remove_route("/import/bulk/upload-workbook", {"POST"})


@bulk.router.post("/upload-workbook")
async def public_county_workbook_upload(file: UploadFile = File(...)):
    return await _stage_uploaded_file(file)


@bulk.router.post("/upload-workbook/chunk")
async def public_county_workbook_chunk(
    upload_id: str = Form(...),
    chunk_index: int = Form(...),
    total_chunks: int = Form(...),
    filename: str = Form(...),
    total_size: int = Form(...),
    chunk: UploadFile = File(...),
):
    upload_id = _safe_upload_id(upload_id)
    filename = _safe_filename(filename)
    _validate_transport(total_chunks, total_size)

    if chunk_index < 0 or chunk_index >= total_chunks:
        raise HTTPException(400, "Invalid chunk index")

    payload = await chunk.read(_MAX_CHUNK_BYTES + 1)
    if not payload:
        raise HTTPException(400, "Chunk is empty")
    if len(payload) > _MAX_CHUNK_BYTES:
        raise HTTPException(413, "Chunk exceeds 10 MiB")

    upload_dir = _upload_dir(upload_id)
    upload_dir.mkdir(parents=True, exist_ok=True)

    destination = upload_dir / f"{chunk_index:06d}.part"
    temporary = upload_dir / f"{chunk_index:06d}.part.tmp"
    temporary.write_bytes(payload)
    temporary.replace(destination)

    return {
        "ok": True,
        "upload_id": upload_id,
        "chunk_index": chunk_index,
        "received_bytes": len(payload),
        "total_chunks": total_chunks,
        "filename": filename,
    }


@bulk.router.post("/upload-workbook/complete")
async def public_county_workbook_complete(
    upload_id: str = Form(...),
    total_chunks: int = Form(...),
    filename: str = Form(...),
    total_size: int = Form(...),
):
    upload_id = _safe_upload_id(upload_id)
    filename = _safe_filename(filename)
    _validate_transport(total_chunks, total_size)

    upload_dir = _upload_dir(upload_id)
    if not upload_dir.exists():
        raise HTTPException(404, "Chunk upload not found")

    try:
        parts = [upload_dir / f"{index:06d}.part" for index in range(total_chunks)]
        missing = [index for index, part in enumerate(parts) if not part.exists()]
        if missing:
            raise HTTPException(409, f"Upload incomplete; missing {len(missing)} chunk(s)")

        # Keep the original extension. openpyxl validates the path suffix before
        # inspecting the ZIP payload and rejects the former ``assembled.upload``
        # name even when the bytes are a valid XLSX workbook.
        assembled_path = upload_dir / f"assembled{Path(filename).suffix.lower()}"
        with assembled_path.open("wb") as destination:
            for part in parts:
                with part.open("rb") as source:
                    shutil.copyfileobj(source, destination, length=1024 * 1024)

        assembled_size = assembled_path.stat().st_size
        if assembled_size != total_size:
            raise HTTPException(
                409,
                f"Upload size mismatch: expected {total_size}, received {assembled_size}",
            )
        if assembled_size > bulk.MAX_PUBLIC_UPLOAD_BYTES:
            raise HTTPException(413, "The upload is larger than 300 MiB")

        if filename.lower().startswith("tad__") and filename.lower().endswith(".xlsx"):
            db = PostgresDatabase()
            await db.connect()
            try:
                result = await stage_targeted_tad_xlsx(
                    db,
                    assembled_path,
                    source_file=filename,
                    prepare_row=bulk._prepare_row,
                )
            finally:
                await db.close()
        else:
            with assembled_path.open("rb") as source:
                uploaded = UploadFile(file=source, filename=filename)
                result = await _stage_uploaded_file(uploaded)

        result["transport"] = {
            **(result.get("transport") or {}),
            "mode": "chunked",
            "upload_id": upload_id,
            "chunks": total_chunks,
            "bytes_received": assembled_size,
        }
        return result
    finally:
        shutil.rmtree(upload_dir, ignore_errors=True)


@bulk.router.delete("/upload-workbook/chunk/{upload_id}")
async def public_county_workbook_abort(upload_id: str):
    upload_id = _safe_upload_id(upload_id)
    shutil.rmtree(_upload_dir(upload_id), ignore_errors=True)
    return {"ok": True, "upload_id": upload_id}


def main() -> None:
    """Start InvestorFlip after upload routes have been registered."""
    import uvicorn
    from import_worker import start_import_worker
    from server import app

    start_import_worker(app)

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8000")),
    )


if __name__ == "__main__":
    main()
