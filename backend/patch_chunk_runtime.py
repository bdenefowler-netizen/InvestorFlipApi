"""Add chunked transport endpoints to the already-patched public uploader."""
from pathlib import Path
path=Path("bulk_import.py")
text=path.read_text()
if '@router.post("/upload-workbook/chunk")' in text:
    print("Chunk upload routes already present")
    raise SystemExit(0)
append=r"""
from fastapi import Form
import shutil
import tempfile

_CHUNK_ROOT = Path(tempfile.gettempdir()) / "investorflip-chunk-uploads"
_CHUNK_ROOT.mkdir(parents=True, exist_ok=True)
_MAX_CHUNK_BYTES = 10 * 1024 * 1024
_MAX_CHUNKS = 2000

def _safe_chunk_upload_id(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "", str(value or ""))
    if not cleaned or len(cleaned) > 120:
        raise HTTPException(400, "Invalid upload id")
    return cleaned

def _safe_chunk_filename(value: str) -> str:
    name = Path(value or "county-import.xlsx").name
    if Path(name).suffix.lower() not in {".csv", ".xls", ".xlsx", ".zip"}:
        raise HTTPException(400, "Upload .csv, .xls, .xlsx, or .zip")
    return name

@router.post("/upload-workbook/chunk")
async def public_county_workbook_chunk(
    upload_id: str = Form(...),
    chunk_index: int = Form(...),
    total_chunks: int = Form(...),
    filename: str = Form(...),
    total_size: int = Form(...),
    chunk: UploadFile = File(...),
):
    upload_id = _safe_chunk_upload_id(upload_id)
    filename = _safe_chunk_filename(filename)
    if total_chunks < 1 or total_chunks > _MAX_CHUNKS:
        raise HTTPException(400, "Invalid total chunk count")
    if chunk_index < 0 or chunk_index >= total_chunks:
        raise HTTPException(400, "Invalid chunk index")
    if total_size < 1 or total_size > MAX_PUBLIC_UPLOAD_BYTES:
        raise HTTPException(413, "The upload is larger than 300 MiB")
    payload = await chunk.read(_MAX_CHUNK_BYTES + 1)
    if not payload:
        raise HTTPException(400, "Chunk is empty")
    if len(payload) > _MAX_CHUNK_BYTES:
        raise HTTPException(413, "Chunk exceeds 10 MiB")
    upload_dir = _CHUNK_ROOT / upload_id
    upload_dir.mkdir(parents=True, exist_ok=True)
    (upload_dir / f"{chunk_index:06d}.part").write_bytes(payload)
    return {"ok": True, "upload_id": upload_id, "chunk_index": chunk_index, "received_bytes": len(payload), "total_chunks": total_chunks, "filename": filename}

@router.post("/upload-workbook/complete")
async def public_county_workbook_complete(
    upload_id: str = Form(...),
    total_chunks: int = Form(...),
    filename: str = Form(...),
    total_size: int = Form(...),
):
    upload_id = _safe_chunk_upload_id(upload_id)
    filename = _safe_chunk_filename(filename)
    if total_chunks < 1 or total_chunks > _MAX_CHUNKS:
        raise HTTPException(400, "Invalid total chunk count")
    if total_size < 1 or total_size > MAX_PUBLIC_UPLOAD_BYTES:
        raise HTTPException(413, "The upload is larger than 300 MiB")
    upload_dir = _CHUNK_ROOT / upload_id
    if not upload_dir.exists():
        raise HTTPException(404, "Chunk upload not found")
    try:
        parts = [upload_dir / f"{index:06d}.part" for index in range(total_chunks)]
        missing = [index for index, part in enumerate(parts) if not part.exists()]
        if missing:
            raise HTTPException(409, f"Upload incomplete; missing {len(missing)} chunk(s)")
        assembled_path = upload_dir / "assembled.upload"
        with assembled_path.open("wb") as destination:
            for part in parts:
                with part.open("rb") as source:
                    shutil.copyfileobj(source, destination, length=1024 * 1024)
        assembled_size = assembled_path.stat().st_size
        if assembled_size != total_size:
            raise HTTPException(409, f"Upload size mismatch: expected {total_size}, received {assembled_size}")
        if assembled_size > MAX_PUBLIC_UPLOAD_BYTES:
            raise HTTPException(413, "The upload is larger than 300 MiB")
        with assembled_path.open("rb") as source:
            uploaded = UploadFile(file=source, filename=filename)
            result = await public_county_workbook_upload(uploaded)
        result["transport"] = {"mode": "chunked", "upload_id": upload_id, "chunks": total_chunks, "bytes_received": assembled_size}
        return result
    finally:
        shutil.rmtree(upload_dir, ignore_errors=True)

@router.delete("/upload-workbook/chunk/{upload_id}")
async def public_county_workbook_abort(upload_id: str):
    upload_id = _safe_chunk_upload_id(upload_id)
    shutil.rmtree(_CHUNK_ROOT / upload_id, ignore_errors=True)
    return {"ok": True, "upload_id": upload_id}
"""
patched=text+append
compile(patched,str(path),"exec")
path.write_text(patched)
print("Chunk transport patch installed")
