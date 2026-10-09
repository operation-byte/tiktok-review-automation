"""Validate imported history + three saved shop sessions before permitting live replies."""
from __future__ import annotations

import csv
import json
import os
from datetime import datetime, timezone
from pathlib import Path

def configured_shops() -> tuple[str, ...]:
    known = ("welive_main", "snackersbox", "konomi_living")
    selected = tuple(x.strip() for x in os.getenv("AUTOMATION_SHOPS", ",".join(known)).split(",") if x.strip())
    if not selected or len(set(selected)) != len(selected) or any(x not in known for x in selected):
        raise ValueError("AUTOMATION_SHOPS must list distinct, known shop profiles")
    return selected


SHOPS = configured_shops()
EXPECTED = ('replied_at', 'profile', 'queue', 'rating_filter', 'order_id',
            'username', 'product', 'review_text', 'reply_text', 'status', 'review_key')


def validate(root: Path) -> dict:
    log_path = root / "logs/replied_reviews.csv"
    if not log_path.is_file():
        raise RuntimeError("Missing imported historical CSV: logs/replied_reviews.csv")
    with log_path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if tuple(reader.fieldnames or ()) != EXPECTED:
            raise RuntimeError("Historical CSV headers do not match the original bot")
        submitted = sum(1 for r in reader if r.get("status") == "submitted")
    if submitted < 1:
        raise RuntimeError("Historical CSV must contain verified submitted replies")
    for shop in SHOPS:
        jar_path = root / f"alerts/cookie_jars/{shop}.json"
        if not jar_path.is_file():
            raise RuntimeError(f"Missing TikTok cookie jar for {shop}. Log in locally first.")
        try:
            cookies = json.loads(jar_path.read_text(encoding="utf-8"))
            if isinstance(cookies, dict):
                cookies = cookies.get("cookies", [])
            if not isinstance(cookies, list) or not cookies:
                raise ValueError()
        except (ValueError, OSError):
            raise RuntimeError(f"Cookie jar missing or invalid for {shop}") from None
    return {"verified_submission_count": submitted,
            "shop_sessions": list(SHOPS),
            "acknowledged_at_utc": datetime.now(timezone.utc).isoformat()}


def main() -> None:
    root = Path(".")
    summary = validate(root)
    path = root / "state/history_imported.ok"
    path.parent.mkdir(exist_ok=True, parents=True)
    path.write_text(json.dumps(summary), encoding="utf-8")
    path.chmod(0o600)
    print("History and cookie-jar structure validated; live posting remains OFF until manually enabled in GitHub settings.")


if __name__ == "__main__":
    main()
