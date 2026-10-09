# One daily check of 3 TikTok shops — finish setup

## Current deployment

This deployment selects **WELIVE and Snacker's Box only** (`AUTOMATION_SHOPS=welive_main,snackersbox`); Konomi Living is excluded. Google Sheets uses the original browser writer with authenticated CSV reads from `Backend Data`, `gid=298261214`. No JSON key is required. The explicit fresh-start marker restricts processing to reviews dated **10 October 2026 or later**, instead of claiming historical replies were imported. Local logins and Sheet reads have been verified. Encrypted sessions are stored in the private GitHub state branch; their key is a GitHub Secret. Use `dry-run.yml` for manual cloud validation. The daily schedule and public replies remain disabled until approved and validated.

**No credit card, no Oracle VM, no need to leave Windows on.** GitHub Actions runs the **original Python/Playwright** review checks at **09:15 Asia/Singapore** each day. Reviews go into your existing **[TikTok Negative Review KPI](https://docs.google.com/spreadsheets/d/1Q_8FRZBSRmuL6ncKshjME1JccLWU0XaMvSCVspwvHKs/edit)**, tab `Backend Data`, once its Google service-account permissions are configured. Telegram warns when login is required.

## One-time installation (automates GitHub setup)

1. Install [Python 3.12+](https://www.python.org/downloads/), [Git](https://git-scm.com/downloads), and [GitHub CLI](https://cli.github.com/), and create a free GitHub account if needed. Do not put any sessions or customer data into a **public** GitHub repository.
2. Create a **new Telegram bot** using [@BotFather](https://t.me/BotFather). Keep the bot token private. You will enter it in a hidden prompt on your own PC. You will be asked to open the bot and send `/start`; the installer retrieves your personal user/chat IDs automatically.
3. Put your original `Tiktok Auto Reply Negative Reviews.zip` in the same folder as this extracted project, or in Windows Downloads. **Double-click `FINISH_SETUP_WINDOWS.cmd`.** The setup wizard also accepts `python -m scripts.finish_setup --original-zip "C:\\your\\path\\Tiktok Auto Reply Negative Reviews.zip"`.
4. Authorize **GitHub in the browser** when asked. The installer automatically creates a private repository, pushes only source files, encrypts and uploads the old review history + 3 TikTok session files into a separate state branch, registers GitHub secrets and variables, and triggers a connection check and an initial **dry-run**. Neither workflow posts a public reply during setup.
5. Open the GitHub Actions link shown by the installer and review the results. TikTok may require a one-time manual login or reject Linux headless browsers. **Live posting is intentionally OFF until all shops have passed a supervised dry-run.** Disable the old Windows Task Scheduler tasks before the first actual live run.

## Connecting the *same* Google Sheet

Browser mode reuses the original Playwright row writer and needs no service-account key. Set the repository variable `GOOGLE_SHEETS_MODE=browser`. Only verified live submissions append rows; dry runs leave the Sheet untouched. The writer checks existing order IDs and preserves `logs/google_sheet_logged_keys.txt` in encrypted state across runs. Sheet failures send a Telegram alert; review the saved CSV and Sheet before retrying.

For each shop's local automation browser, log into TikTok and open the existing KPI Sheet in another tab. Sign into Google if editing requires it, then save that browser session. The saved state must include both TikTok and Google access. The writer reads the CSV export through the authenticated browser before appending and verifying rows. The setup check can read headers anonymously when permitted; private-sheet validation is deferred until Google login. Cloud session portability must be validated before enabling replies. Do not make a private Sheet public to work around access failures.

For a local login, use `.venv/bin/python auto_reply_reviews.py --mode login --profile welive_main` (then `snackersbox`, then `konomi_living`). Complete both TikTok and Google authentication in that shop's browser before pressing ENTER in the terminal. GitHub Actions does not provide an interactive login screen. History import remains required before running cloud checks.

Your existing file and column layout are preserved. Your ChatGPT Google Drive connection does not transfer an API credential to GitHub. For the alternative API mode, a Google Cloud service account with the Google Sheets API enabled must have **Editor** access to that existing Sheet. If you already have its private JSON key, run:

```powershell
python -m scripts.finish_setup --google-credentials "C:\path\service-account.json"
```

The installer reads and verifies Sheet access (without writing), uploads the key as a GitHub Secret and configures `GOOGLE_SHEETS_MODE=api`. If the Sheet has not yet been shared, the installer gives you the service-account email. Share the Sheet, then rerun the command. **Never upload the JSON key to GitHub source control or send it to Telegram.** If you don't have a service account, Google Sheets sync stays OFF; your current spreadsheet is not modified or replaced.

## Re-login alerts

If TikTok expires a shop session, the daily job stops processing that shop, flags the error in Telegram and preserves the latest encrypted state. **Telegram cannot complete TikTok's 2FA for you**, so the affected login must be refreshed interactively. This GitHub free-runner architecture does not provide a persistent remote-browser login. A failed/ambiguous submit must be checked in Seller Center before retrying.

## Safety / running costs

- All scheduled runs are sequential: **WELIVE → Snacker's Box → Konomi Living**.
- The automation will try the first cloud dry-run, but some parts cannot be verified until GitHub, Telegram, Google and TikTok authenticate.
- It restores encrypted customer review history every run; private encryption key stays in GitHub Secrets and separately on your own computer.
- It never enables public posting without your supervision (`ENABLE_SUBMIT=false`). Once verified, change the repository Actions variable `ENABLE_SUBMIT` to `true` and ensure old Windows scheduled jobs are OFF.
- GitHub Free has included Actions minutes for private repositories; once the included allowance is exhausted without payment details, execution is blocked rather than charged. GitHub can delay or skip scheduled starts.

**If the installer reports a failure, fix the named prerequisite and rerun the same script.** It preserves previously uploaded state rather than replacing newer cloud review history.
