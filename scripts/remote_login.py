"""Bounded, owner-controlled remote TikTok login; latest state always restored first."""
import asyncio
import json
import os
import subprocess
import threading
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright
from scripts.github_state import GitHubStateStore
from scripts.state_vault import pack, unpack
from scripts.control_report import publish

SHOPS=('welive_main','snackersbox')


def control(operation):
    url=os.environ['CONTROL_URL'].rstrip('/')+'/api/session/'+os.environ['LOGIN_SESSION']+'/'+operation
    req=urllib.request.Request(url,headers={'Authorization':'Bearer '+os.environ['CONTROL_TOKEN']},
                              method='GET' if operation=='poll' else 'POST')
    with urllib.request.urlopen(req,timeout=15) as r:
        body=r.read()
        return json.loads(body) if operation=='poll' else None


async def relay(stop):
    from websockets.asyncio.client import connect
    url=os.environ['CONTROL_URL'].replace('https://','wss://').rstrip('/')+'/api/session/'+os.environ['LOGIN_SESSION']+'/agent'
    while not stop.is_set():
        try:
            async with connect(url,additional_headers={'Authorization':'Bearer '+os.environ['CONTROL_TOKEN']},
                               max_size=2**22,ping_interval=20) as ws:
                reader,writer=await asyncio.open_connection('127.0.0.1',5900)
                async def upload():
                    while not stop.is_set():
                        data=await reader.read(65536)
                        if not data:break
                        await ws.send(data)
                async def download():
                    async for data in ws:
                        if not isinstance(data,bytes):raise RuntimeError('Unexpected relay frame')
                        writer.write(data);await writer.drain()
                tasks=[asyncio.create_task(upload()),asyncio.create_task(download())]
                try:
                    await asyncio.wait(tasks,return_when=asyncio.FIRST_COMPLETED)
                finally:
                    for task in tasks:task.cancel()
                    await asyncio.gather(*tasks,return_exceptions=True)
                    writer.close();await writer.wait_closed()
        except Exception:
            if not stop.is_set():await asyncio.sleep(2)


def verified_login(page):
    if '/product/rating' not in page.url or 'login' in page.url.lower():return False
    text=page.locator('body').inner_text(timeout=4000).lower()
    return any(marker in text for marker in ('negative review','neutral review','product rating','reply now','product ratings'))


def main():
    import argparse
    from auto_reply_reviews import PROFILES,launch_profile_context,load_cookies_into,save_cookies,PRODUCT_RATING_BASE_URL
    import cloud_controller as c
    shop=os.environ.get('LOGIN_SHOP','')
    if shop not in SHOPS:raise RuntimeError('Unknown login shop')
    # Reject expired/unknown requests before opening a browser or touching sessions.
    session=control('poll')
    if session['shop']!=shop:raise RuntimeError('Session shop mismatch')
    store=GitHubStateStore();blob=store.fetch()
    if not blob:raise RuntimeError('Existing encrypted state required')
    unpack(blob,Path('.'))
    c.state=c.load_state()
    os.environ['DISPLAY']=':99'
    processes=[];stop=threading.Event();success=False
    try:
        processes.append(subprocess.Popen(['Xvfb',':99','-screen','0','1100x850x24','-nolisten','tcp'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL))
        time.sleep(1)
        processes.append(subprocess.Popen(['x11vnc','-display',':99','-localhost','-nopw','-forever','-shared','-rfbport','5900'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL))
        threading.Thread(target=lambda:asyncio.run(relay(stop)),daemon=True).start()
        with sync_playwright() as p:
            context=launch_profile_context(p.chromium,PROFILES[shop],argparse.Namespace(headless=False,browser_window_state='normal'))
            try:
                load_cookies_into(context,shop)
                page=context.pages[0] if context.pages else context.new_page()
                page.goto(PRODUCT_RATING_BASE_URL+'?shop_region=SG',wait_until='domcontentloaded',timeout=60000)
                control('ready')
                deadline=time.monotonic()+14*60
                while time.monotonic()<deadline:
                    page.wait_for_timeout(2500)
                    if not control('poll').get('save'):continue
                    if not verified_login(page):
                        c.notify(f'{shop}: login is not verified yet. Open Product ratings in Seller Center, then press Save login again.');continue
                    page.wait_for_timeout(3000)
                    if not verified_login(page):continue
                    save_cookies(context,shop)
                    c.state.setdefault('shops',{})[shop]={'status':'login refreshed','login_needed':False,'when':c.now().strftime('%Y-%m-%d %H:%M SGT')}
                    c.save_state();store.save(pack(Path('.')));publish(c.state)
                    c.notify(f'✅ {shop}: login verified and encrypted session saved. Use /tt_dryrun {shop} to check, or /tt_run {shop} for live replies.');success=True;break
            finally:context.close()
        if not success:c.notify(f'Login session for {shop} ended without saving. Use /tt_login {shop} for a fresh link. Existing cloud history/session were preserved.')
        return 0 if success else 2
    finally:
        stop.set()
        try:control('finish')
        except Exception:pass
        for proc in reversed(processes):
            proc.terminate()
            try:proc.wait(timeout=5)
            except subprocess.TimeoutExpired:proc.kill()


if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception as exc:
        import cloud_controller as c
        c.notify('Remote login could not complete ('+type(exc).__name__+'). No retry was started. Use /tt_errors or ask Codex to inspect the run.')
        raise SystemExit(1)
