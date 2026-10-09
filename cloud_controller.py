"""Telegram-controlled, sequential, Docker/Linux scheduler for the original Playwright bot.

No TikTok API, password handling, or unverified reply retries. All shop actions
run as bounded subprocesses in a new process group so Chromium children can be
reaped on timeout without Windows taskkill.
"""
from __future__ import annotations

import csv
import json
import logging
import os
import re
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from scripts.bootstrap_state import SHOPS
TZ = ZoneInfo("Asia/Singapore")
STATE_FILE = Path("state/controller.json")
LOG_DIR = Path("logs")
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
ALLOWED = {int(v) for v in os.environ.get("TELEGRAM_ALLOWED_IDS", "").replace(" ", "").split(",") if v.isdecimal()}
ALERT_CHAT_ID = os.environ.get("TELEGRAM_ALERT_CHAT_ID", "").strip()
# Alerts may only be sent directly to an explicitly authorized user.
if ALERT_CHAT_ID and (not ALERT_CHAT_ID.isdecimal() or int(ALERT_CHAT_ID) not in ALLOWED):
    ALERT_CHAT_ID = ""
CHECK_HOURS = {int(x) for x in os.getenv("CHECK_HOURS", "1,5,9,13,17,21").split(",") if x.strip().isdigit()}
CHECK_MINUTE = int(os.getenv("CHECK_MINUTE", "0"))
RUN_TIMEOUT = int(os.getenv("RUN_TIMEOUT_SECONDS", "1500"))
LOGIN_TIMEOUT = int(os.getenv("LOGIN_TIMEOUT_SECONDS", "900"))
SUBMIT_ENABLED = os.getenv("ENABLE_SUBMIT", "false").lower() == "true"
REPORT_HOUR = int(os.getenv("DAILY_REPORT_HOUR", "22"))
REPORT_MINUTE = int(os.getenv("DAILY_REPORT_MINUTE", "5"))
LOG_RETENTION_DAYS = int(os.getenv("LOG_RETENTION_DAYS", "30"))
SHEETS_MODE = os.getenv("GOOGLE_SHEETS_MODE", "off").lower()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("cloud_controller")
state_mutex = threading.RLock()
job_mutex = threading.Lock()
stop = threading.Event()
cancel_requested = threading.Event()
current_process: subprocess.Popen | None = None


def now() -> datetime:
    return datetime.now(TZ)


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            result = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            if isinstance(result, dict):
                return result
        except (ValueError, OSError):
            log.exception("Could not read controller state")
    return {"paused": True, "paused_shops": [], "shops": {}, "last_slot": "", "last_report_date": "", "active": "", "subscriber": ""}


state = load_state()


def save_state() -> None:
    with state_mutex:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
        tmp.chmod(0o600)
        tmp.replace(STATE_FILE)


def telegram(method: str, values: dict, timeout: int = 35) -> dict:
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{TOKEN}/{method}",
        data=urllib.parse.urlencode(values).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        data = json.load(response)
    if not data.get("ok"):
        raise RuntimeError(f"Telegram API method {method} failed")
    return data


def notify(message: str, chat_id: str | int | None = None) -> None:
    dest = chat_id or ALERT_CHAT_ID or state.get("subscriber")
    if not dest and len(ALLOWED) == 1:
        dest = str(next(iter(ALLOWED)))
    if not dest:
        log.warning("Notification unsent: configure TELEGRAM_ALERT_CHAT_ID or /subscribe")
        return
    try:
        telegram("sendMessage", {"chat_id": str(dest), "text": message[:3900], "disable_web_page_preview": "true"})
    except Exception as exc:
        log.warning("Telegram send failed (%s)", type(exc).__name__)


def compact_status() -> str:
    with state_mutex:
        lines = ["TikTok Review Bot — Cloud", f"Scheduler: {'PAUSED' if state.get('paused', True) else 'ACTIVE'}", f"Posting: {'ENABLED' if SUBMIT_ENABLED else 'DRY RUN ONLY'}", f"Google Sheet sync: {SHEETS_MODE.upper()} (live connection not verified)", f"Running: {state.get('active') or 'nothing'}"]
        for shop in SHOPS:
            row = state.get("shops", {}).get(shop, {})
            if shop in state.get("paused_shops", []): status = "paused"
            else: status = row.get("status", "not checked")
            login_flag = " | LOGIN REQUIRED" if row.get("login_needed", status == "login required") else ""
            lines.append(f"{shop}: {status}{login_flag} | last: {row.get('when', 'never')}")
        lines.append(f"Schedule: {','.join(f'{x:02}:00' for x in sorted(CHECK_HOURS))} Singapore time")
        return "\n".join(lines)


