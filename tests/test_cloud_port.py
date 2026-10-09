import csv
import os
import pathlib
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import auto_reply_reviews as reviews
import cloud_controller as controller


class CloudPortTests(unittest.TestCase):
    def test_cloud_profile_paths_are_relative_to_root(self):
        for shop in controller.SHOPS:
            self.assertTrue(reviews.PROFILES[shop]["user_data_dir"].endswith(shop))
            self.assertNotIn("C:\\", reviews.PROFILES[shop]["user_data_dir"])

    def test_high_risk_reviews_are_not_publicly_auto_posted(self):
        args = SimpleNamespace(reply_mode="manual_logic", negative_reply_text="negative", neutral_reply_text="neutral")
        with patch.dict(os.environ, {"AUTO_SUBMIT_HIGH_RISK": "false"}):
            reply = reviews.select_reply_decision(reviews.REVIEW_QUEUES["negative"],
                                                  {"review_text": "Food poisoning after eating this", "product": "Noodles"}, args)
        self.assertFalse(reply.auto_submit)
        self.assertEqual(reply.status, "needs_manual_review_high_risk")

    def test_high_risk_logged_review_is_held_on_subsequent_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "replies.csv"
            with path.open("w", encoding="utf-8-sig", newline="") as fp:
                writer = csv.DictWriter(fp, fieldnames=reviews.LOG_FIELDS)
                writer.writeheader()
                writer.writerow({"review_key": "review-one", "status": "needs_manual_review_high_risk"})
            result = reviews.load_logged_review_states(path)
            self.assertEqual(result["review-one"], reviews.LoggedReviewState.HOLD_MANUAL)

    def test_telegram_summary_redacts_customer_details(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "output.log"
            path.write_text("[welive_main] negative: order=1234 user=@private\n"
                            "[welive_main] negative queue incomplete: order=1234 user=@private\n")
            output = controller.tail_summary(path)
            self.assertNotIn("1234", output)
            self.assertNotIn("@private", output)
            self.assertIn("queue incomplete", output)

    def test_worker_timeout_terminates_its_process_group(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "timeout.log"
            code, timed_out = controller.execute(["-c", "import time;time.sleep(30)"], 0.15, path)
            self.assertTrue(timed_out)
            self.assertEqual(code, 124)
            self.assertIsNone(controller.current_process)

    def test_controller_default_is_dry_run_and_paused(self):
        self.assertFalse(controller.SUBMIT_ENABLED)
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(controller, "STATE_FILE", pathlib.Path(directory) / "missing.json"):
                self.assertTrue(controller.load_state().get("paused", True))


if __name__ == "__main__":
    unittest.main()
