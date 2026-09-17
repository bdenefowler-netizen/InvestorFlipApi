"""Deterministic source-explicit intake for County uploads."""
from __future__ import annotations
import re, uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Mapping
from address_utils import canonical_street_key
from database import PostgresDatabase
import intake as legacy

TAD=("tad account #","tad account number","tad account","account id","account_id","prop_id","property id","appraisal district number")
APN=("apn","parcel id","parcel","tax id/apn","tax account/apn","tax account apn","pin")
LEGAL=("legal description","legal description 1","property/legal description","legal desc","legal")
ADDR=("property address","situs address","site address","violation address","decedent property address","matched address","location address","address")
OWNER=("owner","owner name","current owner","property owner","seller")
PHONE=("phone","phone number","owner phone","seller phone")
EMAIL=("email","owner email","seller email")
MAIL=("owner mailing address","tad owner mailing address","mailing address","owner address")

def _key(v:Any)->str:
    return re.sub(r"[^a-z0-9]+"," ",str(v or "").strip().lower()).strip()

def _text(v:Any)->str:
    v=legacy._clean(v)
    return re.sub(r"\s+"," ",str(v).strip()) if v is not None else ""

def _get(row:Mapping[str,Any], names)->str:
    wanted={_key(n) for n in names}
    for k,v in row.items():
        if _key(k) in wanted:
            t=_text(v)
            if t: return t
    return ""

def _legal(v:Any)->str:
    return re.sub(r"[^A-Z0-9]+"," ",_text(v).upper()).strip()

def _owner(v:Any)->str:
    parts=re.sub(r"[^A-Z0-9]+"," ",_text(v).upper()).split()
    return " ".join(p for p in parts if p not in {"LLC","LTD","INC","LP","LLP","PLLC"})

def _category(source:str)->str:
    s=_key(source)
    if "pre foreclosure" in s or "preforeclosure" in s: return "pre_foreclosure"
    if "code violation" in s: return "code_violations"
    if "probate" in s: return "probate"
    if "owner" in s: return "owner"
    if "taxroll" in s or "tax roll" in s or "tax due" in s or re.search(r"(^|\s)tax(\s|$)",s): return "tax"
    if re.search(r"(^|\s)tad(\s|$)",s): return "tad"
    return "uploaded"

def _ident(row:Mapping[str,Any])->Dict[str,str]:
    a=_get(row,ADDR); l=_get(row,LEGAL)
    return {
        "address":a, "address_key":canonical_street_key(a) if a else "",
        "tad":_get(row,TAD), "apn":_get(row,APN),
        "legal":l, "legal_key":_legal(l),
        "owner":_get(row,OWNER), "phone":_get(row,PHONE),
        "email":_get(row,EMAIL), "mailing":_get(row,MAIL),
    }

def _stable(i:Mapping[str,str], source:str, rownum:int)->str:
    seed=(f"tad:{i['tad'].upper()}" if i["tad"] else
          f"apn:{i['apn'].upper()}" if i["apn"] else
          f"addr:{i['address_key']}" if i["address_key"] else
          f"legal:{i['legal_key']}" if i["legal_key"] else f"row:{source}:{rownum}")
    return str(uuid.uuid5(uuid.NAMESPACE_URL,"investorflip:"+seed))

def _apply_source(r:Dict[str,Any], cat:str)->None:
    cats=list(r.get("source_categories") or [])
    if cat not in cats: cats.append(cat)
    r["source_categories"]=cats
    r["source_category"]=cat
    r["is_live_listing"]=False
    neutral=cat in {"tad","tax","owner","uploaded"}
    if neutral:
        for k in ("pre_foreclosure","has_pre_foreclosure","has_probate","has_uploaded_code_violations"):
            r.pop(k,None)
        if str(r.get("listing_type") or "").lower() in {"foreclosure","pre-foreclosure","probate","code violation"}:
            r["listing_type"]=None
    if cat=="pre_foreclosure":
        r.update(pre_foreclosure=True,has_pre_foreclosure=True,listing_type="Pre-Foreclosure")
    elif cat=="probate":
        r.update(has_probate=True,listing_type="Probate")
    elif cat=="code_violations":
        r["has_uploaded_code_violations"]=True
    elif cat=="tad":
        r["has_uploaded_tad"]=True
    elif cat=="tax":
        r["has_uploaded_tax"]=True
    elif cat=="owner":
        r["owner_upload"]=True