def terminate_group(proc: subprocess.Popen) -> None:
    # The worker was launched in its own session, so neither Xvfb nor the
    # Telegram controller is in this process group. Clean up orphaned Chromium
    # descendants even if the Python parent exited before we got here.
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        pass
    # Give child browsers time to terminate, then forcibly clear remaining
    # members of the worker's dedicated process group.
    time.sleep(0.25)
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        pass


def execute(args: list[str], timeout: int, logfile: Path) -> tuple[int, bool]:
    global current_process
    logfile.parent.mkdir(parents=True, exist_ok=True)
    # No shell. A new process group includes Playwright/Chromium subprocesses.
    with logfile.open("w", encoding="utf-8") as out:
        proc = subprocess.Popen([sys.executable, "-u", *args], stdout=out, stderr=subprocess.STDOUT, start_new_session=True)
        current_process = proc
        try:
            return proc.wait(timeout=timeout), False
        except subprocess.TimeoutExpired:
            terminate_group(proc)
            return 124, True
        finally:
            # Do not leave Chromium children with locks on persistent profiles.
            # Avoid sending SIGKILL to successfully completed jobs unless a
            # process group still exists; if it does, ask it to shut down.
            try:
                os.killpg(proc.pid, 0)
            except ProcessLookupError:
                pass
            else:
                terminate_group(proc)
            current_process = None


def tail_summary(path: Path) -> str:
    """Only surface allowlisted machine statistics; raw logs can contain PII."""
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "No log file created"
    rows = []
    for line in content.splitlines():
        match = re.search(r"\[(welive_main|snackersbox|konomi_living)\] (negative|neutral): outcome=(complete|incomplete|login_required|failed) submitted=(\d+) skipped_old=(\d+) skipped_logged=(\d+) held_manual=(\d+) dry_run=(\d+) manual_check=(\d+) failed=(\d+)", line)
        if match:
            shop, queue, outcome, submitted, old, logged, held, dry, manual, failed = match.groups()
            rows.append(f"{shop}/{queue}: {outcome}, submitted={submitted}, held={held}, ambiguous={manual}, failures={failed}, dry={dry}")
        elif "LOGIN REQUIRED:" in line or "Login required for profiles:" in line:
            rows.append("Login required — refresh this shop session and the encrypted state snapshot")
        elif "PROFILE LAUNCH FAILED:" in line:
            rows.append("Browser profile could not launch")
        elif "queue incomplete:" in line:
            rows.append("Review queue incomplete — inspect private logs")
    return "\n".join(rows[-6:]) or "No structured summary; see private server logs"


