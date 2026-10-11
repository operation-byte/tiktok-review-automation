"""Publish aggregate operational status, never customer rows or login cookies."""
import json
import os
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo


def publish(state):
    url=os.getenv('CONTROL_URL','').rstrip('/')
    key=os.getenv('CONTROL_TOKEN','')
    if not url or not key:
        return
    payload={'when':datetime.now(ZoneInfo('Asia/Singapore')).isoformat(timespec='seconds'),
             'shops':{shop:{k:row.get(k) for k in ('status','login_needed','when')}
                      for shop,row in state.get('shops',{}).items() if shop in ('welive_main','snackersbox')}}
    req=urllib.request.Request(url+'/api/summary',data=json.dumps(payload).encode(),
        headers={'User-Agent':'TikTokReviewControl/1.0','Authorization':'Bearer '+key,'Content-Type':'application/json'},method='POST')
    try:
        with urllib.request.urlopen(req,timeout=15) as r:
            r.read()
    except Exception:
        print('Telegram control status cache could not be refreshed; saved GitHub state remains authoritative.')
