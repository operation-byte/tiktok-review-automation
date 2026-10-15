'use strict';

const fs = require('node:fs');
const path = require('node:path');

async function acquireLock(lockFile, staleLockMs, now = Date.now()) {
  await fs.promises.mkdir(path.dirname(lockFile), { recursive: true });

  try {
    const handle = await fs.promises.open(lockFile, 'wx');
    await handle.writeFile(JSON.stringify({ pid: process.pid, startedAt: new Date(now).toISOString() }), 'utf8');
    await handle.close();
    return async () => fs.promises.unlink(lockFile).catch(() => {});
  } catch (error) {
    if (error.code !== 'EEXIST') throw error;
  }

  let existing;
  try {
    existing = JSON.parse(await fs.promises.readFile(lockFile, 'utf8'));
  } catch {
    existing = null;
  }

  const startedAt = existing?.startedAt ? Date.parse(existing.startedAt) : 0;
  if (!startedAt || now - startedAt > staleLockMs) {
    await fs.promises.unlink(lockFile).catch(() => {});
    return acquireLock(lockFile, staleLockMs, now);
  }

  const error = new Error(`Another export run is active (PID ${existing.pid || 'unknown'}).`);
  error.code = 'ALREADY_RUNNING';
  throw error;
}

module.exports = { acquireLock };
