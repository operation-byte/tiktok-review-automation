# TikTok reviews and BigSeller stock automation

Runs TikTok Seller Center review checks and BigSeller stock exports on GitHub
Actions, with private Telegram controls and a shared phone-compatible login
viewer. Start with [setup and operation](START_HERE.md), or use the
[deployment guide](DEPLOY_NOTES.md) to update an existing installation.

- **Shop selection:** `AUTOMATION_SHOPS` selects WELIVE (`welive_main`), Snacker's Box (`snackersbox`) and/or Konomi Living (`konomi_living`). Only selected shops run, sequentially.
- **Schedule:** TikTok checks run daily at 09:15 Asia/Singapore when `AUTOMATION_ENABLED=true`; `ENABLE_SUBMIT` separately controls public replies.
- **Method:** existing Playwright browser UI; no TikTok API.
- **Google spreadsheet:** original `TikTok Negative Review KPI`, `Backend Data` tab (`gid=298261214`); authenticated browser CSV reads and browser row entry, with duplicate tracking preserved in encrypted state.
- **Safeguards:** historical deduplication ledger, explicit submission flag, sensitive review holds, state restore failure blocks all checks, isolated worker timeouts, encrypted data transfer.
- **Review eligibility:** original rolling 30-day window, no fixed start date. Only unreplied negative/neutral reviews are considered; historical reply identities, ambiguous attempts and Sheet markers are preserved in encrypted state.

More context: `.github/workflows/{daily-reviews,check-setup}.yml`; `github_daily.py`; `scripts/finish_setup.py`.

## Automatic reply rules

The scheduled runner uses `manual_logic` (the historical flag name for automatic
category-based routing). `review_policy.py` supplies the same categories for the
public reply and each new Google Sheet row.

- Only the negative (1–2 stars) and neutral (3 stars) queues are processed. Reviews
  must be dated within the rolling last 30 days, have a current Reply action and
  a confirmed zero response count. Future dates, missing dates, prior submissions
  and ambiguous attempts are not posted automatically.
- Explicit complaints take precedence over praise or “not yet tried”. Multiple
  recognised complaints are retained in the Sheet category and combined in the
  response. Fast delivery and good packaging alone are not complaints.
- “Not yet tried” gets a thank-you without an apology. Positive feedback gets an
  acknowledgement, even in a low-star queue. Date, missing/wrong item, packaging,
  delivery, usage, service, value and taste/quality concerns get relevant wording.
- Unrecognised wording gets a neutral request to contact TikTok Shop chat with
  details. Blank ratings use the queue's fallback. This is deterministic text
  matching, not an AI language model; it cannot guarantee understanding every
  language, slang expression or sarcastic review. The regression cases include
  actual historical wording and common negation/mixed-feedback cases.
- Serious concerns (including illness, injury, contamination and authenticity
  allegations) retain the existing manual hold by default. No unsupported refund,
  replacement, ingredient, safety or heating-mechanism promises are generated.
- A click alone is not a successful reply. TikTok must positively confirm it;
  uncertain submissions block retries. Browser Sheet logging happens only after
  a verified submission, with review-level deduplication and original follow-up
  column defaults. Previously posted replies and historical Sheet rows are not
  rewritten by a routing update.

## Telegram controls and relogin

Send commands privately to `@Welivestockcount_bot` from the configured owner account.
The existing stock-count commands remain intact; review controls use `/tt_`:

| Command | Action |
|---|---|
| `/tt_status` | Current GitHub jobs and last reported shop/login status |
| `/tt_run` or `/tt_run welive_main` | Live check of configured shops or the named shop |
| `/tt_dryrun` | Diagnose without public replies or Sheet writes |
| `/tt_pause` | Pause future live review runs; let an active run finish safely |
| `/tt_resume` | Restore daily 09:15 SGT runs; does not immediately post |
| `/tt_login` | Shows a button for each shop; tap one to get its private login browser link |
| `/tt_login welive_main` | Private WELIVE login browser link |
| `/tt_login snackersbox` | Private Snacker's Box login browser link |
| `/tt_login konomi_living` | Private Konomi Living login browser link |
| `/tt_errors` | Recent failed runs and recovery instructions |
| `/tt_help` | Command guide |

Login links work on phone or computer. Allow a few minutes for the GitHub browser
job to start, then open the private link. On a phone, tap a field in the remote
screen to open the keyboard and type directly. Use **Zoom** for readable text,
**Pan** to move around the enlarged screen, and **Next field** or **Enter** to
navigate. Turn Pan off to type; the keyboard button also shows or hides typing.
Complete TikTok login/OTP directly in the browser, confirm the correct shop, and press
Save login. The job checks the authenticated Product ratings page before saving
the refreshed cookie jar into the latest encrypted state. Telegram confirms the
save. Sessions expire 15 minutes after startup; an incomplete login leaves the
cloud state untouched. The browser closes at job end.

