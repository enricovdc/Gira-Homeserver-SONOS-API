'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { parseNotifyBody, EventManager } = require('../src/sonos/events');
const { StateStore } = require('../src/state');

const SAMPLE_NOTIFY = `<?xml version="1.0"?>
<e:propertyset xmlns:e="urn:schemas-upnp-org:event-1-0">
  <e:property>
    <LastChange>&lt;Event xmlns=&quot;urn:schemas-upnp-org:metadata-1-0/AVT/&quot;&gt;
      &lt;InstanceID val=&quot;0&quot;&gt;
        &lt;TransportState val=&quot;PLAYING&quot;/&gt;
        &lt;CurrentTrackURI val=&quot;x-rincon-mp3radio://stream&quot;/&gt;
        &lt;CurrentTrackMetaData val=&quot;&amp;lt;DIDL-Lite&amp;gt;&amp;lt;item&amp;gt;&amp;lt;dc:title&amp;gt;Hello&amp;lt;/dc:title&amp;gt;&amp;lt;/item&amp;gt;&amp;lt;/DIDL-Lite&amp;gt;&quot;/&gt;
      &lt;/InstanceID&gt;
    &lt;/Event&gt;</LastChange>
  </e:property>
</e:propertyset>`;

test('parseNotifyBody extracts TransportState', () => {
  const out = parseNotifyBody(SAMPLE_NOTIFY);
  assert.equal(out.state, 'PLAYING');
  assert.equal(out.trackUri, 'x-rincon-mp3radio://stream');
  assert.equal(out.title, 'Hello');
});

const VOLUME_NOTIFY = `<?xml version="1.0"?>
<e:propertyset xmlns:e="urn:schemas-upnp-org:event-1-0">
  <e:property>
    <LastChange>&lt;Event&gt;&lt;InstanceID val=&quot;0&quot;&gt;
      &lt;Volume channel=&quot;Master&quot; val=&quot;42&quot;/&gt;
      &lt;Mute channel=&quot;Master&quot; val=&quot;1&quot;/&gt;
    &lt;/InstanceID&gt;&lt;/Event&gt;</LastChange>
  </e:property>
</e:propertyset>`;

test('parseNotifyBody extracts Volume/Mute', () => {
  const out = parseNotifyBody(VOLUME_NOTIFY);
  assert.equal(out.volume, 42);
  assert.equal(out.mute, true);
});

test('parseNotifyBody on empty input returns {}', () => {
  assert.deepEqual(parseNotifyBody(''), {});
});

test('EventManager.handleNotify writes parsed values into state and triggers change', () => {
  const stateStore = new StateStore();
  const changes = [];
  stateStore.on('change', (e) => changes.push(e));
  const mgr = new EventManager({
    registry: { list: () => [], get: () => null, has: () => false },
    stateStore,
    callbackBaseUrl: 'http://x'
  });
  mgr.handleNotify('lr', 'AVTransport', SAMPLE_NOTIFY);
  const s = stateStore.get('lr');
  assert.equal(s.state, 'PLAYING');
  assert.equal(s.track.title, 'Hello');
  assert.equal(s.online, true);
  assert.equal(changes.length, 1);
});

test('EventManager.subscribePlayer issues SUBSCRIBE on both services', async () => {
  const calls = [];
  const httpFn = async (opts) => {
    calls.push({ method: opts.method, path: opts.path, headers: opts.headers });
    return { status: 200, headers: { sid: 'uuid:' + opts.path, timeout: 'Second-1800' }, body: '' };
  };
  const fakeClient = { host: '10.0.0.1', port: 1400, name: 'lr' };
  const registry = { list: () => [fakeClient], get: () => fakeClient, has: () => true };
  const mgr = new EventManager({ registry, stateStore: new StateStore(), callbackBaseUrl: 'http://b', httpFn });
  await mgr.subscribePlayer('lr');
  assert.equal(calls.length, 2);
  assert.equal(calls[0].method, 'SUBSCRIBE');
  assert.ok(calls[0].headers.CALLBACK.startsWith('<http://b/upnp/event/lr/'));
  await mgr.stop();
});
