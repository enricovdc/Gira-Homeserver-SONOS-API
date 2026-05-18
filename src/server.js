'use strict';

const http = require('http');
const { URL } = require('url');

const config = require('./config');
const logger = require('./logger');
const { PlayerRegistry, PlayerError } = require('./players');
const { RadioStationStore, RadioError } = require('./radio');
const { SonosError } = require('./sonos/soap');

function sendJson(res, status, payload) {
  const body = JSON.stringify(payload);
  res.writeHead(status, {
    'Content-Type': 'application/json; charset=utf-8',
    'Content-Length': Buffer.byteLength(body),
    'Cache-Control': 'no-store'
  });
  res.end(body);
}

function errorFor(err) {
  if (err instanceof RadioError) {
    return { status: err.code === 'STATION_NOT_FOUND' ? 404 : 400, code: err.code, message: err.message };
  }
  if (err instanceof PlayerError) {
    return { status: err.code === 'PLAYER_NOT_FOUND' ? 404 : 400, code: err.code, message: err.message };
  }
  if (err instanceof SonosError) {
    const status = err.code === 'PLAYER_UNREACHABLE' ? 503 : err.code === 'INVALID_ARG' ? 400 : 502;
    return { status, code: err.code, message: err.message, faultCode: err.faultCode, faultString: err.faultString };
  }
  return { status: 500, code: 'INTERNAL', message: err.message || 'Internal error' };
}

function sendError(res, err, context) {
  const e = errorFor(err);
  logger.warn('Request failed', { code: e.code, message: e.message, ...context });
  sendJson(res, e.status, { ok: false, error: e.code, message: e.message, faultCode: e.faultCode, faultString: e.faultString });
}

function parseQuery(reqUrl) {
  const url = new URL(reqUrl, 'http://x');
  const q = {};
  for (const [k, v] of url.searchParams.entries()) q[k] = v;
  return { pathname: url.pathname, query: q };
}

function readJsonBody(req, { maxBytes = 64 * 1024 } = {}) {
  return new Promise((resolve, reject) => {
    let size = 0;
    const chunks = [];
    req.on('data', (chunk) => {
      size += chunk.length;
      if (size > maxBytes) {
        reject(new Error('Request body too large'));
        req.destroy();
        return;
      }
      chunks.push(chunk);
    });
    req.on('end', () => {
      if (chunks.length === 0) return resolve({});
      try {
        const body = Buffer.concat(chunks).toString('utf8');
        resolve(body ? JSON.parse(body) : {});
      } catch (err) {
        reject(new Error('Invalid JSON body'));
      }
    });
    req.on('error', reject);
  });
}

function checkAuth(req, token) {
  if (!token) return true;
  const header = req.headers['authorization'] || '';
  if (header === `Bearer ${token}`) return true;
  const url = new URL(req.url, 'http://x');
  if (url.searchParams.get('token') === token) return true;
  return false;
}

