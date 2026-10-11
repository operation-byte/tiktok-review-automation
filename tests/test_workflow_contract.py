"""Keeps GitHub workflows, the Telegram worker shop list and the relogin options in step."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = ROOT / ".github" / "workflows"
WORKER = (ROOT / "telegram-control" / "worker.mjs").read_text(encoding="utf-8")
WORKFLOWS = [w for w in re.search(r"const WORKFLOWS=\[([^\]]*)\]", WORKER).group(1).replace("'", "").split(",")]


def read(name):
    return (WORKFLOW_DIR / name).read_text(encoding="utf-8")


def options(name):
    found = re.findall(r"options:\s*\[([^\]]*)\]", read(name))
    assert len(found) == 1, name
    return [o.strip() for o in found[0].split(",")]


def worker_shops():
    return re.findall(r"'([^']+)'", re.search(r"const SHOPS=\[([^\]]*)\]", WORKER).group(1))


def test_worker_shops_match_relogin_and_manual_options():
    shops = worker_shops()
    assert options("relogin.yml") == shops
    assert options("daily-reviews.yml") == ["all"] + shops
    assert options("dry-run.yml") == ["all"] + shops


def test_worker_workflow_files_exist():
    assert WORKFLOWS
    for name in WORKFLOWS:
        assert (WORKFLOW_DIR / name).is_file(), name


def test_every_workflow_uses_v7_checkout_and_setup_python():
    for path in WORKFLOW_DIR.glob("*.yml"):
        text = path.read_text(encoding="utf-8")
        for action in ("actions/checkout", "actions/setup-python"):
            versions = re.findall(re.escape(action) + r"@(\S+)", text)
            assert versions, f"{path.name} does not use {action}"
            assert set(versions) == {"v7"}, f"{path.name} {action} {versions}"


def test_daily_cron_keeps_singapore_timezone():
    text = read("daily-reviews.yml")
    assert re.search(r'cron:\s*"15 9 \* \* \*"\s*\n\s*timezone:\s*"Asia/Singapore"', text)
