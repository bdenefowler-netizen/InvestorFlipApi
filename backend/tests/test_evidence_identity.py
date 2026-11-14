"""Offline regression tests for conservative county evidence linking."""
import unittest
from importers.evidence_identity import account_key, match_by_account, tax_evidence


class EvidenceIdentityTests(unittest.TestCase):
    def test_account_normalization(self):
        self.assertEqual(account_key("000-123 45"), "12345")
        self.assertEqual(account_key("A-001"), "A001")
        self.assertEqual(account_key(None), "")

    def test_current_balance_is_not_delinquency_or_lien(self):
        evidence = tax_evidence({"current_amount_due": 800, "prior_amount_due": 0})
        self.assertTrue(evidence["has_balance_due"])
        self.assertFalse(evidence["tax_delinquent"])
        self.assertFalse(evidence["tax_lien_verified"])

    def test_prior_balance_does_not_invent_lien(self):
        evidence = tax_evidence({"prior_amount_due": 300})
        self.assertTrue(evidence["has_prior_balance_due"])
        self.assertFalse(evidence["tax_lien_verified"])

    def test_explicit_delinquency_date(self):
        self.assertTrue(tax_evidence({"delinquency_date": "2026-02-01"})["tax_delinquent"])

    def test_account_only_no_address_fallback(self):
        result = match_by_account({"address": "100 MAIN ST"}, [{"id": "1", "address": "100 MAIN ST"}])
        self.assertEqual(result["status"], "unmatched")

    def test_unique_account_link(self):
        result = match_by_account({"account_id": "000123", "county": "Tarrant"}, [
            {"id": "property-1", "account_id": "123", "county": "Tarrant"}
        ])
        self.assertEqual(result["property_id"], "property-1")

    def test_duplicate_account_refused(self):
        result = match_by_account({"account_id": "123"}, [
            {"id": "1", "account_id": "123"},
            {"id": "2", "account_id": "00123"},
        ])
        self.assertEqual(result["status"], "ambiguous")

    def test_county_conflict_refused(self):
        result = match_by_account({"account_id": "123", "county": "Tarrant"}, [
            {"id": "1", "account_id": "123", "county": "Dallas"}
        ])
        self.assertEqual(result["status"], "conflict")


if __name__ == "__main__":
    unittest.main()
