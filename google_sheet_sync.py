"""Fail-closed Google Sheets API mirror for verified, submitted TikTok replies.

The existing Google Sheet stays the same: Backend Data!A:M.  We NEVER update
existing rows, overwrite team fields, or touch TikTok's review service.
Historical CSV entries are excluded by the configured go-live cutoff.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

SHEET_ID = os.environ.get("GOOGLE_SHEET_ID", "1Q_8FRZBSRmuL6ncKshjME1JccLWU0XaMvSCVspwvHKs").strip()
SHEET_TAB = os.environ.get("GOOGLE_SHEET_TAB", "Backend Data").strip()
KEY_PATH = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE", "/run/secrets/google-service-account.json")
START_DATE = os.environ.get("GOOGLE_SHEETS_SYNC_FROM", "").strip()
EXPECTED_HEADERS = (
    "Order ID", "Date of Negative Review", "Shop Name", "Type of Review",
    "Negative Review Category", "Replied on TikTok", "Followed up on Duoke",
    "Did Customer Reply", "Changed Rating", "IC", "Message Template", "Follow Up", "Status",
)
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def a1(column_range: str) -> str:
    safe = SHEET_TAB.replace("'", "''")
    return f"'{safe}'!{column_range}"


class SheetError(RuntimeError):
    pass


class GoogleSheetsAPI:
    """Simple OAuth2 service-account Sheets values API (no browser involved)."""

    def __init__(self, sheet_id: str = SHEET_ID, credentials_path: str = KEY_PATH):
        if not re.fullmatch(r"[a-zA-Z0-9_-]{20,}", sheet_id):
            raise SheetError("Invalid or missing GOOGLE_SHEET_ID")
        if not Path(credentials_path).is_file():
            raise SheetError("Missing Google service-account JSON; configure the secrets mount")
        self.sheet_id = sheet_id
        self.credentials_path = credentials_path
        self._credentials = None

    def _token(self) -> str:
        # Import lazily: offline unit tests don't need credentials or google-auth.
        from google.oauth2 import service_account
        from google.auth.transport.requests import Request
        if self._credentials is None:
            self._credentials = service_account.Credentials.from_service_account_file(
                self.credentials_path, scopes=SCOPES)
        if not self._credentials.valid:
            self._credentials.refresh(Request())
        return self._credentials.token

    def request(self, method: str, resource: str, *, params: dict | None = None, payload: dict | None = None) -> dict:
        base = f"https://sheets.googleapis.com/v4/spreadsheets/{self.sheet_id}/values/"
        suffix = ":append" if resource.endswith(":append") else ""
        bounds = resource[:-len(suffix)] if suffix else resource
        url = base + urllib.parse.quote(bounds, safe="!:'") + suffix
        if params:
            url += "?" + urllib.parse.urlencode(params)
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(url, data=body, method=method, headers={
            "Authorization": "Bearer " + self._token(),
            "Content-Type": "application/json",
        })
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            # Do not log API response bodies (could contain customer/order values).
            raise SheetError(f"Google Sheets HTTP {exc.code} during {method}") from None
        except urllib.error.URLError:
            raise SheetError("Google Sheets connection failed") from None

    def values(self, bounds: str) -> list[list[str]]:
        return self.request("GET", a1(bounds), params={"valueRenderOption": "FORMATTED_VALUE"}).get("values", [])

    def append(self, values: list[str]) -> dict:
        return self.request(
            "POST", a1("A:M") + ":append",
            params={"valueInputOption": "RAW", "insertDataOption": "INSERT_ROWS", "includeValuesInResponse": "false"},
            payload={"majorDimension": "ROWS", "values": [values]},
        )


def tidy(value: object) -> str:
    return str(value or "").strip().lstrip("'")


def validate_headers(api: GoogleSheetsAPI) -> None:
    rows = api.values("A1:M1")
    headers = tuple(tidy(v).casefold() for v in (rows[0] if rows else []))
    expected = tuple(v.casefold() for v in EXPECTED_HEADERS)
    if headers[:len(expected)] != expected:
        raise SheetError("Backend Data header columns do not match the original KPI Sheet; no writes made")


@dataclass
class SyncSummary:
    written: int = 0
    already_exists: int = 0
    skipped_old: int = 0
    skipped_unsafe: int = 0
    failed: int = 0


def parse_start_date(value: str) -> str:
    if not re.fullmatch(r"20\d{2}-\d{2}-\d{2}", value):
        raise SheetError("GOOGLE_SHEETS_SYNC_FROM must be set as YYYY-MM-DD before Sheet writes")
    from datetime import date
    try:
        date.fromisoformat(value)
    except ValueError:
        raise SheetError("GOOGLE_SHEETS_SYNC_FROM is not a valid date") from None
    return value


def sync_csv(api: GoogleSheetsAPI, csv_path: Path, from_date: str, review_dates_path: Path | None = None) -> SyncSummary:
    """Idempotent append; original existing rows and formulas are not modified.

    Only freshly verified 'submitted' entries on or after go-live are eligible.
    Missing order IDs fail closed: no reliable deduplication identifier.
    """
    from auto_reply_reviews import ReplyLogEntry, build_google_sheet_row

    cutoff = parse_start_date(from_date)
    summary = SyncSummary()
    if not csv_path.is_file():
        return summary
    with csv_path.open(newline="", encoding="utf-8-sig") as f:
        records = list(csv.DictReader(f))
    review_dates_path = review_dates_path or csv_path.parent / "review_dates.csv"
    review_dates = {}
    if review_dates_path.is_file():
        with review_dates_path.open(newline="", encoding="utf-8-sig") as f:
            review_dates = {r.get("review_key", ""):r.get("review_date", "") for r in csv.DictReader(f)}
    # Read even if no records so that a misconfigured sheet fails early.
    validate_headers(api)
    existing = {tidy(r[0]) for r in api.values("A2:A") if r and tidy(r[0])}
    for record in records:
        if record.get("status") != "submitted":
            continue
        date_stamp = (record.get("replied_at") or "")[:10]
        if not re.fullmatch(r"20\d{2}-\d{2}-\d{2}", date_stamp) or date_stamp < cutoff:
            summary.skipped_old += 1
            continue
        order_id = tidy(record.get("order_id", ""))
        if not order_id:
            summary.skipped_unsafe += 1
            continue
        if order_id in existing:
            summary.already_exists += 1
            continue
        entry = ReplyLogEntry(**{k:record.get(k, "") for k in ReplyLogEntry.__dataclass_fields__})
        actual_review_date = review_dates.get(entry.review_key, "")
        row = build_google_sheet_row(entry, actual_review_date)
        # If the original review date was not captured, keep B blank rather
        # than incorrectly recording the date we posted our seller reply.
        if not actual_review_date:
            row[1] = ""
        try:
            result = api.append(row)
            # Verify by rereading the authoritative sheet, not by trusting POST alone.
            new_ids = {tidy(r[0]) for r in api.values("A2:A") if r and tidy(r[0])}
            if order_id not in new_ids:
                raise SheetError("Row append not yet verified; retry will check for duplicates")
            existing = new_ids
            summary.written += 1
        except (SheetError, ValueError, KeyError):
            # Do not hammer the API after an ambiguous append. Next run starts
            # by reading the sheet, making a retry safe even after a timeout.
            summary.failed += 1
            break
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sync submitted TikTok reviews to existing Google KPI Sheet")
    parser.add_argument("--log-path", default="logs/replied_reviews.csv")
    parser.add_argument("--check-only", action="store_true", help="Verify Sheet credentials and headers; never write")
    args = parser.parse_args(argv)
    try:
        if args.check_only:
            api = GoogleSheetsAPI()
            validate_headers(api)
            print("SHEETS_CHECK_OK: credentials, access and 13-column header verified (read-only)")
            return 0
        start_date = parse_start_date(START_DATE)
        api = GoogleSheetsAPI()
        summary = sync_csv(api, Path(args.log_path), start_date)
        print("SHEETS_SYNC written={0.written} exists={0.already_exists} old={0.skipped_old} unsafe={0.skipped_unsafe} failed={0.failed}".format(summary))
        return 1 if summary.failed else 0
    except SheetError as exc:
        print(f"SHEETS_SYNC_ERROR: {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
