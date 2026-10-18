"""Private, one-time GitHub deployment assistant.

Automates repository creation, secret storage, encrypted state upload, and
read-only CI setup checks. Never enables TikTok posting automatically.
Requires human authorization for GitHub, Telegram BotFather and Google Cloud.
"""
from __future__ import annotations

import argparse
import base64
import getpass
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from tempfile import TemporaryDirectory
from scripts.bootstrap_state import SHOPS, validate
from scripts.state_vault import unpack, pack

from scripts.bootstrap_from_backup import prepare
from scripts.github_state import GitHubStateStore, StateStoreError

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPO = "tiktok-review-automation"
SHEET_ID = "1Q_8FRZBSRmuL6ncKshjME1JccLWU0XaMvSCVspwvHKs"
STAGED = [
    ".github", ".gitignore", "auto_reply_reviews.py", "cloud_controller.py",
    "cloud_login.py", "github_daily.py", "google_sheet_sync.py", "review_policy.py",
    "queue_processor.py", "reply_submission.py", "review_detection.py", "tiktok_navigation.py",
    "scripts", "tests", "docs", "integrations", "telegram-control",
    "requirements-github.txt", "requirements-relogin.txt", "requirements-bigseller.txt",
    "requirements-dev.txt", "README.md", "START_HERE.md", "DEPLOY_NOTES.md",
    "FINISH_SETUP_WINDOWS.cmd", "FINISH_SETUP_WINDOWS.ps1",
]


class SetupError(RuntimeError):
    pass


def run(args: list[str], *, input_text: str | None = None, cwd: Path = ROOT, allow_fail: bool = False) -> str:
    """Return stdout without exposing credentials in commands, logs or errors."""
    p = subprocess.run(args, cwd=cwd, input=input_text, text=True, stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, encoding="utf-8", errors="replace")
    if p.returncode and not allow_fail:
        # Avoid echoing stderr that can include credential-bearing URLs.
        raise SetupError(f"Command failed: {args[0]} {args[1] if len(args)>1 else ''} (code {p.returncode}); see account authorization and network connectivity")
    return p.stdout.strip() if p.returncode == 0 else ""


def telegram_call(token: str, method: str) -> dict:
    # Never print a token-bearing URL, even for errors.
    try:
        request = urllib.request.Request(f"https://api.telegram.org/bot{token}/{method}",
                                         headers={"User-Agent": "TikTok-review-setup/1"})
        with urllib.request.urlopen(request, timeout=20) as resp:
            result = json.load(resp)
        if not result.get("ok"):
            raise SetupError(f"Telegram rejected {method}")
        return result
    except SetupError:
        raise
    except Exception:
        raise SetupError(f"Telegram connection failed during {method} (token hidden)") from None


def find_telegram_account(token: str) -> tuple[str, str, str]:
    bot = telegram_call(token, "getMe").get("result", {})
    username = bot.get("username", "")
    if not username:
        raise SetupError("BotFather token does not identify a bot")
    print(f"Open https://t.me/{username}, send /start, and return here.")
    input("Press ENTER after you have messaged your own bot... ")
    events = telegram_call(token, "getUpdates").get("result", [])
    candidates = {}
    for event in events:
        msg = event.get("message", {})
        if msg.get("chat", {}).get("type") == "private" and msg.get("text", "").strip().split(" ")[0] == "/start":
            sender = msg.get("from", {}).get("id")
            chat = msg.get("chat", {}).get("id")
            if isinstance(sender, int) and isinstance(chat, int) and sender > 0 and chat > 0:
                candidates[sender] = chat
    if len(candidates) != 1:
        raise SetupError("Could not uniquely identify your private /start. Send /start from ONLY your Telegram account, then run setup again")
    user_id, chat_id = next(iter(candidates.items()))
    return str(user_id), str(chat_id), username


def locate_archive(explicit: str | None) -> Path:
    if explicit:
        p = Path(explicit.strip().strip('"')).expanduser()
        if p.is_file():
            return p
        raise SetupError("Original Windows automation ZIP was not found at supplied path")
    paths = [ROOT / "Tiktok Auto Reply Negative Reviews.zip", ROOT.parent / "Tiktok Auto Reply Negative Reviews.zip",
             Path.home() / "Downloads" / "Tiktok Auto Reply Negative Reviews.zip"]
    for path in paths:
        if path.is_file():
            return path
    print("Choose your ORIGINAL old automation ZIP, not this new ZIP.")
    return locate_archive(input("Full path to 'Tiktok Auto Reply Negative Reviews.zip': "))


