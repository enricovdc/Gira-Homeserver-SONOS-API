'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const { loadFromObject, load } = require('../src/config');
const { startServer } = require('../src/server');
const { saveConfig } = require('../src/persist');
const { createMockRequest } = require('./helpers/mock-sonos');

function request(port, urlPath, { method = 'GET', body, headers = {} } = {}) {
  return new Promise((resolve, reject) => {
    const data = body ? JSON.stringify(body) : null;
    const req = http.request({
      host: '127.0.0.1', port, path: urlPath, method,
      headers: {
        'Content-Type': 'application/json',
        ...(data ? { 'Content-Length': Buffer.byteLength(data) } : {}),
        ...headers
      }
    }, (res) => {
      let buf = '';
      res.on('data', (c) => { buf += c; });
      res.on('end', () => {
        let parsed = null;
        try { parsed = buf ? JSON.parse(buf) : null; } catch { parsed = buf; }
        resolve({ status: res.statusCode, body: parsed, raw: buf, headers: res.headers });
      });
    });
    req.on('error', reject);
    if (data) req.write(data);
    req.end();
  });
}

async function withServer(opts, fn) {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'sonos-bridge-'));
  const cfgPath = path.join(tmp, 'config.json');
  const baseCfg = loadFromObject({
    server: { host: '127.0.0.1', port: 0, authToken: '' },
    players: [{ name: 'lr', host: '10.0.0.1' }],
    defaultPlayer: 'lr',
    radioStations: [{ index: 1, name: 'R1', streamUri: 'http://r1' }],
    eventing: { enabled: false }, // events tested separately
    admin: { enabled: true },
    ...opts.config
  });
  saveConfig(cfgPath, baseCfg);
  const cfg = load(cfgPath);
  const { SonosClient } = require('../src/sonos/client');
  const requestFn = createMockRequest(opts.handlers || {});
  const clientFactory = (c) => new SonosClient({ ...c, requestFn });
  const { server } = await startServer(cfg, { clientFactory, configPath: cfgPath, disableEventing: true });
  const port = server.address().port;
  try {
    await fn(port, requestFn, cfgPath);
  } finally {
    await new Promise((r) => server.close(r));
    fs.rmSync(tmp, { recursive: true, force: true });
  }
}

test('GET / serves admin HTML', async () => {
  await withServer({}, async (port) => {
    const r = await request(port, '/');
    assert.equal(r.status, 200);
    assert.equal(r.headers['content-type'], 'text/html; charset=utf-8');
    assert.match(r.raw, /Gira HomeServer Sonos Bridge/);
  });
});

test('GET /info returns bridge metadata', async () => {
  await withServer({}, async (port) => {
    const r = await request(port, '/info');
    assert.equal(r.status, 200);
    assert.equal(r.body.name, 'Gira HomeServer Sonos Bridge');
    assert.match(r.body.publicUrl, /^http:\/\/127\.0\.0\.1:/);
    assert.match(r.body.adminUrl, /\/$/);
    assert.equal(r.body.eventingEnabled, false);
  });
});

test('GET /api/config returns redacted config', async () => {
  await withServer({
    config: { server: { authToken: 'shh' }, cloud: { clientSecret: 'top' } }
  }, async (port) => {
    const r = await request(port, '/api/config', { headers: { Authorization: 'Bearer shh' } });
    assert.equal(r.status, 200);
    assert.equal(r.body.config.server.authToken, '[set]');
    assert.equal(r.body.config.cloud.clientSecret, '[set]');
  });
});

test('PUT /api/config persists changes', async () => {
  await withServer({}, async (port, _req, cfgPath) => {
    const r = await request(port, '/api/config', {
      method: 'PUT',
      body: { webhook: { url: 'http://127.0.0.1/quad/x', authHeader: 'Bearer abc' } }
    });
    assert.equal(r.status, 200);
    const onDisk = JSON.parse(fs.readFileSync(cfgPath, 'utf8'));
    assert.equal(onDisk.webhook.url, 'http://127.0.0.1/quad/x');
    assert.equal(onDisk.webhook.authHeader, 'Bearer abc');
  });
});

test('POST /api/players adds and persists a player', async () => {
  await withServer({}, async (port, _req, cfgPath) => {
    const r = await request(port, '/api/players', {
      method: 'POST',
      body: { name: 'kitchen', host: '10.0.0.2' }
    });
    assert.equal(r.status, 200);
    const onDisk = JSON.parse(fs.readFileSync(cfgPath, 'utf8'));
    assert.equal(onDisk.players.length, 2);
    assert.ok(onDisk.players.find(p => p.name === 'kitchen'));
  });
});

test('DELETE /api/players/:name removes and persists', async () => {
  await withServer({}, async (port, _req, cfgPath) => {
    const r = await request(port, '/api/players/lr', { method: 'DELETE' });
    assert.equal(r.status, 200);
    const onDisk = JSON.parse(fs.readFileSync(cfgPath, 'utf8'));
    assert.equal(onDisk.players.length, 0);
  });
});

test('POST /api/stations adds a station', async () => {
  await withServer({}, async (port, _req, cfgPath) => {
    const r = await request(port, '/api/stations', {
      method: 'POST',
      body: { index: 2, name: 'R2', streamUri: 'http://r2' }
    });
    assert.equal(r.status, 200);
    const onDisk = JSON.parse(fs.readFileSync(cfgPath, 'utf8'));
    assert.equal(onDisk.radioStations.length, 2);
  });
});

test('invalid player add rejected and not persisted', async () => {
  await withServer({}, async (port, _req, cfgPath) => {
    const before = fs.readFileSync(cfgPath, 'utf8');
    const r = await request(port, '/api/players', { method: 'POST', body: { name: 'x' } });
    assert.equal(r.status, 400);
    const after = fs.readFileSync(cfgPath, 'utf8');
    assert.equal(before, after);
  });
});

test('GET /api/players/full includes live state', async () => {
  await withServer({
    handlers: { Play: { fields: {} } }
  }, async (port) => {
    await request(port, '/players/lr/play', { method: 'POST' });
    const r = await request(port, '/api/players/full');
    assert.equal(r.body.players.length, 1);
    assert.equal(r.body.states.lr.state, 'PLAYING');
  });
});

test('NOTIFY callback updates state without auth', async () => {
  await withServer({
    config: { eventing: { enabled: true } }
  }, async (port) => {
    const body = `<?xml version="1.0"?><e:propertyset xmlns:e="urn:schemas-upnp-org:event-1-0">
      <e:property><LastChange>&lt;Event&gt;&lt;InstanceID val=&quot;0&quot;&gt;
      &lt;Volume channel=&quot;Master&quot; val=&quot;55&quot;/&gt;
      &lt;/InstanceID&gt;&lt;/Event&gt;</LastChange></e:property></e:propertyset>`;
    const r = await new Promise((resolve, reject) => {
      const req = http.request({
        host: '127.0.0.1', port, path: '/upnp/event/lr/RenderingControl',
        method: 'NOTIFY', headers: { 'Content-Length': Buffer.byteLength(body) }
      }, (res) => { res.on('data', () => {}); res.on('end', () => resolve({ status: res.statusCode })); });
      req.on('error', reject);
      req.write(body);
      req.end();
    });
    assert.equal(r.status, 200);
    const full = await request(port, '/api/players/full');
    assert.equal(full.body.states.lr.volume, 55);
  });
});
