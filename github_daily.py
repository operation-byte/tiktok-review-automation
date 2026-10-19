"""Once-a-day GitHub runner: configured shops, in parallel, fail closed.

Do not invoke the long-running Telegram controller on ephemeral Actions runners.
Instead, use the same per-shop job logic and send a consolidated Telegram report.
"""
from __future__ import annotations

import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from scripts.control_report import publish
from scripts.file_lock import STATE_FILES, file_lock
from scripts.github_state import GitHubStateStore, StateStoreError
from scripts.state_vault import pack, unpack


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
    if request_live and not ready_for_live:
        c.notify("🔴 LIVE TikTok posting BLOCKED: historical deduplication state has not been verified and restored.")
        return 2

    # From here on, always use the original Playwright flow. No TikTok API.
    c.state["paused"] = False
    problems = []
    requested = os.getenv("REQUESTED_SHOP", "all") or "all"
    if requested != "all" and requested not in c.SHOPS:
        c.notify("Unknown or disabled shop requested; check did not start.")
        return 2
    selected = [shop for shop in c.SHOPS if requested == "all" or shop == requested]
    save_mutex = threading.Lock()
    crashed = []

    def run_one(shop: str) -> None:
        # Each shop has its own browser profile and cookie jar, so shops run at
        # the same time. Shared CSV and Google Sheet writes are file-locked.
        c.run_shop(shop, force_dry=not request_live)
        # Persist refreshed cookies, verified history and job status as soon as
        # EACH shop finishes, even if another shop is still running or fails.
        with save_mutex, file_lock(STATE_FILES):
            store.save(pack(Path(".")))
        with c.state_mutex:
            publish(c.state)

    with ThreadPoolExecutor(max_workers=max(1, len(selected)), thread_name_prefix="shop") as pool:
        futures = {pool.submit(run_one, shop): shop for shop in selected}
        for future in as_completed(futures):
            shop = futures[future]
            try:
                future.result()
            except Exception as exc:
                crashed.append(shop)
                c.notify(f"🔴 TikTok daily check interrupted at {shop}. Encrypted state save or worker failed. {type(exc).__name__}")
                continue
            status = c.state.get("shops", {}).get(shop, {}).get("status", "unknown")
            if status not in ("complete", "dry run complete", "dry run: review candidates found"):
                problems.append(f"{shop}: {status}")
    if crashed:
        return 3

    if problems:
        c.notify("⚠️ TikTok daily check finished with attention needed.\n" +
                 "\n".join(problems) + "\nIf login is required, use /tt_login <shop> (welive_main, snackersbox, konomi_living). Use /tt_errors for run links.")
    else:
        labels = {"welive_main": "WELIVE", "snackersbox": "Snacker's Box", "konomi_living": "Konomi Living"}
        c.notify("✅ Daily TikTok check complete for " + ", ".join(labels[s] for s in selected) + ".\n" + c.report())
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