def normalize_import_row(row:Mapping[str,Any], source_name:str, row_number:int)->Dict[str,Any]:
    i=_ident(row); cat=_category(source_name); r=None
    if i["address_key"]:
        try: r=legacy.normalize_import_row(row,source_name,row_number)
        except Exception: r=None
    now=datetime.now(timezone.utc).isoformat()
    if not r:
        r={"id":_stable(i,source_name,row_number),"address_key":i["address_key"],
           "situs_address":i["address"],"city":"","state":"TX","zip":"","county":"Tarrant",
           "data_source":source_name,"listing_sources":[source_name],
           "raw_import_row":{str(k):legacy._clean(v) for k,v in row.items()},
           "created_at":now,"updated_at":now}
    if i["tad"]: r["account_id"]=i["tad"]
    if i["apn"]: r["apn"]=r["parcel_id"]=i["apn"]
    if i["legal"]: r["legal_description"]=i["legal"]; r["legal_key"]=i["legal_key"]
    if i["owner"]: r["owner_name"]=i["owner"]
    if i["mailing"]: r["owner_mailing_address"]=i["mailing"]
    if i["phone"]: r["owner_phone"]=i["phone"]
    if i["email"]: r["owner_email"]=i["email"]
    _apply_source(r,cat)
    if cat=="tad" and i["tad"]: r["tad_verified"]=True
    r["updated_at"]=now
    return r

async def _find(db:PostgresDatabase,r:Mapping[str,Any]):
    tad=_text(r.get("account_id"))
    if tad:
        x=await db.properties.find_one({"account_id":tad},{"_id":0})
        if x: return x,"tad_account_exact",100
    apn=_text(r.get("apn") or r.get("parcel_id"))
    if apn:
        for f in ("apn","parcel_id","tax_account_id"):
            x=await db.properties.find_one({f:apn},{"_id":0})
            if x: return x,"apn_exact",100
    ak=_text(r.get("address_key"))
    if ak:
        q={"address_key":ak}
        if r.get("zip"): q["zip"]=r["zip"]
        x=await db.properties.find_one(q,{"_id":0})
        if x:
            same=_legal(r.get("legal_description")) and _legal(r.get("legal_description"))==_legal(x.get("legal_description"))
            return x,("address_and_legal" if same else "address_exact"),(98 if same else 95)
    # Compatibility fallback for records created before address_key existed.
    address=_text(r.get("situs_address"))
    if address:
        street=address.split(",",1)[0].strip()
        q={"situs_address":{"$regex":f"^{re.escape(street)}(?:,|\\s|$)","$options":"i"}}
        if r.get("zip"): q["zip"]=r["zip"]
        x=await db.properties.find_one(q,{"_id":0})
        if x: return x,"address_normalized",94
    lk=_legal(r.get("legal_description"))
    if lk:
        x=await db.properties.find_one({"legal_key":lk},{"_id":0})
        if x: return x,"legal_exact",90
        x=await db.properties.find_one({"legal_description":{"$regex":f"^{re.escape(_text(r.get('legal_description')))}$","$options":"i"}},{"_id":0})
        if x: return x,"legal_exact",90
    return None,"unmatched",0

def _meaningful(v:Any)->bool:
    return v not in (None,"",[],{},())

def _union(a,b):
    out=[]
    for v in list(a or [])+list(b or []):
        if v and v not in out: out.append(v)
    return out

