'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const {
  buildEnvelope,
  parseResponse,
  extractTag,
  xmlEscape,
  soapCall,
  SonosError
} = require('../src/sonos/soap');

test('xmlEscape escapes XML special characters', () => {
  assert.equal(xmlEscape('a & b < c > d "e" \'f\''), 'a &amp; b &lt; c &gt; d &quot;e&quot; &apos;f&apos;');
});

test('buildEnvelope produces a valid SOAP body with action and params', () => {
  const env = buildEnvelope('urn:test:service:1', 'DoThing', { Foo: 'bar', N: 42 });
  assert.match(env, /<u:DoThing xmlns:u="urn:test:service:1">/);
  assert.match(env, /<Foo>bar<\/Foo>/);
  assert.match(env, /<N>42<\/N>/);
  assert.match(env, /<\/s:Envelope>$/);
});

test('parseResponse extracts named fields from a success body', () => {
  const xml = '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">' +
    '<s:Body><u:GetVolumeResponse xmlns:u="urn:x"><CurrentVolume>42</CurrentVolume></u:GetVolumeResponse></s:Body></s:Envelope>';
  const r = parseResponse(xml, 'GetVolume');
  assert.equal(r.CurrentVolume, '42');
});

test('parseResponse throws SonosError on SOAP fault with UPnP error code', () => {
  const xml = '<s:Envelope><s:Body><s:Fault><faultcode>s:Client</faultcode>' +
    '<faultstring>UPnPError</faultstring><detail><UPnPError>' +
    '<errorCode>714</errorCode><errorDescription>Illegal MIME type</errorDescription>' +
    '</UPnPError></detail></s:Fault></s:Body></s:Envelope>';
  assert.throws(() => parseResponse(xml, 'SetAVTransportURI'), (err) => {
    return err instanceof SonosError && err.code === 'SOAP_FAULT' && String(err.faultCode) === '714';
  });
});

test('soapCall maps connection errors to PLAYER_UNREACHABLE', async () => {
  const requestFn = async () => {
    const e = new Error('connect ECONNREFUSED');
    e.code = 'ECONNREFUSED';
    throw e;
  };
  await assert.rejects(
    soapCall({ host: '127.0.0.1', service: 'AVTransport', action: 'Play', requestFn }),
    (err) => err instanceof SonosError && err.code === 'PLAYER_UNREACHABLE'
  );
});

test('extractTag returns null when tag is missing', () => {
  assert.equal(extractTag('<a>x</a>', 'b'), null);
});
