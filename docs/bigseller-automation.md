# BigSeller login, export and Main Dashboard stock sync

The browser runner lives in this repository. The stock importer, dashboard UI,
Procurement refresh and stock alerts live in the existing **Main Dashboard**
Apps Script project (`../Main Dashboard`). Both parts are required for activation.

The implemented flow is:

1. Warehouse → **Log in to BigSeller**, or Telegram `/bs_login`.
2. GitHub opens the existing private noVNC browser transport. Log into BigSeller,
   open Inventory List, confirm the account, and press **Save login**.
3. The login is saved as an encrypted Playwright state in the separate private
   `bigseller-state` branch. TikTok's review ledger and login files are separate.
4. **Dry run** downloads and validates the ZIP's Single SKU → Apply → Export All
   export without changing stock. **Run sync** additionally uploads and imports it.
5. The signed Apps Script endpoint creates the file in the existing Incoming
   folder and invokes the existing importer for that exact file ID.
6. The importer updates `Bigseller Count Stocks!A:H`, refreshes existing formulas,
   invalidates Warehouse/Procurement caches, refreshes Procurement and checks
   the existing Telegram stock-transition alerts.
7. The runner reports success only after a completed receipt matches the
   uploaded file's checksum and expected SKU row count.

When a scheduled or manual export detects an expired session, Telegram sends a
**Login to BigSeller** button. Tap it for the same private login flow. Unrelated
export failures preserve the login-required flag until a verified login or export
succeeds.

Both services share the phone viewer: tap the remote screen to open the keyboard,
use **Zoom** and **Pan** for larger controls, and use **Next field** and **Enter**
above the keyboard. Turn Pan off before typing.

## One-time configuration

Use different random secrets for the dashboard controller and import endpoint.
Keep their values in service secret stores, never in source or Telegram.

| Location | Setting | Value / purpose |
|---|---|---|
| Cloudflare `tiktok-review-control` secret | `BIGSELLER_DASHBOARD_TOKEN` | New secret matching Apps Script `BIGSELLER_CONTROL_TOKEN` |
| Apps Script Script Property | `BIGSELLER_CONTROL_TOKEN` | Same dashboard-control secret |
| Apps Script Script Property | `BIGSELLER_CONTROL_URL` | Existing `tiktok-review-control` HTTPS base URL |
| Apps Script Script Property | `BIGSELLER_IMPORT_SECRET` | Separate HMAC import secret |
| GitHub Actions secret | `BIGSELLER_IMPORT_SECRET` | Same import secret |
| GitHub Actions variable | `BIGSELLER_IMPORT_URL` | Existing Main Dashboard public webhook `/exec` URL |
| GitHub Actions variable | `BIGSELLER_AUTOMATION_ENABLED` | Initially `false`; Resume changes it to `true` |
| Apps Script Script Property, optional | `BIGSELLER_AUTOMATION_ADMIN_EMAILS` | Comma-separated administrators; defaults to `jinyt@welive.network,operation@welive.network` |
| GitHub Actions secret, optional | `BIGSELLER_STATE_ENCRYPTION_KEY` | Separate Fernet key; defaults to existing `STATE_ENCRYPTION_KEY` |
| Cloudflare secret, if downstream key differs | `LEGACY_WEBHOOK_SECRET` | Existing `welivebot` proxy's webhook key; otherwise verified incoming `WEBHOOK_SECRET` is forwarded |

Existing GitHub `CONTROL_URL`, `CONTROL_TOKEN`, `STATE_ENCRYPTION_KEY` and Telegram
secrets remain in use. No Google service-account key or mapped Windows Drive is
needed for the hosted path: the existing Apps Script deployment accesses Drive.

Deploy the updated Main Dashboard code to **both existing deployments**, using
its `scripts/deploy.sh` procedure and preserving their execution identities and
access settings. Deploy the updated Cloudflare Worker with the existing bindings,
and push this repository's workflows and source to its private main branch.
Source edits alone do not activate these services.

The public webhook deployment is HMAC-gated for BigSeller actions; the domain
dashboard uses Google-verified account identity. Only configured administrators
can start login, export, pause or resume. Company users may inspect status.
The dashboard-control token cannot access the runner's remote-session API.

## First run and ongoing operation

1. Open Warehouse and log in to BigSeller. Credentials and OTPs are entered in
   the private remote browser, never sent as chat messages.
2. Run a dry run; confirm the downloaded export validates on a hosted runner.
   Browser/IP-bound BigSeller sessions must be proved in this first run.
3. Resume sync and run one supervised sync. Compare sample SKU quantities in
   BigSeller, the source Sheet, Warehouse and Procurement. Confirm physical
   counts, dates, expiry dates, overrides and history remain intact.
4. Check the Telegram summary and existing stock alerts. Verify ordinary bot
   commands traverse the authenticated legacy proxy as well as `/tt_*` and `/bs_*`.
5. Scheduled syncs run at 00:00, 06:00, 12:00 and 18:00 Asia/Singapore while enabled.
   GitHub may delay scheduled starts. Disable the old Windows producer if both
   would feed the same Incoming folder.

BigSeller login and export jobs share `bigseller-browser-state` concurrency and
do not overwrite the same encrypted state concurrently. BigSeller and TikTok
jobs have separate state locks. A dry run can run while scheduled sync is paused.

## Stock semantics and failures

The stock quantity is **Available for whole warehouse**, not Total On Hand Stock.
The existing physical-count lookups use SKU Name with the column O override;
exact names must be retained. Manual physical quantities, checked dates, expiry
dates and stock history remain owned by the stock-count workflow.

Invalid/empty/duplicate-SKU exports cannot replace the source snapshot. Upload
retries use run ID plus SHA-256, and completed imports use the Drive file receipt
to avoid repeating a committed import after a lost response. `.partial` files
are ignored. If a post-import Procurement/alert step fails, the receipt reports
that partial outcome instead of claiming the whole stock sync succeeded.

This feature refreshes the dashboard's reference stock from BigSeller. Existing
Stock In / Stock Out adjustment exports remain the separate count-adjustment
workflow; the downloader does not submit adjustments back into BigSeller.

## Verification

```sh
python -m pip install -r requirements-dev.txt
python -m pytest -q
cd telegram-control
npm ci --ignore-scripts
npm run build
npm test
cd ../integrations/bigseller-export
npm ci --ignore-scripts
npm run check
npm test
```

Run the Main Dashboard Node and Python tests from that folder as well. A real
BigSeller login, supervised export and deployed import are still required for
end-to-end verification; unit tests do not establish session portability.

Implementation references: [Playwright authentication state](https://playwright.dev/docs/auth),
[Apps Script locks](https://developers.google.com/apps-script/reference/lock/lock-service),
[Cloudflare service bindings](https://developers.cloudflare.com/workers/runtime-apis/bindings/service-bindings/).
