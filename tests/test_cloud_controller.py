"""Local controller authorization, scheduling, cancellation and redaction."""
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from datetime import datetime
from zoneinfo import ZoneInfo

import cloud_controller as controller


class CloudControllerTests(unittest.TestCase):
    def test_whoami_works_without_configured_ids_but_commands_do_not(self):
        msg = {'chat': {'id': 333, 'type': 'private'}, 'from': {'id': 333}, 'text': '/whoami'}
        with patch.object(controller, 'notify') as mocked:
            controller.on_command(msg)
            self.assertIn('333', mocked.call_args.args[0])
            mocked.reset_mock()
            msg['text'] = '/run all'
            with patch.object(controller, 'start_work') as work:
                controller.on_command(msg)
                work.assert_not_called()
                mocked.assert_not_called()

    def test_groups_never_accept_commands(self):
        msg = {'chat': {'id': -333, 'type': 'supergroup'}, 'from': {'id': 333}, 'text': '/whoami'}
        with patch.object(controller, 'notify') as mocked:
            controller.on_command(msg)
            mocked.assert_not_called()

    def test_daily_report_not_sent_twice_in_day(self):
        with tempfile.TemporaryDirectory() as directory:
            previous = controller.state
            orig_file = controller.STATE_FILE
            controller.STATE_FILE = Path(directory) / 'controller.json'
            controller.state = {'last_report_date': '', 'paused': True}
            try:
                timestamp = datetime(2026, 10, 8, 22, 6, tzinfo=ZoneInfo('Asia/Singapore'))
                with patch.object(controller, 'ALLOWED', {333}), patch.object(controller, 'notify') as notify:
                    controller.daily_report_tick(timestamp)
                    controller.daily_report_tick(timestamp)
                    notify.assert_called_once()
            finally:
                controller.state = previous
                controller.STATE_FILE = orig_file

    def test_cancel_signal_only_when_worker_active(self):
        self.assertFalse(controller.job_mutex.locked())
        self.assertFalse(controller.cancel_work())
        controller.job_mutex.acquire()
        try:
            with patch.object(controller, 'current_process', None):
                self.assertTrue(controller.cancel_work())
                self.assertTrue(controller.cancel_requested.is_set())
        finally:
            controller.cancel_requested.clear()
            controller.job_mutex.release()

    def test_log_summary_has_no_customer_order_data(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'raw.log'
            source.write_text('[welive_main] negative: queue incomplete: order=1234 user=@customer\n'
                              '[welive_main] negative: outcome=complete submitted=3 skipped_old=1 skipped_logged=0 held_manual=2 dry_run=0 manual_check=0 failed=0 order=1234\n')
            summary = controller.tail_summary(source)
            self.assertIn('submitted=3', summary)
            self.assertNotIn('1234', summary)
            self.assertNotIn('@customer', summary)


    def test_log_summary_shows_why_a_profile_stopped(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'raw.log'
            source.write_text('[welive_main] PROFILE INCOMPLETE: NavigationError: could not open neutral review queue order=577123456789\n')
            summary = controller.tail_summary(source)
            self.assertIn('Stopped: NavigationError: could not open neutral review queue', summary)
            self.assertNotIn('577123456789', summary)

    def test_log_summary_keeps_queue_reason_and_drops_diagnostics(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'raw.log'
            source.write_text('[snackersbox] PROFILE INCOMPLETE: RuntimeError: snackersbox negative queue incomplete: review requires manual verification before processing diagnostics=debug/x.png\n')
            summary = controller.tail_summary(source)
            self.assertEqual(summary, 'Stopped: RuntimeError: snackersbox negative queue incomplete: review requires manual verification before processing')
            self.assertNotIn('diagnostics', summary)
            self.assertIsNone(re.search(r'\d{6,}', summary))


    def test_cloud_runs_real_windowed_chrome_when_headless_is_off(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(controller, 'LOG_DIR', Path(directory)), \
                patch.object(controller, 'save_state'), patch.object(controller, 'notify'), \
                patch.object(controller, 'execute', side_effect=lambda args, timeout, logfile: (logfile.write_text(''), (0, False))[1]) as execute:
            for headless, expected, absent in ((False, '--browser-window-state', '--headless'),
                                               (True, '--headless', '--browser-window-state')):
                with patch.object(controller, 'BROWSER_HEADLESS', headless):
                    controller.run_shop('snackersbox', force_dry=True)
                args = execute.call_args.args[0]
                self.assertIn(expected, args)
                self.assertNotIn(absent, args)

    def test_github_workflow_uses_chrome_on_virtual_display(self):
        workflow = (Path(__file__).resolve().parents[1] / '.github/workflows/daily-reviews.yml').read_text()
        self.assertIn('BROWSER_CHANNEL: chrome', workflow)
        self.assertIn('BROWSER_HEADLESS: "false"', workflow)
        self.assertIn('xvfb-run -a', workflow)


if __name__ == '__main__':
    unittest.main()
