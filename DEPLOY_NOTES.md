# Deployment and verification

The source repository is `operation-byte/tiktok-review-automation`. GitHub Actions
runs the Python jobs, Cloudflare hosts the Telegram control/viewer, and the
separate Main Dashboard Apps Script project imports BigSeller stock.

Pushing source updates GitHub's next workflow runs. It does not deploy the
Cloudflare Worker or Main Dashboard. The offline regression workflow runs on
pushes to `main` and on pull requests; it does not open authenticated browsers or
change external stock/review data.

## Validate and push source

Follow the local test commands in [README.md](README.md#development-and-tests).
Review `git diff --check`, the staged file list, and the target branch before
committing and pushing. Private runtime data belongs outside source control;
`.gitignore` excludes sessions, credentials, backups, dependencies and diagnostics.

Keep `automation-state` and `bigseller-state` intact. Those branches hold encrypted
operational state and are managed by the runners. Do not replace them with a
source branch or rebootstrap from an older backup during a code update.

The configured TikTok profiles come from the `AUTOMATION_SHOPS` Actions variable;
the available profiles are `welive_main`, `snackersbox`, and `konomi_living`.
`AUTOMATION_ENABLED` gates scheduled review checks and `ENABLE_SUBMIT` permits
public replies. Preserve these settings when updating an existing deployment.
For a new installation, leave public replies disabled until supervised validation.

## Deploy the Telegram Worker

Deploy only `tiktok-review-control`, using `telegram-control/wrangler.toml`.
`welivebot` remains the downstream handler for ordinary stock-bot traffic.

```sh
cd telegram-control
npm ci --ignore-scripts
npm run build
npm test
npx wrangler deploy
```

Build `pages.mjs` before deploying; it bundles the viewer and noVNC and is not
committed. Preserve the `CONTROL` Durable Object, `WEBHOOK_AUDIT` KV binding,
`LEGACY_BOT` service binding, and existing secrets. `keep_vars=true` preserves
remote variables; the deploy command requires authenticated Cloudflare access.

The Worker uses `BOT_TOKEN`, `GH_TOKEN`, `WEBHOOK_SECRET`, `CONTROL_TOKEN`,
`OWNER_ID`, `REPO` and `PUBLIC_URL`. BigSeller dashboard controls additionally use
`BIGSELLER_DASHBOARD_TOKEN`; `LEGACY_WEBHOOK_SECRET` is optional when the downstream
proxy uses a different secret. GitHub uses matching `CONTROL_TOKEN` and
`CONTROL_URL` settings, plus its Telegram and encrypted-state credentials.
Keep values in the service secret stores, never in documentation or commits.

Verify `/health`, private owner commands, login buttons, and forwarding of an
ordinary stock-bot command. Test a fresh private viewer link: enter credentials
in the remote browser, open Product Ratings or BigSeller Inventory List, and tap
**Save login**. Confirm Telegram success before running a supervised dry run.
On a phone, verify tap-to-type, Zoom/Pan, Next field, and Enter with the keyboard
open. Unit tests cannot establish physical-phone keyboard behavior.

## BigSeller and Main Dashboard

Push the BigSeller workflows and deploy the updated Worker before using the new
login-reminder button. The stock importer and Warehouse controls are separate
Apps Script changes; follow [BigSeller setup](docs/bigseller-automation.md) and
[Main Dashboard integration](integrations/main-dashboard/README.md).
Preserve each deployment's execution identity and access settings.

Check a supervised `/bs_dryrun`, then a permitted live sync. Confirm the receipt,
SKU quantities, Procurement refresh and stock alerts. A login failure must leave
stock intact; a refresh failure after import requires receipt-based recovery,
not another upload of the same stock file.

## Operating limits

GitHub runner startup can be delayed. TikTok and BigSeller can reject hosted
browser sessions or require an OTP. Verify real authentication, Telegram delivery,
state persistence and any public submission/import against the deployed services;
offline tests do not establish those external behaviors.
