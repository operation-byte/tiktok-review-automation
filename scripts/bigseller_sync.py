"""Export with the supplied browser workflow, then confirm the dashboard import."""
from __future__ import annotations

import base64
import csv
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import subprocess
import tempfile
import time
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
import uuid

import requests
from scripts.bigseller_state import BigSellerStateStore, write_private
from scripts.bigseller_control import notify, publish

ROOT = Path(__file__).resolve().parents[1]
MAX_EXPORT_BYTES = 10 * 1024 * 1024


def normalized(value):
    return ' '.join(str(value or '').strip().casefold().split())


def validate_rows(values):
    rows = iter(values)
    headers = [normalized(value) for value in next(rows, [])]
    def column(aliases):
        found = [i for i, header in enumerate(headers) if header in aliases]
        if len(found) != 1:
            raise ValueError('Export must have exactly one ' + sorted(aliases)[0] + ' column')
        return found[0]
    name = column({'sku name', 'variation'})
    quantity = column({'available for whole warehouse', 'available stock'})
    kind = column({'sku type', 'sku category'})
    seen, count = set(), 0
    for row in rows:
        if not any(value is not None and str(value).strip() for value in row):
            continue
        if len(row) <= max(name, quantity, kind):
            raise ValueError('Export contains an incomplete row')
        if normalized(row[kind]) != 'single sku':
            raise ValueError('Export contains a non-Single SKU row; filter was not verified')
        identity = normalized(row[name])
        if not identity or identity in seen:
            raise ValueError('Export contains blank or duplicate SKU names')
        seen.add(identity)
        raw = row[quantity]
        if isinstance(raw, bool) or raw is None or not str(raw).strip():
            raise ValueError('Export contains a blank stock quantity')
        try:
            value = float(str(raw).replace(',', '').strip())
        except (ValueError, TypeError):
            raise ValueError('Export contains a nonnumeric stock quantity') from None
        if not math.isfinite(value) or value != int(value):
            raise ValueError('Export contains an invalid stock quantity')
        count += 1
    if not count:
        raise ValueError('Export contains no Single SKU inventory')
    return count


def validate_export(path):
    path = Path(path)
    if not path.is_file() or not 0 < path.stat().st_size <= MAX_EXPORT_BYTES:
        raise ValueError('Export is empty or exceeds the import size limit')
    if path.suffix.lower() == '.csv':
        with path.open(encoding='utf-8-sig', newline='') as handle:
            return validate_rows(csv.reader(handle))
    if path.suffix.lower() == '.xlsx':
        from openpyxl import load_workbook
        workbook = load_workbook(path, read_only=True, data_only=True)
        try:
            return validate_rows(workbook.worksheets[0].iter_rows(values_only=True))
        finally:
            workbook.close()
    if path.suffix.lower() == '.xls':
        import xlrd
        workbook = xlrd.open_workbook(path, on_demand=True)
        try:
            sheet = workbook.sheet_by_index(0)
            return validate_rows(sheet.row_values(i) for i in range(sheet.nrows))
        finally:
            workbook.release_resources()
    raise ValueError('Unsupported BigSeller export type')


