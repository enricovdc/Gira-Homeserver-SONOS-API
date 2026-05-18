'use strict';

const LEVELS = { error: 0, warn: 1, info: 2, debug: 3 };

let currentLevel = LEVELS.info;

function setLevel(level) {
  if (typeof level === 'string' && level in LEVELS) {
    currentLevel = LEVELS[level];
  }
}

function format(level, msg, meta) {
  const entry = {
    ts: new Date().toISOString(),
    level,
    msg
  };
  if (meta && typeof meta === 'object') {
    for (const k of Object.keys(meta)) {
      if (k === 'authToken' || k === 'password' || k === 'secret' || k === 'token') {
        entry[k] = '[redacted]';
      } else {
        entry[k] = meta[k];
      }
    }
  }
  return JSON.stringify(entry);
}

function log(level, msg, meta) {
  if (LEVELS[level] > currentLevel) return;
  const line = format(level, msg, meta);
  if (level === 'error' || level === 'warn') {
    process.stderr.write(line + '\n');
  } else {
    process.stdout.write(line + '\n');
  }
}

module.exports = {
  setLevel,
  error: (msg, meta) => log('error', msg, meta),
  warn: (msg, meta) => log('warn', msg, meta),
  info: (msg, meta) => log('info', msg, meta),
  debug: (msg, meta) => log('debug', msg, meta),
  LEVELS
};
