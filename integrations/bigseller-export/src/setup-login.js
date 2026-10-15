'use strict';

const { loadConfig } = require('./config');
const { launchPersistentBrowser } = require('./browser');

(async () => {
  const config = loadConfig();
  const { context, page } = await launchPersistentBrowser(config, { headless: false });
  await page.goto(config.inventoryUrl, { waitUntil: 'domcontentloaded' });
  console.log('\nBigSeller login setup is open.');
  console.log('1. Log in manually, including any OTP required.');
  console.log('2. Confirm the Inventory List page loads.');
  console.log('3. Close the browser window. Your local session remains in runtime\\browser-profile.\n');
  await new Promise((resolve) => context.on('close', resolve));
})().catch((error) => {
  console.error(error.stack || error.message);
  process.exitCode = 60;
});