def make_repo(repo: str) -> tuple[str, str]:
    if not shutil.which("git") or not shutil.which("gh"):
        raise SetupError("Install Git (https://git-scm.com/downloads) and GitHub CLI (https://cli.github.com/), then rerun setup")
    user = run(["gh", "api", "user", "--jq", ".login"], allow_fail=True).strip()
    if not user:
        run(["gh", "auth", "login", "--web", "--git-protocol", "https", "--scopes", "repo,workflow"])
        user = run(["gh", "api", "user", "--jq", ".login"]).strip()
    if not re.fullmatch(r"[A-Za-z\d](?:[A-Za-z\d-]{0,37}[A-Za-z\d])?", repo):
        raise SetupError("Repository name contains invalid characters")
    full = user + "/" + repo
    existing = run(["gh", "repo", "view", full, "--json", "visibility", "--jq", ".visibility"], allow_fail=True)
    if existing and existing.upper() != "PRIVATE":
        raise SetupError("Refusing to upload to a public GitHub repository")
    if (ROOT / ".git").exists():
        remote = run(["git", "remote", "get-url", "origin"], allow_fail=True)
        if existing:
            # Resume ONLY the repository previously created for this folder.
            if not remote or not remote.lower().removesuffix(".git").endswith("/" + full.lower()):
                raise SetupError("Existing GitHub repo is not this project's origin; refusing to overwrite it")
            print("Reusing previously created private GitHub repository")
            return full, run(["git", "rev-parse", "HEAD"])
        if remote:
            raise SetupError("Folder already has an unrelated remote; cannot safely create repository")
    else:
        if existing:
            raise SetupError("Repository name exists but is not linked to this folder. Choose --repo-name for a new private repository")
        run(["git", "init", "-b", "main"])
    # Only allowlisted automation source files, never local cookies, keys or logs.
    run(["git", "add", "--", *STAGED])
    if run(["git", "diff", "--cached", "--name-only"]):
        run(["git", "-c", "user.name=TikTok Review Setup", "-c", "user.email=setup@users.noreply.github.com",
             "commit", "-m", "Initialize private TikTok review automation (dry-run only)"])
    run(["gh", "auth", "setup-git"])
    run(["gh", "repo", "create", full, "--private", "--source", str(ROOT), "--remote", "origin", "--push"])
    return full, run(["git", "rev-parse", "HEAD"])


def set_secret(repo: str, name: str, value: str) -> None:
    if not value or '\n' in value:
        raise SetupError(f"Secret {name} must be nonempty and one line")
    # GitHub CLI reads from stdin and encrypts the value client-side.
    run(["gh", "secret", "set", name, "--repo", repo], input_text=value + "\n")


def set_variable(repo: str, name: str, value: str) -> None:
    run(["gh", "variable", "set", name, "--repo", repo], input_text=value + "\n")


def prepare_google(repo: str, path: str) -> bool:
    if not path:
        return False
    credential = Path(path.strip().strip('"')).expanduser()
    if not credential.is_file():
        raise SetupError("Google service-account JSON file not found")
    content = credential.read_bytes()
    parsed = json.loads(content)
    if parsed.get("type") != "service_account" or not parsed.get("client_email") or not parsed.get("private_key"):
        raise SetupError("Invalid Google service-account JSON")
    # Auth check before sending to GitHub; prevent silent Sheet failures.
    try:
        from google_sheet_sync import GoogleSheetsAPI, validate_headers
        g = GoogleSheetsAPI(credentials_path=str(credential))
        validate_headers(g)
    except Exception:
        print(f"Google authorization not ready. Share the CURRENT Sheet with {parsed['client_email']} as Editor, then rerun to finish Sheets connection.")
        # Credentials may be valid but not shared yet; avoid uploading unusable key.
        return False
    set_secret(repo, "GOOGLE_SERVICE_ACCOUNT_B64", base64.b64encode(content).decode('ascii'))
    set_variable(repo, "GOOGLE_SHEETS_MODE", "api")
    print("Google Sheets read-only header check succeeded. Current KPI sheet retained.")
    return True


def ensure_private_bootstrap(source: Path) -> dict:
    key_path = ROOT / ".state_key.private"
    encrypted = ROOT / "automation-state.enc"
    if key_path.is_file() and encrypted.is_file():
        with TemporaryDirectory(prefix="tiktok-verify-") as d:
            unpack(encrypted.read_bytes(), Path(d), key_path.read_text(encoding="utf-8").strip())
            summary = validate(Path(d))
        return {"history": summary["verified_submission_count"], "sessions": len(summary["shop_sessions"])}
    if key_path.is_file() and not encrypted.exists():
        # Recover from an interrupted bootstrap only if the private ledger is complete.
        summary = validate(ROOT)
        encrypted.write_bytes(pack(ROOT, key_path.read_text(encoding="utf-8").strip()))
        if os.name != "nt": encrypted.chmod(0o600)
        return {"history": summary["verified_submission_count"], "sessions": len(summary["shop_sessions"])}
    if encrypted.exists() or (ROOT / "logs/replied_reviews.csv").exists():
        raise SetupError("Partial existing bootstrap has no trusted key. Do not overwrite history; restore the private key from your backup")
    summary = prepare(source, ROOT, key_path)
    return summary


