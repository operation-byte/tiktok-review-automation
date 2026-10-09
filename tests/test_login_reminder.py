import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import cloud_controller as c

class LoginReminderTest(unittest.TestCase):
    def test_daily_reminder_only_once_and_only_when_expired(self):
        stamp = datetime(2026,10,8,9,2,tzinfo=ZoneInfo('Asia/Singapore'))
        old_state, old_file = c.state, c.STATE_FILE
        with tempfile.TemporaryDirectory() as d:
            c.state = {'shops': {'welive_main': {'status':'login required'}, 'snackersbox': {'status':'complete'}}, 'last_login_reminder_date': ''}
            c.STATE_FILE = Path(d)/'state.json'
            try:
                with patch.object(c,'notify') as notify:
                    c.login_reminder_tick(stamp)
                    c.login_reminder_tick(stamp)
                    notify.assert_called_once()
                    self.assertIn('welive_main', notify.call_args.args[0])
                    self.assertNotIn('snackersbox', notify.call_args.args[0])
            finally:
                c.state, c.STATE_FILE = old_state, old_file

if __name__ == '__main__': unittest.main()
