"""Regression tests for non-destructive Sheet sync and reliable auth reminders."""
from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import cloud_controller as ctrl
import google_sheet_sync as sheets
from auto_reply_reviews import ReplyLogEntry, build_google_sheet_row


class V5IntegrityTests(unittest.TestCase):
    def test_sheet_row_does_not_invent_followups_or_staff(self):
        row = build_google_sheet_row(ReplyLogEntry(
            replied_at='2026-10-09T12:00:00+08:00', profile='welive_main',
            queue='negative', rating_filter='1,2', order_id='TEST-123',
            username='private', product='test product', review_text='damaged',
            reply_text='Sorry', status='submitted', review_key='key'), '2026-10-08')
        self.assertEqual(len(row), 13)
        self.assertEqual(row[0], 'TEST-123')
        self.assertEqual(row[5], 'Yes')
        self.assertEqual(row[6:], [''] * 7)

    def test_failed_relogin_remains_in_daily_reminder(self):
        stamp = datetime(2026, 10, 9, 9, 15, tzinfo=ZoneInfo('Asia/Singapore'))
        prior_state, prior_file = ctrl.state, ctrl.STATE_FILE
        with tempfile.TemporaryDirectory() as directory:
            ctrl.state = {'shops': {
                'welive_main': {'status':'login failed or timed out', 'login_needed': True},
                'snackersbox': {'status':'complete', 'login_needed': False},
            }, 'last_login_reminder_date': ''}
            ctrl.STATE_FILE = Path(directory)/'controller.json'
            try:
                with patch.object(ctrl, 'notify') as notify:
                    ctrl.login_reminder_tick(stamp)
                    ctrl.login_reminder_tick(stamp)
                    notify.assert_called_once()
                    self.assertIn('welive_main', notify.call_args.args[0])
                    self.assertNotIn('snackersbox', notify.call_args.args[0])
            finally:
                ctrl.state, ctrl.STATE_FILE = prior_state, prior_file

    def test_read_only_google_sheet_preflight_does_not_append(self):
        with patch.object(sheets, 'GoogleSheetsAPI') as api_type:
            instance = api_type.return_value
            instance.values.return_value = [list(sheets.EXPECTED_HEADERS)]
            with patch.object(sheets, 'START_DATE', ''):
                self.assertEqual(sheets.main(['--check-only']), 0)
            instance.append.assert_not_called()

    def test_authorized_loginstatus_only(self):
        old_allowed, old_state = ctrl.ALLOWED, ctrl.state
        ctrl.ALLOWED = {1001}
        ctrl.state = {'shops': {'welive_main': {'status': 'login failed or timed out','login_needed':True}}}
        try:
            with patch.object(ctrl,'notify') as notify:
                ctrl.on_command({'chat':{'id':2002,'type':'private'},'from':{'id':1002},'text':'/loginstatus'})
                notify.assert_not_called()
                ctrl.on_command({'chat':{'id':1001,'type':'private'},'from':{'id':1001},'text':'/loginstatus'})
                notify.assert_called_once()
                self.assertIn('welive_main',notify.call_args.args[0])
        finally:
            ctrl.ALLOWED, ctrl.state = old_allowed, old_state

if __name__ == '__main__': unittest.main()