class DashboardImporter:
    def __init__(self):
        self.secret = os.environ.get('BIGSELLER_IMPORT_SECRET', '')
        parsed = urlparse(os.environ.get('BIGSELLER_IMPORT_URL', ''))
        if not self.secret or parsed.scheme != 'https' or parsed.hostname != 'script.google.com' or not parsed.path.endswith('/exec'):
            raise RuntimeError('Configure the existing dashboard webhook URL and BigSeller import secret')
        query = parse_qs(parsed.query)
        query['bigsellerAutomation'] = ['1']
        self.url = urlunparse(parsed._replace(query=urlencode(query, doseq=True)))

    def call(self, action, payload):
        body = json.dumps({'action': action, 'payload': payload, 'ts': int(time.time() * 1000),
                           'nonce': str(uuid.uuid4())}, separators=(',', ':'))
        envelope = {'body': body, 'signature': hmac.new(self.secret.encode(), body.encode(), hashlib.sha256).hexdigest()}
        try:
            response = requests.post(self.url, json=envelope, timeout=(15, 360), allow_redirects=False)
            if response.status_code in (301, 302, 303):
                target = response.headers.get('Location', '')
                redirect = urlparse(target)
                if redirect.scheme != 'https' or redirect.hostname != 'script.googleusercontent.com':
                    raise RuntimeError('Dashboard returned an unexpected redirect')
                response = requests.get(target, timeout=(15, 360), allow_redirects=False)
            response.raise_for_status()
            result = response.json()
            if not isinstance(result, dict) or result.get('ok') is not True:
                raise RuntimeError('Dashboard refused or failed the BigSeller ' + action)
            return result
        except (requests.RequestException, ValueError):
            raise RuntimeError('Dashboard ' + action + ' response was not confirmed') from None

    def import_file(self, path, rows):
        path = Path(path)
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        identity = {'sha256': digest, 'runId': os.getenv('GITHUB_RUN_ID') or str(uuid.uuid4())}
        mime = {'.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                '.xls': 'application/vnd.ms-excel', '.csv': 'text/csv'}[path.suffix.lower()]
        upload = {**identity, 'name': path.name, 'mimeType': mime, 'data': base64.b64encode(raw).decode()}
        try:
            receipt = self.call('upload', upload)
        except RuntimeError:
            receipt = self.call('status', identity)
        file_id = receipt.get('fileId')
        if not file_id or receipt.get('sha256') != digest:
            raise RuntimeError('Export upload could not be verified')
        if receipt.get('status') != 'completed':
            try:
                receipt = self.call('import', {'fileId': file_id})
            except RuntimeError:
                # The POST may have completed: inspect its persisted receipt before retrying.
                receipt = self.call('status', {'fileId': file_id})
        if receipt.get('status') != 'completed' or receipt.get('fileId') != file_id or receipt.get('sha256') != digest or receipt.get('rows') != rows:
            raise RuntimeError('Dashboard stock import/Procurement refresh is not confirmed; inspect import status before retrying')
        return receipt


def main():
    dry_run = os.getenv('BIGSELLER_DRY_RUN', 'true').lower() == 'true'
    if not dry_run and os.getenv('BIGSELLER_AUTOMATION_ENABLED', '').lower() != 'true':
        print('BigSeller sync paused')
        return 0
    importer = None if dry_run else DashboardImporter()
    store = BigSellerStateStore()
    state = store.fetch()
    if state is None:
        publish('login required', login_needed=True)
        notify('BigSeller needs its first login. Open Login to BigSeller in Warehouse or send /bs_login.')
        return 10
    with tempfile.TemporaryDirectory(prefix='bigseller-') as directory:
        temporary = Path(directory)
        state_file, next_state, manifest = temporary / 'login.json', temporary / 'next-login.json', temporary / 'result.json'
        write_private(state_file, state)
        env = {**os.environ, 'TZ': 'Asia/Singapore', 'BIGSELLER_STATE_FILE': str(state_file),
               'BIGSELLER_SAVE_STATE_FILE': str(next_state), 'BIGSELLER_RESULT_FILE': str(manifest),
               'BIGSELLER_INCOMING_DIR': str(temporary / 'incoming'), 'BIGSELLER_HEADLESS': '1',
               'BIGSELLER_DOWNLOAD_DIR': str(temporary / 'downloads'), 'BIGSELLER_LOG_DIR': str(temporary / 'logs'),
               'BIGSELLER_LOCK_FILE': str(temporary / 'export.lock')}
        (temporary / 'incoming').mkdir()
        result = subprocess.run(['node', 'src/run.js'], cwd=ROOT / 'integrations/bigseller-export', env=env, timeout=16 * 60)
        if result.returncode:
            publish('login required' if result.returncode == 10 else 'export failed', login_needed=result.returncode == 10)
            notify('BigSeller export did not complete. ' + ('Use /bs_login to refresh login.' if result.returncode == 10 else 'Use /bs_status to inspect the failed job. Previous stock data was preserved.'))
            return result.returncode
        exported = json.loads(manifest.read_text())
        path = Path(exported['destination']).resolve()
        if not path.is_relative_to(temporary.resolve()):
            raise RuntimeError('Export manifest points outside this run')
        rows = validate_export(path)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != exported.get('sha256'):
            raise RuntimeError('Export checksum mismatch')
        if next_state.exists():
            store.save(json.loads(next_state.read_text()))
        if dry_run:
            publish('export check passed', login_needed=False, dry_run=True)
            notify(f'✅ BigSeller export check passed: {rows} Single SKU rows validated. Stock data was unchanged. Use Run sync or /bs_run after enabling sync.')
        else:
            receipt = importer.import_file(path, rows)
            publish('stock updated', login_needed=False, rows=rows, fileId=receipt['fileId'], lastImportAt=receipt.get('importedAt'), dry_run=False)
            notify(f'✅ BigSeller stock updated: {rows} Single SKU rows imported into the main dashboard. Procurement refreshed; stock alerts checked.')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print('BigSeller sync failed: ' + type(exc).__name__)
        publish('sync failed')
        raise SystemExit(1)
