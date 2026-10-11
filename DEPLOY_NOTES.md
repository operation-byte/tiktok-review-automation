# Deployment and verification

This archive is a safe source-only patch; it contains **no bot tokens, cookies, encrypted historical state, or .env files**. Do not replace the production `automation-state` branch.

## GitHub

Existing repo from supplied `.git/config`: `operation-byte/tiktok-review-automation`. Push these source files to `main` using your authenticated GitHub CLI or Git credential manager. Do not push the original ZIP: it contains sensitive material. Run GitHub Actions `check-setup.yml`, then a `dry-run.yml` per shop. Confirm `AUTOMATION_SHOPS=welive_main,snackersbox,konomi_living`, and keep `ENABLE_SUBMIT=false` until all three shops pass supervised dry-runs and you confirm the Sheet append writer works.

## Cloudflare

The deployed Worker is `tiktok-review-control` (do **not** overwrite `welivebot`). Bundle `telegram-control/pages.mjs` by running `cd telegram-control && npm ci && npm run build && npm test`. Deploy `worker.mjs` using Wrangler with the existing `Control` Durable Object binding and secrets preserved. No secret values are included here. Preserve existing Telegram webhook to `/telegram` and original stock webhook forwarding.

## Login UX

Send `/tt_login welive_main`, `/tt_login snackersbox`, or `/tt_login konomi_living` to the bot. Open the private link; it now forwards to the browser automatically. The browser reconnects during startup. Sign in to TikTok in the remote browser, navigate to Product Ratings, and tap **Verify & save login**. Confirm Telegram success before executing a dry-run.

## Known limits

This remains no-API Playwright through GitHub Actions; Cloudflare only handles Telegram, temporary encrypted remote-browser relay, and control state. GitHub runner startup can be delayed, and TikTok can refuse cloud-hosted browser login. Actual deployment, webhook reachability, GitHub Secrets, state branch and end-to-end TikTok login must be checked against connected production accounts.
