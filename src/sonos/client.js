'use strict';

const { soapCall, extractTag, xmlEscape, SonosError } = require('./soap');

const DEFAULT_PORT = 1400;
const INSTANCE = 0;

// Minimal DIDL-Lite without any SMAPI cloud-service binding (no <desc> cdudn tag).
// This is the most resilient form for direct-stream radio playback on 2024+ Sonos
// firmware where SMAPI/TuneIn cloud service IDs may be gated or rejected. The player
// connects to the stream URL directly without any cloud handoff.
function buildDirectStreamMetadata({ title }) {
  const id = 'R:0/0/' + Math.random().toString(36).slice(2, 10);
  const safeTitle = xmlEscape(title || 'Radio');
  return (
    '<DIDL-Lite xmlns:dc="http://purl.org/dc/elements/1.1/" ' +
    'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/" ' +
    'xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/">' +
    `<item id="${id}" parentID="-1" restricted="true">` +
    `<dc:title>${safeTitle}</dc:title>` +
    '<upnp:class>object.item.audioItem.audioBroadcast</upnp:class>' +
    '</item>' +
    '</DIDL-Lite>'
  );
}

function normalizeRadioUri(uri) {
  if (!uri) return uri;
  // Use Sonos's preferred direct-broadcast scheme so the player treats it as
  // a continuous broadcast stream rather than a one-shot file. Works on all
  // firmware generations.
  if (uri.startsWith('http://') || uri.startsWith('https://')) {
    return 'x-rincon-mp3radio://' + uri.replace(/^https?:\/\//, '');
  }
  return uri;
}

function parseTrackMeta(xmlString) {
  if (!xmlString) return {};
  return {
    title: extractTag(xmlString, 'dc:title'),
    artist: extractTag(xmlString, 'dc:creator'),
    album: extractTag(xmlString, 'upnp:album'),
    streamContent: extractTag(xmlString, 'r:streamContent'),
    albumArtUri: extractTag(xmlString, 'upnp:albumArtURI')
  };
}

// UPnP error codes that indicate the player rejected our metadata format.
// Retrying with empty metadata is the documented workaround.
const METADATA_REJECTED_CODES = new Set(['714', '716', '402', '501', '800']);

class SonosClient {
  constructor({ host, port = DEFAULT_PORT, name, uuid, timeoutMs = 5000, requestFn } = {}) {
    if (!host) throw new SonosError('SonosClient requires a host', { code: 'INVALID_CONFIG' });
    this.host = host;
    this.port = port;
    this.name = name || host;
    this.uuid = uuid || null;
    this.timeoutMs = timeoutMs;
    this.requestFn = requestFn;
  }

  call(service, action, params) {
    return soapCall({
      host: this.host,
      port: this.port,
      service,
      action,
      params: Object.assign({ InstanceID: INSTANCE }, params || {}),
      timeoutMs: this.timeoutMs,
      requestFn: this.requestFn
    });
  }

  async play() { await this.call('AVTransport', 'Play', { Speed: '1' }); return { ok: true }; }
  async pause() { await this.call('AVTransport', 'Pause'); return { ok: true }; }
  async stop() { await this.call('AVTransport', 'Stop'); return { ok: true }; }
  async next() { await this.call('AVTransport', 'Next'); return { ok: true }; }
  async previous() { await this.call('AVTransport', 'Previous'); return { ok: true }; }

  async setVolume(level) {
    const n = Number(level);
    if (!Number.isFinite(n)) throw new SonosError('Volume must be a number', { code: 'INVALID_ARG' });
    const v = Math.max(0, Math.min(100, Math.round(n)));
    await this.call('RenderingControl', 'SetVolume', { Channel: 'Master', DesiredVolume: v });
    return { ok: true, volume: v };
  }

  async getVolume() {
    const res = await this.call('RenderingControl', 'GetVolume', { Channel: 'Master' });
    return parseInt(res.CurrentVolume, 10);
  }

  async adjustVolume(delta) {
    const current = await this.getVolume();
    const next = Math.max(0, Math.min(100, current + Number(delta)));
    return this.setVolume(next);
  }

  async setMute(mute) {
    await this.call('RenderingControl', 'SetMute', {
      Channel: 'Master',
      DesiredMute: mute ? '1' : '0'
    });
    return { ok: true, mute: !!mute };
  }

  async getMute() {
    const res = await this.call('RenderingControl', 'GetMute', { Channel: 'Master' });
    return res.CurrentMute === '1';
  }

  async getTransportInfo() {
    const res = await this.call('AVTransport', 'GetTransportInfo');
    return {
      state: res.CurrentTransportState || 'UNKNOWN',
      status: res.CurrentTransportStatus || 'UNKNOWN',
      speed: res.CurrentSpeed || '1'
    };
  }

  async getPositionInfo() {
    const res = await this.call('AVTransport', 'GetPositionInfo');
    const meta = parseTrackMeta(res.TrackMetaData);
    return {
      track: parseInt(res.Track, 10) || 0,
      trackDuration: res.TrackDuration || '0:00:00',
      trackUri: res.TrackURI || '',
      relTime: res.RelTime || '0:00:00',
      metadata: meta
    };
  }

  async getMediaInfo() {
    const res = await this.call('AVTransport', 'GetMediaInfo');
    return {
      currentUri: res.CurrentURI || '',
      currentUriMetadata: res.CurrentURIMetaData || ''
    };
  }

  // Retrieve zone/device info including firmware version. Useful for diagnostic
  // output and confirming compatibility with newer Sonos firmware generations.
  async getZoneInfo() {
    try {
      const res = await soapCall({
        host: this.host,
        port: this.port,
        service: 'ZoneGroupTopology',
        action: 'GetZoneGroupAttributes',
        params: {},
        timeoutMs: this.timeoutMs,
        requestFn: this.requestFn
      });
      return {
        groupName: res.CurrentZoneGroupName || null,
        groupId: res.CurrentZoneGroupID || null
      };
    } catch (err) {
      if (err instanceof SonosError && err.code === 'PLAYER_UNREACHABLE') throw err;
      return { groupName: null, groupId: null, error: err.message };
    }
  }

  // Sets the transport URI and starts playback. Implements a metadata-fallback
  // strategy required by Sonos firmware updates from 2024 onward, which more
  // strictly validate DIDL-Lite metadata: if the player rejects the rich
  // metadata, retry with empty metadata and the direct-broadcast URI scheme.
  async playStreamUri(uri, { title, metadata } = {}) {
    const normalizedUri = normalizeRadioUri(uri);
    const richMeta = metadata !== undefined ? metadata : buildDirectStreamMetadata({ title });

    const attempts = [
      { uri: normalizedUri, meta: richMeta, label: 'direct+metadata' },
      { uri: normalizedUri, meta: '', label: 'direct+empty-meta' },
      { uri: uri, meta: '', label: 'raw+empty-meta' }
    ];

    let lastErr = null;
    for (const attempt of attempts) {
      try {
        await this.call('AVTransport', 'SetAVTransportURI', {
          CurrentURI: attempt.uri,
          CurrentURIMetaData: attempt.meta
        });
        await this.play();
        return { ok: true, uri: attempt.uri, strategy: attempt.label };
      } catch (err) {
        lastErr = err;
        const rejected = err instanceof SonosError &&
          err.code === 'SOAP_FAULT' &&
          METADATA_REJECTED_CODES.has(String(err.faultCode));
        if (!rejected) throw err;
      }
    }
    throw lastErr || new SonosError('Failed to start stream', { code: 'STREAM_FAILED' });
  }

  async getStatus() {
    const [transport, position, media, volume, mute] = await Promise.all([
      this.getTransportInfo(),
      this.getPositionInfo(),
      this.getMediaInfo(),
      this.getVolume(),
      this.getMute()
    ]);
    return {
      online: true,
      state: transport.state,
      volume,
      mute,
      track: {
        title: position.metadata.title || null,
        artist: position.metadata.artist || null,
        album: position.metadata.album || null,
        streamContent: position.metadata.streamContent || null,
        uri: position.trackUri,
        duration: position.trackDuration,
        position: position.relTime
      },
      currentUri: media.currentUri
    };
  }

  async ping() {
    try {
      await this.getTransportInfo();
      return true;
    } catch {
      return false;
    }
  }
}

module.exports = {
  SonosClient,
  buildDirectStreamMetadata,
  normalizeRadioUri,
  parseTrackMeta,
  METADATA_REJECTED_CODES
};
