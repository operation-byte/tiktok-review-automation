'use strict';

const fs = require('node:fs');
const path = require('node:path');

function stamp(date = new Date()) {
  return date.toISOString().replace(/[:.]/g, '-');
}

function createLogger(logDir, runId = stamp()) {
  fs.mkdirSync(logDir, { recursive: true });
  const logPath = path.join(logDir, `${runId}.log`);

  function write(level, message, details) {
    const suffix = details === undefined ? '' : ` ${safeJson(details)}`;
    const line = `${new Date().toISOString()} [${level}] ${message}${suffix}`;
    fs.appendFileSync(logPath, `${line}\n`, 'utf8');
    console.log(line);
  }

  return {
    runId,
    logPath,
    info: (message, details) => write('INFO', message, details),
    warn: (message, details) => write('WARN', message, details),
    error: (message, details) => write('ERROR', message, details),
  };
}

function safeJson(value) {
  try {
    return JSON.stringify(value);
  } catch {
    return JSON.stringify({ unstringifiable: String(value) });
  }
}

async function cleanupOldDiagnostics(logDir, retentionDays, now = Date.now()) {
  await fs.promises.mkdir(logDir, { recursive: true });
  const cutoff = now - retentionDays * 24 * 60 * 60 * 1000;
  const entries = await fs.promises.readdir(logDir, { withFileTypes: true });
  await Promise.all(entries.map(async (entry) => {
    if (!entry.isFile()) return;
    const fullPath = path.join(logDir, entry.name);
    const stat = await fs.promises.stat(fullPath);
    if (stat.mtimeMs < cutoff) await fs.promises.unlink(fullPath).catch(() => {});
  }));
}

module.exports = { stamp, createLogger, cleanupOldDiagnostics };
