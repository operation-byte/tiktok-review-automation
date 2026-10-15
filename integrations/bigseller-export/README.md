# WELIVE BigSeller export runner

This is the source-only Node/Playwright integration for the BigSeller Single SKU export. It opens BigSeller, selects **Single SKU**, chooses **Export All**, waits for the generated download, validates the file, and places a checksum-verified copy in a local staging directory.

The hosted GitHub Actions workflow supplies `BIGSELLER_STATE_FILE` and `BIGSELLER_SAVE_STATE_FILE` for an encrypted BigSeller Playwright `storageState` file. It sets `BIGSELLER_CLOUD_MODE=1` and points `BIGSELLER_INCOMING_DIR` at an ephemeral runner directory. The parent workflow uploads the validated destination file to the Main Dashboard Incoming folder and then asks the signed Apps Script endpoint to import it.

For the Windows fallback, omit `BIGSELLER_STATE_FILE` and `BIGSELLER_CLOUD_MODE`, run `scripts\install.cmd`, then `scripts\setup-login.cmd`. The optional `scripts\install-scheduled-task.ps1` installs the original 00:00/06:00/12:00/18:00 local schedule. The mapped Google Drive Incoming directory comes from `BIGSELLER_INCOMING_DIR` or the default configured in `src/config.js`.

The runner writes a result manifest to `BIGSELLER_RESULT_FILE` (default `runtime/export-result.json`):

```json
{
  "schemaVersion": 1,
  "status": "success",
  "runId": "...",
  "destination": "/runner/temp/BigSeller_Single_SKU_...xlsx",
  "sha256": "...",
  "size": 12345,
  "extension": ".xlsx",
  "stateSaved": true
}
```

The destination is local staging output. It is not the Google Drive file ID. The caller must upload it to the configured Main Dashboard Incoming folder and use the manifest checksum for the signed import request.

Install reproducibly with Node 20 or newer:

```sh
npm ci
npx playwright install chromium
npm test
```

Never commit `runtime/`, `node_modules/`, browser state, screenshots, HTML diagnostics, or result manifests. The saved state contains the BigSeller login session and must be encrypted by the parent workflow before persistence.
