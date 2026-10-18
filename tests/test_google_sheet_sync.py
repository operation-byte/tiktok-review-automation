import csv
import tempfile
import unittest
from pathlib import Path

from google_sheet_sync import EXPECTED_HEADERS, SheetError, sync_csv


class FakeAPI:
    def __init__(self, current_ids=None):
        self.ids = set(current_ids or [])
        self.added = []
        self.expected_header = list(EXPECTED_HEADERS)
    def values(self, region):
        if region == 'A1:M1':
            return [self.expected_header]
        if region == 'A2:A':
            return [[x] for x in sorted(self.ids)]
        raise ValueError(region)
    def append(self, row):
        self.ids.add(row[0]); self.added.append(row)
        return {'updates': {'updatedRows': 1}}


class TestSheetSync(unittest.TestCase):
    def _history(self, directory):
        path = Path(directory) / 'replied_reviews.csv'
        columns = ['replied_at','profile','queue','rating_filter','order_id','username','product','review_text','reply_text','status','review_key']
        rows = [
            ['2026-09-01 09:00','snackersbox','negative','1,2','OLDORDER','','','bad','','submitted','old'],
            ['2026-10-08 09:00','snackersbox','negative','1,2','NEWORDER','','','bad','','submitted','new'],
            ['2026-10-08 10:00','welive_main','negative','1,2','DRY','','','','','dry_run','dry'],
            ['2026-10-08 11:00','welive_main','negative','1,2','NEWORDER','','','','','submitted','dupe-id'],
            ['2026-10-08 12:00','welive_main','negative','1,2','','','','','','submitted','no-id'],
        ]
        with path.open('w', newline='', encoding='utf8') as f:
            writer = csv.writer(f);writer.writerow(columns);writer.writerows(rows)
        return path

    def test_appends_only_new_verified_and_preserves_13_cols(self):
        with tempfile.TemporaryDirectory() as d:
            path = self._history(d)
            api = FakeAPI()
            output = sync_csv(api, path, '2026-10-08')
            self.assertEqual(output.written, 1)
            self.assertEqual(output.already_exists, 1)
            self.assertEqual(output.skipped_old, 1)
            self.assertEqual(output.skipped_unsafe, 1)
            self.assertEqual(len(api.added[0]), 13)
            self.assertEqual(api.added[0][0], 'NEWORDER')
            self.assertEqual(api.added[0][5], 'Yes')
            self.assertEqual(sync_csv(api, path, '2026-10-08').written, 0)

    def test_wrong_sheet_header_fails_closed(self):
        with tempfile.TemporaryDirectory() as d:
            api = FakeAPI();api.expected_header[0] = 'Something Else'
            with self.assertRaises(SheetError):
                sync_csv(api, self._history(d), '2026-10-08')
            self.assertEqual(api.added, [])

    def test_cutoff_required(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(SheetError):
                sync_csv(FakeAPI(), self._history(d), '')


if __name__ == '__main__':
    unittest.main()
