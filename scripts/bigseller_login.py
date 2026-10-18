"""The existing temporary noVNC browser, with BigSeller-specific verification."""
from __future__ import annotations

import time
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright
from scripts.bigseller_state import BigSellerStateStore
from scripts.bigseller_control import notify, publish
from scripts.login_browser import control, private_desktop

INVENTORY_URL = 'https://www.bigseller.com/web/inventory/index.htm'


def verified_login(page):
    url = urlparse(page.url)
    if url.scheme != 'https' or url.hostname not in ('www.bigseller.com', 'bigseller.com') or not url.path.startswith('/web/inventory/'):
        return False
    if page.locator('input[type="password"]').first.is_visible():
        return False
    text = page.locator('body').inner_text(timeout=4000).lower()
    return 'inventory list' in text and 'single sku' in text and 'export' in text


def main():
    session = control('poll')
    if session.get('shop') != 'bigseller':
        raise RuntimeError('BigSeller login session does not match')
    store = BigSellerStateStore()
    state = store.fetch()  # Missing is valid for first login; corrupt state is an error.
    success = False
    try:
        with private_desktop(), sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=False)
            context = browser.new_context(storage_state=state, viewport={'width': 1440, 'height': 900}, locale='en-SG', timezone_id='Asia/Singapore')
            try:
                page = context.new_page()
                control('ready')
                page.goto(INVENTORY_URL, wait_until='domcontentloaded', timeout=90000)
                deadline = time.monotonic() + 14 * 60
                while time.monotonic() < deadline:
                    page.wait_for_timeout(2500)
                    if not control('poll').get('save'):
                        continue
                    try:
                        verified = verified_login(page)
                    except Exception:
                        verified = False
                    if not verified:
                        notify('BigSeller login is not verified yet. Open Inventory → Inventory List, confirm your account, then press Save login again.')
                        continue
                    store.save(context.storage_state(indexed_db=True))
                    publish('login refreshed', login_needed=False)
                    notify('✅ BigSeller login verified and encrypted session saved. Use Run sync in Warehouse or /bs_run to export and update stock; /bs_dryrun checks the export first.')
                    success = True
                    break
            finally:
                context.close()
                browser.close()
        if not success:
            notify('BigSeller login ended without saving. Use /bs_login for a new link. Your previous saved session was preserved.')
        return 0 if success else 2
    finally:
        try:
            control('finish')
        except Exception:
            pass


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print('BigSeller login failed: ' + type(exc).__name__)
        publish('login failed')
        raise SystemExit(1)
