'use strict';

const fs = require('fs');
const path = require('path');

class ConfigError extends Error {
  constructor(message, details) {
    super(message);
    this.name = 'ConfigError';
    this.details = details || {};
  }
}

const DEFAULTS = {
  server: { host: '0.0.0.0', port: 8080, authToken: '', publicUrl: '' },
  discovery: { enabled: true, timeoutMs: 4000, refreshIntervalMs: 300000 },
  players: [],
  defaultPlayer: null,
  radioStations: [],
  status: { pollIntervalMs: 5000 },
  logging: { level: 'info' },
  eventing: { enabled: true, callbackBaseUrl: '', timeoutSec: 1800 },
  webhook: { url: '', authHeader: '' },
  cloud: { enabled: false, clientId: '', clientSecret: '' },
  admin: { enabled: true }
};

function isString(v) { return typeof v === 'string' && v.length > 0; }
function isInt(v) { return Number.isInteger(v); }
function isObject(v) { return v !== null && typeof v === 'object' && !Array.isArray(v); }

function mergeDefaults(input) {
  const out = JSON.parse(JSON.stringify(DEFAULTS));
  if (!isObject(input)) return out;
  for (const key of Object.keys(DEFAULTS)) {
    if (input[key] === undefined) continue;
    if (Array.isArray(DEFAULTS[key])) {
      out[key] = Array.isArray(input[key]) ? input[key] : DEFAULTS[key];
    } else if (isObject(DEFAULTS[key])) {
      out[key] = Object.assign({}, DEFAULTS[key], isObject(input[key]) ? input[key] : {});
    } else {
      out[key] = input[key];
    }
  }
  return out;
}

function validate(cfg) {
  const errors = [];

  if (!isString(cfg.server.host)) errors.push('server.host must be a non-empty string');
  if (!isInt(cfg.server.port) || cfg.server.port < 0 || cfg.server.port > 65535) {
    errors.push('server.port must be an integer between 0 and 65535');
  }

  if (!Array.isArray(cfg.players)) {
    errors.push('players must be an array');
  } else {
    const names = new Set();
    cfg.players.forEach((p, i) => {
      if (!isObject(p)) { errors.push(`players[${i}] must be an object`); return; }
      if (!isString(p.name)) errors.push(`players[${i}].name must be a non-empty string`);
      if (!isString(p.host)) errors.push(`players[${i}].host must be a non-empty string`);
      if (p.name && names.has(p.name)) errors.push(`players[${i}].name "${p.name}" is duplicated`);
      if (p.name) names.add(p.name);
    });
  }

  if (cfg.defaultPlayer && cfg.players.length > 0) {
    const found = cfg.players.some(p => p.name === cfg.defaultPlayer);
    if (!found) errors.push(`defaultPlayer "${cfg.defaultPlayer}" is not present in players`);
  }

  if (!Array.isArray(cfg.radioStations)) {
    errors.push('radioStations must be an array');
  } else {
    const indices = new Set();
    cfg.radioStations.forEach((s, i) => {
      if (!isObject(s)) { errors.push(`radioStations[${i}] must be an object`); return; }
      if (!isInt(s.index) || s.index < 1) errors.push(`radioStations[${i}].index must be a positive integer`);
      if (!isString(s.name)) errors.push(`radioStations[${i}].name must be a non-empty string`);
      if (!isString(s.streamUri)) errors.push(`radioStations[${i}].streamUri must be a non-empty string`);
      if (isInt(s.index) && indices.has(s.index)) {
        errors.push(`radioStations[${i}].index ${s.index} is duplicated`);
      }
      if (isInt(s.index)) indices.add(s.index);
    });
  }

  if (errors.length) {
    throw new ConfigError('Invalid configuration', { errors });
  }
  return cfg;
}

function load(configPath) {
  let raw;
  try {
    raw = fs.readFileSync(configPath, 'utf8');
  } catch (err) {
    throw new ConfigError(`Cannot read config file: ${configPath}`, { cause: err.message });
  }
  let parsed;
  try {
    parsed = JSON.parse(raw);
  } catch (err) {
    throw new ConfigError(`Config file is not valid JSON: ${configPath}`, { cause: err.message });
  }
  const merged = mergeDefaults(parsed);
  return validate(merged);
}

function loadFromObject(obj) {
  return validate(mergeDefaults(obj));
}

function resolveConfigPath(envOrArg) {
  if (envOrArg && fs.existsSync(envOrArg)) return path.resolve(envOrArg);
  const candidates = [
    process.env.SONOS_BRIDGE_CONFIG,
    path.join(process.cwd(), 'config.json'),
    path.join(process.cwd(), 'config.local.json')
  ].filter(Boolean);
  for (const c of candidates) {
    if (fs.existsSync(c)) return path.resolve(c);
  }
  return null;
}

module.exports = { load, loadFromObject, resolveConfigPath, ConfigError, DEFAULTS };
