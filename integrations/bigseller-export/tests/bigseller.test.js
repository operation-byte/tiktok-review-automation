'use strict';

const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');
const assert = require('node:assert/strict');

const { firstVisible, sanitizeFilename } = require('../src/bigseller');
const { formatLocalTimestamp, validateExportFile, deliverExport } = require('../src/download');
const { loadConfig } = require('../src/config');
const { writeResult, successResult } = require('../src/manifest');

test('firstVisible skips a hidden first match and returns a later visible match', async () => {
  const hidden = { isVisible: async () => false };
  const visible = { isVisible: async () => true };
  const candidates = [hidden, visible];
  const locator = {
    count: async () => candidates.length,
    nth: (index) => candidates[index],
  };

  assert.equal(await firstVisible([locator]), visible);
});

test('firstVisible waits for a matching element to become visible when requested', async () => {
  let visible = false;
  const candidate = { isVisible: async () => visible };
  const locator = {
    count: async () => 1,
    nth: () => candidate,
  };
  setTimeout(() => { visible = true; }, 25);

  assert.equal(
    await firstVisible([locator], { timeoutMs: 200, pollIntervalMs: 10 }),
    candidate,
  );
});

test('download validation rejects HTML login pages and accepts a real CSV', async () => {
  const dir = await fs.promises.mkdtemp(path.join(os.tmpdir(), 'bigseller-export-test-'));
  try {
    const bad = path.join(dir, 'login.csv');
    await fs.promises.writeFile(bad, '<!doctype html><title>Login</title>');
    await assert.rejects(() => validateExportFile(bad), /HTML login/);

    const good = path.join(dir, 'export.csv');
    await fs.promises.writeFile(good, 'SKU Name,Available for whole warehouse\nSKU-1,4\n');
    const metadata = await validateExportFile(good);
    assert.equal(metadata.extension, '.csv');
    assert.ok(metadata.size > 0);
    assert.match(metadata.sha256, /^[a-f0-9]{64}$/);
  } finally {
    await fs.promises.rm(dir, { recursive: true, force: true });
  }
});

test('deliverExport copies with checksum verification and makes a unique destination', async () => {
  const dir = await fs.promises.mkdtemp(path.join(os.tmpdir(), 'bigseller-export-test-'));
  try {
    const source = path.join(dir, 'download.csv');
    const incoming = path.join(dir, 'incoming');
    await fs.promises.writeFile(source, 'SKU Name,Available for whole warehouse\nSKU-1,4\n');
    const first = await deliverExport(source, incoming, 'BigSeller_Single_SKU', new Set(['.csv']), new Date(2026, 9, 15, 12, 0, 0));
    const second = await deliverExport(source, incoming, 'BigSeller_Single_SKU', new Set(['.csv']), new Date(2026, 9, 15, 12, 0, 0));
    assert.notEqual(first.destination, second.destination);
    assert.equal(first.sha256, second.sha256);
    assert.equal(await fs.promises.readFile(first.destination, 'utf8'), await fs.promises.readFile(source, 'utf8'));
  } finally {
    await fs.promises.rm(dir, { recursive: true, force: true });
  }
});

test('cloud configuration uses runner-local staging and separate storage-state paths', () => {
  const config = loadConfig({
    BIGSELLER_CLOUD_MODE: '1',
    BIGSELLER_RUNTIME_DIR: '/tmp/bigseller-test-runtime',
    BIGSELLER_STATE_FILE: '/tmp/bigseller-state/input.json',
    BIGSELLER_SAVE_STATE_FILE: '/tmp/bigseller-state/output.json',
  });
  assert.equal(config.cloudMode, true);
  assert.equal(config.incomingDir, '/tmp/bigseller-test-runtime/incoming');
  assert.equal(config.stateFile, '/tmp/bigseller-state/input.json');
  assert.equal(config.saveStateFile, '/tmp/bigseller-state/output.json');
});

test('zero recoverable retries is preserved as a valid setting', () => {
  assert.equal(loadConfig({ BIGSELLER_RECOVERABLE_RETRIES: '0' }).recoverableRetries, 0);
});

test('result manifest includes the upload contract fields', async () => {
  const dir = await fs.promises.mkdtemp(path.join(os.tmpdir(), 'bigseller-export-test-'));
  try {
    const resultFile = path.join(dir, 'result.json');
    const result = successResult({
      runId: 'test-run',
      cloudMode: true,
      stateSaved: true,
      delivered: { destination: '/tmp/export.xlsx', sha256: 'a'.repeat(64), size: 42, extension: '.xlsx' },
    });
    await writeResult(resultFile, result);
    const parsed = JSON.parse(await fs.promises.readFile(resultFile, 'utf8'));
    assert.equal(parsed.status, 'success');
    assert.equal(parsed.destination, '/tmp/export.xlsx');
    assert.equal(parsed.sha256, 'a'.repeat(64));
    assert.equal(parsed.extension, '.xlsx');
  } finally {
    await fs.promises.rm(dir, { recursive: true, force: true });
  }
});

test('filename sanitization removes path/control characters', () => {
  assert.equal(sanitizeFilename('..\\secret:/export?.xlsx'), '.._secret__export_.xlsx');
  assert.equal(formatLocalTimestamp(new Date(2026, 9, 15, 1, 2, 3)), '20261015_010203');
});
