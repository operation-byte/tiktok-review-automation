'use strict';

const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');

function formatLocalTimestamp(date = new Date()) {
  const p = (n) => String(n).padStart(2, '0');
  return `${date.getFullYear()}${p(date.getMonth() + 1)}${p(date.getDate())}_${p(date.getHours())}${p(date.getMinutes())}${p(date.getSeconds())}`;
}

async function validateExportFile(filePath, acceptedExtensions = new Set(['.csv', '.xls', '.xlsx'])) {
  const extension = path.extname(filePath).toLowerCase();
  if (!acceptedExtensions.has(extension)) throw invalid(`Unsupported export extension: ${extension || '(none)'}`);

  const stat = await fs.promises.stat(filePath);
  if (!stat.isFile() || stat.size === 0) throw invalid('Export file is empty or not a regular file.');

  const handle = await fs.promises.open(filePath, 'r');
  try {
    const probe = Buffer.alloc(Math.min(4096, stat.size));
    await handle.read(probe, 0, probe.length, 0);
    const text = probe.toString('utf8').trimStart().toLowerCase();
    if (text.startsWith('<!doctype html') || text.startsWith('<html') || text.includes('<title>login')) {
      throw invalid('Downloaded file appears to be an HTML login or error page.');
    }
  } finally {
    await handle.close().catch(() => {});
  }

  return { extension, size: stat.size, sha256: await sha256(filePath) };
}

async function deliverExport(sourcePath, incomingDir, prefix, acceptedExtensions, date = new Date()) {
  await fs.promises.mkdir(incomingDir, { recursive: true });
  await fs.promises.access(incomingDir, fs.constants.W_OK);
  const metadata = await validateExportFile(sourcePath, acceptedExtensions);
  const base = `${prefix}_${formatLocalTimestamp(date)}`;
  let destination = path.join(incomingDir, `${base}${metadata.extension}`);
  let index = 1;
  while (await exists(destination)) {
    destination = path.join(incomingDir, `${base}_${index}${metadata.extension}`);
    index += 1;
  }

  const partial = `${destination}.partial`;
  try {
    await fs.promises.copyFile(sourcePath, partial, fs.constants.COPYFILE_EXCL);
    const copiedStat = await fs.promises.stat(partial);
    const copiedHash = await sha256(partial);
    if (copiedStat.size !== metadata.size || copiedHash !== metadata.sha256) {
      throw invalid('Copied export checksum does not match source.');
    }
    await fs.promises.rename(partial, destination);
  } catch (error) {
    await fs.promises.unlink(partial).catch(() => {});
    throw error;
  }
  return { destination, ...metadata };
}

function sha256(filePath) {
  return new Promise((resolve, reject) => {
    const hash = crypto.createHash('sha256');
    const stream = fs.createReadStream(filePath);
    stream.on('error', reject);
    stream.on('data', (chunk) => hash.update(chunk));
    stream.on('end', () => resolve(hash.digest('hex')));
  });
}

async function exists(filePath) {
  try { await fs.promises.access(filePath); return true; } catch { return false; }
}

function invalid(message) {
  const error = new Error(message);
  error.code = 'INVALID_DOWNLOAD';
  return error;
}

module.exports = { formatLocalTimestamp, validateExportFile, deliverExport, sha256 };