def deploy(args: argparse.Namespace) -> dict:
    source = locate_archive(args.original_zip)
    key_path = ROOT / ".state_key.private"
    if not shutil.which("git") or not shutil.which("gh"):
        raise SetupError("Install Git (https://git-scm.com/downloads) and GitHub CLI (https://cli.github.com/), then rerun setup")
    print("Preparing/validating encrypted state from original Windows backup; secrets remain outside Git commits.")
    bootstrap = ensure_private_bootstrap(source)
    print(f"Imported {bootstrap['history']} verified old replies and {bootstrap['sessions']} session files.")
    token = getpass.getpass("Paste NEW Telegram BotFather token (input hidden): ").strip()
    if not token:
        raise SetupError("Telegram bot token required for expiry alerts")
    user_id, chat_id, username = find_telegram_account(token)
    full, sha = make_repo(args.repo_name)
    set_secret(full, "STATE_ENCRYPTION_KEY", key_path.read_text(encoding="utf-8").strip())
    set_secret(full, "TELEGRAM_BOT_TOKEN", token)
    set_secret(full, "TELEGRAM_ALLOWED_IDS", user_id)
    set_secret(full, "TELEGRAM_ALERT_CHAT_ID", chat_id)
    # Upload encrypted state to independent, PRIVATE state branch after the
    # secrets and workflows exist. Never place the plaintext in the code branch.
    github_token = run(["gh", "auth", "token"])
    original_sha = os.environ.get("GITHUB_SHA")
    os.environ["GITHUB_SHA"] = sha
    try:
        storage = GitHubStateStore(token=github_token, repo=full)
        previous = storage.fetch()
        if previous is not None:
            if previous != (ROOT / "automation-state.enc").read_bytes():
                print("A newer encrypted cloud state exists. Preserving its review history; local bootstrap will not replace it.")
            else:
                print("Encrypted state already uploaded; preserving it")
        else:
            storage.save((ROOT / "automation-state.enc").read_bytes())
    except StateStoreError as exc:
        raise SetupError(f"GitHub encrypted state upload failed: {exc}") from None
    finally:
        if original_sha is None:
            os.environ.pop("GITHUB_SHA", None)
        else:
            os.environ["GITHUB_SHA"] = original_sha
    # Keep public posting safely disabled until the first supervised dry-run.
    set_variable(full, "AUTOMATION_SHOPS", ",".join(SHOPS))
    set_variable(full, "AUTOMATION_ENABLED", "true")
    set_variable(full, "ENABLE_SUBMIT", "false")
    set_variable(full, "GOOGLE_SHEETS_MODE", "off")
    set_variable(full, "GOOGLE_SHEETS_SYNC_FROM", datetime.now(ZoneInfo("Asia/Singapore")).date().isoformat())
    google_enabled = prepare_google(full, args.google_credentials or "")
    check_triggered = True
    try:
        run(["gh", "workflow", "run", "check-setup.yml", "--repo", full, "--ref", "main"])
    except SetupError:
        check_triggered = False
    dryrun_triggered = True
    try:
        run(["gh", "workflow", "run", "daily-reviews.yml", "--repo", full, "--ref", "main"])
    except SetupError:
        dryrun_triggered = False
    print("\n=== SETUP RESULTS ===")
    print("Private GitHub repository: https://github.com/" + full)
    print("Telegram bot: https://t.me/" + username)
    print("Encrypted review history and shop sessions: stored in separate private branch")
    print("Daily schedule: 09:15 Singapore time; shops: " + ", ".join(SHOPS))
    print("Google Sheet: " + ("connected (read-only check passed)" if google_enabled else "original preserved; API connection pending"))
    print("Public TikTok replies: DISABLED until successful supervised validation")
    print("Setup workflow: " + ("dispatched" if check_triggered else "open Actions and run check-setup.yml"))
    print("Dry-run workflow: " + ("dispatched, PUBLIC REPLIES OFF" if dryrun_triggered else "open Actions and run daily-reviews.yml"))
    print("GitHub Actions page: https://github.com/" + full + "/actions")
    print("Next: manually run TikTok daily review checks in dry-run, review Seller Center results, then enable public replies only after verification.")
    return {"repo": full, "telegram": username, "google": google_enabled}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="One-time private GitHub + Telegram TikTok review setup")
    parser.add_argument("--original-zip", help="Path to original Windows automation ZIP")
    parser.add_argument("--repo-name", default=DEFAULT_REPO)
    parser.add_argument("--google-credentials", help="Optional Google service-account JSON (existing Sheet is kept)")
    args = parser.parse_args(argv)
    try:
        deploy(args)
        return 0
    except (SetupError, RuntimeError, OSError, ValueError, json.JSONDecodeError) as exc:
        print("\nSETUP STOPPED SAFELY: " + str(exc))
        print("No TikTok public replies have been enabled.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
