'use strict';

const fs = require('node:fs');
const path = require('node:path');
const { loadConfig, EXIT_CODES } = require('./config');
const { createLogger, cleanupOldDiagnostics, stamp } = require('./logger');
const { acquireLock } = require('./lock');
const { launchBrowser, saveStorageState, closeBrowser } = require('./browser');
const { runBigSellerExport, captureDiagnostics, BigSellerError } = require('./bigseller');
const { deliverExport } = require('./download');
const { writeResult, successResult, failureResult } = require('./manifest');

async function main() {
  const config = loadConfig();
  const logger = createLogger(config.logDir, stamp());
  let releaseLock;
  let session;
  let resultWritten = false;

  try {
    await preflight(config);
    await cleanupOldDiagnostics(config.logDir, config.retentionDays);
    releaseLock = await acquireLock(config.lockFile, config.staleLockMs);
    logger.info('BigSeller export run started.', {
      incomingDir: config.incomingDir,
      headless: config.headless,
      cloudMode: config.cloudMode,
      stateMode: config.stateFile ? 'storage-state' : 'persistent-profile',
    });

    let sourcePath;
    for (let attempt = 0; attempt <= config.recoverableRetries; attempt += 1) {
      try {
        session = await launchBrowser(config);
        sourcePath = await runBigSellerExport(session.page, config, logger);
        break;
      } catch (error) {
        if (session?.page) await captureDiagnostics(session.page, config, logger, `attempt-${attempt + 1}`);
        await closeBrowser(session);
        session = null;

        if (!isRecoverable(error) || attempt >= config.recoverableRetries) throw error;
        logger.warn('Recoverable BigSeller failure; retrying once.', { message: error.message, attempt: attempt + 1 });
        await sleep(5000);
      }
    }

    const delivered = await deliverExport(
      sourcePath,
      config.incomingDir,
      config.filenamePrefix,
      config.acceptedExtensions,
    );

    let stateSaved = false;
    if (config.saveStateFile) {
      stateSaved = await saveStorageState(session.context, config.saveStateFile);
      logger.info('BigSeller browser state saved.', { stateFile: config.saveStateFile });
    }

    await closeBrowser(session);
    session = null;
    await fs.promises.unlink(sourcePath).catch(() => {});

    const result = successResult({
      runId: logger.runId,
      delivered,
      stateSaved,
      cloudMode: config.cloudMode,
    });
    await writeResult(config.resultFile, result);
    resultWritten = true;
    printResult(result);
    logger.info('Export delivered successfully.', delivered);
    return EXIT_CODES.SUCCESS;
  } catch (error) {
    const exitCode = classifyExitCode(error);
    logger.error('BigSeller export run failed.', {
      exitCode,
      code: error.code,
      message: error.message,
      stack: error.stack,
    });
    const result = failureResult({
      runId: logger.runId,
      code: error.code || 'CONFIGURATION_FAILURE',
      error,
      cloudMode: config.cloudMode,
    });
    try {
      await writeResult(config.resultFile, result);
      resultWritten = true;
    } catch (manifestError) {
      logger.error('Could not write result manifest.', { message: manifestError.message });
    }
    if (resultWritten) printResult(result);
    return exitCode;
  } finally {
    await closeBrowser(session);
    if (releaseLock) await releaseLock();
  }
}

async function preflight(config) {
  const directories = [
    config.downloadDir,
    config.logDir,
    path.dirname(config.lockFile),
    path.dirname(config.resultFile),
  ];
  if (!config.stateFile) directories.push(config.profileDir);
  if (config.saveStateFile) directories.push(path.dirname(config.saveStateFile));
  for (const dir of directories) await fs.promises.mkdir(dir, { recursive: true });

  if (config.cloudMode) {
    await fs.promises.mkdir(config.incomingDir, { recursive: true });
  } else {
    try {
      const stat = await fs.promises.stat(config.incomingDir);
      if (!stat.isDirectory()) throw new Error('Incoming path is not a directory.');
      await fs.promises.access(config.incomingDir, fs.constants.W_OK);
    } catch (cause) {
      const error = new Error(`Google Drive Incoming folder is unavailable: ${config.incomingDir}`, { cause });
      error.code = 'DESTINATION_UNAVAILABLE';
      throw error;
    }
  }

  if (config.stateFile) {
    try {
      const stat = await fs.promises.stat(config.stateFile);
      if (!stat.isFile()) throw new Error('Storage state path is not a file.');
      await fs.promises.access(config.stateFile, fs.constants.R_OK);
    } catch (cause) {
      const error = new Error(`BigSeller browser state is unavailable: ${config.stateFile}`, { cause });
      error.code = 'CONFIGURATION_FAILURE';
      throw error;
    }
  }
}

function isRecoverable(error) {
  return error?.code === 'UI_EXPORT_FAILURE' || ['TimeoutError', 'TargetClosedError'].includes(error?.name);
}

function classifyExitCode(error) {
  switch (error?.code) {
    case 'LOGIN_REQUIRED': return EXIT_CODES.LOGIN_REQUIRED;
    case 'DESTINATION_UNAVAILABLE': return EXIT_CODES.DESTINATION_UNAVAILABLE;
    case 'UI_EXPORT_FAILURE': return EXIT_CODES.UI_EXPORT_FAILURE;
    case 'INVALID_DOWNLOAD': return EXIT_CODES.INVALID_DOWNLOAD;
    case 'ALREADY_RUNNING': return EXIT_CODES.ALREADY_RUNNING;
    default:
      if (error instanceof BigSellerError) return EXIT_CODES.UI_EXPORT_FAILURE;
      return EXIT_CODES.CONFIGURATION_FAILURE;
  }
}

function printResult(result) {
  process.stdout.write(`${JSON.stringify(result)}\n`);
}

function sleep(ms) { return new Promise((resolve) => setTimeout(resolve, ms)); }

if (require.main === module) {
  main().then((code) => { process.exitCode = code; });
}

module.exports = { main, preflight, classifyExitCode, isRecoverable, printResult };
