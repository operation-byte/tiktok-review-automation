"""v7 local-only tests, no network calls or live TikTok sessions."""
from __future__ import annotations

import csv
import json
import os
import sys
import types
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from scripts.bootstrap_state import EXPECTED, SHOPS, validate
from scripts.state_vault import pack


def test_new_storage_state_sessions_work_in_bootstrap(tmp_path):
    (tmp_path / "alerts/cookie_jars").mkdir(parents=True)
    (tmp_path / "logs").mkdir()
    with (tmp_path / "logs/replied_reviews.csv").open("w", newline="") as out:
        csvout = csv.DictWriter(out, fieldnames=EXPECTED)
        csvout.writeheader()
        csvout.writerow({"profile": "welive_main", "status": "submitted", "order_id": "test"})
    for shop in SHOPS:
        (tmp_path / f"alerts/cookie_jars/{shop}.json").write_text(json.dumps({
            "cookies": [{"name": "session", "value": "cookie"}],
            "origins": [{"origin": "https://seller-sg.tiktok.com", "localStorage": [{"name":"a", "value":"b"}]}],
        }))
    assert validate(tmp_path)["verified_submission_count"] == 1


def test_new_storage_state_is_encrypted_and_restored(tmp_path):
    (tmp_path / "alerts/cookie_jars").mkdir(parents=True)
    (tmp_path / "alerts/cookie_jars/welive_main.json").write_text(json.dumps({
        "cookies": [{"name":"id", "value":"SESSION_PRIVATE"}],
        "origins": [{"origin":"https://seller-sg.tiktok.com", "localStorage": [{"name":"auth", "value":"LOCAL_PRIVATE"}]}],
    }))
    key = Fernet.generate_key().decode()
    data = pack(tmp_path, key)
    assert b"SESSION_PRIVATE" not in data and b"LOCAL_PRIVATE" not in data


def test_no_state_blocks_dry_run_before_work(monkeypatch, tmp_path):
    from github_daily import main
    monkeypatch.chdir(tmp_path)
    notices = []
    fake = types.SimpleNamespace(state={}, load_state=lambda: {}, notify=notices.append, SHOPS=SHOPS)
    monkeypatch.setitem(sys.modules, "cloud_controller", fake)
    import github_daily

    class Store:
        def fetch(self):
            return None

    monkeypatch.setattr(github_daily, "GitHubStateStore", lambda: Store())
    monkeypatch.setenv("AUTOMATION_ENABLED", "true")
    monkeypatch.setenv("ENABLE_SUBMIT", "false")
    assert main() == 2
    assert any("BLOCKED" in x for x in notices)


def test_google_sheets_off_in_dry_run_before_launch(monkeypatch, tmp_path):
    import github_daily
    monkeypatch.chdir(tmp_path)
    (tmp_path / "state").mkdir()
    (tmp_path / "state/history_imported.ok").write_text("{}"); (tmp_path / "logs").mkdir()
    notices = []
    fake = types.SimpleNamespace(
        state={}, load_state=lambda: {}, notify=notices.append, SHOPS=SHOPS,
        SHEETS_MODE="api", job_mutex=types.SimpleNamespace(acquire=lambda timeout: True),
        work=lambda shops, force_dry: fake.state.setdefault("shops", {}).update({shops[0]: {"status": "dry run complete"}}),
        report=lambda: "daily report"
    )
    monkeypatch.setitem(sys.modules, "cloud_controller", fake)

    class Store:
        def fetch(self):
            return b"snapshot"
        def save(self, encrypted):
            pass

    monkeypatch.setattr(github_daily, "GitHubStateStore", lambda: Store())
    monkeypatch.setattr(github_daily, "unpack", lambda blob, root: None)
    monkeypatch.setattr(github_daily, "pack", lambda root: b"packed")
    monkeypatch.setenv("AUTOMATION_ENABLED", "true")
    monkeypatch.setenv("ENABLE_SUBMIT", "false")
    assert github_daily.main() == 0
    assert fake.SHEETS_MODE == "off"


def test_direct_invocation_dry_run_by_default():
    from auto_reply_reviews import build_parser
    assert build_parser().parse_args([]).submit is False
    assert build_parser().parse_args(["--submit"]).submit is True


def test_save_and_restore_browser_storage(monkeypatch, tmp_path):
    import auto_reply_reviews as app
    monkeypatch.setattr(app, "COOKIE_JAR_DIR", tmp_path)
    original_state = {"cookies": [{"name": "sid", "value": "private", "domain": ".tiktok.com", "path": "/"}],
                      "origins": [{"origin": "https://seller-sg.tiktok.com", "localStorage": [{"name": "localKey", "value": "test"}]}]}
    class Context:
        def __init__(self):
            self.loaded = None
            self.init_script = None
        def storage_state(self):
            return original_state
        def add_cookies(self, values):
            self.loaded = values
        def add_init_script(self, script):
            self.init_script = script
    ctx = Context()
    app.save_cookies(ctx, "welive_main")
    target = Context()
    assert app.load_cookies_into(target, "welive_main") is True
    assert target.loaded == original_state["cookies"]
    assert "window.location.origin" in target.init_script
    assert "seller-sg.tiktok.com" in target.init_script


