'use strict';

const fs = require('node:fs');
const path = require('node:path');

const SCHEMA_VERSION = 1;

async function writeResult(resultFile, result) {
  const destination = String(resultFile || '').trim();
  if (!destination) throw new Error('BIGSELLER_RESULT_FILE is required for a result manifest.');
  await fs.promises.mkdir(path.dirname(destination), { recursive: true });
  const temporary = `${destination}.tmp-${process.pid}-${Date.now()}`;
  await fs.promises.writeFile(temporary, `${JSON.stringify({ schemaVersion: SCHEMA_VERSION, ...result }, null, 2)}\n`, 'utf8');
  await fs.promises.rm(destination, { force: true });
  await fs.promises.rename(temporary, destination);
  return destination;
}

function successResult({ runId, delivered, stateSaved = false, cloudMode = false, finishedAt = new Date().toISOString() }) {
  return {
    status: 'success',
    runId,
    finishedAt,
    cloudMode: Boolean(cloudMode),
    stateSaved: Boolean(stateSaved),
    destination: delivered.destination,
    sha256: delivered.sha256,
    size: delivered.size,
    extension: delivered.extension,
  };
}

function failureResult({ runId, code, error, cloudMode = false, finishedAt = new Date().toISOString() }) {
  return {
    status: 'failed',
    runId,
    finishedAt,
    cloudMode: Boolean(cloudMode),
    code: String(code || 'CONFIGURATION_FAILURE'),
    message: String(error?.message || error || 'BigSeller export failed').slice(0, 500),
  };
}

module.exports = { SCHEMA_VERSION, writeResult, successResult, failureResult };
