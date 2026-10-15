"""BigSeller browser state, encrypted independently of the TikTok review ledger."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from scripts.github_state import GitHubStateStore, StateStoreError

BRANCH = 'bigseller-state'
FILENAME = 'bigseller-state.enc'
MAX_BYTES = 700_000


def cipher():
    key = os.environ.get('BIGSELLER_STATE_ENCRYPTION_KEY') or os.environ.get('STATE_ENCRYPTION_KEY', '')
    if not key:
        raise StateStoreError('Browser state encryption key is not configured')
    return Fernet(key.encode())


def validate_state(state):
    if not isinstance(state, dict) or not isinstance(state.get('cookies'), list) or not isinstance(state.get('origins'), list):
        raise StateStoreError('Invalid BigSeller browser state')
    return state


class BigSellerStateStore(GitHubStateStore):
    def fetch(self):
        result = self.request('GET', f'/contents/{FILENAME}?ref={BRANCH}', missing_ok=True)
        if result is None:
            return None
        if result.get('type') != 'file' or result.get('encoding') != 'base64':
            raise StateStoreError('Invalid encrypted BigSeller state response')
        self._sha = result.get('sha')
        try:
            blob = base64.b64decode(result['content'], validate=False)
            if len(blob) > MAX_BYTES * 2:
                raise ValueError()
            return validate_state(json.loads(cipher().decrypt(blob)))
        except (InvalidToken, ValueError, KeyError, TypeError):
            raise StateStoreError('BigSeller state could not be decrypted; saved state was preserved') from None

    def save(self, state):
        raw = json.dumps(validate_state(state), separators=(',', ':')).encode()
        if len(raw) > MAX_BYTES:
            raise StateStoreError('BigSeller browser state exceeds storage limit')
        if not self.request('GET', f'/git/ref/heads/{BRANCH}', missing_ok=True):
            sha = os.environ.get('GITHUB_SHA', '')
            if len(sha) != 40 or any(c not in '0123456789abcdef' for c in sha.lower()):
                raise StateStoreError('Actions commit SHA required for state branch setup')
            self.request('POST', '/git/refs', {'ref': 'refs/heads/' + BRANCH, 'sha': sha})
        body = {'message': 'Update encrypted BigSeller login', 'branch': BRANCH,
                'content': base64.b64encode(cipher().encrypt(raw)).decode()}
        if self._sha:
            body['sha'] = self._sha
        result = self.request('PUT', f'/contents/{FILENAME}', body)
        self._sha = result.get('content', {}).get('sha')
        if not self._sha:
            raise StateStoreError('BigSeller state save was not confirmed')


def write_private(path, state):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Open with restrictive permissions from creation, including on new runners.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as handle:
        json.dump(validate_state(state), handle)
