"""Public high-capacity County workbook upload routes."""
from __future__ import annotations
import csv, io, re, uuid, zipfile
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Mapping
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from database import PostgresDatabase

router = APIRouter(prefix="/import/bulk", tags=["bulk-import"])
MAX_PUBLIC_UPLOAD_BYTES = 300 * 1024 * 1024
MAX_ZIP_EXPANDED_BYTES = 750 * 1024 * 1024

class BulkImportResult(BaseModel):
    batch_id: str
    total: int
    succeeded: int
    failed: int
    errors: List[Dict[str, str]]
    results: List[Dict[str, Any]]

@router.post("/mortgage-lookup", response_model=BulkImportResult)
async def bulk_mortgage_lookup(file: UploadFile = File(...)):
    content = await file.read()
    reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig")))
    if not reader.fieldnames or "address" not in reader.fieldnames:
        raise HTTPException(400, "CSV must have 'address' column")
    batch_id, results, errors, succeeded, failed = str(uuid.uuid4()), [], [], 0, 0
    for row_num, row in enumerate(reader, start=2):
        address = (row.get("address") or "").strip()
        if not address: continue
        try:
            from mortgage_lookup import full_mortgage_report
            report = await full_mortgage_report(address)
            report.update(row=row_num, notes=row.get("notes", ""))
            results.append(report); succeeded += 1
        except Exception as exc:
            errors.append({"row": str(row_num), "address": address, "error": str(exc)}); failed += 1
    return BulkImportResult(batch_id=batch_id,total=succeeded+failed,succeeded=succeeded,failed=failed,errors=errors,results=results)

@router.post("/properties")
async def bulk_import_properties(file: UploadFile = File(...), source: str = Form("csv_upload")):
    content = await file.read()
    reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig")))
    if not reader.fieldnames or "address" not in reader.fieldnames:
        raise HTTPException(400, "CSV must have 'address' column")
    db = PostgresDatabase()
    try:
        await db.connect()
        imported, errors = 0, []
        for row_num, row in enumerate(reader, start=2):
            address = (row.get("address") or "").strip()
            if not address: continue
            try:
                await db.properties.insert_one({
                    "id": str(uuid.uuid4()), "address": address, "city": row.get("city",""),
                    "state": row.get("state","TX"), "zip": row.get("zip",""),
                    "price": float(row["price"]) if row.get("price") else None,
                    "beds": int(float(row["beds"])) if row.get("beds") else None,
                    "baths": float(row["baths"]) if row.get("baths") else None,
                    "sqft": float(row["sqft"]) if row.get("sqft") else None,
                    "notes": row.get("notes",""), "source": source,
                    "imported_at": datetime.now(timezone.utc).isoformat(), "is_synthetic": False,
                }); imported += 1
            except Exception as exc: errors.append({"row":str(row_num),"error":str(exc)})
        return {"imported":imported,"errors":errors,"source":source}
    finally: await db.close()

def _key(v: Any) -> str:
    return re.sub(r"[^a-z0-9]+"," ",str(v or "").strip().lower()).strip()

def _lookup(row: Mapping[str,Any], *names: str) -> Any:
    wanted={_key(n) for n in names}
    for k,v in row.items():
        if _key(k) in wanted and v not in (None,""):
            try:
                if v != v: continue
            except Exception: pass
            return v
    return None

def _categories(filename: str, rows: List[Dict[str,Any]]) -> List[str]:
    name=_key(filename); headers={_key(k) for r in rows[:20] for k in r}
    if "probate" in name or (any("decedent" in h for h in headers) and any("applicant" in h for h in headers)): return ["probate"]
    if "preforeclosure" in name or "pre foreclosure" in name or {"county cause number","scheduled auction","foreclosure status"} & headers: return ["pre_foreclosure"]
    if ("code" in name and "violation" in name) or {"violation address","complaint type description"} & headers: return ["code_violations"]
    if "tad" in name and "taxroll" not in name and "tax roll" not in name: return ["tad"]
    tax={"tax account number","current year amount due","prior year amount due","delinquency date","adjusted levy","appraised value"}
    if "taxroll" in name or "tax roll" in name or len(headers & tax)>=2:
        out=["tax_roll"]
        if headers & {"current year amount due","prior year amount due","amount due","delinquency date"}: out.append("tax_due")
        return out
    return ["uploaded"]

