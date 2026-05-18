'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { loadFromObject, ConfigError, DEFAULTS } = require('../src/config');

test('loadFromObject merges defaults', () => {
  const cfg = loadFromObject({
    players: [{ name: 'lr', host: '192.168.1.10' }]
  });
  assert.equal(cfg.server.port, DEFAULTS.server.port);
  assert.equal(cfg.discovery.enabled, true);
  assert.equal(cfg.players[0].name, 'lr');
});

test('loadFromObject rejects duplicate player names', () => {
  assert.throws(() => loadFromObject({
    players: [
      { name: 'a', host: '1.1.1.1' },
      { name: 'a', host: '1.1.1.2' }
    ]
  }), (err) => err instanceof ConfigError && err.details.errors.some(e => /duplicated/.test(e)));
});

test('loadFromObject rejects defaultPlayer not in players', () => {
  assert.throws(() => loadFromObject({
    players: [{ name: 'a', host: '1.1.1.1' }],
    defaultPlayer: 'nope'
  }), (err) => err instanceof ConfigError);
});

test('loadFromObject rejects duplicate station indices', () => {
  assert.throws(() => loadFromObject({
    players: [{ name: 'a', host: '1.1.1.1' }],
    radioStations: [
      { index: 1, name: 'A', streamUri: 'http://a' },
      { index: 1, name: 'B', streamUri: 'http://b' }
    ]
  }), (err) => err instanceof ConfigError);
});

test('loadFromObject accepts valid config', () => {
  const cfg = loadFromObject({
    server: { port: 9000 },
    players: [{ name: 'a', host: '1.1.1.1' }],
    defaultPlayer: 'a',
    radioStations: [{ index: 1, name: 'A', streamUri: 'http://a' }]
  });
  assert.equal(cfg.server.port, 9000);
  assert.equal(cfg.defaultPlayer, 'a');
});
