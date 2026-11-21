import unittest
from datetime import date
from importers.lead_selection import (
    is_open_code_case, one_month_ago, one_month_out, recent_probate, upcoming_foreclosure,
)

class LeadSelectionTests(unittest.TestCase):
    def test_open(self):
        self.assertTrue(is_open_code_case({"Violation_Current_Status":"Open"}))
        self.assertTrue(is_open_code_case({"Case_Current_Status":"In Progress"}))
        self.assertFalse(is_open_code_case({"Violation_Current_Status":"Closed","Case_Current_Status":"Open"}))
        self.assertFalse(is_open_code_case({"Case_Current_Status":"Something New"}))
        self.assertFalse(is_open_code_case({}))

    def test_real_world_open_case_variants(self):
        for status in ["Open Case", "CASE OPEN", "Active - Case", "Reopened", "Notice Issued", "Pending Inspection"]:
            self.assertTrue(is_open_code_case({"Violation_Current_Status": status}), status)
        for status in ["Case Closed", "Closed - Resolved", "Complied", "Dismissed", "Inactive", "Not Open", "Cancelled"]:
            self.assertFalse(is_open_code_case({"Violation_Current_Status": status}), status)
        self.assertTrue(is_open_code_case({"code_latest_status": "Open"}))


    def test_foreclosure_window(self):
        today=date(2026,11,14)
        self.assertFalse(upcoming_foreclosure({"sale_date":"2026-11-14"},today))
        self.assertTrue(upcoming_foreclosure({"sale_date":"2026-12-14"},today))
        self.assertTrue(upcoming_foreclosure({"sale_date":"2026-12-15"},today))
        self.assertTrue(upcoming_foreclosure({"sale_date":"2027-06-01"},today))
        self.assertFalse(upcoming_foreclosure({"sale_date":"2026-11-13"},today))
        self.assertFalse(upcoming_foreclosure({},today))
        self.assertFalse(upcoming_foreclosure({"sale_date":"2026-12-15","status":"postponed"},today))
        self.assertTrue(upcoming_foreclosure({"Scheduled Auction Date":"12/15/2026 10:00 AM"},today))

    def test_probate_window(self):
        today=date(2026,11,14)
        self.assertTrue(recent_probate({"filed_date":"2026-10-14"},today))
        self.assertTrue(recent_probate({"filed date":"2026-11-14"},today))
        self.assertTrue(recent_probate({"Filled_Date":"2026-11-01"},today))
        self.assertFalse(recent_probate({"filed_date":"2026-10-13"},today))
        self.assertFalse(recent_probate({"filed_date":"2026-11-15"},today))
        self.assertFalse(recent_probate({},today))

    def test_month_rollover(self):
        self.assertEqual(one_month_out(date(2026,1,31)),date(2026,2,28))
        self.assertEqual(one_month_out(date(2026,12,31)),date(2027,1,31))
        self.assertEqual(one_month_ago(date(2026,3,31)),date(2026,2,28))
        self.assertEqual(one_month_ago(date(2026,1,15)),date(2025,12,15))