Login expiry is detected during scheduled/manual checks, not instantaneously.
A failed daily check sends the affected shop name and relogin command again.
For ordinary failures, use `/tt_errors` and `/tt_dryrun`, or send the run link to
Codex. Ambiguous replies require Seller Center inspection; no Telegram command
clears the duplicate history or blindly retries an uncertain submission.

The `tiktok-review-control` Cloudflare Worker authenticates Telegram's webhook
secret and the owner's private chat ID. Other messages forward to the original
`welivebot` Worker, which is not modified. A Durable Object deduplicates commands
and relays authenticated WebSockets; it does not log remote input or screen data.
The browser and VNC server exist only in the bounded GitHub relogin job, and VNC
listens only on loopback. Review and login jobs share the same concurrency lock.

Worker source: `telegram-control/`. Build with `npm ci --ignore-scripts && npm run
build`, then run `npm test` in that directory. Worker secrets are BOT_TOKEN,
GH_TOKEN, WEBHOOK_SECRET and CONTROL_TOKEN. Keep these out of source control.
GitHub uses CONTROL_TOKEN and the CONTROL_URL variable. Local setup recovery data
is protected under ignored `secrets/telegram-control.private`.

## BigSeller and Main Dashboard stock sync

The BigSeller integration uses the same private login-browser transport via
`/bs_login` and the Main Dashboard Warehouse controls. A separate GitHub runner
follows the supplied Single SKU / Export All workflow, validates the export and
asks the existing Main Dashboard Apps Script importer to update stock and refresh
Procurement. `/bs_dryrun` checks the export without changing stock;
`/bs_status`, `/bs_run`, `/bs_pause` and `/bs_resume` control this separate service.

See [BigSeller activation and verification](docs/bigseller-automation.md) for the
required deployment settings, four-times-daily schedule and first-login check.
The Main Dashboard source changes must be deployed alongside this repo's Worker
and workflows before these controls become available in the live dashboard.

## Repository layout

| Path | Responsibility |
|---|---|
| `auto_reply_reviews.py`, `review_policy.py`, `review_detection.py`, `queue_processor.py`, `reply_submission.py`, `tiktok_navigation.py` | Browser review flow, reply routing and submission checks |
| `github_daily.py`, `cloud_controller.py`, `google_sheet_sync.py` | Hosted job orchestration, shared shop logic and optional Sheet API sync |
| `scripts/` | Encrypted state, setup, login browsers and BigSeller import orchestration |
| `telegram-control/` | Cloudflare Worker, bundled noVNC viewer and control tests |
| `integrations/bigseller-export/` | Node/Playwright inventory exporter and Windows fallback scripts |
| `integrations/main-dashboard/` | Patches and deployment notes for the separate Apps Script project |
| `.github/workflows/`, `tests/` | Hosted jobs and offline Python regressions |

`cloud_controller.py` also retains a standalone Telegram polling/scheduling entry
point and `cloud_login.py` for privately hosted servers. GitHub Actions imports
its job logic and uses the temporary Worker login viewer; it does not run the
standalone poller. Do not run both schedulers for the same shops.

## Development and tests

Use Python 3.12+ and Node.js 22. Create a virtual environment and install the
complete offline test dependencies:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m pytest -q
npm --prefix telegram-control ci --ignore-scripts
npm --prefix telegram-control run build
npm --prefix telegram-control test
npm --prefix integrations/bigseller-export ci --ignore-scripts
npm --prefix integrations/bigseller-export run check
npm --prefix integrations/bigseller-export test
git diff --check
```

On Windows, create the environment with `py -m venv .venv` and activate it with
`.venv\Scripts\Activate.ps1`. Offline tests need no Chromium download, saved
sessions, live account credentials or running cloud services. GitHub runs these
checks in `tests.yml`. Actual login, phone keyboard behavior, review submissions
and dashboard imports need separate supervised verification.

`requirements-github.txt` installs review-job dependencies;
`requirements-relogin.txt` adds the private-browser relay;
`requirements-bigseller.txt` adds inventory-file validation;
`requirements-dev.txt` adds pytest. Node lockfiles are committed; dependencies,
Python caches, local Cloudflare files and generated `telegram-control/pages.mjs`
are ignored. Rebuild the viewer after editing its source before deploying it.
