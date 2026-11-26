#!/usr/bin/env bash
set -euo pipefail

python -m pip install -r requirements-relogin.txt
# Verify Playwright can actually start the runner's Chrome before selecting it.
if command -v google-chrome >/dev/null && python - <<'PY'
from playwright.sync_api import sync_playwright

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(channel="chrome", headless=True)
    browser.close()
PY
then
  echo "BROWSER_CHANNEL=chrome" >> "$GITHUB_ENV"
else
  python -m playwright install --with-deps --no-shell chromium
  echo "BROWSER_CHANNEL=" >> "$GITHUB_ENV"
fi

packages=()
command -v Xvfb >/dev/null || packages+=(xvfb)
command -v x11vnc >/dev/null || packages+=(x11vnc)
if (( ${#packages[@]} )); then
  sudo apt-get install -y --no-install-recommends "${packages[@]}" || {
    sudo apt-get update
    sudo apt-get install -y --no-install-recommends "${packages[@]}"
  }
fi
