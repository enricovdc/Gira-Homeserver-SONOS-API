'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const { loadFromObject } = require('../src/config');
const { startServer } = require('../src/server');
const { createMockRequest } = require('./helpers/mock-sonos');

function get(port, path, { method = 'GET', body, headers = {} } = {}) {
  return new Promise((resolve, reject) => {
    const data = body ? JSON.stringify(body) : null;
    const req = http.request({
      host: '127.0.0.1',
      port,
      path,
      method,
      headers: {
        'Content-Type': 'application/json',
        ...(data ? { 'Content-Length': Buffer.byteLength(data) } : {}),
        ...headers
      }
    }, (res) => {
      let buf = '';
      res.on('data', (c) => { buf += c; });
      res.on('end', () => {
        try {
          resolve({ status: res.statusCode, body: buf ? JSON.parse(buf) : null });
        } catch (e) { reject(e); }
      });
    });
    req.on('error', reject);
    if (data) req.write(data);
    req.end();
  });
}

function makeCfg(overrides = {}) {
  return loadFromObject({
    server: { host: '127.0.0.1', port: 0, authToken: '' },
    players: [
      { name: 'livingroom', host: '10.0.0.1' },
      { name: 'kitchen', host: '10.0.0.2' }
    ],
    defaultPlayer: 'livingroom',
    radioStations: [
      { index: 1, name: 'R1', streamUri: 'http://r1' },
      { index: 2, name: 'R2', streamUri: 'http://r2' }
    ],
    eventing: { enabled: false },
    ...overrides
  });
}

async function withServer(opts, fn) {
  const cfg = makeCfg(opts.config);
  const { SonosClient } = require('../src/sonos/client');
  const requestFn = createMockRequest(opts.handlers || {});
  const clientFactory = (cfgIn) => new SonosClient({ ...cfgIn, requestFn });
  const { server } = await startServer(cfg, { clientFactory, disableEventing: true });
  const port = server.address().port;
  try {
    await fn(port, requestFn);
  } finally {
    await new Promise((r) => server.close(r));
  }
}

test('GET /health returns ok', async () => {
  await withServer({}, async (port) => {
    const r = await get(port, '/health');
    assert.equal(r.status, 200);
    assert.equal(r.body.ok, true);
    assert.equal(r.body.players, 2);
  });
});

test('GET /players returns list', async () => {
  await withServer({}, async (port) => {
    const r = await get(port, '/players');
    assert.equal(r.status, 200);
    assert.equal(r.body.players.length, 2);
    assert.equal(r.body.defaultPlayer, 'livingroom');
  });
});

test('GET /stations returns sorted stations', async () => {
  await withServer({}, async (port) => {
    const r = await get(port, '/stations');
    assert.deepEqual(r.body.stations.map(s => s.index), [1, 2]);
  });
});

test('POST /players/livingroom/play triggers Play action', async () => {
  await withServer({
    handlers: { Play: { fields: {} } }
  }, async (port, requestFn) => {
    const r = await get(port, '/players/livingroom/play', { method: 'POST' });
    assert.equal(r.status, 200);
    assert.equal(r.body.ok, true);
    assert.ok(requestFn.calls.some(c => c.action === 'Play'));
  });
});

test('POST /players/livingroom/volume sets volume', async () => {
  await withServer({
    handlers: { SetVolume: { fields: {} } }
  }, async (port, requestFn) => {
    const r = await get(port, '/players/livingroom/volume', { method: 'POST', body: { level: 50 } });
    assert.equal(r.status, 200);
    assert.equal(r.body.volume, 50);
    assert.match(requestFn.calls[0].body, /<DesiredVolume>50<\/DesiredVolume>/);
  });
});

test('POST /players/livingroom/volume/up adjusts volume', async () => {
  await withServer({
    handlers: {
      GetVolume: { fields: { CurrentVolume: '10' } },
      SetVolume: { fields: {} }
    }
  }, async (port) => {
    const r = await get(port, '/players/livingroom/volume/up?step=5', { method: 'POST' });
    assert.equal(r.status, 200);
    assert.equal(r.body.volume, 15);
  });
});

test('POST /players/livingroom/radio/start by index selects station', async () => {
  await withServer({
    handlers: {
      SetAVTransportURI: { fields: {} },
      Play: { fields: {} }
    }
  }, async (port, requestFn) => {
    const r = await get(port, '/players/livingroom/radio/start', { method: 'POST', body: { index: 2 } });
    assert.equal(r.status, 200);
    assert.equal(r.body.station.index, 2);
    const setCall = requestFn.calls.find(c => c.action === 'SetAVTransportURI');
    assert.match(setCall.body, /r2/);
  });
});