def _merge(existing:Dict[str,Any],incoming:Dict[str,Any],method:str,confidence:int)->Dict[str,Any]:
    out=dict(existing); cat=incoming.get("source_category"); owner_upload=cat=="owner"
    inc_owner=_text(incoming.get("owner_name")); old_owner=_text(existing.get("owner_name"))
    owner_ok=bool(owner_upload and inc_owner and old_owner and _owner(inc_owner)==_owner(old_owner))
    protected={"owner_name","owner_phone","owner_email","owner_mailing_address"}
    for k,v in incoming.items():
        if k in {"id","created_at","listing_sources","source_categories"} or not _meaningful(v): continue
        if owner_upload and k in protected and old_owner and not owner_ok: continue
        if cat in {"tad","tax","owner","uploaded"} and k in {"listing_type","listing_status"}: continue
        out[k]=v
    out["id"]=existing.get("id") or incoming["id"]
    out["created_at"]=existing.get("created_at") or incoming.get("created_at")
    out["listing_sources"]=_union(existing.get("listing_sources"),incoming.get("listing_sources"))
    out["source_categories"]=_union(existing.get("source_categories"),incoming.get("source_categories"))
    out.update(match_status="matched",match_method=method,match_confidence=confidence,
               property_match_verified=confidence==100,updated_at=datetime.now(timezone.utc).isoformat())
    if method=="tad_account_exact": out["tad_verified"]=True
    if owner_upload:
        if owner_ok:
            out["owner_verified"]=True; out["owner_verification_status"]="verified_against_tad_owner"
            for f in ("owner_phone","owner_email","owner_mailing_address"):
                if incoming.get(f): out[f]=incoming[f]
        elif old_owner and inc_owner:
            out["owner_verification_status"]="name_mismatch"
            out["owner_contact_candidate"]={"name":inc_owner,"phone":incoming.get("owner_phone"),"email":incoming.get("owner_email"),"mailing_address":incoming.get("owner_mailing_address")}
        else:
            out["owner_verification_status"]="unverified_no_tad_owner"
    return out

def _dedupe_key(r:Mapping[str,Any])->str:
    if r.get("account_id"): return "tad:"+_text(r["account_id"]).upper()
    if r.get("apn") or r.get("parcel_id"): return "apn:"+_text(r.get("apn") or r.get("parcel_id")).upper()
    if r.get("address_key"): return f"addr:{r['address_key']}:{_text(r.get('zip'))}"
    if r.get("legal_key"): return "legal:"+str(r["legal_key"])
    return "id:"+str(r.get("id"))

async def upsert_import_records(database:PostgresDatabase, rows:Iterable[Mapping[str,Any]], source_name:str)->Dict[str,Any]:
    accepted=[]; rejected=[]
    for n,row in enumerate(rows,start=2):
        r=normalize_import_row(row,source_name,n)
        if any(r.get(f) for f in ("account_id","apn","parcel_id","address_key","legal_key")): accepted.append(r)
        else: rejected.append({"row":n,"reason":"missing TAD/APN/address/legal property identity"})
    unique={}; duplicates=0
    for r in accepted:
        k=_dedupe_key(r)
        if k in unique:
            base=unique[k]
            for f,v in r.items():
                if _meaningful(v) and f not in {"id","created_at"}: base[f]=v
            base["listing_sources"]=_union(base.get("listing_sources"),r.get("listing_sources"))
            base["source_categories"]=_union(base.get("source_categories"),i.get("source_categories"))
            duplicates+=1
        else: unique[k]=r
    ids=[]; inserted=updated=unmatched=0; matched_by={}
    for incoming in unique.values():
        existing,method,confidence=await _find(database,incoming)
        if existing:
            record=_merge(existing,incoming,method,confidence); updated+=1
            matched_by[method]=matched_by.get(method,0)+1
        else:
            record=dict(incoming); record.update(match_status="unmatched",match_method="unmatched",match_confidence=0,property_match_verified=False)
            if record.get("source_category")=="owner": record["owner_verification_status"]="unmatched_property"
            inserted+=1; unmatched+=1
        await database.properties.update_one({"id":record["id"]},{"$set":record},upsert=True)
        ids.append(record["id"])
    return {"rows_read":len(accepted)+len(rejected),"accepted":len(unique),"rejected":len(rejected),
            "duplicates_merged":duplicates,"inserted":inserted,"updated":updated,"matched_by":matched_by,
            "unmatched":unmatched,"property_ids":ids,"rejections":rejected[:25]}
