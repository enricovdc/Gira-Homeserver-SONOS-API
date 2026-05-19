#!/usr/bin/env node
// Bundled by scripts/bundle.js — do not edit by hand.
// Version: 0.2.0
'use strict';

const __realRequire   = require;
const __bundleModules = Object.create(null);
const __bundleCache   = Object.create(null);

// require() inside a bundled module: if the id is one of our bundled
// modules, load it from the in-memory table; otherwise fall through to
// the real Node require (so http, fs, dgram, events, os, path, url work).
function __makeRequire() {
  return function (id) {
    if (id in __bundleModules) return __bundleRequire(id);
    return __realRequire(id);
  };
}

function __bundleRequire(id) {
  if (__bundleCache[id]) return __bundleCache[id].exports;
  const mod = __bundleCache[id] = { exports: {} };
  const fn = __bundleModules[id];
  if (!fn) throw new Error("Unknown bundled module: " + id);
  fn(mod, mod.exports, __makeRequire());
  return mod.exports;
}

__bundleModules['package.json'] = function (module) { module.exports = {"name":"gira-homeserver-sonos-api","version":"0.2.0"}; };

__bundleModules["src/admin-ui.js"] = function (module, exports, require) {
'use strict';

// Single-file vanilla HTML/JS admin UI. Served at "/" so that opening the
// bridge's URL in a browser yields the configuration page directly. Embedded
// as a string to avoid file IO on every request and to keep the bridge
// dependency-free.

function adminHtml({ bridgeUrl, version }) {
  return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Gira HomeServer Sonos Bridge — Configuration</title>
<style>
  :root { font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif; }
  body { margin: 0; background: #f5f6f8; color: #1a1f2c; }
  header { background: #1f6feb; color: #fff; padding: 1rem 1.5rem; }
  header h1 { margin: 0; font-size: 1.2rem; font-weight: 600; }
  header .url { font-size: 0.85rem; opacity: 0.9; font-family: ui-monospace, monospace; }
  main { max-width: 980px; margin: 1.25rem auto; padding: 0 1rem; }
  section { background: #fff; border: 1px solid #d7dce3; border-radius: 8px; margin-bottom: 1rem; padding: 1rem 1.25rem; }
  section h2 { margin: 0 0 0.5rem; font-size: 1rem; }
  table { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
  th, td { text-align: left; padding: 0.4rem 0.5rem; border-bottom: 1px solid #eef0f3; vertical-align: middle; }
  th { background: #f7f8fa; font-weight: 600; }
  input[type=text], input[type=number], input[type=url] {
    width: 100%; padding: 0.35rem 0.5rem; border: 1px solid #cdd3dc; border-radius: 4px; font: inherit;
  }
  button { font: inherit; padding: 0.4rem 0.75rem; background: #1f6feb; color: #fff;
           border: 0; border-radius: 4px; cursor: pointer; }
  button.secondary { background: #6b7280; }
  button.danger    { background: #c0392b; }
  button:disabled  { opacity: 0.5; cursor: not-allowed; }
  .row { display: flex; gap: 0.5rem; align-items: center; flex-wrap: wrap; }
  .pill { display: inline-block; padding: 2px 8px; border-radius: 99px; font-size: 0.75rem; font-weight: 600; }
  .pill.online  { background: #def7ec; color: #03543e; }
  .pill.offline { background: #fde8e8; color: #9b1c1c; }
  .pill.playing { background: #e1effe; color: #1e429f; }
  .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 0.5rem 1rem; }
  .grid label { font-size: 0.85rem; color: #4a5366; }
  .toast { position: fixed; right: 1rem; bottom: 1rem; background: #1f6feb; color: #fff;
           padding: 0.6rem 1rem; border-radius: 6px; box-shadow: 0 2px 6px rgba(0,0,0,0.2); display: none; }
  .toast.error { background: #c0392b; }
  .muted { color: #6b7280; font-size: 0.85rem; }
  details { margin-top: 0.5rem; }
  summary { cursor: pointer; color: #1f6feb; font-size: 0.85rem; }
  code { background: #f1f3f6; padding: 1px 5px; border-radius: 3px; font-size: 0.85em; }
</style>
</head>
<body>
<header>
  <h1>Gira HomeServer Sonos Bridge</h1>
  <div class="url">${escapeHtml(bridgeUrl)} &middot; v${escapeHtml(version)}</div>
</header>
<main>

<section>
  <h2>Players</h2>
  <div id="players-state" class="muted">Loading…</div>
  <table id="players-table" style="margin-top: 0.5rem;">
    <thead><tr>
      <th style="width:20%">Name</th><th style="width:20%">Host</th>
      <th style="width:15%">State</th><th style="width:10%">Vol</th>
      <th style="width:20%">Track</th><th style="width:15%">Actions</th>
    </tr></thead>
    <tbody></tbody>
  </table>
  <div class="row" style="margin-top: 0.75rem;">
    <input id="np-name" type="text" placeholder="name (e.g. livingroom)" style="max-width: 180px">
    <input id="np-host" type="text" placeholder="host (e.g. 192.168.1.50)" style="max-width: 180px">
    <button id="np-add">Add player</button>
    <button id="np-discover" class="secondary">Discover (SSDP)</button>
  </div>
</section>

<section>
  <h2>Radio stations</h2>
  <table id="stations-table">
    <thead><tr>
      <th style="width:10%">Idx</th><th style="width:25%">Name</th>
      <th>Stream URI</th><th style="width:15%">Actions</th>
    </tr></thead>
    <tbody></tbody>
  </table>
  <div class="row" style="margin-top: 0.75rem;">
    <input id="ns-idx" type="number" min="1" placeholder="index" style="max-width: 80px">
    <input id="ns-name" type="text" placeholder="name" style="max-width: 200px">
    <input id="ns-uri" type="url" placeholder="stream URL (http://… or x-rincon-mp3radio://…)">
    <button id="ns-add">Add station</button>
  </div>
</section>

<section>
  <h2>Cloud / Event push</h2>
  <div class="grid">
    <label for="wh-url">Webhook URL (e.g. HomeServer endpoint)</label>
    <input id="wh-url" type="url" placeholder="http://homeserver.local/quad/sonos-event">
    <label for="wh-auth">Webhook Authorization header (optional)</label>
    <input id="wh-auth" type="text" placeholder="Bearer ...">
    <label for="cloud-toggle">Enable Sonos Cloud Control API (optional fallback)</label>
    <div><input id="cloud-toggle" type="checkbox"> <span class="muted">requires OAuth client credentials below</span></div>
    <label for="cloud-id">Cloud client ID</label>
    <input id="cloud-id" type="text" placeholder="client_id">
    <label for="cloud-secret">Cloud client secret</label>
    <input id="cloud-secret" type="text" placeholder="client_secret">
  </div>
  <div class="row" style="margin-top: 0.75rem;">
    <button id="cfg-save">Save configuration</button>
    <button id="cfg-test-webhook" class="secondary">Test webhook</button>
    <button id="cfg-resub" class="secondary">Re-subscribe UPnP events</button>
  </div>
  <details><summary>What this does</summary>
    <p class="muted">When UPnP events arrive from a Sonos player (volume change, pause, station change), the bridge updates its in-memory state cache. If a webhook URL is configured, the bridge POSTs a small JSON payload to that URL so the HomeServer can update KNX outputs without polling. The Sonos Cloud Control API is supported as an optional <em>fallback</em> only — local SOAP is preferred and works on all current and 2026 firmware.</p>
  </details>
</section>

<section>
  <h2>Live event stream</h2>
  <pre id="event-log" style="height: 12em; overflow-y: auto; background: #0d1117; color: #c9d1d9; padding: 0.5rem; border-radius: 6px; font-size: 0.8rem; margin: 0;"></pre>
  <p class="muted" style="margin-top: 0.5rem;">Streams events via Server-Sent Events from <code>${escapeHtml(bridgeUrl)}/events</code>.</p>
</section>

<section>
  <h2>HomeServer integration</h2>
  <p class="muted">Add this URL to the HomeServer homepage or debug page so the configuration is one click away:</p>
  <pre style="background: #0d1117; color: #c9d1d9; padding: 0.5rem; border-radius: 6px; margin: 0;"><code id="hs-url">${escapeHtml(bridgeUrl)}</code></pre>
  <p class="muted" style="margin-top: 0.5rem;">See <code>homeserver/HOMEPAGE.md</code> for an iframe snippet to embed this page inside the HomeServer visualisation.</p>
</section>

</main>
<div id="toast" class="toast"></div>

<script>
const BASE = location.origin;
function toast(msg, err) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.className = 'toast' + (err ? ' error' : '');
  t.style.display = 'block';
  setTimeout(() => { t.style.display = 'none'; }, 2500);
}
async function api(method, path, body) {
  const opts = { method, headers: { 'Content-Type': 'application/json' } };
  if (body) opts.body = JSON.stringify(body);
  const res = await fetch(BASE + path, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.message || ('HTTP ' + res.status));
  return data;
}
async function refreshPlayers() {
  try {
    const { players, states } = await api('GET', '/api/players/full');
    const tbody = document.querySelector('#players-table tbody');
    tbody.innerHTML = '';
    for (const p of players) {
      const st = states[p.name] || {};
      const online = st.online !== false;
      const tr = document.createElement('tr');
      tr.innerHTML =
        '<td>' + escapeHtml(p.name) + '</td>' +
        '<td>' + escapeHtml(p.host) + '</td>' +
        '<td><span class="pill ' + (online ? (st.state === 'PLAYING' ? 'playing' : 'online') : 'offline') + '">' +
        escapeHtml(online ? (st.state || 'unknown') : 'offline') + '</span></td>' +
        '<td>' + (st.volume != null ? st.volume : '-') + (st.mute ? ' 🔇' : '') + '</td>' +
        '<td>' + escapeHtml((st.track && (st.track.title || st.track.streamContent)) || '') + '</td>' +
        '<td class="row">' +
        '<button data-act="play" data-p="' + escapeAttr(p.name) + '">▶</button>' +
        '<button data-act="pause" data-p="' + escapeAttr(p.name) + '" class="secondary">⏸</button>' +
        '<button data-act="del" data-p="' + escapeAttr(p.name) + '" class="danger">✕</button>' +
        '</td>';
      tbody.appendChild(tr);
    }
    document.getElementById('players-state').textContent = players.length + ' player(s) configured.';
  } catch (e) { toast(e.message, true); }
}
async function refreshStations() {
  try {
    const { stations } = await api('GET', '/api/stations');
    const tbody = document.querySelector('#stations-table tbody');
    tbody.innerHTML = '';
    for (const s of stations) {
      const tr = document.createElement('tr');
      tr.innerHTML =
        '<td>' + s.index + '</td>' +
        '<td>' + escapeHtml(s.name) + '</td>' +
        '<td><code>' + escapeHtml(s.streamUri) + '</code></td>' +
        '<td><button data-sact="del" data-idx="' + s.index + '" class="danger">✕</button></td>';
      tbody.appendChild(tr);
    }
  } catch (e) { toast(e.message, true); }
}
async function refreshConfig() {
  try {
    const { config } = await api('GET', '/api/config');
    document.getElementById('wh-url').value = (config.webhook && config.webhook.url) || '';
    document.getElementById('wh-auth').value = (config.webhook && config.webhook.authHeader) || '';
    document.getElementById('cloud-toggle').checked = !!(config.cloud && config.cloud.enabled);
    document.getElementById('cloud-id').value = (config.cloud && config.cloud.clientId) || '';
    document.getElementById('cloud-secret').value = (config.cloud && config.cloud.clientSecret) || '';
  } catch (e) { toast(e.message, true); }
}
document.addEventListener('click', async (ev) => {
  const t = ev.target;
  if (t.tagName !== 'BUTTON') return;
  try {
    if (t.dataset.act === 'play')  { await api('POST', '/players/' + encodeURIComponent(t.dataset.p) + '/play'); refreshPlayers(); }
    if (t.dataset.act === 'pause') { await api('POST', '/players/' + encodeURIComponent(t.dataset.p) + '/pause'); refreshPlayers(); }
    if (t.dataset.act === 'del')   {
      if (!confirm('Remove player ' + t.dataset.p + '?')) return;
      await api('DELETE', '/api/players/' + encodeURIComponent(t.dataset.p)); refreshPlayers();
    }
    if (t.dataset.sact === 'del')  {
      await api('DELETE', '/api/stations/' + encodeURIComponent(t.dataset.idx)); refreshStations();
    }
  } catch (e) { toast(e.message, true); }
});
document.getElementById('np-add').addEventListener('click', async () => {
  const name = document.getElementById('np-name').value.trim();
  const host = document.getElementById('np-host').value.trim();
  if (!name || !host) return toast('name and host required', true);
  try {
    await api('POST', '/api/players', { name, host });
    document.getElementById('np-name').value = '';
    document.getElementById('np-host').value = '';
    refreshPlayers();
    toast('Player added');
  } catch (e) { toast(e.message, true); }
});
document.getElementById('np-discover').addEventListener('click', async () => {
  try {
    const r = await api('GET', '/players/discover');
    if (!r.discovered.length) return toast('No players found on LAN');
    toast('Found ' + r.discovered.length + ' player(s)');
  } catch (e) { toast(e.message, true); }
});
document.getElementById('ns-add').addEventListener('click', async () => {
  const index = parseInt(document.getElementById('ns-idx').value, 10);
  const name = document.getElementById('ns-name').value.trim();
  const streamUri = document.getElementById('ns-uri').value.trim();
  if (!index || !name || !streamUri) return toast('index, name and URI required', true);
  try {
    await api('POST', '/api/stations', { index, name, streamUri });
    document.getElementById('ns-idx').value = '';
    document.getElementById('ns-name').value = '';
    document.getElementById('ns-uri').value = '';
    refreshStations();
    toast('Station added');
  } catch (e) { toast(e.message, true); }
});
document.getElementById('cfg-save').addEventListener('click', async () => {
  try {
    await api('PUT', '/api/config', {
      webhook: {
        url: document.getElementById('wh-url').value.trim(),
        authHeader: document.getElementById('wh-auth').value.trim()
      },
      cloud: {
        enabled: document.getElementById('cloud-toggle').checked,
        clientId: document.getElementById('cloud-id').value.trim(),
        clientSecret: document.getElementById('cloud-secret').value.trim()
      }
    });
    toast('Configuration saved');
  } catch (e) { toast(e.message, true); }
});
document.getElementById('cfg-test-webhook').addEventListener('click', async () => {
  try { const r = await api('POST', '/api/webhook/test'); toast(r.ok ? 'Webhook OK' : 'Webhook failed', !r.ok); }
  catch (e) { toast(e.message, true); }
});
document.getElementById('cfg-resub').addEventListener('click', async () => {
  try { await api('POST', '/api/events/resubscribe'); toast('Re-subscribed'); }
  catch (e) { toast(e.message, true); }
});

function escapeHtml(s) {
  return String(s == null ? '' : s)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}
function escapeAttr(s) { return escapeHtml(s); }

function openEventStream() {
  const log = document.getElementById('event-log');
  const es = new EventSource(BASE + '/events');
  es.onmessage = (ev) => {
    const line = new Date().toLocaleTimeString() + '  ' + ev.data + '\\n';
    log.textContent += line;
    log.scrollTop = log.scrollHeight;
    if (ev.data.indexOf('"event":"state"') !== -1) refreshPlayers();
  };
  es.onerror = () => { /* browser auto-reconnects */ };
}

refreshPlayers();
refreshStations();
refreshConfig();
openEventStream();
setInterval(refreshPlayers, 15000);
</script>
</body>
</html>`;
}

function escapeHtml(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

module.exports = { adminHtml };

};

__bundleModules["src/config.js"] = function (module, exports, require) {
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

};

__bundleModules["src/logger.js"] = function (module, exports, require) {
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

};

__bundleModules["src/persist.js"] = function (module, exports, require) {
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

};

__bundleModules["src/players.js"] = function (module, exports, require) {
'use strict';

const { SonosClient } = __bundleRequire("src/sonos/client.js");

class PlayerError extends Error {
  constructor(message, code) {
    super(message);
    this.name = 'PlayerError';
    this.code = code || 'PLAYER_ERROR';
  }
}

class PlayerRegistry {
  constructor({ players = [], defaultPlayer = null, clientFactory } = {}) {
    this.byName = new Map();
    this.defaultName = defaultPlayer;
    this.clientFactory = clientFactory || ((opts) => new SonosClient(opts));
    for (const p of players) this.register(p);
  }

  register(playerCfg) {
    if (!playerCfg || !playerCfg.name || !playerCfg.host) {
      throw new PlayerError(`Invalid player config: ${JSON.stringify(playerCfg)}`, 'INVALID_CONFIG');
    }
    const client = this.clientFactory({
      host: playerCfg.host,
      port: playerCfg.port || 1400,
      name: playerCfg.name,
      uuid: playerCfg.uuid
    });
    this.byName.set(playerCfg.name.toLowerCase(), client);
    if (!this.defaultName) this.defaultName = playerCfg.name;
  }

  get(name) {
    const key = (name || this.defaultName || '').toLowerCase();
    if (!key) throw new PlayerError('No player specified and no default configured', 'NO_DEFAULT');
    const client = this.byName.get(key);
    if (!client) throw new PlayerError(`Unknown player: ${name}`, 'PLAYER_NOT_FOUND');
    return client;
  }

  list() {
    return Array.from(this.byName.values()).map((c) => ({
      name: c.name,
      host: c.host,
      port: c.port,
      uuid: c.uuid
    }));
  }

  has(name) {
    return this.byName.has(String(name || '').toLowerCase());
  }

  unregister(name) {
    const key = String(name || '').toLowerCase();
    const existed = this.byName.delete(key);
    if (this.defaultName && this.defaultName.toLowerCase() === key) {
      const next = this.byName.values().next().value;
      this.defaultName = next ? next.name : null;
    }
    return existed;
  }
}

module.exports = { PlayerRegistry, PlayerError };

};

__bundleModules["src/radio.js"] = function (module, exports, require) {
'use strict';

class RadioError extends Error {
  constructor(message, code) {
    super(message);
    this.name = 'RadioError';
    this.code = code || 'RADIO_ERROR';
  }
}

class RadioStationStore {
  constructor(stations = []) {
    this.stations = new Map();
    this.byIndex = new Map();
    for (const s of stations) this.add(s);
  }

  add(station) {
    if (!station || !station.name || !station.streamUri || !Number.isInteger(station.index)) {
      throw new RadioError(`Invalid station: ${JSON.stringify(station)}`, 'INVALID_STATION');
    }
    const key = station.name.toLowerCase();
    this.stations.set(key, station);
    this.byIndex.set(station.index, station);
  }

  list() {
    return Array.from(this.byIndex.values()).sort((a, b) => a.index - b.index);
  }

  byName(name) {
    if (!name) throw new RadioError('Station name required', 'INVALID_ARG');
    const s = this.stations.get(String(name).toLowerCase());
    if (!s) throw new RadioError(`Unknown station: ${name}`, 'STATION_NOT_FOUND');
    return s;
  }

  remove(index) {
    const i = Number(index);
    const s = this.byIndex.get(i);
    if (!s) return false;
    this.byIndex.delete(i);
    this.stations.delete(s.name.toLowerCase());
    return true;
  }

  byIndexLookup(index) {
    const i = Number(index);
    if (!Number.isInteger(i)) throw new RadioError('Station index must be integer', 'INVALID_ARG');
    const s = this.byIndex.get(i);
    if (!s) throw new RadioError(`Unknown station index: ${i}`, 'STATION_NOT_FOUND');
    return s;
  }
}

module.exports = { RadioStationStore, RadioError };

};

__bundleModules["src/server.js"] = function (module, exports, require) {
'use strict';

const http = require('http');
const os = require('os');
const path = require('path');
const { URL } = require('url');

const config = __bundleRequire("src/config.js");
const logger = __bundleRequire("src/logger.js");
const { PlayerRegistry, PlayerError } = __bundleRequire("src/players.js");
const { RadioStationStore, RadioError } = __bundleRequire("src/radio.js");
const { SonosError } = __bundleRequire("src/sonos/soap.js");
const { StateStore } = __bundleRequire("src/state.js");
const { EventManager, parseNotifyBody } = __bundleRequire("src/sonos/events.js");
const { WebhookPublisher } = __bundleRequire("src/webhook.js");
const { saveConfig } = __bundleRequire("src/persist.js");
const { adminHtml } = __bundleRequire("src/admin-ui.js");

const VERSION = __bundleRequire('package.json').version;

function sendJson(res, status, payload) {
  const body = JSON.stringify(payload);
  res.writeHead(status, {
    'Content-Type': 'application/json; charset=utf-8',
    'Content-Length': Buffer.byteLength(body),
    'Cache-Control': 'no-store'
  });
  res.end(body);
}

function sendText(res, status, body, contentType = 'text/plain; charset=utf-8') {
  const buf = Buffer.from(body);
  res.writeHead(status, {
    'Content-Type': contentType,
    'Content-Length': buf.length,
    'Cache-Control': 'no-store'
  });
  res.end(buf);
}

function errorFor(err) {
  if (err instanceof RadioError) return { status: err.code === 'STATION_NOT_FOUND' ? 404 : 400, code: err.code, message: err.message };
  if (err instanceof PlayerError) return { status: err.code === 'PLAYER_NOT_FOUND' ? 404 : 400, code: err.code, message: err.message };
  if (err instanceof SonosError) {
    const status = err.code === 'PLAYER_UNREACHABLE' ? 503 : err.code === 'INVALID_ARG' ? 400 : 502;
    return { status, code: err.code, message: err.message, faultCode: err.faultCode, faultString: err.faultString };
  }
  if (err && err.name === 'ConfigError') return { status: 400, code: 'INVALID_CONFIG', message: err.message, details: err.details };
  if (err && err.name === 'PersistError') return { status: 500, code: 'PERSIST_FAILED', message: err.message };
  return { status: 500, code: 'INTERNAL', message: err.message || 'Internal error' };
}

function sendError(res, err, context) {
  const e = errorFor(err);
  logger.warn('Request failed', { code: e.code, message: e.message, ...context });
  sendJson(res, e.status, { ok: false, error: e.code, message: e.message, faultCode: e.faultCode, faultString: e.faultString, details: e.details });
}

function parseUrl(reqUrl) {
  const url = new URL(reqUrl, 'http://x');
  const q = {};
  for (const [k, v] of url.searchParams.entries()) q[k] = v;
  return { pathname: url.pathname, query: q };
}

function readBody(req, { maxBytes = 256 * 1024 } = {}) {
  return new Promise((resolve, reject) => {
    let size = 0;
    const chunks = [];
    req.on('data', (chunk) => {
      size += chunk.length;
      if (size > maxBytes) { reject(new Error('Body too large')); req.destroy(); return; }
      chunks.push(chunk);
    });
    req.on('end', () => resolve(Buffer.concat(chunks)));
    req.on('error', reject);
  });
}

async function readJsonBody(req) {
  const buf = await readBody(req);
  if (!buf.length) return {};
  try { return JSON.parse(buf.toString('utf8')); }
  catch { throw new Error('Invalid JSON body'); }
}

function checkAuth(req, token) {
  if (!token) return true;
  const header = req.headers['authorization'] || '';
  if (header === `Bearer ${token}`) return true;
  const url = new URL(req.url, 'http://x');
  if (url.searchParams.get('token') === token) return true;
  return false;
}

function firstLanAddress() {
  const ifaces = os.networkInterfaces();
  for (const name of Object.keys(ifaces)) {
    for (const a of ifaces[name] || []) {
      if (a.family === 'IPv4' && !a.internal) return a.address;
    }
  }
  return '127.0.0.1';
}

// Determines the URL Sonos players should call back to with NOTIFY events.
// Order of precedence: explicit config, then auto-detected LAN address.
function resolveCallbackBaseUrl(cfg, listenAddr) {
  if (cfg.eventing.callbackBaseUrl) return cfg.eventing.callbackBaseUrl.replace(/\/$/, '');
  const host = (cfg.server.host && cfg.server.host !== '0.0.0.0' && cfg.server.host !== '::')
    ? cfg.server.host
    : firstLanAddress();
  return `http://${host}:${listenAddr.port}`;
}

function resolvePublicUrl(cfg, listenAddr) {
  if (cfg.server.publicUrl) return cfg.server.publicUrl.replace(/\/$/, '');
  const host = (cfg.server.host && cfg.server.host !== '0.0.0.0' && cfg.server.host !== '::')
    ? cfg.server.host
    : firstLanAddress();
  return `http://${host}:${listenAddr.port}`;
}

function createApp(ctx) {
  const { cfg, registry, radioStore, stateStore, eventMgr, webhook, configPath, sseClients, urls } = ctx;

  async function handleControl(player, action, query, body) {
    let result;
    switch (action) {
      case 'play': result = await player.play(); stateStore.set(player.name, { state: 'PLAYING' }); break;
      case 'pause': result = await player.pause(); stateStore.set(player.name, { state: 'PAUSED_PLAYBACK' }); break;
      case 'stop': result = await player.stop(); stateStore.set(player.name, { state: 'STOPPED' }); break;
      case 'next': result = await player.next(); break;
      case 'previous': result = await player.previous(); break;
      case 'volume': {
        const level = body.level !== undefined ? body.level : query.level;
        if (level === undefined) throw new SonosError('Missing volume level', { code: 'INVALID_ARG' });
        result = await player.setVolume(level);
        stateStore.set(player.name, { volume: result.volume });
        break;
      }
      case 'volume/up': {
        const step = Number(body.step ?? query.step ?? 2);
        result = await player.adjustVolume(step);
        stateStore.set(player.name, { volume: result.volume });
        break;
      }
      case 'volume/down': {
        const step = Number(body.step ?? query.step ?? 2);
        result = await player.adjustVolume(-step);
        stateStore.set(player.name, { volume: result.volume });
        break;
      }
      case 'mute': {
        const mute = body.mute ?? query.mute;
        const v = mute === undefined ? true : (mute === true || mute === 'true' || mute === '1');
        result = await player.setMute(v);
        stateStore.set(player.name, { mute: v });
        break;
      }
      case 'unmute': result = await player.setMute(false); stateStore.set(player.name, { mute: false }); break;
      case 'mute/toggle': {
        const current = await player.getMute();
        result = await player.setMute(!current);
        stateStore.set(player.name, { mute: !current });
        break;
      }
      default:
        throw new SonosError(`Unknown action: ${action}`, { code: 'INVALID_ARG' });
    }
    return result;
  }

  async function handleRadio(player, op, query, body) {
    let station;
    if (op === 'start') {
      const ident = body.index ?? query.index ?? body.name ?? query.name;
      if (ident === undefined) throw new RadioError('Specify index or name', 'INVALID_ARG');
      station = /^\d+$/.test(String(ident))
        ? radioStore.byIndexLookup(ident)
        : radioStore.byName(ident);
    } else if (op === 'index') {
      station = radioStore.byIndexLookup(body.index ?? query.index);
    } else if (op === 'name') {
      station = radioStore.byName(body.name ?? query.name);
    } else {
      throw new RadioError(`Unknown radio op: ${op}`, 'INVALID_ARG');
    }
    await player.playStreamUri(station.streamUri, { title: station.metadata?.title || station.name });
    stateStore.setActiveStation(player.name, station);
    return { ok: true, station: { index: station.index, name: station.name } };
  }

  // ------- Admin / config CRUD -------
  function persist() {
    if (!configPath) return;
    saveConfig(configPath, cfg);
  }

  function validateInProcess(patched) {
    return config.loadFromObject(patched);
  }

  async function handleAdmin(method, segments, body) {
    // /api/config
    if (segments[1] === 'config' && segments.length === 2) {
      if (method === 'GET') return { ok: true, config: redactConfig(cfg) };
      if (method === 'PUT') {
        const merged = mergeDeep(JSON.parse(JSON.stringify(cfg)), body || {});
        const validated = validateInProcess(merged);
        Object.assign(cfg, validated);
        webhook.setUrl(cfg.webhook.url);
        webhook.setAuthHeader(cfg.webhook.authHeader);
        persist();
        return { ok: true, config: redactConfig(cfg) };
      }
    }
    // /api/players (full payload includes live state)
    if (segments[1] === 'players' && segments[2] === 'full' && method === 'GET') {
      return { ok: true, players: registry.list(), states: stateStore.all() };
    }
    // /api/players  POST add
    if (segments[1] === 'players' && segments.length === 2 && method === 'POST') {
      const players = [...cfg.players, body];
      const merged = { ...cfg, players };
      const validated = validateInProcess(merged);
      Object.assign(cfg, validated);
      registry.register(body);
      persist();
      if (cfg.eventing.enabled && eventMgr) eventMgr.subscribePlayer(body.name).catch(() => {});
      return { ok: true, player: body };
    }
    // /api/players/:name  DELETE
    if (segments[1] === 'players' && segments.length === 3 && method === 'DELETE') {
      const name = decodeURIComponent(segments[2]);
      cfg.players = cfg.players.filter((p) => p.name !== name);
      registry.unregister(name);
      persist();
      return { ok: true };
    }
    // /api/stations  GET (also exposed publicly at /stations)
    if (segments[1] === 'stations' && segments.length === 2 && method === 'GET') {
      return { ok: true, stations: radioStore.list() };
    }
    // /api/stations  POST add
    if (segments[1] === 'stations' && segments.length === 2 && method === 'POST') {
      const stations = [...cfg.radioStations, body];
      const merged = { ...cfg, radioStations: stations };
      const validated = validateInProcess(merged);
      Object.assign(cfg, validated);
      radioStore.add(body);
      persist();
      return { ok: true, station: body };
    }
    // /api/stations/:index  DELETE
    if (segments[1] === 'stations' && segments.length === 3 && method === 'DELETE') {
      const idx = parseInt(decodeURIComponent(segments[2]), 10);
      cfg.radioStations = cfg.radioStations.filter((s) => s.index !== idx);
      radioStore.remove(idx);
      persist();
      return { ok: true };
    }
    // /api/webhook/test
    if (segments[1] === 'webhook' && segments[2] === 'test' && method === 'POST') {
      if (!webhook.enabled()) return { ok: false, error: 'WEBHOOK_DISABLED' };
      await webhook.publish('test', { from: urls.publicUrl, at: new Date().toISOString() });
      return { ok: true };
    }
    // /api/events/resubscribe
    if (segments[1] === 'events' && segments[2] === 'resubscribe' && method === 'POST') {
      if (!eventMgr) return { ok: false, error: 'EVENTING_DISABLED' };
      await eventMgr.stop();
      await eventMgr.start();
      return { ok: true };
    }
    return null;
  }

  return async function handle(req, res) {
    const started = Date.now();
    try {
      const { pathname, query } = parseUrl(req.url);
      const segments = pathname.split('/').filter(Boolean);

      // UPnP NOTIFY callbacks from Sonos players. These come in *without* the
      // bearer token because they originate from the player, not the user.
      // Auth is by URL path obscurity + binding only to LAN.
      if (segments[0] === 'upnp' && segments[1] === 'event' && req.method === 'NOTIFY') {
        const playerName = decodeURIComponent(segments[2] || '');
        const buf = await readBody(req, { maxBytes: 1024 * 1024 });
        const parsed = parseNotifyBody(buf.toString('utf8'));
        if (parsed && Object.keys(parsed).length > 0) {
          const patch = { online: true };
          if (parsed.state !== undefined) patch.state = parsed.state;
          if (parsed.volume !== undefined) patch.volume = parsed.volume;
          if (parsed.mute !== undefined) patch.mute = parsed.mute;
          if (parsed.trackUri !== undefined || parsed.title !== undefined) {
            const prev = stateStore.get(playerName) || {};
            patch.track = {
              ...(prev.track || {}),
              ...(parsed.title !== undefined ? { title: parsed.title } : {}),
              ...(parsed.artist !== undefined ? { artist: parsed.artist } : {}),
              ...(parsed.streamContent !== undefined ? { streamContent: parsed.streamContent } : {}),
              ...(parsed.trackUri !== undefined ? { uri: parsed.trackUri } : {})
            };
          }
          stateStore.markEventReceived(playerName);
          stateStore.set(playerName, patch);
        }
        res.writeHead(200, { 'Content-Length': '0' });
        return res.end();
      }

      // Auth wall for everything else.
      if (!checkAuth(req, cfg.server.authToken)) {
        return sendJson(res, 401, { ok: false, error: 'UNAUTHORIZED', message: 'Invalid or missing token' });
      }

      // Root → admin UI.
      if (pathname === '/' || pathname === '/admin') {
        if (!cfg.admin.enabled) return sendJson(res, 404, { ok: false, error: 'ADMIN_DISABLED' });
        return sendText(res, 200, adminHtml({ bridgeUrl: urls.publicUrl, version: VERSION }), 'text/html; charset=utf-8');
      }

      // Server-Sent Events stream for the admin UI and any HomeServer client
      // that prefers SSE over webhooks.
      if (pathname === '/events' && req.method === 'GET') {
        res.writeHead(200, {
          'Content-Type': 'text/event-stream',
          'Cache-Control': 'no-cache',
          'Connection': 'keep-alive'
        });
        res.write('retry: 5000\n\n');
        const send = (payload) => {
          try { res.write(`data: ${JSON.stringify(payload)}\n\n`); } catch { /* client gone */ }
        };
        sseClients.add(send);
        // Send a snapshot immediately.
        send({ event: 'snapshot', state: stateStore.all() });
        req.on('close', () => sseClients.delete(send));
        return;
      }

      // Bridge metadata — surfaced to HomeServer so it can show the URL on
      // its homepage / debug page.
      if (pathname === '/info') {
        return sendJson(res, 200, {
          ok: true,
          name: 'Gira HomeServer Sonos Bridge',
          version: VERSION,
          publicUrl: urls.publicUrl,
          adminUrl: urls.publicUrl + '/',
          eventsUrl: urls.publicUrl + '/events',
          callbackBaseUrl: urls.callbackBaseUrl,
          hostname: os.hostname(),
          players: registry.list().map(p => ({ name: p.name, host: p.host })),
          stations: radioStore.list().length,
          eventingEnabled: cfg.eventing.enabled,
          webhookConfigured: webhook.enabled(),
          cloudEnabled: !!cfg.cloud.enabled
        });
      }

      if (pathname === '/health' || pathname === '/healthz') {
        return sendJson(res, 200, { ok: true, uptime: process.uptime(), version: VERSION, players: registry.list().length });
      }
      if (pathname === '/players' && segments.length === 1) {
        return sendJson(res, 200, { ok: true, players: registry.list(), defaultPlayer: registry.defaultName });
      }
      if (pathname === '/stations' && segments.length === 1) {
        return sendJson(res, 200, { ok: true, stations: radioStore.list() });
      }

      const body = req.method === 'POST' || req.method === 'PUT' || req.method === 'DELETE'
        ? await readJsonBody(req).catch(() => ({}))
        : {};

      if (segments[0] === 'players' && segments[1] === 'discover') {
        const { discover } = __bundleRequire("src/sonos/discovery.js");
        const players = await discover({ timeoutMs: cfg.discovery.timeoutMs });
        return sendJson(res, 200, { ok: true, discovered: players });
      }

      // Admin API.
      if (segments[0] === 'api') {
        const result = await handleAdmin(req.method, segments, body);
        if (result === null) return sendJson(res, 404, { ok: false, error: 'NOT_FOUND' });
        return sendJson(res, 200, result);
      }

      // Per-player routes.
      if (segments[0] === 'players' && segments.length >= 3) {
        const player = registry.get(decodeURIComponent(segments[1]));
        const action = segments.slice(2).join('/');
        if (action === 'status') {
          const status = await player.getStatus().catch((err) => {
            if (err instanceof SonosError && err.code === 'PLAYER_UNREACHABLE') {
              return { online: false, error: err.message };
            }
            throw err;
          });
          if (status.online !== false) stateStore.set(player.name, status);
          const cached = stateStore.get(player.name) || {};
          return sendJson(res, 200, {
            ok: true, player: player.name, ...status,
            activeStation: cached.activeStation || null,
            cached: !!cached.updatedAt,
            eventDriven: stateStore.hasRecentEvent(player.name)
          });
        }
        if (action.startsWith('radio/')) {
          const op = action.slice('radio/'.length);
          const result = await handleRadio(player, op, query, body);
          return sendJson(res, 200, result);
        }
        const result = await handleControl(player, action, query, body);
        return sendJson(res, 200, { ok: true, player: player.name, ...result });
      }

      if (segments[0] === 'status') {
        const player = registry.get(query.player || body.player);
        const status = await player.getStatus().catch((err) => {
          if (err instanceof SonosError && err.code === 'PLAYER_UNREACHABLE') return { online: false, error: err.message };
          throw err;
        });
        return sendJson(res, 200, { ok: true, player: player.name, ...status });
      }

      return sendJson(res, 404, { ok: false, error: 'NOT_FOUND', message: `No route: ${pathname}` });
    } catch (err) {
      sendError(res, err, { url: req.url, method: req.method, durationMs: Date.now() - started });
    }
  };
}

// Deep merge for config patches: arrays replaced wholesale, objects merged.
function mergeDeep(target, source) {
  if (!source || typeof source !== 'object') return target;
  for (const key of Object.keys(source)) {
    const sv = source[key];
    if (Array.isArray(sv)) target[key] = sv;
    else if (sv && typeof sv === 'object') {
      target[key] = mergeDeep(target[key] && typeof target[key] === 'object' ? target[key] : {}, sv);
    } else {
      target[key] = sv;
    }
  }
  return target;
}

function redactConfig(cfg) {
  const out = JSON.parse(JSON.stringify(cfg));
  if (out.server && out.server.authToken) out.server.authToken = '[set]';
  if (out.cloud && out.cloud.clientSecret) out.cloud.clientSecret = '[set]';
  if (out.webhook && out.webhook.authHeader) out.webhook.authHeader = '[set]';
  return out;
}

async function startServer(cfg, { clientFactory, configPath, disableEventing, httpFn } = {}) {
  logger.setLevel(cfg.logging.level);
  const registry = new PlayerRegistry({
    players: cfg.players,
    defaultPlayer: cfg.defaultPlayer,
    clientFactory
  });
  const radioStore = new RadioStationStore(cfg.radioStations);
  const stateStore = new StateStore();
  const sseClients = new Set();
  const webhook = new WebhookPublisher({ url: cfg.webhook.url, authHeader: cfg.webhook.authHeader, logger });

  stateStore.on('change', ({ player, state }) => {
    const payload = { event: 'state', player, state };
    for (const send of sseClients) send(payload);
    if (webhook.enabled()) webhook.publish('state', { player, state }).catch(() => {});
  });

  let eventMgr = null;
  let urls = {};

  const ctx = { cfg, registry, radioStore, stateStore, sseClients, webhook, configPath, urls, eventMgr };
  const app = createApp(ctx);
  const server = http.createServer(app);

  return new Promise((resolve) => {
    server.listen(cfg.server.port, cfg.server.host, async () => {
      const addr = server.address();
      urls.publicUrl = resolvePublicUrl(cfg, addr);
      urls.callbackBaseUrl = resolveCallbackBaseUrl(cfg, addr);
      logger.info('Bridge listening', {
        host: addr.address, port: addr.port,
        publicUrl: urls.publicUrl, callbackBaseUrl: urls.callbackBaseUrl,
        players: registry.list().length, stations: radioStore.list().length,
        eventing: cfg.eventing.enabled, admin: cfg.admin.enabled
      });
      if (cfg.eventing.enabled && !disableEventing) {
        eventMgr = new EventManager({
          registry,
          stateStore,
          callbackBaseUrl: urls.callbackBaseUrl,
          logger,
          httpFn
        });
        ctx.eventMgr = eventMgr;
        eventMgr.start().catch((err) => logger.warn('EventManager start failed', { error: err.message }));
      }
      resolve({ server, app, registry, radioStore, stateStore, eventMgr, webhook, urls });
    });
  });
}

if (require.main === module) {
  const cfgPath = config.resolveConfigPath(process.argv[2]);
  if (!cfgPath) {
    logger.error('No config file found. Copy config.example.json to config.json or set SONOS_BRIDGE_CONFIG.');
    process.exit(1);
  }
  try {
    const cfg = config.load(cfgPath);
    logger.info('Config loaded', { configPath: cfgPath });
    startServer(cfg, { configPath: cfgPath }).then(({ server, eventMgr }) => {
      const shutdown = async (sig) => {
        logger.info('Shutting down', { signal: sig });
        if (eventMgr) await eventMgr.stop();
        server.close(() => process.exit(0));
        setTimeout(() => process.exit(0), 3000).unref();
      };
      process.on('SIGTERM', () => shutdown('SIGTERM'));
      process.on('SIGINT', () => shutdown('SIGINT'));
    }).catch((err) => {
      logger.error('Failed to start server', { error: err.message });
      process.exit(1);
    });
  } catch (err) {
    logger.error('Configuration error', { error: err.message, details: err.details });
    process.exit(1);
  }
}

module.exports = { createApp, startServer };

};

__bundleModules["src/sonos/client.js"] = function (module, exports, require) {
'use strict';

const { soapCall, extractTag, xmlEscape, SonosError } = __bundleRequire("src/sonos/soap.js");

const DEFAULT_PORT = 1400;
const INSTANCE = 0;

// Minimal DIDL-Lite without any SMAPI cloud-service binding (no <desc> cdudn tag).
// This is the most resilient form for direct-stream radio playback on 2024+ Sonos
// firmware where SMAPI/TuneIn cloud service IDs may be gated or rejected. The player
// connects to the stream URL directly without any cloud handoff.
function buildDirectStreamMetadata({ title }) {
  const id = 'R:0/0/' + Math.random().toString(36).slice(2, 10);
  const safeTitle = xmlEscape(title || 'Radio');
  return (
    '<DIDL-Lite xmlns:dc="http://purl.org/dc/elements/1.1/" ' +
    'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/" ' +
    'xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/">' +
    `<item id="${id}" parentID="-1" restricted="true">` +
    `<dc:title>${safeTitle}</dc:title>` +
    '<upnp:class>object.item.audioItem.audioBroadcast</upnp:class>' +
    '</item>' +
    '</DIDL-Lite>'
  );
}

function normalizeRadioUri(uri) {
  if (!uri) return uri;
  // Use Sonos's preferred direct-broadcast scheme so the player treats it as
  // a continuous broadcast stream rather than a one-shot file. Works on all
  // firmware generations.
  if (uri.startsWith('http://') || uri.startsWith('https://')) {
    return 'x-rincon-mp3radio://' + uri.replace(/^https?:\/\//, '');
  }
  return uri;
}

function parseTrackMeta(xmlString) {
  if (!xmlString) return {};
  return {
    title: extractTag(xmlString, 'dc:title'),
    artist: extractTag(xmlString, 'dc:creator'),
    album: extractTag(xmlString, 'upnp:album'),
    streamContent: extractTag(xmlString, 'r:streamContent'),
    albumArtUri: extractTag(xmlString, 'upnp:albumArtURI')
  };
}

// UPnP error codes that indicate the player rejected our metadata format.
// Retrying with empty metadata is the documented workaround.
const METADATA_REJECTED_CODES = new Set(['714', '716', '402', '501', '800']);

class SonosClient {
  constructor({ host, port = DEFAULT_PORT, name, uuid, timeoutMs = 5000, requestFn } = {}) {
    if (!host) throw new SonosError('SonosClient requires a host', { code: 'INVALID_CONFIG' });
    this.host = host;
    this.port = port;
    this.name = name || host;
    this.uuid = uuid || null;
    this.timeoutMs = timeoutMs;
    this.requestFn = requestFn;
  }

  call(service, action, params) {
    return soapCall({
      host: this.host,
      port: this.port,
      service,
      action,
      params: Object.assign({ InstanceID: INSTANCE }, params || {}),
      timeoutMs: this.timeoutMs,
      requestFn: this.requestFn
    });
  }

  async play() { await this.call('AVTransport', 'Play', { Speed: '1' }); return { ok: true }; }
  async pause() { await this.call('AVTransport', 'Pause'); return { ok: true }; }
  async stop() { await this.call('AVTransport', 'Stop'); return { ok: true }; }
  async next() { await this.call('AVTransport', 'Next'); return { ok: true }; }
  async previous() { await this.call('AVTransport', 'Previous'); return { ok: true }; }

  async setVolume(level) {
    const n = Number(level);
    if (!Number.isFinite(n)) throw new SonosError('Volume must be a number', { code: 'INVALID_ARG' });
    const v = Math.max(0, Math.min(100, Math.round(n)));
    await this.call('RenderingControl', 'SetVolume', { Channel: 'Master', DesiredVolume: v });
    return { ok: true, volume: v };
  }

  async getVolume() {
    const res = await this.call('RenderingControl', 'GetVolume', { Channel: 'Master' });
    return parseInt(res.CurrentVolume, 10);
  }

  async adjustVolume(delta) {
    const current = await this.getVolume();
    const next = Math.max(0, Math.min(100, current + Number(delta)));
    return this.setVolume(next);
  }

  async setMute(mute) {
    await this.call('RenderingControl', 'SetMute', {
      Channel: 'Master',
      DesiredMute: mute ? '1' : '0'
    });
    return { ok: true, mute: !!mute };
  }

  async getMute() {
    const res = await this.call('RenderingControl', 'GetMute', { Channel: 'Master' });
    return res.CurrentMute === '1';
  }

  async getTransportInfo() {
    const res = await this.call('AVTransport', 'GetTransportInfo');
    return {
      state: res.CurrentTransportState || 'UNKNOWN',
      status: res.CurrentTransportStatus || 'UNKNOWN',
      speed: res.CurrentSpeed || '1'
    };
  }

  async getPositionInfo() {
    const res = await this.call('AVTransport', 'GetPositionInfo');
    const meta = parseTrackMeta(res.TrackMetaData);
    return {
      track: parseInt(res.Track, 10) || 0,
      trackDuration: res.TrackDuration || '0:00:00',
      trackUri: res.TrackURI || '',
      relTime: res.RelTime || '0:00:00',
      metadata: meta
    };
  }

  async getMediaInfo() {
    const res = await this.call('AVTransport', 'GetMediaInfo');
    return {
      currentUri: res.CurrentURI || '',
      currentUriMetadata: res.CurrentURIMetaData || ''
    };
  }

  // Retrieve zone/device info including firmware version. Useful for diagnostic
  // output and confirming compatibility with newer Sonos firmware generations.
  async getZoneInfo() {
    try {
      const res = await soapCall({
        host: this.host,
        port: this.port,
        service: 'ZoneGroupTopology',
        action: 'GetZoneGroupAttributes',
        params: {},
        timeoutMs: this.timeoutMs,
        requestFn: this.requestFn
      });
      return {
        groupName: res.CurrentZoneGroupName || null,
        groupId: res.CurrentZoneGroupID || null
      };
    } catch (err) {
      if (err instanceof SonosError && err.code === 'PLAYER_UNREACHABLE') throw err;
      return { groupName: null, groupId: null, error: err.message };
    }
  }

  // Sets the transport URI and starts playback. Implements a metadata-fallback
  // strategy required by Sonos firmware updates from 2024 onward, which more
  // strictly validate DIDL-Lite metadata: if the player rejects the rich
  // metadata, retry with empty metadata and the direct-broadcast URI scheme.
  async playStreamUri(uri, { title, metadata } = {}) {
    const normalizedUri = normalizeRadioUri(uri);
    const richMeta = metadata !== undefined ? metadata : buildDirectStreamMetadata({ title });

    const attempts = [
      { uri: normalizedUri, meta: richMeta, label: 'direct+metadata' },
      { uri: normalizedUri, meta: '', label: 'direct+empty-meta' },
      { uri: uri, meta: '', label: 'raw+empty-meta' }
    ];

    let lastErr = null;
    for (const attempt of attempts) {
      try {
        await this.call('AVTransport', 'SetAVTransportURI', {
          CurrentURI: attempt.uri,
          CurrentURIMetaData: attempt.meta
        });
        await this.play();
        return { ok: true, uri: attempt.uri, strategy: attempt.label };
      } catch (err) {
        lastErr = err;
        const rejected = err instanceof SonosError &&
          err.code === 'SOAP_FAULT' &&
          METADATA_REJECTED_CODES.has(String(err.faultCode));
        if (!rejected) throw err;
      }
    }
    throw lastErr || new SonosError('Failed to start stream', { code: 'STREAM_FAILED' });
  }

  async getStatus() {
    const [transport, position, media, volume, mute] = await Promise.all([
      this.getTransportInfo(),
      this.getPositionInfo(),
      this.getMediaInfo(),
      this.getVolume(),
      this.getMute()
    ]);
    return {
      online: true,
      state: transport.state,
      volume,
      mute,
      track: {
        title: position.metadata.title || null,
        artist: position.metadata.artist || null,
        album: position.metadata.album || null,
        streamContent: position.metadata.streamContent || null,
        uri: position.trackUri,
        duration: position.trackDuration,
        position: position.relTime
      },
      currentUri: media.currentUri
    };
  }

  async ping() {
    try {
      await this.getTransportInfo();
      return true;
    } catch {
      return false;
    }
  }
}

module.exports = {
  SonosClient,
  buildDirectStreamMetadata,
  normalizeRadioUri,
  parseTrackMeta,
  METADATA_REJECTED_CODES
};

};

__bundleModules["src/sonos/discovery.js"] = function (module, exports, require) {
'use strict';

const dgram = require('dgram');

const SSDP_HOST = '239.255.255.250';
const SSDP_PORT = 1900;

// Multiple search targets so discovery still works against newer Sonos
// firmware generations which may change how the player advertises itself.
// `ssdp:all` is the catch-all; the others are device-specific.
const SEARCH_TARGETS = [
  'urn:schemas-upnp-org:device:ZonePlayer:1',
  'urn:smartspeaker-audio:service:SpeakerGroup:1',
  'ssdp:all'
];

function buildSearchMessage(mx, target) {
  return [
    'M-SEARCH * HTTP/1.1',
    `HOST: ${SSDP_HOST}:${SSDP_PORT}`,
    'MAN: "ssdp:discover"',
    `MX: ${mx}`,
    `ST: ${target}`,
    '',
    ''
  ].join('\r\n');
}

function looksLikeSonos(headers) {
  const server = (headers['SERVER'] || '').toLowerCase();
  const usn = (headers['USN'] || '').toLowerCase();
  return server.includes('sonos') || usn.includes('rincon') || usn.includes('zoneplayer');
}

function parseResponse(buf, rinfo) {
  const text = buf.toString('utf8');
  const lines = text.split(/\r?\n/);
  const headers = {};
  for (const line of lines.slice(1)) {
    const idx = line.indexOf(':');
    if (idx > 0) headers[line.slice(0, idx).trim().toUpperCase()] = line.slice(idx + 1).trim();
  }
  const location = headers['LOCATION'];
  const usn = headers['USN'] || '';
  if (!location) return null;
  if (!looksLikeSonos(headers)) return null;
  let host = rinfo.address;
  let port = 1400;
  try {
    const url = new URL(location);
    host = url.hostname;
    port = parseInt(url.port, 10) || 1400;
  } catch {
    /* keep rinfo defaults */
  }
  const uuidMatch = usn.match(/uuid:([A-Za-z0-9_-]+)/);
  return {
    host,
    port,
    uuid: uuidMatch ? uuidMatch[1] : null,
    location,
    server: headers['SERVER'] || null
  };
}

function discover({ timeoutMs = 4000, mx = 2 } = {}) {
  return new Promise((resolve) => {
    const found = new Map();
    const socket = dgram.createSocket({ type: 'udp4', reuseAddr: true });
    const finish = () => {
      try { socket.close(); } catch { /* ignore */ }
      resolve(Array.from(found.values()));
    };
    socket.on('error', () => finish());
    socket.on('message', (msg, rinfo) => {
      const player = parseResponse(msg, rinfo);
      if (player && !found.has(player.host)) {
        found.set(player.host, player);
      }
    });
    socket.bind(0, () => {
      for (const target of SEARCH_TARGETS) {
        const message = Buffer.from(buildSearchMessage(mx, target));
        socket.send(message, 0, message.length, SSDP_PORT, SSDP_HOST, () => { /* ignore per-message errors */ });
      }
    });
    setTimeout(finish, timeoutMs);
  });
}

module.exports = { discover, parseResponse, buildSearchMessage, SEARCH_TARGETS };

};

__bundleModules["src/sonos/events.js"] = function (module, exports, require) {
'use strict';

// UPnP eventing: SUBSCRIBE to a Sonos player's event endpoints, then receive
// NOTIFY POSTs from the player on a callback URL we expose. This avoids the
// need to poll for status — KNX outputs can reflect external state changes
// (someone using the Sonos app, AirPlay handoff, etc.) within ~1 s.
//
// Sonos subscription lifecycle:
//   SUBSCRIBE  → player returns a SID + Timeout (e.g. 1800 s)
//   NOTIFY     → player pushes XML to our callback URL on every state change
//   SUBSCRIBE  → with SID, renews before timeout expires
//   UNSUBSCRIBE → with SID, removes the subscription
//
// Compatibility: this is part of UPnP eventing, which Sonos has supported
// continuously across every firmware generation including 2024+ / 2026. It is
// the same mechanism the official Sonos app and Home Assistant use.

const http = require('http');
const { extractTag, xmlUnescape, SonosError } = __bundleRequire("src/sonos/soap.js");

const EVENT_PATHS = {
  AVTransport: '/MediaRenderer/AVTransport/Event',
  RenderingControl: '/MediaRenderer/RenderingControl/Event'
};

const RENEWAL_MARGIN_MS = 60 * 1000;

function httpUpnp(opts, body, { timeoutMs = 5000 } = {}) {
  return new Promise((resolve, reject) => {
    const req = http.request(opts, (res) => {
      let data = '';
      res.setEncoding('utf8');
      res.on('data', (c) => { data += c; });
      res.on('end', () => resolve({ status: res.statusCode, headers: res.headers, body: data }));
    });
    req.on('error', reject);
    req.setTimeout(timeoutMs, () => req.destroy(new Error('SUBSCRIBE timeout')));
    if (body) req.write(body);
    req.end();
  });
}

async function subscribe({ host, port = 1400, eventPath, callbackUrl, sid, timeoutSec = 1800, httpFn = httpUpnp }) {
  const headers = sid
    ? { SID: sid, TIMEOUT: `Second-${timeoutSec}` }
    : { CALLBACK: `<${callbackUrl}>`, NT: 'upnp:event', TIMEOUT: `Second-${timeoutSec}` };
  const res = await httpFn({ host, port, method: 'SUBSCRIBE', path: eventPath, headers });
  if (res.status >= 400) {
    throw new SonosError(`SUBSCRIBE failed: HTTP ${res.status}`, {
      code: res.status === 412 ? 'SUBSCRIPTION_EXPIRED' : 'SUBSCRIBE_FAILED',
      status: res.status
    });
  }
  const newSid = res.headers['sid'] || sid;
  const timeoutHeader = res.headers['timeout'] || `Second-${timeoutSec}`;
  const match = String(timeoutHeader).match(/Second-(\d+)/);
  const grantedSec = match ? parseInt(match[1], 10) : timeoutSec;
  return { sid: newSid, timeoutSec: grantedSec };
}

async function unsubscribe({ host, port = 1400, eventPath, sid, httpFn = httpUpnp }) {
  if (!sid) return;
  try {
    await httpFn({ host, port, method: 'UNSUBSCRIBE', path: eventPath, headers: { SID: sid } });
  } catch {
    /* best effort */
  }
}

// Parses a Sonos NOTIFY body. Sonos wraps event values in two levels of XML
// encoding: the outer envelope contains <e:property> entries whose values are
// themselves URL-decoded XML carrying the actual data.
function parseNotifyBody(rawXml) {
  if (!rawXml) return {};
  const lastChangeRaw = extractTag(rawXml, 'LastChange');
  if (!lastChangeRaw) return { raw: rawXml };
  const lastChange = xmlUnescape(lastChangeRaw);

  // <InstanceID val="0"> contains nested elements like
  //   <TransportState val="PLAYING"/>
  //   <Volume channel="Master" val="30"/>
  const out = {};
  const transportState = lastChange.match(/<TransportState\s+val="([^"]*)"/);
  if (transportState) out.state = transportState[1];

  const volume = lastChange.match(/<Volume\s+channel="Master"\s+val="([^"]*)"/);
  if (volume) out.volume = parseInt(volume[1], 10);

  const mute = lastChange.match(/<Mute\s+channel="Master"\s+val="([^"]*)"/);
  if (mute) out.mute = mute[1] === '1';

  const currentTrackUri = lastChange.match(/<CurrentTrackURI\s+val="([^"]*)"/);
  if (currentTrackUri) out.trackUri = xmlUnescape(currentTrackUri[1]);

  const currentTrackMetaRaw = lastChange.match(/<CurrentTrackMetaData\s+val="([^"]*)"/);
  if (currentTrackMetaRaw) {
    const meta = xmlUnescape(currentTrackMetaRaw[1]);
    out.title = extractTag(meta, 'dc:title');
    out.artist = extractTag(meta, 'dc:creator');
    out.streamContent = extractTag(meta, 'r:streamContent');
  }
  return out;
}

class EventManager {
  constructor({ registry, stateStore, callbackBaseUrl, logger, httpFn }) {
    this.registry = registry;
    this.stateStore = stateStore;
    this.callbackBaseUrl = callbackBaseUrl;
    this.logger = logger || { info() {}, warn() {}, error() {}, debug() {} };
    this.httpFn = httpFn;
    this.subs = new Map();
    this.renewTimers = new Map();
    this.stopped = false;
  }

  callbackUrlFor(playerName, service) {
    const base = this.callbackBaseUrl.replace(/\/$/, '');
    return `${base}/upnp/event/${encodeURIComponent(playerName)}/${service}`;
  }

  async start() {
    for (const player of this.registry.list()) {
      await this.subscribePlayer(player.name).catch((err) => {
        this.logger.warn('UPnP subscribe failed', { player: player.name, error: err.message });
      });
    }
  }

  async subscribePlayer(name) {
    const client = this.registry.get(name);
    for (const [service, path] of Object.entries(EVENT_PATHS)) {
      const key = `${name}:${service}`;
      const callbackUrl = this.callbackUrlFor(name, service);
      try {
        const result = await subscribe({
          host: client.host,
          port: client.port,
          eventPath: path,
          callbackUrl,
          httpFn: this.httpFn
        });
        this.subs.set(key, { sid: result.sid, service, name, eventPath: path });
        this.scheduleRenewal(key, result.timeoutSec);
        this.logger.info('UPnP subscribed', { player: name, service, sid: result.sid, timeoutSec: result.timeoutSec });
      } catch (err) {
        this.logger.warn('UPnP subscribe failed', { player: name, service, error: err.message });
      }
    }
  }

  scheduleRenewal(key, timeoutSec) {
    const existing = this.renewTimers.get(key);
    if (existing) clearTimeout(existing);
    const delay = Math.max(5000, timeoutSec * 1000 - RENEWAL_MARGIN_MS);
    const t = setTimeout(() => this.renew(key).catch((err) => {
      this.logger.warn('UPnP renewal failed', { key, error: err.message });
    }), delay);
    if (typeof t.unref === 'function') t.unref();
    this.renewTimers.set(key, t);
  }

  async renew(key) {
    if (this.stopped) return;
    const sub = this.subs.get(key);
    if (!sub) return;
    const client = this.registry.get(sub.name);
    try {
      const result = await subscribe({
        host: client.host,
        port: client.port,
        eventPath: sub.eventPath,
        sid: sub.sid,
        httpFn: this.httpFn
      });
      sub.sid = result.sid;
      this.scheduleRenewal(key, result.timeoutSec);
    } catch (err) {
      if (err.code === 'SUBSCRIPTION_EXPIRED') {
        this.logger.info('UPnP subscription expired, resubscribing', { key });
        this.subs.delete(key);
        await this.subscribePlayer(sub.name);
      } else {
        throw err;
      }
    }
  }

  // Invoked by the HTTP server when a NOTIFY arrives.
  handleNotify(playerName, service, rawXml) {
    const parsed = parseNotifyBody(rawXml);
    if (!parsed || Object.keys(parsed).length === 0) return;
    const patch = {};
    if (parsed.state !== undefined) patch.state = parsed.state;
    if (parsed.volume !== undefined) patch.volume = parsed.volume;
    if (parsed.mute !== undefined) patch.mute = parsed.mute;
    if (parsed.trackUri !== undefined || parsed.title !== undefined) {
      const prev = this.stateStore.get(playerName) || {};
      patch.track = {
        ...(prev.track || {}),
        ...(parsed.title !== undefined ? { title: parsed.title } : {}),
        ...(parsed.artist !== undefined ? { artist: parsed.artist } : {}),
        ...(parsed.streamContent !== undefined ? { streamContent: parsed.streamContent } : {}),
        ...(parsed.trackUri !== undefined ? { uri: parsed.trackUri } : {})
      };
    }
    patch.online = true;
    this.stateStore.markEventReceived(playerName);
    this.stateStore.set(playerName, patch);
  }

  async stop() {
    this.stopped = true;
    for (const t of this.renewTimers.values()) clearTimeout(t);
    this.renewTimers.clear();
    const tasks = [];
    for (const sub of this.subs.values()) {
      const client = this.registry.has(sub.name) ? this.registry.get(sub.name) : null;
      if (!client) continue;
      tasks.push(unsubscribe({
        host: client.host,
        port: client.port,
        eventPath: sub.eventPath,
        sid: sub.sid,
        httpFn: this.httpFn
      }));
    }
    await Promise.all(tasks);
    this.subs.clear();
  }
}

module.exports = { EventManager, subscribe, unsubscribe, parseNotifyBody, EVENT_PATHS };

};

__bundleModules["src/sonos/soap.js"] = function (module, exports, require) {
'use strict';

const http = require('http');

class SonosError extends Error {
  constructor(message, { code, status, faultCode, faultString, cause } = {}) {
    super(message);
    this.name = 'SonosError';
    this.code = code || 'SONOS_ERROR';
    this.status = status;
    this.faultCode = faultCode;
    this.faultString = faultString;
    if (cause) this.cause = cause;
  }
}

const SERVICES = {
  AVTransport: {
    controlUrl: '/MediaRenderer/AVTransport/Control',
    type: 'urn:schemas-upnp-org:service:AVTransport:1'
  },
  RenderingControl: {
    controlUrl: '/MediaRenderer/RenderingControl/Control',
    type: 'urn:schemas-upnp-org:service:RenderingControl:1'
  },
  ContentDirectory: {
    controlUrl: '/MediaServer/ContentDirectory/Control',
    type: 'urn:schemas-upnp-org:service:ContentDirectory:1'
  },
  ZoneGroupTopology: {
    controlUrl: '/ZoneGroupTopology/Control',
    type: 'urn:schemas-upnp-org:service:ZoneGroupTopology:1'
  }
};

function xmlEscape(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&apos;');
}

function xmlUnescape(s) {
  return String(s)
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&apos;/g, "'")
    .replace(/&amp;/g, '&');
}

function buildEnvelope(serviceType, action, params) {
  const body = Object.entries(params || {})
    .map(([k, v]) => `<${k}>${v == null ? '' : xmlEscape(v)}</${k}>`)
    .join('');
  return (
    '<?xml version="1.0" encoding="utf-8"?>' +
    '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" ' +
    's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">' +
    '<s:Body>' +
    `<u:${action} xmlns:u="${serviceType}">${body}</u:${action}>` +
    '</s:Body>' +
    '</s:Envelope>'
  );
}

function extractTag(xml, tag) {
  const re = new RegExp(`<${tag}[^>]*>([\\s\\S]*?)</${tag}>`);
  const m = xml.match(re);
  return m ? xmlUnescape(m[1]) : null;
}

function parseResponse(xml, action) {
  const fault = extractTag(xml, 's:Fault') || extractTag(xml, 'SOAP-ENV:Fault');
  if (fault) {
    const faultCode = extractTag(fault, 'faultcode') || extractTag(fault, 'errorCode');
    const faultString = extractTag(fault, 'faultstring') || extractTag(fault, 'errorDescription');
    const upnpErrorCode = extractTag(fault, 'errorCode');
    throw new SonosError(`SOAP fault: ${faultString || 'unknown'}`, {
      code: 'SOAP_FAULT',
      faultCode: upnpErrorCode || faultCode,
      faultString
    });
  }
  const bodyMatch = xml.match(/<u:[^>]*Response[^>]*>([\s\S]*?)<\/u:[^>]*Response>/);
  if (!bodyMatch) return {};
  const inner = bodyMatch[1];
  const result = {};
  const tagRegex = /<([A-Za-z][A-Za-z0-9_]*)[^>]*>([\s\S]*?)<\/\1>/g;
  let m;
  while ((m = tagRegex.exec(inner)) !== null) {
    result[m[1]] = xmlUnescape(m[2]);
  }
  return result;
}

function httpRequest(opts, body, { timeoutMs = 5000 } = {}) {
  return new Promise((resolve, reject) => {
    const req = http.request(opts, (res) => {
      let data = '';
      res.setEncoding('utf8');
      res.on('data', (chunk) => { data += chunk; });
      res.on('end', () => resolve({ status: res.statusCode, body: data, headers: res.headers }));
    });
    req.on('error', (err) => reject(err));
    req.setTimeout(timeoutMs, () => {
      req.destroy(new Error(`Request timed out after ${timeoutMs}ms`));
    });
    if (body) req.write(body);
    req.end();
  });
}

async function soapCall({ host, port = 1400, service, action, params, timeoutMs, requestFn }) {
  const svc = SERVICES[service];
  if (!svc) throw new SonosError(`Unknown service: ${service}`, { code: 'UNKNOWN_SERVICE' });
  const envelope = buildEnvelope(svc.type, action, params);
  const opts = {
    host,
    port,
    method: 'POST',
    path: svc.controlUrl,
    headers: {
      'Content-Type': 'text/xml; charset="utf-8"',
      'Content-Length': Buffer.byteLength(envelope),
      SOAPACTION: `"${svc.type}#${action}"`
    }
  };
  const doRequest = requestFn || httpRequest;
  let res;
  try {
    res = await doRequest(opts, envelope, { timeoutMs });
  } catch (err) {
    throw new SonosError(`HTTP error: ${err.message}`, {
      code: err.code === 'ECONNREFUSED' || err.code === 'EHOSTUNREACH' || err.code === 'ENETUNREACH'
        ? 'PLAYER_UNREACHABLE'
        : 'HTTP_ERROR',
      cause: err
    });
  }
  if (res.status >= 200 && res.status < 300) {
    return parseResponse(res.body, action);
  }
  if (res.status >= 400 && res.status < 600 && res.body) {
    try {
      return parseResponse(res.body, action);
    } catch (parseErr) {
      if (parseErr instanceof SonosError) {
        parseErr.status = res.status;
        throw parseErr;
      }
    }
  }
  throw new SonosError(`Unexpected HTTP status: ${res.status}`, {
    code: 'HTTP_ERROR',
    status: res.status
  });
}

module.exports = {
  soapCall,
  buildEnvelope,
  parseResponse,
  extractTag,
  xmlEscape,
  xmlUnescape,
  SERVICES,
  SonosError,
  httpRequest
};

};

__bundleModules["src/state.js"] = function (module, exports, require) {
'use strict';

// In-memory state cache. Updated by:
//   1. Successful control actions (optimistic local update)
//   2. UPnP event NOTIFY callbacks (authoritative push from the player)
//   3. On-demand status polls (fallback for never-seen players)
//
// Anyone holding a subscription (Server-Sent Events, webhook push) is notified
// via the EventEmitter so KNX status outputs can update without polling.

const { EventEmitter } = require('events');

class StateStore extends EventEmitter {
  constructor() {
    super();
    this.byPlayer = new Map();
    this.activeStation = new Map();
    this.lastEventAt = new Map();
  }

  get(playerName) {
    return this.byPlayer.get(playerName) || null;
  }

  set(playerName, patch) {
    const prev = this.byPlayer.get(playerName) || {};
    const next = { ...prev, ...patch, updatedAt: new Date().toISOString() };
    this.byPlayer.set(playerName, next);
    this.emit('change', { player: playerName, state: next });
    return next;
  }

  markEventReceived(playerName) {
    this.lastEventAt.set(playerName, Date.now());
  }

  hasRecentEvent(playerName, maxAgeMs = 60000) {
    const ts = this.lastEventAt.get(playerName);
    if (!ts) return false;
    return (Date.now() - ts) < maxAgeMs;
  }

  setActiveStation(playerName, station) {
    if (station) this.activeStation.set(playerName, station);
    else this.activeStation.delete(playerName);
    this.set(playerName, {
      activeStation: station ? { index: station.index, name: station.name } : null
    });
  }

  getActiveStation(playerName) {
    return this.activeStation.get(playerName) || null;
  }

  all() {
    const out = {};
    for (const [name, state] of this.byPlayer.entries()) {
      out[name] = state;
    }
    return out;
  }
}

module.exports = { StateStore };

};

__bundleModules["src/webhook.js"] = function (module, exports, require) {
'use strict';

// Optional outbound webhook. When state changes (driven by UPnP events), POST
// a small JSON payload to a URL the HomeServer exposes. This is how the
// HomeServer learns about external state changes without polling.
//
// Configured via cfg.webhook.url. If empty, webhook push is disabled.

const http = require('http');
const https = require('https');
const { URL } = require('url');

function post(url, payload, { timeoutMs = 5000, authHeader, logger } = {}) {
  return new Promise((resolve) => {
    let parsed;
    try { parsed = new URL(url); } catch {
      if (logger) logger.warn('Webhook URL invalid', { url });
      return resolve({ ok: false, error: 'INVALID_URL' });
    }
    const body = Buffer.from(JSON.stringify(payload));
    const lib = parsed.protocol === 'https:' ? https : http;
    const headers = {
      'Content-Type': 'application/json',
      'Content-Length': body.length,
      'User-Agent': 'gira-homeserver-sonos-bridge'
    };
    if (authHeader) headers['Authorization'] = authHeader;
    const req = lib.request({
      protocol: parsed.protocol,
      host: parsed.hostname,
      port: parsed.port || (parsed.protocol === 'https:' ? 443 : 80),
      path: parsed.pathname + parsed.search,
      method: 'POST',
      headers
    }, (res) => {
      res.on('data', () => {});
      res.on('end', () => resolve({ ok: res.statusCode < 400, status: res.statusCode }));
    });
    req.on('error', (err) => {
      if (logger) logger.warn('Webhook delivery failed', { url, error: err.message });
      resolve({ ok: false, error: err.message });
    });
    req.setTimeout(timeoutMs, () => req.destroy(new Error('Webhook timeout')));
    req.write(body);
    req.end();
  });
}

class WebhookPublisher {
  constructor({ url, authHeader, logger }) {
    this.url = url || '';
    this.authHeader = authHeader || '';
    this.logger = logger || { warn() {}, debug() {} };
  }

  enabled() { return !!this.url; }

  setUrl(url) { this.url = url || ''; }
  setAuthHeader(h) { this.authHeader = h || ''; }

  async publish(event, payload) {
    if (!this.enabled()) return;
    await post(this.url, { event, payload, ts: new Date().toISOString() }, {
      authHeader: this.authHeader,
      logger: this.logger
    });
  }
}

module.exports = { WebhookPublisher, post };

};

// Entry: load the bundled server module to register its exports, then start it.
const __entry  = __bundleRequire("src/server.js");
const __config = __bundleRequire('src/config.js');
const __logger = __bundleRequire('src/logger.js');
module.exports = __entry;

if (require.main === module) {
  const fs = require("fs");
  const argPath = process.argv[2];
  const cfgPath = (argPath && fs.existsSync(argPath)) ? argPath
    : (process.env.SONOS_BRIDGE_CONFIG && fs.existsSync(process.env.SONOS_BRIDGE_CONFIG)) ? process.env.SONOS_BRIDGE_CONFIG
    : (fs.existsSync("./config.json") ? "./config.json" : null);
  if (!cfgPath) {
    console.error("No config found. Pass a path, set SONOS_BRIDGE_CONFIG, or create ./config.json.");
    process.exit(1);
  }
  try {
    const cfg = __config.load(cfgPath);
    __logger.info("Config loaded", { configPath: cfgPath });
    __entry.startServer(cfg, { configPath: cfgPath }).then(({ server, eventMgr }) => {
      const shutdown = async (sig) => {
        __logger.info("Shutting down", { signal: sig });
        if (eventMgr) await eventMgr.stop();
        server.close(() => process.exit(0));
        setTimeout(() => process.exit(0), 3000).unref();
      };
      process.on("SIGTERM", () => shutdown("SIGTERM"));
      process.on("SIGINT",  () => shutdown("SIGINT"));
    }).catch((err) => {
      __logger.error("Failed to start server", { error: err.message });
      process.exit(1);
    });
  } catch (err) {
    __logger.error("Configuration error", { error: err.message, details: err.details });
    process.exit(1);
  }
}