test('POST /players/livingroom/radio/start by name selects station', async () => {
  await withServer({
    handlers: {
      SetAVTransportURI: { fields: {} },
      Play: { fields: {} }
    }
  }, async (port) => {
    const r = await get(port, '/players/livingroom/radio/start', { method: 'POST', body: { name: 'R1' } });
    assert.equal(r.body.station.name, 'R1');
  });
});

test('radio start with unknown index returns 404', async () => {
  await withServer({}, async (port) => {
    const r = await get(port, '/players/livingroom/radio/start', { method: 'POST', body: { index: 99 } });
    assert.equal(r.status, 404);
    assert.equal(r.body.error, 'STATION_NOT_FOUND');
  });
});

test('unknown player returns 404', async () => {
  await withServer({}, async (port) => {
    const r = await get(port, '/players/bedroom/play', { method: 'POST' });
    assert.equal(r.status, 404);
    assert.equal(r.body.error, 'PLAYER_NOT_FOUND');
  });
});

test('GET /players/livingroom/status returns aggregated status', async () => {
  await withServer({
    handlers: {
      GetTransportInfo: { fields: { CurrentTransportState: 'PLAYING', CurrentTransportStatus: 'OK', CurrentSpeed: '1' } },
      GetPositionInfo: { fields: { Track: '1', TrackDuration: '0:01:00', TrackURI: 'x-rincon-mp3radio://x', RelTime: '0:00:10', TrackMetaData: '' } },
      GetMediaInfo: { fields: { CurrentURI: 'x-rincon-mp3radio://x', CurrentURIMetaData: '' } },
      GetVolume: { fields: { CurrentVolume: '20' } },
      GetMute: { fields: { CurrentMute: '0' } }
    }
  }, async (port) => {
    const r = await get(port, '/players/livingroom/status');
    assert.equal(r.status, 200);
    assert.equal(r.body.state, 'PLAYING');
    assert.equal(r.body.volume, 20);
    assert.equal(r.body.online, true);
  });
});

test('offline player returns 503 on direct action', async () => {
  await withServer({
    handlers: {
      Play: () => ({ httpError: { message: 'refused', code: 'ECONNREFUSED' } })
    }
  }, async (port) => {
    const r = await get(port, '/players/livingroom/play', { method: 'POST' });
    assert.equal(r.status, 503);
    assert.equal(r.body.error, 'PLAYER_UNREACHABLE');
  });
});

test('offline player on /status returns 200 with online:false', async () => {
  await withServer({
    handlers: {
      GetTransportInfo: () => ({ httpError: { message: 'refused', code: 'ECONNREFUSED' } }),
      GetPositionInfo: () => ({ httpError: { message: 'refused', code: 'ECONNREFUSED' } }),
      GetMediaInfo: () => ({ httpError: { message: 'refused', code: 'ECONNREFUSED' } }),
      GetVolume: () => ({ httpError: { message: 'refused', code: 'ECONNREFUSED' } }),
      GetMute: () => ({ httpError: { message: 'refused', code: 'ECONNREFUSED' } })
    }
  }, async (port) => {
    const r = await get(port, '/status?player=livingroom');
    assert.equal(r.status, 200);
    assert.equal(r.body.online, false);
  });
});

test('authToken rejects unauthenticated requests', async () => {
  await withServer({
    config: { server: { host: '127.0.0.1', port: 0, authToken: 'sekret' } }
  }, async (port) => {
    const r = await get(port, '/health');
    assert.equal(r.status, 401);
    const ok = await get(port, '/health', { headers: { Authorization: 'Bearer sekret' } });
    assert.equal(ok.status, 200);
  });
});

test('POST /players/livingroom/mute/toggle inverts current mute', async () => {
  let muteState = false;
  await withServer({
    handlers: {
      GetMute: () => ({ fields: { CurrentMute: muteState ? '1' : '0' } }),
      SetMute: ({ body }) => {
        muteState = /<DesiredMute>1<\/DesiredMute>/.test(body);
        return { fields: {} };
      }
    }
  }, async (port) => {
    const r1 = await get(port, '/players/livingroom/mute/toggle', { method: 'POST' });
    assert.equal(r1.body.mute, true);
    const r2 = await get(port, '/players/livingroom/mute/toggle', { method: 'POST' });
    assert.equal(r2.body.mute, false);
  });
});
