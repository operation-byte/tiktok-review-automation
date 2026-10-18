"""One-time local bootstrap from the original Windows ZIP.

Only the reply-history CSV and the configured shop session JSONs are read. They are
NEVER added to the distribution or printed. The output encrypted file may be
committed to the private `automation-state` branch, never to a public repo.
"""
from __future__ import annotations

import argparse
import os
import sys
import zipfile
from pathlib import Path

from cryptography.fernet import Fernet
from scripts.bootstrap_state import SHOPS, validate
from scripts.state_vault import pack

SAFE_MAX = 2_000_000  # per zip member; protects against zip bombs and mistakes
KEY_NAME = ".state_key.private"


def extract_member(z: zipfile.ZipFile, suffix: str) -> bytes:
    norm = suffix.replace("\\", "/")
    # ZIP may have one outer project folder; do not trust arbitrary paths.
    choices = [m for m in z.infolist()
               if m.filename.replace("\\", "/").endswith("/" + norm)
               or m.filename.replace("\\", "/") == norm]
    if len(choices) != 1:
        raise RuntimeError(f"Expected exactly one {norm!r} in backup ZIP, found {len(choices)}")
    member = choices[0]
    if member.file_size > SAFE_MAX or member.is_dir():
        raise RuntimeError(f"Refusing unexpected large or directory member: {norm}")
    with z.open(member, "r") as stream:
        data = stream.read(SAFE_MAX + 1)
    if len(data) > SAFE_MAX:
        raise RuntimeError("Backup entry exceeded allowed size")
    return data


def prepare(source_zip: Path, output_root: Path, key_file: Path, history_zip: Path | None = None) -> dict:
    """Keep all decrypted bytes strictly within ignored working directories."""
    root = output_root.resolve()
    if not source_zip.is_file():
        raise RuntimeError("Cannot find original Windows backup ZIP")
    if history_zip is not None and not history_zip.is_file():
        raise RuntimeError("Cannot find separate history migration ZIP")
    # One-time operation only: replacing a newer live reply ledger with an
    # older backup would reintroduce duplicate-public-reply risk.
    if (root / "logs/replied_reviews.csv").exists() or (root / "automation-state.enc").exists():
        raise RuntimeError("Bootstrap is already initialized here. Refusing to overwrite reply history or encryption state.")
    with zipfile.ZipFile(source_zip) as source:
        jars = {shop: extract_member(source, f"alerts/cookie_jars/{shop}.json") for shop in SHOPS}
        if history_zip is None:
            history = extract_member(source, "logs/replied_reviews.csv")
        else:
            with zipfile.ZipFile(history_zip) as h:
                history = extract_member(h, "replied_reviews.csv")
    for shop, data in jars.items():
        path = root / "alerts/cookie_jars" / (shop + ".json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        if os.name != "nt":
            path.chmod(0o600)
    path = root / "logs/replied_reviews.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(history)
    if os.name != "nt":
        path.chmod(0o600)
    result = validate(root)  # raises if backup is malformed or missing verified submissions
    from datetime import datetime, timezone
    marker = root / "state/history_imported.ok"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(__import__("json").dumps({
        **result, "acknowledged_at_utc": datetime.now(timezone.utc).isoformat(),
    }), encoding="utf-8")
    if os.name != "nt":
        marker.chmod(0o600)
    if key_file.exists():
        key = key_file.read_text(encoding="utf-8").strip()
        Fernet(key.encode())  # validate key; never silently replace existing key
    else:
        key = Fernet.generate_key().decode()
        key_file.parent.mkdir(parents=True, exist_ok=True)
        # Use exclusive creation so existing keys can never be overwritten by race.
        with key_file.open("x", encoding="utf-8") as k:
            k.write(key + "\n")
        if os.name != "nt":
            key_file.chmod(0o600)
    encrypted = root / "automation-state.enc"
    encrypted.write_bytes(pack(root, key))
    if os.name != "nt":
        encrypted.chmod(0o600)
    return {"history": result["verified_submission_count"], "sessions": len(jars),
            "encrypted_path": str(encrypted), "private_key_path": str(key_file)}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Prepare encrypted state for GitHub from your OLD Windows project ZIP")
    p.add_argument("--original-zip", required=True, type=Path)
    p.add_argument("--history-zip", type=Path, default=None,
                   help="Optional private migration ZIP. Omit to use history in original ZIP.")
    p.add_argument("--root", type=Path, default=Path("."))
    p.add_argument("--key-file", type=Path, default=Path(KEY_NAME))
    args = p.parse_args(argv)
    try:
        result = prepare(args.original_zip, args.root, args.key_file, args.history_zip)
    except (OSError, ValueError, RuntimeError, zipfile.BadZipFile) as exc:
        print(f"BOOTSTRAP_FAILED: {exc}")
        return 1
    print(f"Validated {result['history']} historical submitted replies and {result['sessions']} saved session files.")
    print(f"Encrypted private GitHub state: {result['encrypted_path']}")
    print(f"Private key file: {result['private_key_path']} (NEVER upload to GitHub or share in a chat)")
    print("Next: put the encrypted .enc file only on the PRIVATE automation-state GitHub branch, ")
    print("and copy the private key CONTENT into the GitHub Secret STATE_ENCRYPTION_KEY.")
    print("These cookies could be expired; dry-run every configured shop before enabling real replies.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
