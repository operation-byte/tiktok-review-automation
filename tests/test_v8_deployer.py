"""Offline security and setup regression tests (no external services invoked)."""
from __future__ import annotations
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
import pytest
from scripts import finish_setup as setup


def test_disallow_secrets_in_git_stage():
    names = " ".join(setup.STAGED).lower()
    for forbidden in ("cookie_jars", "automation-state.enc", ".state_key", "secrets/", "logs/"):
        assert forbidden not in names
    assert ".github" in setup.STAGED


def test_secret_written_through_stdin_not_argument(monkeypatch):
    calls=[]
    def fake_run(args, cwd=None, input=None, **kw):
        calls.append((args, input))
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    monkeypatch.setattr(subprocess, "run", fake_run)
    setup.set_secret("account/private", "TELEGRAM_BOT_TOKEN", "sensitive-example-token")
    assert "sensitive-example-token" not in repr(calls[0][0])
    assert calls[0][1] == "sensitive-example-token\n"
    assert "gh" == calls[0][0][0]


def test_telegram_login_detection_unique_private_start(monkeypatch):
    def fake_call(token, method):
        if method=="getMe": return {"ok":True,"result":{"username":"test_ourprivatebot"}}
        if method=="getUpdates": return {"ok":True,"result":[
            {"message":{"chat":{"type":"group","id":-2},"from":{"id":44},"text":"/start"}},
            {"message":{"chat":{"type":"private","id":12345},"from":{"id":12345},"text":"/start"}},
        ]}
        raise AssertionError
    monkeypatch.setattr(setup,"telegram_call",fake_call)
    monkeypatch.setattr("builtins.input",lambda msg: "")
    assert setup.find_telegram_account("private-token")==("12345","12345","test_ourprivatebot")


def test_bootstrap_existing_encrypted_does_not_reimport(monkeypatch, tmp_path):
    from cryptography.fernet import Fernet
    from scripts.state_vault import pack
    from scripts.bootstrap_state import EXPECTED, SHOPS
    import csv
    key=Fernet.generate_key().decode()
    (tmp_path/".state_key.private").write_text(key)
    (tmp_path/"logs").mkdir()
    with (tmp_path/"logs/replied_reviews.csv").open("w",newline="") as f:
        writer=csv.DictWriter(f,fieldnames=EXPECTED);writer.writeheader();writer.writerow({"profile":"welive_main","status":"submitted"})
    (tmp_path/"alerts/cookie_jars").mkdir(parents=True)
    for shop in SHOPS:
        (tmp_path/f"alerts/cookie_jars/{shop}.json").write_text(json.dumps([{"name":"id","value":"session"}]))
    (tmp_path/"automation-state.enc").write_bytes(pack(tmp_path, key))
    monkeypatch.setattr(setup,"ROOT",tmp_path)
    monkeypatch.setattr(setup,"prepare",lambda *args: pytest.fail("must not reimport history"))
    result=setup.ensure_private_bootstrap(Path("unused-original.zip"))
    assert result["history"]==1


def test_github_repo_resume_requires_private_origin(monkeypatch,tmp_path):
    monkeypatch.setattr(setup,"ROOT",tmp_path)
    (tmp_path/".git").mkdir()
    monkeypatch.setattr(setup.shutil,"which",lambda x:"/fake/"+x)
    def fake_run(args,**kwargs):
        if args[:3]==["gh","api","user"]: return "owner"
        if args[:3]==["gh","repo","view"]: return "PRIVATE"
        if args[:4]==["git","remote","get-url","origin"]: return "https://github.com/owner/tiktok-review-automation.git"
        if args[:3]==["git","rev-parse","HEAD"]: return "a"*40
        raise AssertionError(args)
    monkeypatch.setattr(setup,"run",fake_run)
    assert setup.make_repo("tiktok-review-automation")==('owner/tiktok-review-automation','a'*40)
