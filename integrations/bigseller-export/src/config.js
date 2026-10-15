'use strict';

const path = require('node:path');

const ROOT_DIR = path.resolve(__dirname, '..');
const WINDOWS_INCOMING_DIR = 'G:\\Shared drives\\WELIVE NETWORK\\Operations Team\\Operations SOP\\BigSeller Automated Imports\\Incoming';

const EXIT_CODES = Object.freeze({
  SUCCESS: 0,
  LOGIN_REQUIRED: 10,
  DESTINATION_UNAVAILABLE: 20,
  UI_EXPORT_FAILURE: 30,
  INVALID_DOWNLOAD: 40,
  ALREADY_RUNNING: 50,
  CONFIGURATION_FAILURE: 60,
});

function enabled(value, fallback = false) {
  if (value === undefined || value === null || value === '') return fallback;
  return !['0', 'false', 'no', 'off'].includes(String(value).trim().toLowerCase());
}

function positiveNumber(value, fallback) {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

function nonNegativeInteger(value, fallback) {
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed >= 0 ? parsed : fallback;
}

function pathValue(value, fallback) {
  return value === undefined || value === '' ? fallback : String(value);
}

function loadConfig(env = process.env) {
  const cloudMode = enabled(env.BIGSELLER_CLOUD_MODE, Boolean(env.BIGSELLER_STATE_FILE));
  const runtimeDir = pathValue(env.BIGSELLER_RUNTIME_DIR, path.join(ROOT_DIR, 'runtime'));
  const stateFile = pathValue(env.BIGSELLER_STATE_FILE, '');
  const saveStateFile = pathValue(env.BIGSELLER_SAVE_STATE_FILE, '');

  return {
    rootDir: ROOT_DIR,
    cloudMode,
    inventoryUrl: env.BIGSELLER_INVENTORY_URL || 'https://www.bigseller.com/web/inventory/index.htm',
    incomingDir: pathValue(env.BIGSELLER_INCOMING_DIR, cloudMode ? path.join(runtimeDir, 'incoming') : WINDOWS_INCOMING_DIR),
    profileDir: pathValue(env.BIGSELLER_PROFILE_DIR, path.join(runtimeDir, 'browser-profile')),
    downloadDir: pathValue(env.BIGSELLER_DOWNLOAD_DIR, path.join(runtimeDir, 'downloads')),
    logDir: pathValue(env.BIGSELLER_LOG_DIR, path.join(runtimeDir, 'logs')),
    lockFile: pathValue(env.BIGSELLER_LOCK_FILE, path.join(runtimeDir, 'locks', 'export.lock.json')),
    resultFile: pathValue(env.BIGSELLER_RESULT_FILE, path.join(runtimeDir, 'export-result.json')),
    stateFile,
    saveStateFile,
    headless: enabled(env.BIGSELLER_HEADLESS, true),
    navigationTimeoutMs: positiveNumber(env.BIGSELLER_NAV_TIMEOUT_MS, 90000),
    actionTimeoutMs: positiveNumber(env.BIGSELLER_ACTION_TIMEOUT_MS, 30000),
    exportTimeoutMs: positiveNumber(env.BIGSELLER_EXPORT_TIMEOUT_MS, 300000),
    historyPollIntervalMs: positiveNumber(env.BIGSELLER_HISTORY_POLL_MS, 5000),
    staleLockMs: positiveNumber(env.BIGSELLER_STALE_LOCK_MS, 45 * 60 * 1000),
    retentionDays: positiveNumber(env.BIGSELLER_LOG_RETENTION_DAYS, 30),
    recoverableRetries: nonNegativeInteger(env.BIGSELLER_RECOVERABLE_RETRIES, 1),
    acceptedExtensions: new Set(['.csv', '.xls', '.xlsx']),
    filenamePrefix: 'BigSeller_Single_SKU',
  };
}

module.exports = { ROOT_DIR, EXIT_CODES, WINDOWS_INCOMING_DIR, loadConfig };
