'use strict';

const fs = require('node:fs');
const path = require('node:path');

class BigSellerError extends Error {
  constructor(code, message, cause) {
    super(message, { cause });
    this.name = 'BigSellerError';
    this.code = code;
  }
}

async function runBigSellerExport(page, config, logger) {
  logger.info('Opening BigSeller inventory page.', { url: config.inventoryUrl });
  await page.goto(config.inventoryUrl, { waitUntil: 'domcontentloaded' });
  await page.waitForLoadState('networkidle', { timeout: 20000 }).catch(() => {});

  if (await isLoginRequired(page)) {
    throw new BigSellerError('LOGIN_REQUIRED', 'BigSeller login is required. Refresh the storage state or run setup-login.');
  }

  await ensureInventoryVisible(page);
  await selectSingleSku(page, logger);
  await applyFilter(page, logger);
  return requestExport(page, config, logger);
}

async function isLoginRequired(page) {
  const url = page.url().toLowerCase();
  if (/login|passport|signin|auth/.test(url)) return true;
  const password = page.locator('input[type="password"]');
  if (await password.first().isVisible().catch(() => false)) return true;
  const loginText = page.getByText(/log\s*in|sign\s*in/i).first();
  return await loginText.isVisible().catch(() => false) && !/inventory/.test(url);
}

async function ensureInventoryVisible(page) {
  const evidence = [
    page.getByText(/inventory list/i),
    page.getByText(/single sku/i),
    page.getByRole('button', { name: /export/i }),
  ];
  for (const locator of evidence) {
    if (await locator.first().isVisible().catch(() => false)) return;
  }
  await page.waitForTimeout(3000);
  if (await isLoginRequired(page)) throw new BigSellerError('LOGIN_REQUIRED', 'BigSeller session has expired.');
  throw new BigSellerError('UI_EXPORT_FAILURE', 'Inventory List did not become visible. BigSeller may have changed its page layout.');
}

async function selectSingleSku(page, logger) {
  const candidates = [
    page.getByRole('tab', { name: /single sku/i }),
    page.getByRole('radio', { name: /single sku/i }),
    page.getByRole('checkbox', { name: /single sku/i }),
    page.getByRole('button', { name: /single sku/i }),
    page.getByText(/^single sku$/i),
    page.locator('label').filter({ hasText: /single sku/i }),
  ];
  const target = await firstVisible(candidates);
  if (!target) throw new BigSellerError('UI_EXPORT_FAILURE', 'Could not find the Single SKU control.');
  await target.click();
  logger.info('Selected Single SKU filter.');
}

async function applyFilter(page, logger) {
  const candidates = [
    page.getByRole('button', { name: /^search$/i }),
    page.getByRole('button', { name: /^apply$/i }),
    page.getByRole('button', { name: /confirm/i }),
    page.locator('button').filter({ hasText: /^search$/i }),
  ];
  const target = await firstVisible(candidates);
  if (target) {
    await Promise.all([
      page.waitForLoadState('networkidle', { timeout: 15000 }).catch(() => {}),
      target.click(),
    ]);
    logger.info('Applied inventory filter.');
  } else {
    logger.warn('No Apply/Search button found; assuming Single SKU selection applies immediately.');
  }
  await page.waitForTimeout(1500);
}

async function requestExport(page, config, logger) {
  const exportAllCandidates = [
    page.locator('li[value="exportAll"]'),
    page.locator('li[value="exportAll"] .level_label').filter({ hasText: /^export all$/i }),
  ];

  const exportButtons = page.locator('button').filter({ hasText: /import\s*&\s*export/i });
  const exportButtonCount = await exportButtons.count().catch(() => 0);
  let exportAll = null;

  for (let index = 0; index < exportButtonCount; index += 1) {
    const exportButton = exportButtons.nth(index);
    if (!await exportButton.isVisible().catch(() => false)) continue;

    await exportButton.click();
    exportAll = await firstVisible(exportAllCandidates, { timeoutMs: 2000, pollIntervalMs: 100 });
    if (exportAll) break;
  }

  if (exportButtonCount === 0) {
    throw new BigSellerError('UI_EXPORT_FAILURE', 'Could not find the Import & Export button.');
  }
  if (!exportAll) throw new BigSellerError('UI_EXPORT_FAILURE', 'Could not find Export All after opening Export.');

  logger.info('Opened Export menu and confirmed Export All is visible.');
  await exportAll.click();
  logger.info('Requested Export All.');

  logger.info('Waiting for the generated export modal and Download button.');
  return pollExportHistory(page, config, logger);
}

