"""Interactive TikTok Seller Center login inside the private noVNC X display."""
from __future__ import annotations
import argparse
import time
from playwright.sync_api import sync_playwright
from auto_reply_reviews import (PROFILES, PRODUCT_RATING_BASE_URL, SELLER_REGION,
                                launch_profile_context, load_cookies_into, save_cookies)


def do_login(profile: str, seconds: int) -> int:
    print(f"Starting login UI for {profile}. Access noVNC using an SSH tunnel.", flush=True)
    with sync_playwright() as p:
        args = argparse.Namespace(headless=False, browser_window_state="maximized")
        context = launch_profile_context(p.chromium, PROFILES[profile], args)
        try:
            load_cookies_into(context, profile)
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(f"{PRODUCT_RATING_BASE_URL}?shop_region={SELLER_REGION}", wait_until="domcontentloaded", timeout=60000)
            expiry = time.monotonic() + seconds
            # Confirmation needs authenticated page navigation AND review-management content,
            # not simply a URL change that can happen during a login redirect.
            streak = 0
            while time.monotonic() < expiry:
                page.wait_for_timeout(3000)
                if page.is_closed():
                    print("Login page closed before verification")
                    return 2
                url = page.url.lower()
                try:
                    text = page.locator("body").inner_text(timeout=4000).lower()[:16000]
                except Exception:
                    text = ""
                if ("/product/rating" in url and "login" not in url
                        and any(x in text for x in ("negative review", "neutral review", "product rating", "reply now", "product ratings"))):
                    streak += 1
                else:
                    streak = 0
                if streak >= 2:
                    save_cookies(context, profile)
                    print("LOGIN_OK", flush=True)
                    return 0
            print("Login not verified before timeout; session was not saved", flush=True)
            return 1
        finally:
            context.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", choices=list(PROFILES), required=True)
    parser.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args()
    raise SystemExit(do_login(args.profile, args.timeout))
