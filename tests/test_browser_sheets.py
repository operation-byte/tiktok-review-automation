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

def test_existing_order_does_not_suppress_different_review(monkeypatch,tmp_path):
    args=SimpleNamespace(google_sheets=True,google_log_attempts=False,google_sheet_marker_path=str(tmp_path/'keys'),google_sheet_url='https://docs.google.com/spreadsheets/d/test/edit',google_sheet_after_paste_wait_ms=0)
    entry=reviews.ReplyLogEntry(**{key:'' for key in reviews.ReplyLogEntry.__dataclass_fields__})
    entry.status='submitted'; entry.order_id='sample-order'; entry.review_key='different-review'; entry.profile='welive_main'
    monkeypatch.setattr(reviews,'fetch_google_sheet_rows',lambda **kwargs: [['Order ID'], ['sample-order', '2026-10-08']])
    paste=Mock(); monkeypatch.setattr(reviews,'paste_google_sheet_row',paste)
    monkeypatch.setattr(reviews,'google_sheet_row_matches',lambda *a,**kw: True)
    reviews.manual_append_google_sheet_log(Mock(),args,entry,'2026-10-09')
    paste.assert_called_once()
    assert paste.call_args.args[1] == 3
    assert (tmp_path/'keys').read_text().strip()=='different-review'


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

def test_worker_uses_rolling_window_despite_stale_cutoff(monkeypatch,tmp_path):
    monkeypatch.chdir(tmp_path); (tmp_path/'logs').mkdir()
    monkeypatch.setenv('TIKTOK_REVIEWS_SINCE','2026-10-10')
    monkeypatch.setattr(controller,'state',{})
    monkeypatch.setattr(controller,'notify',Mock())
    captured=[]
    def execute(args,timeout,logfile):
        captured.extend(args); logfile.write_text('complete'); return 0,False
    monkeypatch.setattr(controller,'execute',execute)
    controller.job_mutex.acquire(); controller.work(['welive_main'],force_dry=True)
    assert '--since-date' not in captured
    assert captured[captured.index('--recent-days')+1]=='30'
