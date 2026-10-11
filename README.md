# TikTok Seller Center daily review automation — GitHub + Telegram

Start with **[START_HERE.md](START_HERE.md)**. On Windows, run `FINISH_SETUP_WINDOWS.cmd` for assisted no-card setup, including a private GitHub repository, secure encrypted historical state and outbound Telegram alerts.

- **Configured shops:** WELIVE and Snacker's Box, sequentially; Konomi Living excluded via `AUTOMATION_SHOPS`.
- **Schedule:** enabled daily at 09:15 Asia/Singapore for the two configured shops.
- **Method:** existing Playwright browser UI; no TikTok API.
- **Google spreadsheet:** original `TikTok Negative Review KPI`, `Backend Data` tab (`gid=298261214`); authenticated browser CSV reads and browser row entry, with duplicate tracking preserved in encrypted state.
- **Safeguards:** historical deduplication ledger, explicit submission flag, sensitive review holds, state restore failure blocks all checks, isolated worker timeouts, encrypted data transfer.
- **Not possible without external authorization:** create your personal GitHub login, issue a new Telegram BotFather token, authorize a Google service account, or complete TikTok's interactive authentication. These require one-time actions in accounts controlled by you.
- **Review eligibility:** original rolling 30-day window, no fixed start date. Only unreplied negative/neutral reviews are considered; historical submitted/ambiguous identities are preserved. The original backup history and review-level Sheet markers have been merged into encrypted state.

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

Run regression checks with `.venv/bin/python -m pytest tests --ignore=tests/test_cloud_v3.py -q`.
The legacy `test_cloud_v3.py` currently cannot collect because its `deploy` module
is absent from this repository.

## Telegram controls and relogin

Send commands privately to `@Welivestockcount_bot` from the configured owner account.
The existing stock-count commands remain intact; review controls use `/tt_`:

| Command | Action |
|---|---|
| `/tt_status` | Current GitHub jobs and last reported shop/login status |
| `/tt_run` or `/tt_run welive_main` | Live check of both shops or the named shop |
| `/tt_dryrun` | Diagnose without public replies or Sheet writes |
| `/tt_pause` | Pause future live review runs; let an active run finish safely |
| `/tt_resume` | Restore daily 09:15 SGT runs; does not immediately post |
| `/tt_login welive_main` | Private WELIVE login browser link |
| `/tt_login snackersbox` | Private Snacker's Box login browser link |
| `/tt_login konomi_living` | Private Konomi Living login browser link |
| `/tt_errors` | Recent failed runs and recovery instructions |
| `/tt_help` | Command guide |

Login links work on phone or computer. Allow a few minutes for the GitHub browser
job to start, then open the private link. Tap a field in the remote screen; on
mobile, use the masked text box and Send button to type into that field. Complete
TikTok login/OTP directly in the browser, confirm the correct shop, and press
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
