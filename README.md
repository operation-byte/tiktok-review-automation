# TikTok Seller Center daily review automation — GitHub + Telegram

Start with **[START_HERE.md](START_HERE.md)**. On Windows, run `FINISH_SETUP_WINDOWS.cmd` for assisted no-card setup, including a private GitHub repository, secure encrypted historical state and outbound Telegram alerts.

- **Schedule:** daily at 09:15 Asia/Singapore, all three shops sequentially.
- **Method:** existing Playwright browser UI; no TikTok API.
- **Google spreadsheet:** original `TikTok Negative Review KPI`, original `Backend Data` tab; read-only connection test precedes new-record appends.
- **Safeguards:** historical deduplication ledger, explicit submission flag, sensitive review holds, state restore failure blocks all checks, isolated worker timeouts, encrypted data transfer.
- **Not possible without external authorization:** create your personal GitHub login, issue a new Telegram BotFather token, authorize a Google service account, or complete TikTok's interactive authentication. These require one-time actions in accounts controlled by you.
- **Initial state:** public replies OFF. Live browser operation not yet verified against TikTok Singapore on GitHub-hosted Linux.

More context: `.github/workflows/{daily-reviews,check-setup}.yml`; `github_daily.py`; `scripts/finish_setup.py`.
