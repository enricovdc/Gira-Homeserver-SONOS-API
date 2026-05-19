'use strict';

// Optional outbound webhook. When state changes (driven by UPnP events), POST
// a small JSON payload to a URL the HomeServer exposes. This is how the
// HomeServer learns about external state changes without polling.
//
// Configured via cfg.webhook.url. If empty, webhook push is disabled.

const http = require('http');
const https = require('https');
const { URL } = require('url');

function post(url, payload, { timeoutMs = 5000, authHeader, logger } = {}) {
  return new Promise((resolve) => {
    let parsed;
    try { parsed = new URL(url); } catch {
      if (logger) logger.warn('Webhook URL invalid', { url });
      return resolve({ ok: false, error: 'INVALID_URL' });
    }
    const body = Buffer.from(JSON.stringify(payload));
    const lib = parsed.protocol === 'https:' ? https : http;
    const headers = {
      'Content-Type': 'application/json',
      'Content-Length': body.length,
      'User-Agent': 'gira-homeserver-sonos-bridge'
    };
    if (authHeader) headers['Authorization'] = authHeader;
    const req = lib.request({
      protocol: parsed.protocol,
      host: parsed.hostname,
      port: parsed.port || (parsed.protocol === 'https:' ? 443 : 80),
      path: parsed.pathname + parsed.search,
      method: 'POST',
      headers
    }, (res) => {
      res.on('data', () => {});
      res.on('end', () => resolve({ ok: res.statusCode < 400, status: res.statusCode }));
    });
    req.on('error', (err) => {
      if (logger) logger.warn('Webhook delivery failed', { url, error: err.message });
      resolve({ ok: false, error: err.message });
    });
    req.setTimeout(timeoutMs, () => req.destroy(new Error('Webhook timeout')));
    req.write(body);
    req.end();
  });
}

class WebhookPublisher {
  constructor({ url, authHeader, logger }) {
    this.url = url || '';
    this.authHeader = authHeader || '';
    this.logger = logger || { warn() {}, debug() {} };
  }

  enabled() { return !!this.url; }

  setUrl(url) { this.url = url || ''; }
  setAuthHeader(h) { this.authHeader = h || ''; }

  async publish(event, payload) {
    if (!this.enabled()) return;
    await post(this.url, { event, payload, ts: new Date().toISOString() }, {
      authHeader: this.authHeader,
      logger: this.logger
    });
  }
}

module.exports = { WebhookPublisher, post };
