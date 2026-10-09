"""Explicit, read-only deployment preflight on a GitHub Actions runner.

Validates private Telegram delivery, encrypted state and optionally Google Sheet
access. Does NOT open TikTok, post replies, or modify spreadsheet cells.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from scripts.bootstrap_state import validate
from scripts.github_state import GitHubStateStore, StateStoreError
from scripts.state_vault import unpack


def check_telegram() -> str:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.environ.get("TELEGRAM_ALERT_CHAT_ID", "").strip()
    allowed = {x.strip() for x in os.environ.get("TELEGRAM_ALLOWED_IDS", "").split(",") if x.strip()}
    if not token or not chat or not chat.isdecimal() or chat not in allowed:
        raise RuntimeError("Set Telegram bot token, numeric alert chat ID and matching allowed ID in GitHub Secrets")

    def api(method: str, data: dict[str, str]) -> dict:
        req = urllib.request.Request(
            "https://api.telegram.org/bot" + token + "/" + method,
            data=urllib.parse.urlencode(data).encode("utf-8"),
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=12) as res:
                result = json.load(res)
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"Telegram {method} returned HTTP {exc.code}. Check token/chat authorization") from None
        except (urllib.error.URLError, TimeoutError):
            raise RuntimeError("Telegram API is unreachable") from None
        if not result.get("ok"):
            raise RuntimeError("Telegram rejected the request; check that you sent /start to your bot")
        return result

    api("getMe", {})
    api("sendMessage", {"chat_id": chat, "text": "✅ TikTok review bot setup check: Telegram alerts are connected. No reviews were posted."})
    return "Telegram configured and test notification delivered"


def check_state() -> str:
    try:
        blob = GitHubStateStore().fetch()
    except StateStoreError as exc:
        raise RuntimeError("Cannot read encrypted automation-state branch (check GITHUB_TOKEN permissions and branch setup)") from None
    if not blob:
        raise RuntimeError("No automation-state.enc found in private automation-state branch; import history and 3 logins first")
    with tempfile.TemporaryDirectory(prefix="tiktok-state-check-") as d:
        unpack(blob, Path(d))
        validated = validate(Path(d))
    return f"Encrypted history and all three session files present ({validated['verified_submission_count']} historical submitted replies)"


def check_google() -> str:
    mode = os.environ.get("GOOGLE_SHEETS_MODE", "off").lower()
    if mode == "browser":
        from auto_reply_reviews import fetch_google_sheet_rows
        from google_sheet_sync import EXPECTED_HEADERS, tidy
        try:
            rows = fetch_google_sheet_rows()
        except urllib.error.URLError:
            return "Browser Sheets mode configured; anonymous CSV unavailable. Signed-in browser access must be validated locally before enabling live replies (no writes made)"
        headers = tuple(tidy(v).casefold() for v in (rows[0] if rows else []))
        if headers[:len(EXPECTED_HEADERS)] != tuple(v.casefold() for v in EXPECTED_HEADERS):
            raise RuntimeError("Browser Sheets CSV header check failed; no writes made")
        return "Browser Sheets CSV read and 13-column header check passed; browser editing access still needs interactive validation (no writes made)"
    if mode != "api":
        return "Google Sheets write mode OFF (safe, read-only check deferred)"
    from google_sheet_sync import GoogleSheetsAPI, validate_headers
    api = GoogleSheetsAPI()
    validate_headers(api)
    return "Existing Google Sheet is accessible and 13-column layout matches (read-only)"


def main() -> int:
    steps = (check_telegram, check_state, check_google)
    failed = False
    for step in steps:
        try:
            print("PASS: " + step())
        except Exception as exc:
            print(f"FAIL: {step.__name__}: {exc}")
            failed = True
    if failed:
        print("Preflight incomplete. No TikTok review actions or Google Sheet writes occurred.")
        return 1
    print("Preflight passed. Next run the daily workflow with ENABLE_SUBMIT=false.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
