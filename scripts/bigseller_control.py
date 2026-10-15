"""Aggregate BigSeller status and owner notifications; never browser credentials."""
import json
import os
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo


def notify(text):
    from scripts.notify_failure import send_notice
    send_notice(text)


def publish(status, **details):
    payload = {'service': 'bigseller', 'status': status,
               'when': datetime.now(ZoneInfo('Asia/Singapore')).isoformat(timespec='seconds')}
    payload.update({k: v for k, v in details.items() if k in ('login_needed', 'rows', 'fileId', 'lastImportAt', 'dry_run')})
    url, key = os.getenv('CONTROL_URL', '').rstrip('/'), os.getenv('CONTROL_TOKEN', '')
    if not url or not key:
        return
    req = urllib.request.Request(url + '/api/summary', data=json.dumps(payload).encode(), method='POST',
                                 headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json', 'User-Agent': 'BigSellerControl/1.0'})
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            response.read()
    except Exception:
        print('BigSeller control summary could not be refreshed')