def purge_old_job_logs() -> None:
    """Keep bounded disk usage on free-tier instances; never remove CSV history."""
    if LOG_RETENTION_DAYS <= 0:
        return
    cutoff = time.time() - LOG_RETENTION_DAYS * 86400
    for path in LOG_DIR.glob("cloud_*.log"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            log.warning("Could not prune old log file")


def work(shops: list[str], force_dry: bool = False, login: bool = False) -> None:
    # start_work reserves job_mutex before starting this thread.
    try:
        for shop in shops:
            if cancel_requested.is_set() or stop.is_set():
                break
            with state_mutex:
                state["active"] = ("login " if login else "review check ") + shop
                save_state()
            datecode = now().strftime("%Y%m%d_%H%M%S")
            logfile = LOG_DIR / f"cloud_{shop}_{datecode}{'_login' if login else ''}.log"
            if login:
                args = ["cloud_login.py", "--profile", shop, "--timeout", str(LOGIN_TIMEOUT)]
                notify(f"Login browser started for {shop}. Connect to the private noVNC screen via the SSH tunnel and complete TikTok login within {LOGIN_TIMEOUT // 60} minutes. Do not send passwords or OTPs here.")
            else:
                sheets_flag = "--google-sheets" if SHEETS_MODE == "browser" and SUBMIT_ENABLED and not force_dry else "--no-google-sheets"
                args = ["auto_reply_reviews.py", "--profile", shop, "--headless", sheets_flag, "--log-path", "logs/replied_reviews.csv"]
                cutoff = os.getenv("TIKTOK_REVIEWS_SINCE", "").strip()
                if cutoff:
                    if date.fromisoformat(cutoff).isoformat() != cutoff:
                        raise ValueError("Invalid TikTok review cutoff")
                    args.extend(["--since-date", cutoff])
                if force_dry or not SUBMIT_ENABLED:
                    args.append("--dry-run")
                else:
                    args.append("--submit")
            code, timed_out = execute(args, LOGIN_TIMEOUT + 45 if login else RUN_TIMEOUT, logfile)
            was_cancelled = cancel_requested.is_set()
            output = logfile.read_text(encoding="utf-8", errors="replace")[-20000:]
            if was_cancelled:
                status = "cancelled by operator"
            elif login:
                success = code == 0 and "LOGIN_OK" in output
                status = "login refreshed" if success else "login failed or timed out"
            else:
                requires_login = "LOGIN REQUIRED:" in output or "Login required for profiles:" in output
                dry = force_dry or not SUBMIT_ENABLED
                if requires_login: status = "login required"
                elif timed_out: status = "timeout (worker cleaned up)"
                elif dry and "dry run inspected" in output.lower(): status = "dry run: review candidates found"
                elif code == 0: status = "dry run complete" if dry else "complete"
                else: status = "incomplete — inspect logs"
            with state_mutex:
                previous_row = state.get("shops", {}).get(shop, {})
                previous_status = previous_row.get("status", "")
                previous_login_needed = previous_row.get("login_needed", previous_status == "login required")
                # Preserve login-required state after a failed login attempt or a
                # network/browser failure: a failed login MUST NOT silence alerts.
                if login:
                    login_needed = not success
                elif requires_login:
                    login_needed = True
                elif code == 0 and not was_cancelled:
                    login_needed = False
                else:
                    login_needed = previous_login_needed
                state.setdefault("shops", {})[shop] = {
                    "status": status,
                    "login_needed": login_needed,
                    "when": now().strftime("%Y-%m-%d %H:%M SGT"),
                    "log": str(logfile),
                }
                save_state()
            message = f"{shop}: {status}"
            if not login:
                message += "\n" + tail_summary(logfile)
                if SHEETS_MODE == "browser" and any(text in output for text in (
                    "Google Sheet manual append failed", "Google Sheet manual append could not be verified",
                )):
                    notify("Google Sheet browser logging needs attention. The reply is saved locally; inspect the private log and Sheet before retrying.")
                if SHEETS_MODE == "api":
                    sync_file = LOG_DIR / f"sheets_sync_{datecode}.log"
                    sheet_code, sheet_timeout = execute(
                        ["google_sheet_sync.py", "--log-path", "logs/replied_reviews.csv"], 180, sync_file
                    )
                    if sheet_code != 0 or sheet_timeout:
                        notify("Google Sheet sync needs attention: cloud replies remain saved locally. Check private sync logs; existing Sheet data was not overwritten.")
            if login or ("login required" in status and not previous_login_needed) or (code != 0 and not was_cancelled and "login required" not in status):
                notify(message)
            else:
                log.info("%s", message)
    except Exception:
        log.exception("Worker management error")
        notify("Automation controller encountered an error. Check Docker logs.")
    finally:
        with state_mutex:
            state["active"] = ""
            save_state()
        cancel_requested.clear()
        job_mutex.release()
        purge_old_job_logs()


def start_work(shops: list[str], *, force_dry=False, login=False) -> bool:
    if not job_mutex.acquire(blocking=False):
        return False
    cancel_requested.clear()
    try:
        threading.Thread(target=work, args=(shops, force_dry, login), daemon=True).start()
    except Exception:
        job_mutex.release()
        raise
    return True


def cancel_work() -> bool:
    """Request cancellation and terminate only the active worker process group."""
    if not job_mutex.locked():
        return False
    cancel_requested.set()
    proc = current_process
    if proc is not None and proc.poll() is None:
        terminate_group(proc)
    return True


def report() -> str:
    today = now().date().isoformat()
    counts = {shop: 0 for shop in SHOPS}
    holds = {shop: 0 for shop in SHOPS}
    path = Path("logs/replied_reviews.csv")
    if path.exists():
        with path.open(encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                shop = row.get("profile", "")
                if shop not in counts or not row.get("replied_at", "").startswith(today):
                    continue
                if row.get("status") == "submitted": counts[shop] += 1
                if row.get("status", "").startswith("needs_manual") or "ambiguous" in row.get("status", ""):
                    holds[shop] += 1
    lines = [f"Review report — {today} (SGT)"]
    for shop in SHOPS:
        lines.append(f"{shop}: {counts[shop]} verified replies; {holds[shop]} manual/ambiguous entries")
    lines.append("Based on local CSV logging; the actual TikTok Seller Center remains authoritative.")
    return "\n".join(lines)


def pending_summary() -> str:
    """Only aggregate manual-review counts, never customer text on Telegram."""
    counts = {shop: 0 for shop in SHOPS}
    history = Path("logs/replied_reviews.csv")
    if history.exists():
        with history.open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                if row.get("profile") in counts and (row.get("status", "").startswith("needs_manual") or "ambiguous" in row.get("status", "")):
                    counts[row["profile"]] += 1
    return "Historical manual review log entries (not deduplicated open tasks):\n" + "\n".join(f"{shop}: {count}" for shop, count in counts.items())


def daily_report_tick(tick: datetime) -> None:
    if not ALLOWED or tick.hour != REPORT_HOUR or tick.minute < REPORT_MINUTE:
        return
    day = tick.date().isoformat()
    with state_mutex:
        if state.get("last_report_date") == day:
            return
        state["last_report_date"] = day
        save_state()
    notify(report())


def on_command(message: dict) -> None:
    chat = message.get("chat", {}).get("id")
    user_id = message.get("from", {}).get("id")
    raw = (message.get("text") or "").strip()
    if not chat or not isinstance(user_id, int): return
    # Ignore privileged commands from group chats, even when sent by an
    # administrator. A compromised group must not control public replies.
    if message.get("chat", {}).get("type") != "private":
        return
    if raw.split(" ")[0].split("@")[0] == "/whoami":
        notify(f"Your Telegram user ID is {user_id}. Only configure it in TELEGRAM_ALLOWED_IDS if you trust this account.", chat)
        return
    if user_id not in ALLOWED:
        # Ignore all privileged commands from unauthorized users.
        return
    parts = raw.lower().split()
    if not parts: return
    cmd = parts[0].split("@")[0]
    target = parts[1] if len(parts) > 1 else "all"
    if target not in (*SHOPS, "all") and cmd in ("/run", "/dryrun", "/login", "/pause", "/resume"):
        notify("Unknown shop. Use welive_main, snackersbox, konomi_living, or all.", chat)
        return
    if cmd in ("/start", "/help"):
        notify("Commands: /status, /run [shop|all], /dryrun [shop|all], /login [shop], /cancel, /pause [shop|all], /resume [shop|all], /report, /pending, /logs, /subscribe, /whoami, /loginstatus. Login requires a private SSH tunnel to noVNC. Public replies are disabled until ENABLE_SUBMIT=true is set on the server.", chat)
    elif cmd == "/subscribe":
        with state_mutex:
            state["subscriber"] = str(chat)
            save_state()
        notify("This authorized chat will receive job alerts.", chat)
    elif cmd == "/status": notify(compact_status(), chat)
    elif cmd == "/loginstatus":
        with state_mutex:
            required = [shop for shop in SHOPS if state.get("shops", {}).get(shop, {}).get("login_needed", state.get("shops", {}).get(shop, {}).get("status") == "login required")]
        notify("TikTok login required: " + (", ".join(required) if required else "none detected at last completed checks") + ". This is not a live check; scheduled checks detect expiry.", chat)
    elif cmd == "/report": notify(report(), chat)
    elif cmd == "/pending": notify(pending_summary(), chat)
    elif cmd == "/cancel":
        notify("Cancellation requested for active worker; remaining shops will not run." if cancel_work() else "No active worker.", chat)
    elif cmd == "/logs":
        with state_mutex:
            lines = [f"{k}: {state.get('shops', {}).get(k, {}).get('status','not checked')}" for k in SHOPS]
        notify("Recent job summaries (no customer details):\n" + "\n".join(lines), chat)
    elif cmd in ("/pause", "/resume"):
        paused = cmd == "/pause"
        with state_mutex:
            if target == "all": state["paused"] = paused
            else:
                ps = set(state.get("paused_shops", []))
                ps.add(target) if paused else ps.discard(target)
                state["paused_shops"] = sorted(ps)
            save_state()
        notify(f"{'Paused' if paused else 'Resumed'} future scheduled runs for {target}. Current running job, if any, is not interrupted.", chat)
    elif cmd in ("/run", "/dryrun", "/login"):
        if cmd == "/login" and target == "all":
            notify("Use /login welive_main (one shop at a time) to prevent accidental cross-login.", chat)
            return
        shops = list(SHOPS) if target == "all" else [target]
        if start_work(shops, force_dry=cmd == "/dryrun", login=cmd == "/login"):
            notify(f"Started {cmd} for {', '.join(shops)}. Only one worker runs at a time.{' Public replies require ENABLE_SUBMIT=true.' if not SUBMIT_ENABLED else ''}", chat)
        else:
            notify("Already running a worker. Try again when it finishes.", chat)


def scheduler_tick() -> None:
    if not ALLOWED:
        return
    tick = now()
    # Small grace interval allows Telegram long polling to return at least twice.
    if tick.hour not in CHECK_HOURS or not CHECK_MINUTE <= tick.minute < CHECK_MINUTE + 3:
        return
    slot = tick.strftime("%Y-%m-%dT%H")
    with state_mutex:
        if state.get("last_slot") == slot or state.get("paused", True): return
        shops = [s for s in SHOPS if s not in state.get("paused_shops", [])]
        if not shops: return
        if job_mutex.locked():
            # Don't mark slot; controller will retry if still in the 3-minute window.
            return
        state["last_slot"] = slot
        save_state()
    if start_work(shops):
        log.info("Scheduled job: %s", slot)
    else:
        # Unlock scheduling if race between reservation and worker start.
        with state_mutex:
            state["last_slot"] = ""
            save_state()



def login_reminder_tick(timestamp: datetime) -> None:
    """Daily, consolidated reminder for shops with still-expired sessions."""
    if timestamp.hour != 9:
        return
    today = timestamp.strftime("%Y-%m-%d")
    with state_mutex:
        if state.get("last_login_reminder_date") == today:
            return
        expired = [shop for shop, data in state.get("shops", {}).items()
                   if shop in SHOPS and data.get("login_needed", data.get("status") == "login required")]
        if not expired:
            return
        state["last_login_reminder_date"] = today
        save_state()
    notify("TikTok login still required for: " + ", ".join(expired)
           + ". Reply /login <shop> and complete authentication through the private viewer."
           + " Never send your password or OTP to this bot.")

def main() -> None:
    if not TOKEN or TOKEN.startswith("replace-"):
        raise SystemExit("Set a valid TELEGRAM_BOT_TOKEN in .env")
    if not ALLOWED:
        log.warning("BOOTSTRAP MODE: no authorized Telegram IDs. Only /whoami works in private chats. Scheduler disabled. Add ID to .env and restart to enable commands.")
    for name in ("logs", "alerts", "reports", "observations", "state", "browser_profiles"):
        Path(name).mkdir(parents=True, exist_ok=True)
    save_state()
    def handle_signal(signum, _frame):
        log.warning("Received signal %s", signum)
        stop.set()
        if current_process and current_process.poll() is None:
            terminate_group(current_process)
    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)
    # Drop queued updates after restart to prevent old /run commands being replayed.
    try: telegram("deleteWebhook", {"drop_pending_updates": "true"}, timeout=10)
    except Exception: log.warning("Could not clear pending Telegram updates")
    offset = None
    while not stop.is_set():
        if ALLOWED:
            scheduler_tick()
            daily_report_tick(now())
            login_reminder_tick(now())
        try:
            args = {"timeout": "20", "allowed_updates": json.dumps(["message"])}
            if offset is not None: args["offset"] = str(offset)
            events = telegram("getUpdates", args, timeout=30).get("result", [])
            for item in events:
                offset = item["update_id"] + 1
                if "message" in item: on_command(item["message"])
        except Exception as exc:
            log.warning("Telegram polling error: %s", type(exc).__name__)
            stop.wait(8)
    log.info("Controller stopped")


if __name__ == "__main__":
    main()
