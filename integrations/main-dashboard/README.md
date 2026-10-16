# BigSeller hosted import fix

`bigseller-hosted-import.patch` applies to the existing Main Dashboard Apps Script
project. The browser runner in this repo and the importer are separate deployments.

The hosted importer must update the BigSeller reference snapshot without
rewriting Physical Count formulas or repairing its merchant-code cells. Those
cells can be protected against the webhook identity. Apps Script batches writes,
so the protection exception can surface at the later Procurement source read,
after the stock snapshot has already been committed. Existing formulas recalculate
from the reference snapshot; explicit dashboard setup/repair actions still own
Physical Count configuration.

Technical import/refresh failures are recorded in signed import receipts and
Apps Script execution logs. Procurement group messages remain stock-transition
alerts. GitHub's operational notices remain restricted to the configured private
owner chat.

Apply the patch from the Main Dashboard project root and run its Node tests.
Deploy to the existing public webhook with its existing execution identity and
access settings. Time triggers use the updated source. Domain dashboard releases
must retain their separate execution identity.

For a `refresh_failed` receipt, inspect the matching file ID and checksum, then
use the signed import action with `retryRefresh: true` to retry Procurement only.
Do not upload or reimport the same stock file just to recover a refresh failure.

`procurement-super-center-link.patch` applies after the hosted import patch.
Alert links use `DASHBOARD_SIGN_IN_URL_` from `Code.js` with `view=procurement`.
The public webhook's `ScriptApp.getService().getUrl()` cannot serve the Super
Center UI and must never be used as the alert destination. Stale legacy URL
properties are ignored. Existing Telegram messages retain their original URLs;
new stock alerts use the corrected link.

## Deployment identity

The webhook also serves the private Task Tracker runner. It must execute as
`jinyt@welive.network`; the company dashboard remains deployed as `operation@`.
Never infer the Google identity from a credential profile called `default`.

Use a dedicated `clasp -u jinyt login` profile. After reviewing and testing an
Apps Script version, release it with:

```sh
python -m scripts.deploy_private_runner --version <tested-version>
```

The helper checks the actual OAuth account, the webhook manifest, and that the
current source matches the tested version before any deployment write. It
creates a fresh version as the verified owner and checks the live runner after
deployment. An unsigned probe must return `Unauthorized.`, rather than the
wrong-execution-account error. Updating an existing version alone did not
restore the execution identity during the October 16 recovery.
