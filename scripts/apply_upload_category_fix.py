"""Fix category selection for direct, prefixed InvestorFlip uploads (offline patch).
Run from repo root: python scripts/apply_upload_category_fix.py
"""
from pathlib import Path

def apply(root=Path(".")):
    path = root / "backend" / "bulk_import.py"
    original = path.read_text(encoding="utf-8")
    anchor = """def _upload_categories(filename: str, rows: List[Dict[str, Any]]) -> List[str]:
    name = _key(filename)
    headers = {_key(k) for row in rows[:20] for k in row.keys()}
"""
    replacement = """def _upload_categories(filename: str, rows: List[Dict[str, Any]]) -> List[str]:
    name = _key(filename)
    headers = {_key(k) for row in rows[:20] for k in row.keys()}
    # Source selection in ADD is encoded in the client-side filename prefix.
    # Honor the user's selection before guessing based on headers.
    source_prefixes = {
        "tad": ["tad"],
        "taxroll": ["tax_roll"],
        "tax_lien": ["tax_lien"],
        "preforeclosure": ["pre_foreclosure"],
        "probate": ["probate"],
        "code_violation": ["code_violations"],
        "owner": ["owner"],
    }
    parts = Path(filename).name.lower().split("__", 1)
    if len(parts) == 2 and parts[0] in source_prefixes:
        categories = list(source_prefixes[parts[0]])
        if parts[0] == "taxroll" and headers & {
            "current year amount due", "prior year amount due",
            "amount due", "delinquency date"
        }:
            categories.append("tax_due")
        return categories
"""
    runtime_path = root / "backend" / "upload_runtime.py"
    runtime_original = runtime_path.read_text(encoding="utf-8")
    zip_anchor = """                                member.filename,
                                zf.read(member),
                                Path(member.filename).suffix.lower(),
"""
    zip_replacement = """                                (
                                    f"{filename.split('__', 1)[0]}__{Path(member.filename).name}"
                                    if filename.split("__", 1)[0] in {
                                        "tad", "taxroll", "tax_lien", "preforeclosure",
                                     "probate", "code_violation", "owner"
                                    }
                                    else member.filename
                               zf.read(member),
                                Path(member.filename).suffix.lower(),
"""
    if replacement in original:
        updated = original
    elif original.count(anchor) == 1:
        updated = original.replace(anchor, replacement, 1)
    else:
        raise RuntimeError("Classifier anchor changed; no changes made")
    if zip_replacement in runtime_original:
        runtime_updated = runtime_original
    elif runtime_original.count(zip_anchor) == 1:
        runtime_updated = runtime_original.replace(zip_anchor, zip_replacement, 1)
    else:
        raise RuntimeError("ZIP member anchor changed; no changes made")
    compile(updated, str(path), "exec")
    compile(runtime_updated, str(runtime_path), "exec")
    # Both files must pass anchor checks and compilation before either is written.
    if updated != original:
        path.write_text(updated, encoding="utf-8")
    if runtime_updated != runtime_original:
        runtime_path.write_text(runtime_updated, encoding="utf-8")
    print("Upload source and ZIP routing: patched or already applied")

if __name__ == "__main__":
    apply()
