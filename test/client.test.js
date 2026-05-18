'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { SonosClient, normalizeRadioUri, buildDirectStreamMetadata } = require('../src/sonos/client');
const { SonosError } = require('../src/sonos/soap');
const { createMockRequest } = require('./helpers/mock-sonos');

function makeClient(handlers, extra = {}) {
  const requestFn = createMockRequest(handlers);
  const client = new SonosClient({ host: '127.0.0.1', name: 'test', requestFn, ...extra });
  return { client, requestFn };
}

test('SonosClient.play issues AVTransport#Play', async () => {
  const { client, requestFn } = makeClient({ Play: { fields: {} } });
  const res = await client.play();
  assert.deepEqual(res, { ok: true });
  assert.equal(requestFn.calls[0].action, 'Play');
  assert.match(requestFn.calls[0].body, /<Speed>1<\/Speed>/);
});

test('SonosClient.setVolume clamps to 0..100 and rounds', async () => {
  const { client, requestFn } = makeClient({ SetVolume: { fields: {} } });
  const a = await client.setVolume(150);
  assert.equal(a.volume, 100);
  await client.setVolume(-3);
  assert.match(requestFn.calls[1].body, /<DesiredVolume>0<\/DesiredVolume>/);
  await client.setVolume(33.7);
  assert.match(requestFn.calls[2].body, /<DesiredVolume>34<\/DesiredVolume>/);
});

test('SonosClient.setVolume rejects non-numeric', async () => {
  const { client } = makeClient({});
  await assert.rejects(client.setVolume('abc'), (e) => e instanceof SonosError && e.code === 'INVALID_ARG');
});

test('SonosClient.adjustVolume reads then writes', async () => {
  const { client, requestFn } = makeClient({
    GetVolume: { fields: { CurrentVolume: '30' } },
    SetVolume: { fields: {} }
  });
  const r = await client.adjustVolume(5);
  assert.equal(r.volume, 35);
  assert.equal(requestFn.calls[0].action, 'GetVolume');
  assert.equal(requestFn.calls[1].action, 'SetVolume');
});

test('SonosClient.setMute sends 1/0', async () => {
  const { client, requestFn } = makeClient({ SetMute: { fields: {} } });
  await client.setMute(true);
  assert.match(requestFn.calls[0].body, /<DesiredMute>1<\/DesiredMute>/);
  await client.setMute(false);
  assert.match(requestFn.calls[1].body, /<DesiredMute>0<\/DesiredMute>/);
});

test('normalizeRadioUri converts http(s) to x-rincon-mp3radio', () => {
  assert.equal(normalizeRadioUri('http://stream.example.com/r1.mp3'), 'x-rincon-mp3radio://stream.example.com/r1.mp3');
  assert.equal(normalizeRadioUri('https://stream.example.com/r1.mp3'), 'x-rincon-mp3radio://stream.example.com/r1.mp3');
  assert.equal(normalizeRadioUri('x-rincon-stream://uuid'), 'x-rincon-stream://uuid');
});

test('buildDirectStreamMetadata contains no SMAPI cloud-service binding', () => {
  const meta = buildDirectStreamMetadata({ title: 'Foo' });
  assert.match(meta, /<dc:title>Foo<\/dc:title>/);
  assert.match(meta, /audioBroadcast/);
  // Critical for 2024+ firmware: no <desc> with SMAPI cdudn handoff
  assert.doesNotMatch(meta, /SA_RINCON/);
  assert.doesNotMatch(meta, /<desc/);
});

test('playStreamUri sends rich metadata on first attempt when player accepts it', async () => {
  const { client, requestFn } = makeClient({
    SetAVTransportURI: { fields: {} },
    Play: { fields: {} }
  });
  const r = await client.playStreamUri('http://example.com/r.mp3', { title: 'My Radio' });
  assert.equal(r.ok, true);
  assert.equal(r.strategy, 'direct+metadata');
  const setCall = requestFn.calls.find(c => c.action === 'SetAVTransportURI');
  assert.match(setCall.body, /x-rincon-mp3radio:\/\/example\.com\/r\.mp3/);
  assert.match(setCall.body, /My Radio/);
});

test('playStreamUri falls back to empty metadata on UPnP 714 (Illegal MIME)', async () => {
  let setCalls = 0;
  const { client } = makeClient({
    SetAVTransportURI: () => {
      setCalls++;
      if (setCalls === 1) return { fault: { code: 714, message: 'Illegal MIME' } };
      return { fields: {} };
    },
    Play: { fields: {} }
  });
  const r = await client.playStreamUri('http://example.com/r.mp3', { title: 'My Radio' });
  assert.equal(r.strategy, 'direct+empty-meta');
  assert.equal(setCalls, 2);
});

test('playStreamUri does NOT retry on non-metadata errors', async () => {
  const { client } = makeClient({
    SetAVTransportURI: { fault: { code: 701, message: 'Transition not available' } }
  });
  await assert.rejects(
    client.playStreamUri('http://example.com/r.mp3'),
    (err) => err instanceof SonosError && String(err.faultCode) === '701'
  );
});

test('getStatus aggregates transport, position, media, volume, mute', async () => {
  const { client } = makeClient({
    GetTransportInfo: { fields: { CurrentTransportState: 'PLAYING', CurrentTransportStatus: 'OK', CurrentSpeed: '1' } },
    GetPositionInfo: { fields: { Track: '1', TrackDuration: '0:03:21', TrackURI: 'x-rincon-mp3radio://s', RelTime: '0:00:42', TrackMetaData: '' } },
    GetMediaInfo: { fields: { CurrentURI: 'x-rincon-mp3radio://s', CurrentURIMetaData: '' } },
    GetVolume: { fields: { CurrentVolume: '42' } },
    GetMute: { fields: { CurrentMute: '0' } }
  });
  const s = await client.getStatus();
  assert.equal(s.state, 'PLAYING');
  assert.equal(s.volume, 42);
  assert.equal(s.mute, false);
  assert.equal(s.currentUri, 'x-rincon-mp3radio://s');
});

test('ping returns false when player is unreachable', async () => {
  const { client } = makeClient({
    GetTransportInfo: () => ({ httpError: { message: 'refused', code: 'ECONNREFUSED' } })
  });
  const ok = await client.ping();
  assert.equal(ok, false);
});
