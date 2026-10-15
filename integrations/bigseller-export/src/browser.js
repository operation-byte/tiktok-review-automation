'use strict';

const fs = require('node:fs');
const { chromium } = require('playwright');

function browserOptions(config, headless) {
  return {
    headless,
    acceptDownloads: true,
    downloadsPath: config.downloadDir,
    args: ['--disable-dev-shm-usage'],
  };
}

function contextOptions() {
  return {
    acceptDownloads: true,
    viewport: { width: 1440, height: 900 },
    locale: 'en-SG',
    timezoneId: 'Asia/Singapore',
  };
}

function configureContext(context, config) {
  context.setDefaultNavigationTimeout(config.navigationTimeoutMs);
  context.setDefaultTimeout(config.actionTimeoutMs);
  return context;
}

async function launchPersistentBrowser(config, options = {}) {
  await fs.promises.mkdir(config.profileDir, { recursive: true });
  await fs.promises.mkdir(config.downloadDir, { recursive: true });

  const context = await chromium.launchPersistentContext(config.profileDir, {
    ...browserOptions(config, options.headless ?? config.headless),
    viewport: contextOptions().viewport,
    locale: contextOptions().locale,
    timezoneId: contextOptions().timezoneId,
  });
  configureContext(context, config);
  const pages = context.pages();
  const page = pages[0] || await context.newPage();
  return { browser: null, context, page, stateMode: 'persistent-profile' };
}

async function launchStorageStateBrowser(config, options = {}) {
  const stateFile = options.stateFile || config.stateFile;
  if (!stateFile) {
    const error = new Error('BIGSELLER_STATE_FILE is required for storage-state browser mode.');
    error.code = 'CONFIGURATION_FAILURE';
    throw error;
  }
  await fs.promises.access(stateFile, fs.constants.R_OK);
  await fs.promises.mkdir(config.downloadDir, { recursive: true });

  const browser = await chromium.launch(browserOptions(config, options.headless ?? config.headless));
  try {
    const context = await browser.newContext({
      ...contextOptions(),
      storageState: stateFile,
    });
    configureContext(context, config);
    const pages = context.pages();
    const page = pages[0] || await context.newPage();
    return { browser, context, page, stateMode: 'storage-state', stateFile };
  } catch (error) {
    await browser.close().catch(() => {});
    throw error;
  }
}

async function launchBrowser(config, options = {}) {
  if (options.stateFile || config.stateFile) return launchStorageStateBrowser(config, options);
  return launchPersistentBrowser(config, options);
}

async function saveStorageState(context, stateFile) {
  if (!stateFile) return false;
  await fs.promises.mkdir(require('node:path').dirname(stateFile), { recursive: true });
  const temporary = `${stateFile}.tmp-${process.pid}-${Date.now()}`;
  try {
    await context.storageState({ path: temporary, indexedDB: true });
    await fs.promises.chmod(temporary, 0o600);
    await fs.promises.rm(stateFile, { force: true });
    await fs.promises.rename(temporary, stateFile);
    return true;
  } catch (error) {
    await fs.promises.rm(temporary, { force: true }).catch(() => {});
    const wrapped = new Error('Could not save BigSeller browser state.', { cause: error });
    wrapped.code = 'CONFIGURATION_FAILURE';
    throw wrapped;
  }
}

async function closeBrowser(session) {
  if (!session) return;
  await session.context?.close().catch(() => {});
  await session.browser?.close().catch(() => {});
}

module.exports = {
  launchBrowser,
  launchPersistentBrowser,
  launchStorageStateBrowser,
  saveStorageState,
  closeBrowser,
};
