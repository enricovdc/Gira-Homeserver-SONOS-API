'use strict';

const fs = require('fs');
const path = require('path');

class PersistError extends Error {
  constructor(message, cause) {
    super(message);
    this.name = 'PersistError';
    if (cause) this.cause = cause;
  }
}

// Atomic write: serialize → write to .tmp → fsync → rename. Prevents a half-
// written file if the process is killed mid-write.
function saveConfig(filePath, cfg) {
  if (!filePath) throw new PersistError('No config path bound; cannot persist');
  const abs = path.resolve(filePath);
  const tmp = abs + '.tmp';
  const body = JSON.stringify(cfg, null, 2) + '\n';
  try {
    const fd = fs.openSync(tmp, 'w', 0o600);
    try {
      fs.writeSync(fd, body);
      fs.fsyncSync(fd);
    } finally {
      fs.closeSync(fd);
    }
    fs.renameSync(tmp, abs);
  } catch (err) {
    try { fs.unlinkSync(tmp); } catch { /* ignore */ }
    throw new PersistError(`Failed to write config: ${err.message}`, err);
  }
}

module.exports = { saveConfig, PersistError };
