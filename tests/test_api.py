import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from backend.api import API
from backend.db import Database

class ApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.db=Database(Path(self.tmp.name)/"test.sqlite3")
        self.api=API(db=self.db)
        self.contact_id=self.db.save_contact({"name":"Rajesh Sharma","email":"rajesh@example.com"})
        self.draft_id=self.db.save_draft({"contact_id":self.contact_id,"user_input":"Please send the invoice.","subject":"Invoice","email_en":"Please send the invoice.","summary_hi":"कृपया बिल भेजें।","factcheck":{"passed":True,"items":[]}})

    def tearDown(self): self.tmp.cleanup()

    def test_contacts_persist(self): self.assertEqual(self.db.contact(self.contact_id)["email"],"rajesh@example.com")

    def test_queue_returns_pending(self): self.assertEqual(self.api.queue_list()["drafts"][0]["id"],self.draft_id)

    def test_draft_mode_does_not_require_send_confirmation(self):
        with patch("backend.api.outlook.create_draft",return_value={"mode":"classic","sent":False}) as create:
            result=self.api.to_outlook(self.draft_id,"draft")
        self.assertTrue(result["ok"]); create.assert_called_once()

    def test_send_rejects_without_confirmation(self):
        result=self.api.to_outlook(self.draft_id,"send")
        self.assertFalse(result["ok"])
        self.assertIn("Confirm",result["error"])

    def test_send_has_cancellable_countdown(self):
        result=self.api.begin_send_confirmation(self.draft_id)
        self.assertTrue(result["ok"])
        too_early=self.api.to_outlook(self.draft_id,"send",result["confirmation_token"])
        self.assertFalse(too_early["ok"])
        self.assertIn("10-second",too_early["error"])
        self.assertTrue(self.api.cancel_send_confirmation(result["confirmation_token"])["ok"])

    def test_send_allowed_only_after_countdown(self):
        result=self.api.begin_send_confirmation(self.draft_id)
        self.api._send_tokens[result["confirmation_token"]]=(self.draft_id,time.monotonic()-1)
        with patch("backend.api.outlook.create_draft",return_value={"mode":"classic","sent":True}) as create:
            sent=self.api.to_outlook(self.draft_id,"send",result["confirmation_token"])
        self.assertTrue(sent["ok"]); create.assert_called_once()
        self.assertEqual(self.db.get_draft(self.draft_id)["status"],"sent")

if __name__ == "__main__": unittest.main()
