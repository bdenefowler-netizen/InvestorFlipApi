import unittest
from diagnostics.property_link_audit import account_key, audit_rows

class LinkAuditTests(unittest.TestCase):
    def test_normalization(self):
        self.assertEqual(account_key("000-123 450"), "123450")
        self.assertEqual(account_key(" A-001 "), "A001")
        self.assertEqual(account_key(None), "")

    def test_matching(self):
        county = [{"account_id": "000123", "has_tad": True, "has_tax_roll": True}]
        props = [{"account_id": "123"}, {"account_id": "777"}, {}]
        result = audit_rows(county, props, [])
        self.assertEqual(result["county_with_both"], 1)
        self.assertEqual(result["property_exact_account_county_match"], 1)
        self.assertEqual(result["properties_without_account"], 1)

    def test_ambiguous(self):
        county = [{"account_id": "000123"}, {"account_id": "123"}]
        result = audit_rows(county, [{"account_id": "123"}], [])
        self.assertEqual(result["property_ambiguous_account"], 1)
        self.assertNotIn("property_exact_account_county_match", result)
