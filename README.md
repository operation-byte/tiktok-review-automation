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
