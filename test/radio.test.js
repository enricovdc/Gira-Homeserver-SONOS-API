'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { RadioStationStore, RadioError } = require('../src/radio');

const STATIONS = [
  { index: 1, name: 'Radio One', streamUri: 'http://a/1.mp3' },
  { index: 2, name: 'Radio Two', streamUri: 'http://a/2.mp3' },
  { index: 5, name: 'Jazz FM', streamUri: 'http://a/jazz.mp3' }
];

test('byIndexLookup finds by integer or numeric string', () => {
  const s = new RadioStationStore(STATIONS);
  assert.equal(s.byIndexLookup(2).name, 'Radio Two');
  assert.equal(s.byIndexLookup('5').name, 'Jazz FM');
});

test('byName is case-insensitive', () => {
  const s = new RadioStationStore(STATIONS);
  assert.equal(s.byName('jazz fm').index, 5);
  assert.equal(s.byName('RADIO ONE').index, 1);
});

test('list returns sorted by index', () => {
  const s = new RadioStationStore([STATIONS[2], STATIONS[0], STATIONS[1]]);
  assert.deepEqual(s.list().map(x => x.index), [1, 2, 5]);
});

test('unknown station throws STATION_NOT_FOUND', () => {
  const s = new RadioStationStore(STATIONS);
  assert.throws(() => s.byIndexLookup(99), (e) => e instanceof RadioError && e.code === 'STATION_NOT_FOUND');
  assert.throws(() => s.byName('nope'), (e) => e instanceof RadioError && e.code === 'STATION_NOT_FOUND');
});

test('invalid station rejected on construction', () => {
  assert.throws(() => new RadioStationStore([{ name: 'x' }]), (e) => e.code === 'INVALID_STATION');
});