function createApp({ cfg, registry, radioStore, statusCache }) {
  async function handleControl(player, action, query, body) {
    switch (action) {
      case 'play': return player.play();
      case 'pause': return player.pause();
      case 'stop': return player.stop();
      case 'next': return player.next();
      case 'previous': return player.previous();
      case 'volume': {
        const level = body.level !== undefined ? body.level : query.level;
        if (level === undefined) throw new SonosError('Missing volume level', { code: 'INVALID_ARG' });
        return player.setVolume(level);
      }
      case 'volume/up': {
        const step = Number(body.step ?? query.step ?? 2);
        return player.adjustVolume(step);
      }
      case 'volume/down': {
        const step = Number(body.step ?? query.step ?? 2);
        return player.adjustVolume(-step);
      }
      case 'mute': {
        const mute = body.mute ?? query.mute;
        const v = mute === undefined ? true : (mute === true || mute === 'true' || mute === '1');
        return player.setMute(v);
      }
      case 'unmute': return player.setMute(false);
      case 'mute/toggle': {
        const current = await player.getMute();
        return player.setMute(!current);
      }
      default:
        throw new SonosError(`Unknown action: ${action}`, { code: 'INVALID_ARG' });
    }
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
      const idx = body.index ?? query.index;
      station = radioStore.byIndexLookup(idx);
    } else if (op === 'name') {
      const n = body.name ?? query.name;
      station = radioStore.byName(n);
    } else {
      throw new RadioError(`Unknown radio op: ${op}`, 'INVALID_ARG');
    }
    await player.playStreamUri(station.streamUri, {
      title: station.metadata?.title || station.name
    });
    statusCache.markActiveStation(player.name, station);
    return { ok: true, station: { index: station.index, name: station.name } };
  }

  return async function handle(req, res) {
    const started = Date.now();
    try {
      if (!checkAuth(req, cfg.server.authToken)) {
        return sendJson(res, 401, { ok: false, error: 'UNAUTHORIZED', message: 'Invalid or missing token' });
      }
      const { pathname, query } = parseQuery(req.url);
      const segments = pathname.split('/').filter(Boolean);

      if (segments[0] === 'health') {
        return sendJson(res, 200, { ok: true, uptime: process.uptime(), players: registry.list().length });
      }
      if (segments[0] === 'players' && segments.length === 1) {
        return sendJson(res, 200, { ok: true, players: registry.list(), defaultPlayer: registry.defaultName });
      }
      if (segments[0] === 'stations' && segments.length === 1) {
        return sendJson(res, 200, { ok: true, stations: radioStore.list() });
      }

      const body = req.method === 'POST' || req.method === 'PUT' ? await readJsonBody(req) : {};

      if (segments[0] === 'players' && segments[1] === 'discover') {
        const { discover } = require('./sonos/discovery');
        const players = await discover({ timeoutMs: cfg.discovery.timeoutMs });
        return sendJson(res, 200, { ok: true, discovered: players });
      }

      const playerName = query.player || body.player || segments[1];
      const isPlayerScoped = segments[0] === 'players' && segments.length >= 3;

      if (isPlayerScoped) {
        const player = registry.get(playerName);
        const action = segments.slice(2).join('/');
        if (action === 'status') {
          const status = await player.getStatus();
          const active = statusCache.getActiveStation(player.name);
          return sendJson(res, 200, {
            ok: true,
            player: player.name,
            online: true,
            ...status,
            activeStation: active ? { index: active.index, name: active.name } : null
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
        try {
          const status = await player.getStatus();
          const active = statusCache.getActiveStation(player.name);
          return sendJson(res, 200, {
            ok: true, player: player.name, online: true, ...status,
            activeStation: active ? { index: active.index, name: active.name } : null
          });
        } catch (err) {
          if (err instanceof SonosError && err.code === 'PLAYER_UNREACHABLE') {
            return sendJson(res, 200, { ok: true, player: player.name, online: false, error: err.message });
          }
          throw err;
        }
      }

      return sendJson(res, 404, { ok: false, error: 'NOT_FOUND', message: `No route: ${pathname}` });
    } catch (err) {
      sendError(res, err, { url: req.url, method: req.method, durationMs: Date.now() - started });
    }
  };
}

function createStatusCache() {
  const activeStations = new Map();
  return {
    markActiveStation(player, station) { activeStations.set(player, station); },
    getActiveStation(player) { return activeStations.get(player) || null; },
    clearActiveStation(player) { activeStations.delete(player); }
  };
}

function startServer(cfg, { clientFactory } = {}) {
  logger.setLevel(cfg.logging.level);
  const registry = new PlayerRegistry({
    players: cfg.players,
    defaultPlayer: cfg.defaultPlayer,
    clientFactory
  });
  const radioStore = new RadioStationStore(cfg.radioStations);
  const statusCache = createStatusCache();
  const app = createApp({ cfg, registry, radioStore, statusCache });
  const server = http.createServer(app);
  return new Promise((resolve) => {
    server.listen(cfg.server.port, cfg.server.host, () => {
      const addr = server.address();
      logger.info('Bridge listening', { host: addr.address, port: addr.port, players: registry.list().length, stations: radioStore.list().length });
      resolve({ server, app, registry, radioStore, statusCache });
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
    startServer(cfg).catch((err) => {
      logger.error('Failed to start server', { error: err.message });
      process.exit(1);
    });
  } catch (err) {
    logger.error('Configuration error', { error: err.message, details: err.details });
    process.exit(1);
  }
}

module.exports = { createApp, startServer, createStatusCache };
