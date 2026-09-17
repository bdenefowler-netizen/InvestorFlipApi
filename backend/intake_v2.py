"""Deterministic source-explicit intake for County uploads."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Mapping

from address_utils import canonical_street_key
from database import PostgresDatabase
import intake as legacy

TAD = ("tad account #","tad account number","tad account","account id","account_id","prop_id","property id")
APN = ("apn","parcel id","parcel","tax id/apn","tax account/apn","tax account apn","pin")
LEGAL = ("legal description","legal description 1","property/legal description","legal desc","legal")
ADDRESS = ("property address","situs address","site address","violation address","decedent property address","matched address","location address","address")
OWNER = ("owner","owner name","current owner","property owner","seller")
PHONE = ("phone","phone number","owner phone","seller phone")
EMAIL = ("email","owner email","seller email")
MAILING = ("owner mailing address","tad owner mailing address","mailing address","owner address")

def _key(v: Any) -> str:
    return re.sub(r"[^a-z0-9]+"," ",str(v or "").strip().lower()).strip()

def _text(v: Any) -> str:
    v = legacy._clean(v)
    return re.sub(r"\s+"," ",str(v).strip()) if v is not None else ""

def _get(row: Mapping[str,Any], names) -> str:
    wanted = {_key(x) for x in names}
    for k,v in row.items():
        if _key(k) in wanted:
            t = _text(v)
            if t:
                return t
    return ""

def _legal(v: Any) -> str:
    return re.sub(r"[^A-Z0-9]+"," ",_text(v).upper()).strip()

def _owner(v: Any) -> str:
    parts = re.sub(r"[^A-Z0-9]+"," ",_text(v).upper()).split()
    return " ".join(p for p in parts if p not in {"LLC","LTD","INC","LP","LLP"})

def _category(source: str) -> str:
    s = _key(source)
    if "pre foreclosure" in s or "preforeclosure" in s: return "pre_foreclosure"
    if "code violation" in s: return "code_violations"
    if "probate" in s: return "probate"
    if "owner" in s: return "owner"
    if "taxroll" in s or "tax roll" in s or "tax due" in s: return "tax"
    if re.search(r"(^|\s)tad(\s|$)", s): return "tad"
    return "uploaded"

def _ident(row: Mapping[str,Any]) -> Dict[str,str]:
    a = _get(row, ADDRESS); l = _get(row, LEGAL)
    return {
        "address": a, "address_key": canonical_street_key(a) if a else "",
        "tad": _get(row,TAD), "apn": _get(row,APN),
        "legal": l, "legal_key": _legal(l),
        "owner": _get(row,OWNER), "phone": _get(row,PHONE),
        "email": _get(row,EMAIL), "mailing": _get(row,MAILING),
    }

def _apply_source(record: Dict[str,Any], category: str) -> None:
    record["source_category"] = category
    record["is_live_listing"] = False
    for k in ("pre_foreclosure","has_pre_foreclosure","has_probate","has_uploaded_code_violations"):
        record.pop(k,None)
    if category == "pre_foreclosure":
        record.update(listing_type="Pre-Foreclosure",pre_foreclosure=True,has_pre_foreclosure=True)
    elif category == "probate":
        record.update(listing_type="Probate",has_probate=True)
    elif category == "code_violations":
        record.update(listing_type="Code Violation",has_uploaded_code_violations=True)
    elif record.get("listing_type") in {"Foreclosure","Pre-Foreclosure","Probate","Code Violation"}:
        record["listing_type"] = None

def normalize_import_row(row: Mapping[str,Any], source_name: str, row_number: int) -> Dict[str,Any]:
    i = _ident(row); cat = _category(source_name)
    record = None
    if i["address_key"]:
        try:
            record = legacy.normalize_import_row(row, source_name, row_number)
        except Exception:
            pass
    if not record:
        now = datetime.now(timezone.utc).isoformat()
        record = {
            "id": legacy._stable_uuid(f"{source_name}:{row_number}:{i['tad'] or i['apn'] or i['address_key'] or i['legal_key']}"),
            "address_key": i["address_key"], "situs_address": i["address"],
            "city":"","state":"TX","zip":"","county":"Tarrant",
            "data_source":source_name,"listing_sources":[source_name],
            "raw_import_row":{str(k):legacy._clean(v) for k,v in row.items()},
            "created_at":now,"updated_at":now,
        }
    if i["tad"]: record["account_id"] = i["tad"]
    if i["apn"]: record["apn"] = record["parcel_id"] = i["apn"]
    if i["legal"]: record["legal_description"] = i["legal"]; record["legal_key"] = i["legal_key"]
    if i["owner"]: record["owner_name"] = i["owner"]
    if i["mailing"]: record["owner_mailing_address"] = i["mailing"]
    if i["phone"]: record["owner_phone"] = i["phone"]
    if i["email"]: record["owner_email"] = i["email"]
    _apply_source(record, cat)
    if cat == "tad": record["tad_verified"] = bool(i["tad"])
    if cat == "owner": record["owner_upload"] = True
    return record

async def _find(db: PostgresDatabase, r: Mapping[str,Any]):
    if r.get("account_id"):
        x = await db.properties.find_one({"account_id":r["account_id"]},{"_id":0})
        if x: return x,"tad_account_exact",100
    apn = r.get("apn") or r.get("parcel_id")
    if apn:
        for f in ("apn","parcel_id","tax_account_id"):
            x = await db.properties.find_one({f:apn},{"_id":0})
            if x: return x,"apn_exact",100
    if r.get("address_key"):
        q={"address_key":r["address_key"]}
        if r.get("zip"): q["zip"]=r["zip"]
        x=await db.properties.find_one(q,{"_id":0})
        if x:
            same_legal = _legal(r.get("legal_description")) and _legal(r.get("legal_description")) == _legal(x.get("legal_description"))
            return x,("address_and_legal" if same_legal else "address_exact"),(98 if same_legal else 95)
    if r.get("legal_key"):
        x=await db.properties.find_one({"legal_key":r["legal_key"]},{"_id":0})
        if x: return x,"legal_exact",90
        legal = _text(r.get("legal_description"))
        if legal:
            x=await db.properties.find_one({"legal_description":{"$regex":f"^{re.escape(legal)}$","$options":"i"}},{"_id":0})
            if x: return x,"legal_exact",90
    return None,"unmatched",0

def _meaningful(v): return v not in (None,"",[],{})

async def upsert_import_records(database: PostgresDatabase, rows: Iterable[Mapping[str,Any]], source_name: str) -> Dict[str,Any]:
    accepted=[]; rejected=[]
    for n,row in enumerate(rows,start=2):
        r=normalize_import_row(row,source_name,n)
        if any(r.get(f) for f in ("account_id","apn","parcel_id","address_key","legal_key")):
            accepted.append(r)
        else:
            rejected.append({"row":n,"reason":"missing TAD/APN/address/legal property identity"})

    ids=[]; inserted=updated=unmatched=0; matched_by={}
    for incoming in accepted:
        existing,method,confidence=await _find(database,incoming)
        if existing:
            merged=dict(existing)
            owner_upload=incoming.get("source_category")=="owner"
            owner_ok=bool(owner_upload and _owner(incoming.get("owner_name")) and _owner(incoming.get("owner_name"))==_owner(existing.get("owner_name")))
            for k,v in incoming.items():
                if k in {"id","created_at","listing_sources"} or not _meaningful(v): continue
                if owner_upload and k in {"owner_name","owner_phone","owner_email","owner_mailing_address"} and existing.get("owner_name") and not owner_ok:
                    continue
                merged[k]=v
            merged["id"]=existing.get("id") or incoming["id"]
            merged["listing_sources"]=list(dict.fromkeys((existing.get("listing_sources") or [])+(incoming.get("listing_sources") or [])))
            merged["match_status"]="matched"; merged["match_method"]=method; merged["match_confidence"]=confidence
            merged["property_match_verified"]=confidence==100
            if method=="tad_account_exact": merged["tad_verified"]=True
            if owner_upload:
                if owner_ok:
                    merged["owner_verified"]=True; merged["owner_verification_status"]="verified_against_tad_owner"
                    for f in ("owner_phone","owner_email","owner_mailing_address"):
                        if incoming.get(f): merged[f]=incoming[f]
                elif existing.get("owner_name") and incoming.get("owner_name"):
                    merged["owner_verification_status"]="name_mismatch"
                    merged["owner_contact_candidate"]={"name":incoming.get("owner_name"),"phone":incoming.get("owner_phone"),"email":incoming.get("owner_email"),"mailing_address":incoming.get("owner_mailing_address")}
                else:
                    merged["owner_verification_status"]="unverified_no_tad_owner"
            r=merged; updated+=1; matched_by[method]=matched_by.get(method,0)+1
        else:
            r=dict(incoming)
            r.update(match_status="unmatched",match_method="unmatched",match_confidence=0,property_match_verified=False)
            if r.get("source_category")=="owner": r["owner_verification_status"]="unmatched_property"
            inserted+=1; unmatched+=1
        r["updated_at"]=datetime.now(timezone.utc).isoformat()
        await database.properties.update_one({"id":r["id"]},{"$set":r},upsert=True)
        ids.append(r["id"])

    return {
        "rows_read":len(accepted)+len(rejected),"accepted":len(accepted),"rejected":len(rejected),
        "duplicates_merged":0,"inserted":inserted,"updated":updated,"matched_by":matched_by,
        "unmatched":unmatched,"property_ids":ids,"rejections":rejected[:25],
    }
