import os
import unittest
from unittest.mock import patch
from importers.provider_probe import attom_by_address, repliers_listing, tarrant_by_account, ProviderError

class ProbeTests(unittest.TestCase):
    def test_attom_fields(self):
        with patch.dict(os.environ, {"ATTOM_API_KEY": "fake"}), patch("importers.provider_probe._get", return_value={}) as request:
            attom_by_address("1 Main St", "Fort Worth, TX")
            self.assertEqual(request.call_args.args[1], {"address1": "1 Main St", "address2": "Fort Worth, TX"})

    def test_repliers(self):
        with patch("importers.provider_probe._get", return_value={}) as request:
            repliers_listing("ABC123")
            self.assertTrue(request.call_args.args[0].endswith("/listings/ABC123"))

    def test_tarrant(self):
        with patch("importers.provider_probe._get", return_value={"features": [{"attributes": {"ACCOUNT": "123"}}]}) as request:
            self.assertEqual(tarrant_by_account("123"), [{"ACCOUNT": "123"}])
            self.assertEqual(request.call_args.args[1]["where"], "ACCOUNT='123'")

    def test_bad_account(self):
        with self.assertRaises(ValueError):
            tarrant_by_account("1' OR 1=1")

    def test_bad_listing(self):
        with self.assertRaises(ValueError):
            repliers_listing("../secret")

    def test_missing_key(self):
        with patch.dict(os.environ, {"ATTOM_API_KEY": ""}):
            with self.assertRaises(ProviderError):
                attom_by_address("1 Main St", "Fort Worth, TX")

if __name__ == "__main__":
    unittest.main()
