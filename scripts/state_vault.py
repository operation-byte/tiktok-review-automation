"""Authenticated encryption for small, explicit TikTok automation state files.

Never stores unencrypted sessions or customer review history in the Git repository.
Use an independently generated Fernet key in the GitHub secret STATE_ENCRYPTION_KEY.
"""
from __future__ import annotations

import argparse
import io
import os
import stat
import zipfile
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

ROOTS = ("alerts/cookie_jars/",)
FILES = {
    "logs/replied_reviews.csv", "logs/review_dates.csv",
    "logs/google_sheet_logged_keys.txt",
    "state/controller.json", "state/history_imported.ok",
    "alerts/login_required_profiles.txt", "alerts/profile_launch_failed_profiles.txt",
}
MAX_BYTES = 800_000  # GitHub Contents API size limit is 1 MB, leave room for encryption.
MAGIC = b"TIKTOK_REVIEW_V6_1\n"


def allowed(filename: str) -> bool:
    p = Path(filename)
    return (not p.is_absolute() and ".." not in p.parts and
            (filename in FILES or any(filename.startswith(x) and filename.endswith(".json") for x in ROOTS)))


def get_cipher(key: str | None = None) -> Fernet:
    key = (key or os.environ.get("STATE_ENCRYPTION_KEY", "")).strip()
    if not key:
        raise RuntimeError("STATE_ENCRYPTION_KEY not set; refusing to access state")
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, TypeError):
        raise RuntimeError("STATE_ENCRYPTION_KEY must be a valid Fernet key") from None


def pack(root: Path, key: str | None = None) -> bytes:
    root = root.resolve()
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=8) as z:
        for name in sorted(FILES):
            p = root / name
            if p.is_file() and not p.is_symlink():
                if p.stat().st_size > MAX_BYTES:
                    raise RuntimeError(f"State file too large to package: {name}")
                z.write(p, arcname=name)
        jar_dir = root / "alerts/cookie_jars"
        if jar_dir.is_dir():
            for p in sorted(jar_dir.glob("*.json")):
                name = p.relative_to(root).as_posix()
                if not p.is_symlink() and allowed(name):
                    z.write(p, arcname=name)
    if len(data.getvalue()) > MAX_BYTES:
        raise RuntimeError("State snapshot too large for GitHub Contents API; inspect local files")
    return MAGIC + get_cipher(key).encrypt(data.getvalue())


def unpack(encrypted: bytes, root: Path, key: str | None = None) -> None:
    if not encrypted.startswith(MAGIC):
        raise RuntimeError("Unknown state format: refusing unsafe restore")
    try:
        plaintext = get_cipher(key).decrypt(encrypted[len(MAGIC):])
    except InvalidToken:
        raise RuntimeError("State decryption failed: wrong key or corrupted state") from None
    if len(plaintext) > 5 * MAX_BYTES:
        raise RuntimeError("Decompressed state exceeded safety limit")
    root = root.resolve()
    with zipfile.ZipFile(io.BytesIO(plaintext)) as z:
        for member in z.infolist():
            if not allowed(member.filename) or member.is_dir():
                raise RuntimeError("State archive has unexpected file; restore aborted")
            # Reject symlinks in ZIP created outside our packer.
            mode = member.external_attr >> 16
            if stat.S_ISLNK(mode) or member.file_size > MAX_BYTES:
                raise RuntimeError("Unsafe state file; restore aborted")
        for member in z.infolist():
            dest = root / member.filename
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_suffix(dest.suffix + ".tmp")
            tmp.write_bytes(z.read(member))
            tmp.chmod(0o600)
            tmp.replace(dest)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("pack", "unpack"))
    parser.add_argument("filename")
    parser.add_argument("--root", default=".")
    args = parser.parse_args()
    file = Path(args.filename)
    if args.action == "pack":
        file.write_bytes(pack(Path(args.root)))
        file.chmod(0o600)
        print("Encrypted state packaged. Never upload the decrypted files to GitHub.")
    else:
        unpack(file.read_bytes(), Path(args.root))
        print("Encrypted state restored.")


if __name__ == "__main__":
    main()
