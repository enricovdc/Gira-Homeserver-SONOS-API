'use strict';

const http = require('http');
const os = require('os');
const path = require('path');
const { URL } = require('url');

const config = require('./config');
const logger = require('./logger');
const { PlayerRegistry, PlayerError } = require('./players');
const { RadioStationStore, RadioError } = require('./radio');
const { SonosError } = require('./sonos/soap');
const { StateStore } = require('./state');
const { EventManager, parseNotifyBody } = require('./sonos/events');
const { WebhookPublisher } = require('./webhook');
const { saveConfig } = require('./persist');
const { adminHtml } = require('./admin-ui');

const VERSION = require('../package.json').version;

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
        const { discover } = require('./sonos/discovery');
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
