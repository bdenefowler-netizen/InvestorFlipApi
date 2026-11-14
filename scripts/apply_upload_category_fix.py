"""Apply selected upload source routing and preserve ZIP member category."""
from pathlib import Path
import re

def apply(root=Path(".")):
    p=root/"backend/bulk_import.py"
    q=root/"backend/upload_runtime.py"
    a=p.read_text()
    b=q.read_text()
    insert="""    selected = Path(filename).name.lower().split("__", 1)
    categories = {"tad": ["tad"], "taxroll": ["tax_roll"],
                  "tax_lien": ["tax_lien"], "preforeclosure": ["pre_foreclosure"],
                  "probate": ["probate"], "code_violation": ["code_violations"],
                  "owner": ["owner"]}
    if len(selected) == 2 and selected[0] in categories:
        return categories[selected[0]]
"""
    marker="    headers = {_key(k) for row in rows[:20] for k in row.keys()}\n"
    if insert.strip() not in a:
        if a.count(marker)!=1: raise RuntimeError("Classifier anchor not found")
        a=a.replace(marker,marker+insert,1)
    helper="""def _zip_upload_name(outer, member):
    prefix = Path(outer).name.lower().split("__", 1)
    allowed = {"tad", "taxroll", "tax_lien", "preforeclosure",
               "probate", "code_violation", "owner"}
    return (f"{prefix[0]}__{Path(member).name}"
            if len(prefix)==2 and prefix[0] in allowed else member)


"""
    if "def _zip_upload_name(" not in b:
        anchor="async def _stage_uploaded_file("
        if b.count(anchor)!=1: raise RuntimeError("ZIP function anchor not found")
        b=b.replace(anchor,helper+anchor,1)
    pattern=r"(?m)^(\s*)member\.filename,\n(\s*)(archive|zf)\.read\(member\),"
    if "_zip_upload_name(filename, member.filename)," not in b:
        b,n=re.subn(pattern,lambda m: (
            m[1]+"_zip_upload_name(filename, member.filename),\n"+
            m[2]+m[3]+".read(member),"),b)
        if n!=1: raise RuntimeError(f"ZIP call anchor count: {n}")
    compile(a,str(p),"exec")
    compile(b,str(q),"exec")
    if a!=p.read_text(): p.write_text(a)
    if b!=q.read_text(): q.write_text(b)
    print("Upload categories and ZIP routing patched or already applied.")

if __name__=="__main__":
    apply()
