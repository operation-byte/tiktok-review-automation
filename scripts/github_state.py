"""Read and write only the encrypted state blob in a separate private GitHub branch.

Requires Actions GITHUB_TOKEN permissions: contents: write. Has no TikTok API access.
"""
from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request

BRANCH = "automation-state"
FILENAME = "automation-state.enc"

class StateStoreError(RuntimeError):
    pass


class GitHubStateStore:
    def __init__(self, token: str | None = None, repo: str | None = None):
        self.token = token or os.environ.get("GH_STATE_TOKEN", "")
        self.repo = repo or os.environ.get("GITHUB_REPOSITORY", "")
        if not self.token or self.repo.count("/") != 1:
            raise StateStoreError("GITHUB_REPOSITORY/GH_STATE_TOKEN required for encrypted state")
        self.base = "https://api.github.com/repos/" + self.repo
        self._sha = None

    def request(self, method: str, path: str, payload: dict | None = None, *, missing_ok: bool = False):
        headers = {
            "Authorization": "Bearer " + self.token,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "tiktok-daily-review-bot",
        }
        body = json.dumps(payload).encode() if payload is not None else None
        if body is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.base + path, data=body, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as res:
                return json.load(res)
        except urllib.error.HTTPError as e:
            if e.code == 404 and missing_ok:
                return None
            # No response-body logging: avoid leaking any access information.
            raise StateStoreError(f"GitHub state HTTP {e.code} during {method}") from None
        except urllib.error.URLError:
            raise StateStoreError("GitHub state API unreachable") from None

    def fetch(self) -> bytes | None:
        path = f"/contents/{FILENAME}?ref={BRANCH}"
        result = self.request("GET", path, missing_ok=True)
        if result is None:
            return None
        if not isinstance(result, dict) or result.get("type") != "file":
            raise StateStoreError("Unexpected GitHub state file response")
        self._sha = result.get("sha")
        if result.get("encoding") != "base64":
            raise StateStoreError("State blob is not base64 encoded")
        return base64.b64decode(result["content"])

    def ensure_branch(self) -> None:
        branch = self.request("GET", f"/git/ref/heads/{BRANCH}", missing_ok=True)
        if branch:
            return
        # Create from the default workflow commit. Existing main contents on this
        # private branch remain secret-free; only the .enc file carries sessions.
        sha = os.environ.get("GITHUB_SHA", "")
        if len(sha) != 40 or not all(c in "0123456789abcdef" for c in sha.lower()):
            raise StateStoreError("Cannot create state branch without the Actions commit SHA")
        self.request("POST", "/git/refs", {"ref": "refs/heads/" + BRANCH, "sha": sha})

    def save(self, encrypted: bytes) -> None:
        if len(encrypted) > 950_000:
            raise StateStoreError("Encrypted state is too large for GitHub Contents API")
        if not self._sha:
            self.ensure_branch()
        body = {
            "message": "Update encrypted TikTok review state",
            "branch": BRANCH,
            "content": base64.b64encode(encrypted).decode("ascii"),
        }
        if self._sha:
            body["sha"] = self._sha
        resp = self.request("PUT", f"/contents/{FILENAME}", body)
        self._sha = resp.get("content", {}).get("sha")
        if not self._sha:
            raise StateStoreError("State save returned no committed blob SHA")
