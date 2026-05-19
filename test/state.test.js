'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { StateStore } = require('../src/state');

test('StateStore.set merges and emits a change event', () => {
  const s = new StateStore();
  const events = [];
  s.on('change', (e) => events.push(e));
  s.set('lr', { volume: 10 });
  s.set('lr', { state: 'PLAYING' });
  assert.equal(events.length, 2);
  const final = s.get('lr');
  assert.equal(final.volume, 10);
  assert.equal(final.state, 'PLAYING');
  assert.ok(final.updatedAt);
});

test('StateStore.setActiveStation updates state too', () => {
  const s = new StateStore();
  s.setActiveStation('lr', { index: 3, name: 'Jazz' });
  assert.deepEqual(s.getActiveStation('lr'), { index: 3, name: 'Jazz' });
  assert.deepEqual(s.get('lr').activeStation, { index: 3, name: 'Jazz' });
});

test('StateStore.hasRecentEvent reflects markEventReceived', () => {
  const s = new StateStore();
  assert.equal(s.hasRecentEvent('lr'), false);
  s.markEventReceived('lr');
  assert.equal(s.hasRecentEvent('lr', 60000), true);
  assert.equal(s.hasRecentEvent('lr', 0), false);
});
