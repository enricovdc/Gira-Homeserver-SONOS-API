'use strict';

const dgram = require('dgram');

const SSDP_HOST = '239.255.255.250';
const SSDP_PORT = 1900;

// Multiple search targets so discovery still works against newer Sonos
// firmware generations which may change how the player advertises itself.
// `ssdp:all` is the catch-all; the others are device-specific.
const SEARCH_TARGETS = [
  'urn:schemas-upnp-org:device:ZonePlayer:1',
  'urn:smartspeaker-audio:service:SpeakerGroup:1',
  'ssdp:all'
];

function buildSearchMessage(mx, target) {
  return [
    'M-SEARCH * HTTP/1.1',
    `HOST: ${SSDP_HOST}:${SSDP_PORT}`,
    'MAN: "ssdp:discover"',
    `MX: ${mx}`,
    `ST: ${target}`,
    '',
    ''
  ].join('\r\n');
}

function looksLikeSonos(headers) {
  const server = (headers['SERVER'] || '').toLowerCase();
  const usn = (headers['USN'] || '').toLowerCase();
  return server.includes('sonos') || usn.includes('rincon') || usn.includes('zoneplayer');
}

function parseResponse(buf, rinfo) {
  const text = buf.toString('utf8');
  const lines = text.split(/\r?\n/);
  const headers = {};
  for (const line of lines.slice(1)) {
    const idx = line.indexOf(':');
    if (idx > 0) headers[line.slice(0, idx).trim().toUpperCase()] = line.slice(idx + 1).trim();
  }
  const location = headers['LOCATION'];
  const usn = headers['USN'] || '';
  if (!location) return null;
  if (!looksLikeSonos(headers)) return null;
  let host = rinfo.address;
  let port = 1400;
  try {
    const url = new URL(location);
    host = url.hostname;
    port = parseInt(url.port, 10) || 1400;
  } catch {
    /* keep rinfo defaults */
  }
  const uuidMatch = usn.match(/uuid:([A-Za-z0-9_-]+)/);
  return {
    host,
    port,
    uuid: uuidMatch ? uuidMatch[1] : null,
    location,
    server: headers['SERVER'] || null
  };
}

function discover({ timeoutMs = 4000, mx = 2 } = {}) {
  return new Promise((resolve) => {
    const found = new Map();
    const socket = dgram.createSocket({ type: 'udp4', reuseAddr: true });
    const finish = () => {
      try { socket.close(); } catch { /* ignore */ }
      resolve(Array.from(found.values()));
    };
    socket.on('error', () => finish());
    socket.on('message', (msg, rinfo) => {
      const player = parseResponse(msg, rinfo);
      if (player && !found.has(player.host)) {
        found.set(player.host, player);
      }
    });
    socket.bind(0, () => {
      for (const target of SEARCH_TARGETS) {
        const message = Buffer.from(buildSearchMessage(mx, target));
        socket.send(message, 0, message.length, SSDP_PORT, SSDP_HOST, () => { /* ignore per-message errors */ });
      }
    });
    setTimeout(finish, timeoutMs);
  });
}

module.exports = { discover, parseResponse, buildSearchMessage, SEARCH_TARGETS };