async function pollExportHistory(page, config, logger) {
  const deadline = Date.now() + config.exportTimeoutMs;
  while (Date.now() < deadline) {
    const ready = await firstVisible([
      page.getByRole('dialog').getByText(/^download$/i),
      page.getByRole('dialog').getByRole('link', { name: /^download$/i }),
      page.getByRole('dialog').getByRole('button', { name: /^download$/i }),
      page.locator('.ant-modal-content').getByText(/^download$/i),
      page.locator('[role="dialog"] a').filter({ hasText: /^download$/i }),
      page.locator('[role="dialog"] button').filter({ hasText: /^download$/i }),
    ]);

    if (ready) {
      const downloadPromise = page.waitForEvent('download', { timeout: 60000 });
      logger.info('Generated export is ready; clicking Download in the export modal.');
      await ready.click();
      return saveDownload(await downloadPromise, config, logger);
    }

    const failedText = page.getByText(/export failed|generation failed|task failed/i).first();
    if (await failedText.isVisible().catch(() => false)) {
      throw new BigSellerError('UI_EXPORT_FAILURE', 'BigSeller reported that export generation failed.');
    }

    await page.waitForTimeout(Math.min(config.historyPollIntervalMs, 500));
  }
  throw new BigSellerError('UI_EXPORT_FAILURE', 'Timed out waiting for BigSeller to generate a downloadable export.');
}

async function saveDownload(download, config, logger) {
  const suggested = sanitizeFilename(download.suggestedFilename() || `bigseller-export-${Date.now()}.xlsx`);
  const target = path.join(config.downloadDir, `${Date.now()}-${suggested}`);
  await download.saveAs(target);
  const failure = await download.failure();
  if (failure) throw new BigSellerError('INVALID_DOWNLOAD', `Browser download failed: ${failure}`);
  logger.info('Browser download completed.', { target });
  return target;
}

async function captureDiagnostics(page, config, logger, stage) {
  const safeStage = String(stage || 'failure').replace(/[^a-z0-9_-]+/gi, '-');
  const base = path.join(config.logDir, `${logger.runId}-${safeStage}`);
  try { await page.screenshot({ path: `${base}.png`, fullPage: true }); } catch {}
  try { await fs.promises.writeFile(`${base}.html`, await page.content(), 'utf8'); } catch {}
  logger.error('Diagnostic snapshot captured.', { url: page.url(), screenshot: `${base}.png`, html: `${base}.html` });
}

async function firstVisible(locators, options = {}) {
  const timeoutMs = options.timeoutMs || 0;
  const pollIntervalMs = options.pollIntervalMs || 100;
  const deadline = Date.now() + timeoutMs;

  do {
    for (const locator of locators) {
      const count = await locator.count().catch(() => 0);
      for (let index = 0; index < count; index += 1) {
        const candidate = locator.nth(index);
        if (await candidate.isVisible().catch(() => false)) return candidate;
      }
    }

    if (Date.now() >= deadline) break;
    await new Promise((resolve) => setTimeout(resolve, Math.min(pollIntervalMs, deadline - Date.now())));
  } while (Date.now() <= deadline);

  return null;
}

function sanitizeFilename(name) {
  return String(name).replace(/[<>:"/\\|?*\x00-\x1F]/g, '_').slice(0, 180);
}

module.exports = {
  BigSellerError,
  runBigSellerExport,
  captureDiagnostics,
  isLoginRequired,
  firstVisible,
  sanitizeFilename,
};
