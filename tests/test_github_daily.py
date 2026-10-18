"""Offline safety and schedule tests for the no-credit-card daily runner."""
from __future__ import annotations

import csv
import json
import sys
import types
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from scripts.state_vault import allowed, pack, unpack
from scripts.bootstrap_state import EXPECTED, SHOPS, validate


def test_vault_encrypt_restore_only_allowlisted(tmp_path):
    key = Fernet.generate_key().decode()
    data = tmp_path / "data"
    (data / "alerts/cookie_jars").mkdir(parents=True)
    (data / "logs").mkdir()
    (data / "secrets").mkdir()
    cookie = '[{"name":"sessionid","value":"PRIVATE_COOKIE"}]'
    (data / "alerts/cookie_jars/welive_main.json").write_text(cookie)
    (data / "logs/replied_reviews.csv").write_text("order_id,username\n999,PRIVATE_NAME\n")
    (data / "secrets/google-service-account.json").write_text("PRIVATE_GOOGLE_KEY")
    payload = pack(data, key)
    assert b"PRIVATE_COOKIE" not in payload
    assert b"PRIVATE_NAME" not in payload
    assert b"PRIVATE_GOOGLE_KEY" not in payload
    target = tmp_path / "restored"
    unpack(payload, target, key)
    assert (target / "alerts/cookie_jars/welive_main.json").read_text() == cookie
    assert not (target / "secrets/google-service-account.json").exists()


def test_vault_wrong_key_or_tampering_is_rejected(tmp_path):
    one = Fernet.generate_key().decode()
    two = Fernet.generate_key().decode()
    blob = pack(tmp_path, one)
    with pytest.raises(RuntimeError, match="decryption failed"):
        unpack(blob, tmp_path / "other", two)
    with pytest.raises(RuntimeError):
        unpack(blob[:-1] + b"X", tmp_path / "other", one)
    assert not allowed("secrets/service-account.json")
    assert not allowed("alerts/cookie_jars/../../secret.json")


def test_history_bootstrap_requires_all_shops_and_verified_rows(tmp_path):
    (tmp_path / "logs").mkdir()
    (tmp_path / "alerts/cookie_jars").mkdir(parents=True)
    with (tmp_path / "logs/replied_reviews.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=EXPECTED)
        w.writeheader()
        w.writerow({"status": "submitted", "profile": "welive_main", "order_id": "order_1"})
    for shop in SHOPS:
        (tmp_path / f"alerts/cookie_jars/{shop}.json").write_text(json.dumps([{"name": "a", "value": "b"}]))
    assert validate(tmp_path)["verified_submission_count"] == 1
    (tmp_path / "alerts/cookie_jars/konomi_living.json").unlink()
    with pytest.raises(RuntimeError, match="konomi_living"):
        validate(tmp_path)


def test_workflow_is_single_daily_run_for_three_shops():
    workflow = Path(__file__).resolve().parents[1] / ".github/workflows/daily-reviews.yml"
    text = workflow.read_text()
    assert 'cron: "15 9 * * *"' in text
    assert 'timezone: "Asia/Singapore"' in text
    assert 'concurrency:' in text
    code = (workflow.parents[2] / "github_daily.py").read_text()
    assert 'for shop in c.SHOPS' in code
    assert 'store.save(pack(Path(".")))' in code
    assert 'ready_for_live = has_snapshot and Path("state/history_imported.ok").is_file()' in code


def test_daily_runner_never_runs_live_without_history(monkeypatch, tmp_path):
    from github_daily import main
    monkeypatch.chdir(tmp_path)
    notifications = []
    fake = types.SimpleNamespace(
        SHOPS=SHOPS,
        state={},
        load_state=lambda: {},
        notify=notifications.append,
    )
    monkeypatch.setitem(sys.modules, "cloud_controller", fake)
    import github_daily
    class FakeStore:
        def fetch(self):
            return None
    monkeypatch.setattr(github_daily, "GitHubStateStore", lambda: FakeStore())
    monkeypatch.setenv("AUTOMATION_ENABLED", "true")
    monkeypatch.setenv("ENABLE_SUBMIT", "true")
    assert main() == 2
    assert any("BLOCKED" in x for x in notifications)
