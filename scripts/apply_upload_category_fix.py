"""Fail-closed repair for selected upload categories and ZIP member routing."""
from pathlib import Path

def apply(root=Path(".")):
    importer = root / "backend/bulk_import.py"
    runtime = root / "backend/upload_runtime.py"
    src = importer.read_text()
    zip_src = runtime.read_text()
    a = "    headers = {_key(k) for row in rows[:20] for k in row.keys()}\n"
    b = """    # Explicit upload type encoded by the Add screen takes precedence over heuristics.
    prefix = Path(filename).name.lower().split("__", 1)
    categories = {
        "tad": ["tad"], "taxroll": ["tax_roll"], "tax_lien": ["tax_lien"],
        "preforeclosure": ["pre_foreclosure"], "probate": ["probate"],
        "code_violation": ["code_violations"], "owner": ["owner"]
    }
    if len(prefix) == 2 and prefix[0] in categories:
        return list(categories[prefix[0]])
"""
    c = """                                member.filename,
                                zf.read(member),
                                Path(member.filename).suffix.lower(),
"""
    d = """                                (
                                    f"{filename.split('__', 1)[0]}__{Path(member.filename).name}"
                                    if filename.split("__", 1)[0] in {
                                        "tad", "taxroll", "tax_lien", "preforeclosure",
                                        "probate", "code_violation", "owner"
                                    }
                                    else member.filename
                                ),
                                zf.read(member),
                                Path(member.filename).suffix.lower(),
"""
    if b in src:
        new = src
    elif src.count(a) == 1 and "def _upload_categories(" in src:
        new = src.replace(a, a + b, 1)
    else:
        raise RuntimeError("Importer anchor not found")
    if d in zip_src:
        new_zip = zip_src
    elif zip_src.count(c) == 1:
        new_zip = zip_src.replace(c, d, 1)
    else:
        raise RuntimeError("ZIP anchor not found")
    compile(new, str(importer), "exec")
    compile(new_zip, str(runtime), "exec")
    if new != src: importer.write_text(new)
    if new_zip != zip_src: runtime.write_text(new_zip)
    print("Source routing applied")

if __name__ == "__main__":
    apply()
