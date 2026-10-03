import unittest
from datetime import date
from backend.factcheck import check

class FactCheckTests(unittest.TestCase):
    def test_preserved_amount(self): self.assertTrue(check("send ₹5,000", "Please send ₹5,000.")["passed"])
    def test_missing_amount(self): self.assertEqual(check("send 5000", "Please send the payment.")["items"][0]["status"],"missing")
    def test_changed_amount(self): self.assertFalse(check("send 5000", "Please send 6000.")["passed"])
    def test_devanagari_digits(self): self.assertTrue(check("₹५०००", "₹5,000")["passed"])
    def test_lakh_normalized(self): self.assertTrue(check("2 lakh", "200000")["passed"])
    def test_crore_normalized(self): self.assertTrue(check("1 crore", "10000000")["passed"])
    def test_thousand_normalized(self): self.assertTrue(check("50 hazaar", "50000")["passed"])
    def test_added_amount(self): self.assertEqual(check("hello", "The amount is 500.")["items"][0]["status"],"added")
    def test_kal_resolves(self): self.assertTrue(check("kal", "2026-10-04",today=date(2026,10,3))["passed"])
    def test_parso_resolves(self): self.assertTrue(check("parso", "2026-10-05",today=date(2026,10,3))["passed"])
    def test_agle_monday_resolves(self): self.assertTrue(check("agle Monday", "Monday 5 October",today=date(2026,10,3))["passed"])
    def test_iso_date_preserved(self): self.assertTrue(check("2026-12-03", "2026-12-03")["passed"])
    def test_numeric_date_matches_month_name(self): self.assertTrue(check("03/10/2026", "3 October 2026")["passed"])
    def test_added_weekday_is_detected(self): self.assertFalse(check("hello", "We will meet Monday.")["passed"])
    def test_sentence_start_not_mistaken_for_name(self): self.assertTrue(check("payment is due", "Payment is due soon.")["passed"])
    def test_contact_name_allowed(self): self.assertTrue(check("Sharma ji ko likho", "Dear Sharma, hello.",{"name":"Rajesh Sharma","aliases":"Sharma ji","salutation":"Dear Sharma"})["passed"])
    def test_missing_contact_name(self): self.assertFalse(check("Rajesh Sharma ko likho", "Dear Sir, hello.",{"name":"Rajesh Sharma"})["passed"])

if __name__ == "__main__": unittest.main()
