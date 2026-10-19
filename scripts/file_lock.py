"""Cross-process file locks for state shared by shops running in parallel.

Each lock name maps to its own lock file. Never nest the same lock name in one
process: flock locks belong to the open file, so a second acquire would block.
"""
from __future__ import annotations

import contextlib
from pathlib import Path

try:
    import fcntl
except ImportError:  # Windows desktop runs shops one at a time.
    fcntl = None

LOCK_DIR = Path("logs")
# Local CSV ledgers and markers that the encrypted state snapshot packages.
STATE_FILES = "state_files"
# Google Sheet next-blank-row detection, paste and verification.
GOOGLE_SHEET = "google_sheet"


@contextlib.contextmanager
def file_lock(name: str):
    if fcntl is None:
        yield
        return
    LOCK_DIR.mkdir(parents=True, exist_ok=True)
    with (LOCK_DIR / f".{name}.lock").open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
