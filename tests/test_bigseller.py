import base64
import hashlib
import json

from cryptography.fernet import Fernet
import pytest

from scripts.bigseller_state import BigSellerStateStore, write_private
from scripts.bigseller_sync import DashboardImporter, validate_rows, validate_export
from scripts.bigseller_login import verified_login


HEADERS = ['SKU Name', 'Available for whole warehouse', 'SKU Type']


def test_export_requires_valid_complete_single_sku_rows():
    assert validate_rows([HEADERS, ['Single juice', '1,200', 'Single SKU'], ['Zero', 0, 'Single SKU'], ['Oversold', -1, 'Single SKU']]) == 3
    invalid = [[], [HEADERS], [HEADERS, ['x', '', 'Single SKU']],
               [HEADERS, ['x', float('nan'), 'Single SKU']], [HEADERS, ['x', 1.5, 'Single SKU']],
               [HEADERS, ['x', 1, 'Combination SKU']], [HEADERS, ['x', 1, 'Single SKU'], [' X ', 2, 'Single SKU']],
               [['SKU Name', 'Total On Hand Stock', 'SKU Type'], ['x', 1, 'Single SKU']]]
    for values in invalid:
        with pytest.raises(ValueError):
            validate_rows(values)


def test_csv_reads_bom_and_preserves_zero_stock(tmp_path):
    path = tmp_path / 'inventory.csv'
    path.write_text('\ufeffSKU Name,Available for whole warehouse,SKU Type\nJuice,0,Single SKU\n')
    assert validate_export(path) == 1


def test_inventory_header_can_follow_blank_rows_and_uses_existing_sheet_aliases():
    assert validate_rows([[], ['', None], ['Inventory export'], ['SKU', 'Available Qty', 'SKU Category'], ['Single juice', 0, 'Single SKU']]) == 1


def test_excel_inventory_uses_available_stock_and_rejects_blank_formula_cache(tmp_path):
    from openpyxl import Workbook
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(HEADERS + ['Total On Hand Stock'])
    sheet.append(['SKU 001', 0, 'Single SKU', 999])
    path = tmp_path / 'inventory.xlsx'
    workbook.save(path)
    assert validate_export(path) == 1
    sheet['B2'] = '=1+2'
    workbook.save(path)
    with pytest.raises(ValueError, match='blank stock'):
        validate_export(path)


def test_bigseller_state_is_encrypted_and_uses_a_separate_branch(monkeypatch, tmp_path):
    monkeypatch.setenv('STATE_ENCRYPTION_KEY', Fernet.generate_key().decode())
    monkeypatch.setenv('GITHUB_SHA', 'a' * 40)
    calls = []
    store = BigSellerStateStore(token='test', repo='test/repo')
    def request(method, path, payload=None, **kwargs):
        calls.append((method, path, payload))
        return {'content': {'sha': 'next'}} if method == 'PUT' else None
    store.request = request
    state = {'cookies': [{'name': 'session', 'value': 'private-cookie'}], 'origins': []}
    store.save(state)
    put = calls[-1]
    assert put[1] == '/contents/bigseller-state.enc'
    assert put[2]['branch'] == 'bigseller-state'
    encrypted = base64.b64decode(put[2]['content'])
    assert b'private-cookie' not in encrypted
    store.request = lambda *a, **k: {'type': 'file', 'encoding': 'base64', 'sha': 'next', 'content': base64.b64encode(encrypted).decode()}
    assert store.fetch() == state
    path = tmp_path / 'session.json'
    write_private(path, state)
    assert path.stat().st_mode & 0o777 == 0o600


def test_corrupt_saved_state_is_never_treated_as_a_first_login(monkeypatch):
    monkeypatch.setenv('STATE_ENCRYPTION_KEY', Fernet.generate_key().decode())
    store = BigSellerStateStore(token='test', repo='test/repo')
    store.request = lambda *a, **k: {'type': 'file', 'encoding': 'base64', 'content': 'd3Jvbmc='}
    with pytest.raises(RuntimeError, match='decrypted'):
        store.fetch()


class Locator:
    first = property(lambda self: self)
    def __init__(self, text='', visible=False):
        self.text, self.visible = text, visible
    def is_visible(self):
        return self.visible
    def inner_text(self, **kwargs):
        return self.text


class Page:
    url = 'https://www.bigseller.com/web/inventory/index.htm'
    def __init__(self, password=False):
        self.password = password
    def locator(self, selector):
        return Locator('Inventory List Single SKU Import & Export', self.password if selector != 'body' else False)


def test_login_verifies_bigseller_inventory_and_rejects_login_pages():
    assert verified_login(Page())
    assert not verified_login(Page(password=True))
    page = Page()
    page.url = 'https://attacker.example/web/inventory/index.htm'
    assert not verified_login(page)
    page.url = 'https://www.bigseller.com/login'
    assert not verified_login(page)


def test_importer_checks_committed_stock_receipt_not_just_upload(monkeypatch, tmp_path):
    monkeypatch.setenv('BIGSELLER_IMPORT_SECRET', 'test-secret')
    monkeypatch.setenv('BIGSELLER_IMPORT_URL', 'https://script.google.com/macros/s/test/exec')
    monkeypatch.setenv('GITHUB_RUN_ID', '123')
    path = tmp_path / 'inventory.csv'
    path.write_text('export')
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    importer = DashboardImporter()
    calls = []
    def call(action, payload):
        calls.append((action, payload))
        return {'ok': True, 'fileId': 'file', 'sha256': digest, 'status': 'uploaded' if action == 'upload' else 'completed', 'rows': 4}
    importer.call = call
    assert importer.import_file(path, 4)['status'] == 'completed'
    assert calls[0][1]['runId'] == '123'
    assert [call[0] for call in calls] == ['upload', 'import']
    with pytest.raises(RuntimeError, match='not confirmed'):
        importer.import_file(path, 5)


def test_lost_import_response_checks_status_before_any_resubmission(monkeypatch, tmp_path):
    monkeypatch.setenv('BIGSELLER_IMPORT_SECRET', 'test-secret')
    monkeypatch.setenv('BIGSELLER_IMPORT_URL', 'https://script.google.com/macros/s/test/exec')
    path = tmp_path / 'inventory.csv'
    path.write_text('export')
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    importer = DashboardImporter()
    calls = []
    def call(action, payload):
        calls.append(action)
        if action == 'import':
            raise RuntimeError('lost response')
        return {'ok': True, 'fileId': 'file', 'sha256': digest, 'status': 'completed' if action == 'status' else 'uploaded', 'rows': 4}
    importer.call = call
    assert importer.import_file(path, 4)['status'] == 'completed'
    assert calls == ['upload', 'import', 'status']