def _prepare(row: Mapping[str,Any], cats: List[str]) -> Dict[str,Any]:
    out=dict(row); c=cats[0] if cats else "uploaded"
    if c=="probate":
        a=_lookup(row,"Decedent Property Address","Decedent Address")
        if a: out["address"]=a
        d=_lookup(row,"Decedent")
        if d and not _lookup(row,"owner","owner name","current owner"): out["owner"]=d
        out["listing type"]="Probate"
    elif c=="code_violations":
        a=_lookup(row,"Violation Address","Property Address","Address")
        if a: out["address"]=a
        out["listing type"]="Code Violation"
    elif c=="pre_foreclosure":
        a=_lookup(row,"Property Address","Matched Address","Situs Address","Address")
        if a: out["address"]=a
        out["listing type"]="Pre-Foreclosure"
    elif c in {"tax_roll","tax_due"}:
        a=_lookup(row,"Street Name (Property Location)","Property Location","Property Address","Situs Address","Address")
        if a: out["address"]=a
    elif c=="tad":
        a=_lookup(row,"Property Address","Situs Address","Address","Location Address")
        if a: out["address"]=a
    tad=_lookup(row,"TAD Account #","TAD Account Number","TAD Account","Appraisal District Number")
    apn=_lookup(row,"Tax ID/APN","Tax Account/APN","Tax Account APN","APN","Parcel ID","PIN")
    legal=_lookup(row,"Legal Description","Legal Description 1","Property/Legal Description")
    cause=_lookup(row,"County Cause Number","Cause Number","Cause No","Cause #")
    if tad is not None: out["tad account #"]=tad; out.setdefault("account id",tad)
    if apn is not None: out["apn"]=apn
    if legal is not None: out["legal description"]=legal
    if cause is not None: out["cause number"]=cause
    out["upload categories"]=" | ".join(cats)
    return out

async def _tag(db, ids: List[str], cats: List[str]):
    if not ids: return
    p=cats[0] if cats else "uploaded"
    fields={"has_uploaded":True,"upload_category":p,"upload_categories":cats}
    if p=="pre_foreclosure": fields.update(pre_foreclosure=True,has_pre_foreclosure=True)
    elif p=="probate": fields["has_probate"]=True
    elif p=="code_violations": fields["has_uploaded_code_violations"]=True
    elif p=="tad": fields["has_uploaded_tad"]=True
    if "tax_roll" in cats: fields["has_uploaded_tax_roll"]=True
    if "tax_due" in cats: fields["has_uploaded_tax_due"]=True
    for pid in ids: await db.properties.update_one({"id":pid},{"$set":fields})

@router.post("/upload-workbook")
async def public_county_workbook_upload(file: UploadFile = File(...)):
    """Public ADD: no admin key; CSV/XLS/XLSX/ZIP; 300 MiB; no row cap; classify + enrich."""
    from county_records_routes import _read_upload_rows
    from intake import upsert_import_records
    from importers.county_records import enrich_live_properties_from_county_records
    filename=Path(file.filename or "upload.xlsx").name; suffix=Path(filename).suffix.lower()
    if suffix not in {".csv",".xls",".xlsx",".zip"}: raise HTTPException(400,"Upload .csv, .xls, .xlsx, or .zip")
    raw=await file.read(MAX_PUBLIC_UPLOAD_BYTES+1)
    if not raw: raise HTTPException(400,"The uploaded file is empty")
    if len(raw)>MAX_PUBLIC_UPLOAD_BYTES: raise HTTPException(413,"The upload is larger than 300 MB")
    db=PostgresDatabase(); await db.connect()
    try:
        ids=[]; reports=[]; seen=[]; totals={"accepted":0,"rejected":0,"inserted":0,"updated":0}
        async def one(name,payload,ext):
            rows,sheets=_read_upload_rows(payload,ext,name); cats=_categories(name,rows); prepared=[_prepare(r,cats) for r in rows]
            rep=await upsert_import_records(db,prepared,f"User upload [{'+'.join(cats)}]: {name}")
            pids=list(dict.fromkeys(rep["property_ids"])); ids.extend(pids); await _tag(db,pids,cats)
            for c in cats:
                if c not in seen: seen.append(c)
            for k in totals: totals[k]+=int(rep.get(k,0) or 0)
            reports.append({"file":name,"status":"ok","categories":cats,"rows":rep["rows_read"],"accepted":rep["accepted"],"rejected":rep["rejected"],"inserted":rep["inserted"],"updated":rep["updated"],"sheets":sheets})
        if suffix==".zip":
            try:
                with zipfile.ZipFile(BytesIO(raw)) as z:
                    members=[m for m in z.infolist() if not m.is_dir() and Path(m.filename).suffix.lower() in {".csv",".xls",".xlsx"}]
                    if not members: raise HTTPException(400,"ZIP contains no CSV or Excel files")
                    if sum(max(0,m.file_size) for m in members)>MAX_ZIP_EXPANDED_BYTES: raise HTTPException(413,"ZIP expands beyond the 750 MB safety limit")
                    for m in members:
                        try: await one(m.filename,z.read(m),Path(m.filename).suffix.lower())
                        except Exception as exc: reports.append({"file":m.filename,"status":"error","reason":str(exc)[:300]})
            except HTTPException: raise
            except Exception as exc: raise HTTPException(400,f"Could not read ZIP: {str(exc)[:220]}") from exc
        else:
            try: await one(filename,raw,suffix)
            except Exception as exc: raise HTTPException(400,f"Could not import workbook: {str(exc)[:220]}") from exc
        unique=list(dict.fromkeys(ids))
        county=await enrich_live_properties_from_county_records(db,property_ids=unique,lookup_missing_tad=True) if unique else {"live_checked":0,"enriched":0,"tad_lookups":0,"missing":0}
        return {"ok":bool(totals["accepted"]),"filename":filename,"categories":seen,"files":reports,"rows_read":totals["accepted"]+totals["rejected"],**totals,"property_ids":unique,"enrichment":{"county":county}}
    finally: await db.close()
