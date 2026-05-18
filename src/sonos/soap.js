'use strict';

const http = require('http');

class SonosError extends Error {
  constructor(message, { code, status, faultCode, faultString, cause } = {}) {
    super(message);
    this.name = 'SonosError';
    this.code = code || 'SONOS_ERROR';
    this.status = status;
    this.faultCode = faultCode;
    this.faultString = faultString;
    if (cause) this.cause = cause;
  }
}

const SERVICES = {
  AVTransport: {
    controlUrl: '/MediaRenderer/AVTransport/Control',
    type: 'urn:schemas-upnp-org:service:AVTransport:1'
  },
  RenderingControl: {
    controlUrl: '/MediaRenderer/RenderingControl/Control',
    type: 'urn:schemas-upnp-org:service:RenderingControl:1'
  },
  ContentDirectory: {
    controlUrl: '/MediaServer/ContentDirectory/Control',
    type: 'urn:schemas-upnp-org:service:ContentDirectory:1'
  },
  ZoneGroupTopology: {
    controlUrl: '/ZoneGroupTopology/Control',
    type: 'urn:schemas-upnp-org:service:ZoneGroupTopology:1'
  }
};

function xmlEscape(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&apos;');
}

function xmlUnescape(s) {
  return String(s)
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&apos;/g, "'")
    .replace(/&amp;/g, '&');
}

function buildEnvelope(serviceType, action, params) {
  const body = Object.entries(params || {})
    .map(([k, v]) => `<${k}>${v == null ? '' : xmlEscape(v)}</${k}>`)
    .join('');
  return (
    '<?xml version="1.0" encoding="utf-8"?>' +
    '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" ' +
    's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">' +
    '<s:Body>' +
    `<u:${action} xmlns:u="${serviceType}">${body}</u:${action}>` +
    '</s:Body>' +
    '</s:Envelope>'
  );
}

function extractTag(xml, tag) {
  const re = new RegExp(`<${tag}[^>]*>([\\s\\S]*?)</${tag}>`);
  const m = xml.match(re);
  return m ? xmlUnescape(m[1]) : null;
}

function parseResponse(xml, action) {
  const fault = extractTag(xml, 's:Fault') || extractTag(xml, 'SOAP-ENV:Fault');
  if (fault) {
    const faultCode = extractTag(fault, 'faultcode') || extractTag(fault, 'errorCode');
    const faultString = extractTag(fault, 'faultstring') || extractTag(fault, 'errorDescription');
    const upnpErrorCode = extractTag(fault, 'errorCode');
    throw new SonosError(`SOAP fault: ${faultString || 'unknown'}`, {
      code: 'SOAP_FAULT',
      faultCode: upnpErrorCode || faultCode,
      faultString
    });
  }
  const bodyMatch = xml.match(/<u:[^>]*Response[^>]*>([\s\S]*?)<\/u:[^>]*Response>/);
  if (!bodyMatch) return {};
  const inner = bodyMatch[1];
  const result = {};
  const tagRegex = /<([A-Za-z][A-Za-z0-9_]*)[^>]*>([\s\S]*?)<\/\1>/g;
  let m;
  while ((m = tagRegex.exec(inner)) !== null) {
    result[m[1]] = xmlUnescape(m[2]);
  }
  return result;
}

function httpRequest(opts, body, { timeoutMs = 5000 } = {}) {
  return new Promise((resolve, reject) => {
    const req = http.request(opts, (res) => {
      let data = '';
      res.setEncoding('utf8');
      res.on('data', (chunk) => { data += chunk; });
      res.on('end', () => resolve({ status: res.statusCode, body: data, headers: res.headers }));
    });
    req.on('error', (err) => reject(err));
    req.setTimeout(timeoutMs, () => {
      req.destroy(new Error(`Request timed out after ${timeoutMs}ms`));
    });
    if (body) req.write(body);
    req.end();
  });
}

async function soapCall({ host, port = 1400, service, action, params, timeoutMs, requestFn }) {
  const svc = SERVICES[service];
  if (!svc) throw new SonosError(`Unknown service: ${service}`, { code: 'UNKNOWN_SERVICE' });
  const envelope = buildEnvelope(svc.type, action, params);
  const opts = {
    host,
    port,
    method: 'POST',
    path: svc.controlUrl,
    headers: {
      'Content-Type': 'text/xml; charset="utf-8"',
      'Content-Length': Buffer.byteLength(envelope),
      SOAPACTION: `"${svc.type}#${action}"`
    }
  };
  const doRequest = requestFn || httpRequest;
  let res;
  try {
    res = await doRequest(opts, envelope, { timeoutMs });
  } catch (err) {
    throw new SonosError(`HTTP error: ${err.message}`, {
      code: err.code === 'ECONNREFUSED' || err.code === 'EHOSTUNREACH' || err.code === 'ENETUNREACH'
        ? 'PLAYER_UNREACHABLE'
        : 'HTTP_ERROR',
      cause: err
    });
  }
  if (res.status >= 200 && res.status < 300) {
    return parseResponse(res.body, action);
  }
  if (res.status >= 400 && res.status < 600 && res.body) {
    try {
      return parseResponse(res.body, action);
    } catch (parseErr) {
      if (parseErr instanceof SonosError) {
        parseErr.status = res.status;
        throw parseErr;
      }
    }
  }
  throw new SonosError(`Unexpected HTTP status: ${res.status}`, {
    code: 'HTTP_ERROR',
    status: res.status
  });
}

module.exports = {
  soapCall,
  buildEnvelope,
  parseResponse,
  extractTag,
  xmlEscape,
  xmlUnescape,
  SERVICES,
  SonosError,
  httpRequest
};
