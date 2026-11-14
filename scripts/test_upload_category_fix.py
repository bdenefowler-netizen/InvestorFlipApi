import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from apply_upload_category_fix import apply

SNIPPET = """from pathlib import Path
from typing import Any, Dict, List
def _key(value): return str(value).lower()
def _upload_categories(filename: str, rows: List[Dict[str, Any]]) -> List[str]:
    name = _key(filename)
    headers = {_key(k) for row in rows[:20] for k in row.keys()}
    if "probate" in name: return ["probate"]
    return ["uploaded"]
"""

ZIP_SNIPPET = """from pathlib import Path
async def import_one(name, payload, ext):
    pass
async def run(member, zf):
    await import_one(
                                member.filename,
                                zf.read(member),
                                Path(member.filename).suffix.lower(),
    )
"""

class UploadCategoryTests(unittest.TestCase):
    def test_patching_and_classification(self):
        with TemporaryDirectory() as temp:
            root=Path(temp); (root/"backend").mkdir()
            target=root/"backend"/"bulk_import.py"
            target.write_text(SNIPPET)
            (root/"backend"/"upload_runtime.py").write_text(ZIP_SNIPPET)
            apply(root)
            scope={}
            exec(target.read_text(),scope)
            cases={
                "owner__contacts.xlsx":"owner",
                "tax_lien__sale.xlsx":"tax_lien",
                "preforeclosure__auctions.csv":"pre_foreclosure",
                "code_violation__cases.xlsx":"code_violations",
                "probate__cases.xlsx":"probate",
                "tad__properties.xlsx":"tad",
                "taxroll__receivables.xlsx":"tax_roll",
            }
            for filename, expected in cases.items():
                with self.subTest(filename=filename):
                    self.assertEqual(scope["_upload_categories"](filename,[{"Some Column":"data"}])[0],expected)
            apply(root)
            self.assertIn("filename.split", (root/"backend"/"upload_runtime.py").read_text())
    def test_drift_fails_closed(self):
        with TemporaryDirectory() as temp:
            root=Path(temp); (root/"backend").mkdir()
            target=root/"backend"/"bulk_import.py"
            target.write_text("unrelated code")
            (root/"backend"/"upload_runtime.py").write_text(ZIP_SNIPPET)
            with self.assertRaises(RuntimeError): apply(root)
            self.assertEqual(target.read_text(),"unrelated code")

if __name__=="__main__":
    unittest.main()
