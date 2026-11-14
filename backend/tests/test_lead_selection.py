import unittest
from datetime import date
from importers.lead_selection import is_open_code_case, upcoming_foreclosure, one_month_out

class LeadSelectionTests(unittest.TestCase):
    def test_open(self):
        self.assertTrue(is_open_code_case({"Violation_Current_Status":"Open"}))
        self.assertTrue(is_open_code_case({"Case_Current_Status":"In Progress"}))
        self.assertFalse(is_open_code_case({"Violation_Current_Status":"Closed","Case_Current_Status":"Open"}))
        self.assertFalse(is_open_code_case({"Case_Current_Status":"Something New"}))
        self.assertFalse(is_open_code_case({}))

    def test_foreclosure_window(self):
        today=date(2026,11,14)
        self.assertTrue(upcoming_foreclosure({"sale_date":"2026-11-14"},today))
        self.assertTrue(upcoming_foreclosure({"sale_date":"2026-12-14"},today))
        self.assertFalse(upcoming_foreclosure({"sale_date":"2026-12-15"},today))
        self.assertFalse(upcoming_foreclosure({"sale_date":"2026-11-13"},today))
        self.assertFalse(upcoming_foreclosure({},today))
        self.assertFalse(upcoming_foreclosure({"sale_date":"2026-12-01","status":"postponed"},today))

    def test_month_rollover(self):
        self.assertEqual(one_month_out(date(2026,1,31)),date(2026,2,28))
        self.assertEqual(one_month_out(date(2026,12,31)),date(2027,1,31))
