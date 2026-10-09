"""Browser Sheets mode: live-only writing and encrypted deduplication."""
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from cryptography.fernet import Fernet
import auto_reply_reviews as reviews
import cloud_controller as controller
from scripts.state_vault import pack, unpack

@pytest.mark.parametrize('submit,force_dry,enabled', [(True,False,True),(True,True,False),(False,False,False)])
def test_worker_enables_browser_sheets_only_for_live(monkeypatch,tmp_path,submit,force_dry,enabled):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(controller,'SHEETS_MODE','browser')
    monkeypatch.setattr(controller,'SUBMIT_ENABLED',submit)
    monkeypatch.setattr(controller,'state',{})
    monkeypatch.setattr(controller,'notify',Mock())
    captured=[]
    def execute(args,timeout,logfile):
        captured.append(args)
        logfile.write_text('complete')
        return 0,False
    monkeypatch.setattr(controller,'execute',execute)
    (tmp_path/'logs').mkdir()
    controller.job_mutex.acquire()
    controller.work(['welive_main'],force_dry=force_dry)
    assert ('--google-sheets' in captured[0]) is enabled
    assert ('--no-google-sheets' in captured[0]) is (not enabled)

def test_sheet_marker_survives_encrypted_restore(tmp_path):
    source=tmp_path/'source'; (source/'logs').mkdir(parents=True)
    (source/'logs/google_sheet_logged_keys.txt').write_text('review-key\n')
    key=Fernet.generate_key().decode()
    target=tmp_path/'restore'; unpack(pack(source,key),target,key)
    assert (target/'logs/google_sheet_logged_keys.txt').read_text()=='review-key\n'

def test_existing_order_is_skipped_without_opening_sheet(monkeypatch,tmp_path):
    args=SimpleNamespace(google_sheets=True,google_log_attempts=False,google_sheet_marker_path=str(tmp_path/'keys'))
    entry=reviews.ReplyLogEntry(**{key:'' for key in reviews.ReplyLogEntry.__dataclass_fields__})
    entry.status='submitted'; entry.order_id='123'; entry.review_key='key'; entry.profile='welive_main'
    monkeypatch.setattr(reviews,'google_sheet_contains_order_id',lambda order, **kwargs: True)
    monkeypatch.setattr(reviews,'detect_google_sheet_next_blank_row',Mock(side_effect=AssertionError('Must skip existing order')))
    page=Mock()
    reviews.manual_append_google_sheet_log(page,args,entry,'2026-10-09')
    page.context.new_page.assert_not_called()
    assert (tmp_path/'keys').read_text().strip()=='key'

def test_preflight_browser_mode_checks_sheet_without_api_key(monkeypatch):
    from scripts.check_setup import check_google
    from google_sheet_sync import EXPECTED_HEADERS
    monkeypatch.setenv('GOOGLE_SHEETS_MODE','browser')
    monkeypatch.setattr(reviews,'fetch_google_sheet_rows',lambda: [list(EXPECTED_HEADERS)])
    assert 'Browser' in check_google()

def test_preflight_browser_mode_rejects_wrong_sheet_headers(monkeypatch):
    from scripts.check_setup import check_google
    monkeypatch.setenv('GOOGLE_SHEETS_MODE','browser')
    monkeypatch.setattr(reviews,'fetch_google_sheet_rows',lambda: [['wrong sheet']])
    with pytest.raises(RuntimeError,match='header'):
        check_google()

def test_private_sheet_csv_uses_authenticated_browser_request(monkeypatch):
    request=Mock()
    request.get.return_value=SimpleNamespace(ok=True,text=lambda: 'Order ID,Date\n123,2026-10-09\n')
    monkeypatch.setattr(reviews.urllib.request,'urlopen',Mock(side_effect=AssertionError('Must use signed-in browser')))
    assert reviews.fetch_google_sheet_rows(request_context=request)[1][0]=='123'
