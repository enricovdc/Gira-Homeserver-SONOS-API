'use strict';

const { extractTag } = require('../../src/sonos/soap');

function soapResponse(action, fields = {}) {
  const inner = Object.entries(fields).map(([k, v]) => `<${k}>${v}</${k}>`).join('');
  return (
    '<?xml version="1.0"?>' +
    '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">' +
    '<s:Body>' +
    `<u:${action}Response xmlns:u="urn:schemas-upnp-org:service:Mock:1">${inner}</u:${action}Response>` +
    '</s:Body>' +
    '</s:Envelope>'
  );
}

function soapFault(faultCode, faultString) {
  return (
    '<?xml version="1.0"?>' +
    '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">' +
    '<s:Body><s:Fault>' +
    '<faultcode>s:Client</faultcode>' +
    '<faultstring>UPnPError</faultstring>' +
    '<detail><UPnPError xmlns="urn:schemas-upnp-org:control-1-0">' +
    `<errorCode>${faultCode}</errorCode>` +
    `<errorDescription>${faultString}</errorDescription>` +
    '</UPnPError></detail>' +
    '</s:Fault></s:Body></s:Envelope>'
  );
}

function createMockRequest(handlers) {
  const calls = [];
  async function requestFn(opts, body) {
    const soapAction = (opts.headers.SOAPACTION || '').replace(/"/g, '');
    const action = soapAction.split('#')[1] || 'unknown';
    calls.push({ action, opts, body });
    const handler = handlers[action];
    if (!handler) {
      return { status: 500, body: soapFault(401, `No handler for ${action}`), headers: {} };
    }
    const result = typeof handler === 'function' ? await handler({ body, action, opts }) : handler;
    if (result && result.fault) {
      return { status: 500, body: soapFault(result.fault.code, result.fault.message), headers: {} };
    }
    if (result && result.httpError) {
      const err = new Error(result.httpError.message || 'mock failure');
      err.code = result.httpError.code;
      throw err;
    }
    return { status: 200, body: soapResponse(action, (result && result.fields) || {}), headers: {} };
  }
  requestFn.calls = calls;
  return requestFn;
}

module.exports = { createMockRequest, soapResponse, soapFault, extractTag };
