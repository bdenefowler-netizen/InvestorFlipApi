import os
import unittest
from unittest.mock import patch
from importers.provider_probe import attom_by_apn, repliers_listing, tarrant_by_account, ProviderError

class ProviderProbeTests(unittest.TestCase):
    def test_attom_apn_fips(self):
        with patch.dict(os.environ, {"ATTOM_API_KEY":"dummy"}), patch("importers.provider_probe._request", return_value={"status":"ok"}) as fetch:
            self.assertEqual(attom_by_apn("123456","48439"), {"status":"ok"})
            self.assertEqual(fetch.call_args.kwargs["params"]["fips"], "48439")
            self.assertEqual(fetch.call_args.kwargs["headers"]["apikey"], "dummy")

    def test_repliers_one_listing(self):
        with patch.dict(os.environ, {"REPLIERS_API_KEY":"dummy"}), patch("importers.provider_probe._request", return_value={"mlsNumber":"AB123"}) as fetch:
            repliers_listing("AB123")
            self.assertTrue(fetch.call_args.args[0].endswith("/listings/AB123"))

    def test_tarrant_exact_account(self):
        with patch("importers.provider_probe._request", return_value={"features":[{"attributes":{"ACCOUNT":"12345678"}}]}) as fetch:
            self.assertEqual(tarrant_by_account("12345678"), [{"ACCOUNT":"12345678"}])
            self.assertEqual(fetch.call_args.kwargs["params"]["where"], "ACCOUNT='12345678'")
            self.assertEqual(fetch.call_args.kwargs["params"]["resultRecordCount"], "5")

    def test_no_sql_injection(self):
        with self.assertRaises(ValueError): tarrant_by_account("123' OR 1=1")

    def test_missing_api_key(self):
        with patch.dict(os.environ, {"ATTOM_API_KEY":""}):
            with self.assertRaises(ProviderError): attom_by_apn("123","48439")

    def test_reject_invalid_mls(self):
        with self.assertRaises(ValueError): repliers_listing("../etc/passwd")

if __name__=="__main__":
    unittest.main()
