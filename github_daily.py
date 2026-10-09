"""Once-a-day GitHub runner: all three shops, sequential, fail closed.

Do not invoke the long-running Telegram controller on ephemeral Actions runners.
Instead, use the same per-shop job logic and send a consolidated Telegram report.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from scripts.github_state import GitHubStateStore, StateStoreError
from scripts.state_vault import pack, unpack
from scripts.bootstrap_state import validate


def env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() == "true"


def main() -> int:
    import cloud_controller as c

    store = None
    has_snapshot = False
    try:
        store = GitHubStateStore()
        blob = store.fetch()
        if blob:
            unpack(blob, Path("."))
            has_snapshot = True
        # Controller state is evaluated at import, before the snapshot restore.
        c.state = c.load_state()
    except (StateStoreError, RuntimeError) as exc:
        c.notify("🔴 Daily TikTok check NOT started: encrypted state could not be restored. " + str(exc))
        return 1

    if not env_bool("AUTOMATION_ENABLED"):
        c.notify("TikTok daily automation is still PAUSED. Enable AUTOMATION_ENABLED after setup.")
        return 0

    # Missing state must NEVER trigger an uninitialized run or overwrite an
    # existing reply ledger. Install the encrypted bootstrap file first.
    if not has_snapshot:
        c.notify("🔴 Daily TikTok check BLOCKED: encrypted historical state was not found on the automation-state branch.")
        return 2

    # Dry runs must not write historical or new rows to Google Sheets, even if
    # repository variables have already enabled Google Sheets API syncing.
    if not env_bool("ENABLE_SUBMIT"):
        c.SHEETS_MODE = "off"

    # Private history marker must be initialized locally from real imported logs
    # before enabling any LIVE public posting.
    request_live = env_bool("ENABLE_SUBMIT")
    ready_for_live = has_snapshot and Path("state/history_imported.ok").is_file()
    fresh_start = Path("state/fresh_start.json").is_file()
    if fresh_start:
        try:
            validated = validate(Path("."))
            os.environ["TIKTOK_REVIEWS_SINCE"] = validated["reviews_since"]
            ready_for_live = has_snapshot and bool(validated["reviews_since"])
        except (RuntimeError, ValueError, OSError):
            c.notify("🔴 TikTok check BLOCKED: fresh-start state or review cutoff is invalid.")
            return 2
    if request_live and not ready_for_live:
        c.notify("🔴 LIVE TikTok posting BLOCKED: historical deduplication state has not been verified and restored.")
        return 2

    # From here on, always use the original Playwright flow. No TikTok API.
    c.state["paused"] = False
    problems = []
    for shop in c.SHOPS:
        try:
            if not c.job_mutex.acquire(timeout=5):
                raise RuntimeError("Could not acquire shop worker lock")
            c.work([shop], force_dry=not request_live)
            status = c.state.get("shops", {}).get(shop, {}).get("status", "unknown")
            if status not in ("complete", "dry run complete", "dry run: review candidates found"):
                problems.append(f"{shop}: {status}")
            # Persist refreshed cookies, verified history and job status after EACH
            # shop, even if later shop checks fail.
            store.save(pack(Path(".")))
        except Exception as exc:
            problems.append(f"{shop}: state persistence or runtime error")
            c.notify(f"🔴 TikTok daily check interrupted at {shop}. Encrypted state save or worker failed; remaining shops skipped. {type(exc).__name__}")
            return 3

    if problems:
        c.notify("⚠️ TikTok daily check finished with attention needed.\n" +
                 "\n".join(problems) + "\nIf login is required, reauthenticate manually and refresh encrypted GitHub state.")
    else:
        labels = {"welive_main": "WELIVE", "snackersbox": "Snacker's Box", "konomi_living": "Konomi Living"}
        c.notify("✅ Daily TikTok check complete for " + ", ".join(labels[s] for s in c.SHOPS) + ".\n" + c.report())
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
