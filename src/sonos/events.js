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
const { extractTag, xmlUnescape, SonosError } = require('./soap');

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