def test_legacy_cookie_list_still_restores(monkeypatch, tmp_path):
    import auto_reply_reviews as app
    monkeypatch.setattr(app, "COOKIE_JAR_DIR", tmp_path)
    (tmp_path / "welive_main.json").write_text('[{"name":"sid","value":"session"}]')
    class Context:
        def add_cookies(self, values):
            self.cookies = values
        def add_init_script(self, script):
            raise AssertionError("Legacy format should not inject localStorage")
    context = Context()
    assert app.load_cookies_into(context, "welive_main")
    assert context.cookies[0]["value"] == "session"

def test_selected_shops_require_only_selected_sessions(monkeypatch,tmp_path):
    import scripts.bootstrap_state as bootstrap
    monkeypatch.setattr(bootstrap,'SHOPS',('welive_main','snackersbox'))
    (tmp_path/'logs').mkdir(); (tmp_path/'alerts/cookie_jars').mkdir(parents=True)
    with (tmp_path/'logs/replied_reviews.csv').open('w',newline='') as out:
        writer=csv.DictWriter(out,fieldnames=EXPECTED); writer.writeheader(); writer.writerow({'status':'submitted'})
    for shop in bootstrap.SHOPS:
        (tmp_path/f'alerts/cookie_jars/{shop}.json').write_text('[{"name":"sid","value":"test"}]')
    assert bootstrap.validate(tmp_path)['shop_sessions']==['welive_main','snackersbox']

def test_shop_selection_excludes_konomi(monkeypatch):
    from scripts.bootstrap_state import configured_shops
    monkeypatch.setenv('AUTOMATION_SHOPS','welive_main,snackersbox')
    assert configured_shops()==('welive_main','snackersbox')
    monkeypatch.setenv('AUTOMATION_SHOPS','welive_main,unknown')
    with pytest.raises(ValueError): configured_shops()

def test_fresh_start_accepts_empty_history_with_explicit_cutoff(monkeypatch,tmp_path):
    import scripts.bootstrap_state as bootstrap
    monkeypatch.setattr(bootstrap,'SHOPS',('welive_main','snackersbox'))
    (tmp_path/'logs').mkdir(); (tmp_path/'state').mkdir(); (tmp_path/'alerts/cookie_jars').mkdir(parents=True)
    with (tmp_path/'logs/replied_reviews.csv').open('w',newline='') as out: csv.writer(out).writerow(EXPECTED)
    for shop in bootstrap.SHOPS: (tmp_path/f'alerts/cookie_jars/{shop}.json').write_text('[{"name":"sid","value":"test"}]')
    (tmp_path/'state/fresh_start.json').write_text(json.dumps({'mode':'fresh_start','reviews_since':'2026-10-10'}))
    result=bootstrap.validate(tmp_path)
    assert result['verified_submission_count']==0
    assert result['reviews_since']=='2026-10-10'
    (tmp_path/'state/fresh_start.json').write_text(json.dumps({'mode':'fresh_start','reviews_since':'invalid'}))
    with pytest.raises(RuntimeError): bootstrap.validate(tmp_path)

def test_daily_restores_and_enforces_fresh_start_cutoff(monkeypatch,tmp_path):
    import github_daily
    import scripts.bootstrap_state as bootstrap
    from scripts.state_vault import unpack
    shops=('welive_main','snackersbox')
    monkeypatch.setattr(bootstrap,'SHOPS',shops)
    monkeypatch.chdir(tmp_path)
    (tmp_path/'logs').mkdir(); (tmp_path/'state').mkdir(); (tmp_path/'alerts/cookie_jars').mkdir(parents=True)
    with (tmp_path/'logs/replied_reviews.csv').open('w',newline='') as out: csv.writer(out).writerow(EXPECTED)
    for shop in shops: (tmp_path/f'alerts/cookie_jars/{shop}.json').write_text('[{"name":"sid","value":"test"}]')
    (tmp_path/'state/fresh_start.json').write_text(json.dumps({'mode':'fresh_start','reviews_since':'2026-10-10'}))
    key=Fernet.generate_key().decode(); blob=pack(tmp_path,key)
    (tmp_path/'state/fresh_start.json').unlink()
    monkeypatch.setenv('STATE_ENCRYPTION_KEY',key)
    monkeypatch.setenv('AUTOMATION_ENABLED','true'); monkeypatch.setenv('ENABLE_SUBMIT','false')
    monkeypatch.setenv('TIKTOK_REVIEWS_SINCE','2020-01-01')
    worked=[]
    fake=types.SimpleNamespace(state={},load_state=lambda:{},notify=lambda message:None,SHOPS=shops,SHEETS_MODE='browser',job_mutex=types.SimpleNamespace(acquire=lambda timeout:True),report=lambda:'done')
    def work(selected,force_dry):
        assert force_dry and fake.SHEETS_MODE=='off'
        assert os.environ['TIKTOK_REVIEWS_SINCE']=='2026-10-10'
        worked.extend(selected)
        fake.state.setdefault('shops',{})[selected[0]]={'status':'dry run complete'}
    fake.work=work
    monkeypatch.setitem(sys.modules,'cloud_controller',fake)
    class Store:
        def fetch(self): return blob
        def save(self,encrypted): pass
    monkeypatch.setattr(github_daily,'GitHubStateStore',Store)
    assert github_daily.main()==0
    assert worked==list(shops)
