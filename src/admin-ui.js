'use strict';

// Single-file vanilla HTML/JS admin UI. Served at "/" so that opening the
// bridge's URL in a browser yields the configuration page directly. Embedded
// as a string to avoid file IO on every request and to keep the bridge
// dependency-free.

function adminHtml({ bridgeUrl, version }) {
  return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Gira HomeServer Sonos Bridge — Configuration</title>
<style>
  :root { font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif; }
  body { margin: 0; background: #f5f6f8; color: #1a1f2c; }
  header { background: #1f6feb; color: #fff; padding: 1rem 1.5rem; }
  header h1 { margin: 0; font-size: 1.2rem; font-weight: 600; }
  header .url { font-size: 0.85rem; opacity: 0.9; font-family: ui-monospace, monospace; }
  main { max-width: 980px; margin: 1.25rem auto; padding: 0 1rem; }
  section { background: #fff; border: 1px solid #d7dce3; border-radius: 8px; margin-bottom: 1rem; padding: 1rem 1.25rem; }
  section h2 { margin: 0 0 0.5rem; font-size: 1rem; }
  table { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
  th, td { text-align: left; padding: 0.4rem 0.5rem; border-bottom: 1px solid #eef0f3; vertical-align: middle; }
  th { background: #f7f8fa; font-weight: 600; }
  input[type=text], input[type=number], input[type=url] {
    width: 100%; padding: 0.35rem 0.5rem; border: 1px solid #cdd3dc; border-radius: 4px; font: inherit;
  }
  button { font: inherit; padding: 0.4rem 0.75rem; background: #1f6feb; color: #fff;
           border: 0; border-radius: 4px; cursor: pointer; }
  button.secondary { background: #6b7280; }
  button.danger    { background: #c0392b; }
  button:disabled  { opacity: 0.5; cursor: not-allowed; }
  .row { display: flex; gap: 0.5rem; align-items: center; flex-wrap: wrap; }
  .pill { display: inline-block; padding: 2px 8px; border-radius: 99px; font-size: 0.75rem; font-weight: 600; }
  .pill.online  { background: #def7ec; color: #03543e; }
  .pill.offline { background: #fde8e8; color: #9b1c1c; }
  .pill.playing { background: #e1effe; color: #1e429f; }
  .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 0.5rem 1rem; }
  .grid label { font-size: 0.85rem; color: #4a5366; }
  .toast { position: fixed; right: 1rem; bottom: 1rem; background: #1f6feb; color: #fff;
           padding: 0.6rem 1rem; border-radius: 6px; box-shadow: 0 2px 6px rgba(0,0,0,0.2); display: none; }
  .toast.error { background: #c0392b; }
  .muted { color: #6b7280; font-size: 0.85rem; }
  details { margin-top: 0.5rem; }
  summary { cursor: pointer; color: #1f6feb; font-size: 0.85rem; }
  code { background: #f1f3f6; padding: 1px 5px; border-radius: 3px; font-size: 0.85em; }
</style>
</head>
<body>
<header>
  <h1>Gira HomeServer Sonos Bridge</h1>
  <div class="url">${escapeHtml(bridgeUrl)} &middot; v${escapeHtml(version)}</div>
</header>
<main>

<section>
  <h2>Players</h2>
  <div id="players-state" class="muted">Loading…</div>
  <table id="players-table" style="margin-top: 0.5rem;">
    <thead><tr>
      <th style="width:20%">Name</th><th style="width:20%">Host</th>
      <th style="width:15%">State</th><th style="width:10%">Vol</th>
      <th style="width:20%">Track</th><th style="width:15%">Actions</th>
    </tr></thead>
    <tbody></tbody>
  </table>
  <div class="row" style="margin-top: 0.75rem;">
    <input id="np-name" type="text" placeholder="name (e.g. livingroom)" style="max-width: 180px">
    <input id="np-host" type="text" placeholder="host (e.g. 192.168.1.50)" style="max-width: 180px">
    <button id="np-add">Add player</button>
    <button id="np-discover" class="secondary">Discover (SSDP)</button>
  </div>
</section>

<section>
  <h2>Radio stations</h2>
  <table id="stations-table">
    <thead><tr>
      <th style="width:10%">Idx</th><th style="width:25%">Name</th>
      <th>Stream URI</th><th style="width:15%">Actions</th>
    </tr></thead>
    <tbody></tbody>
  </table>
  <div class="row" style="margin-top: 0.75rem;">
    <input id="ns-idx" type="number" min="1" placeholder="index" style="max-width: 80px">
    <input id="ns-name" type="text" placeholder="name" style="max-width: 200px">
    <input id="ns-uri" type="url" placeholder="stream URL (http://… or x-rincon-mp3radio://…)">
    <button id="ns-add">Add station</button>
  </div>
</section>

<section>
  <h2>Cloud / Event push</h2>
  <div class="grid">
    <label for="wh-url">Webhook URL (e.g. HomeServer endpoint)</label>
    <input id="wh-url" type="url" placeholder="http://homeserver.local/quad/sonos-event">
    <label for="wh-auth">Webhook Authorization header (optional)</label>
    <input id="wh-auth" type="text" placeholder="Bearer ...">
    <label for="cloud-toggle">Enable Sonos Cloud Control API (optional fallback)</label>
    <div><input id="cloud-toggle" type="checkbox"> <span class="muted">requires OAuth client credentials below</span></div>
    <label for="cloud-id">Cloud client ID</label>
    <input id="cloud-id" type="text" placeholder="client_id">
    <label for="cloud-secret">Cloud client secret</label>
    <input id="cloud-secret" type="text" placeholder="client_secret">
  </div>
  <div class="row" style="margin-top: 0.75rem;">
    <button id="cfg-save">Save configuration</button>
    <button id="cfg-test-webhook" class="secondary">Test webhook</button>
    <button id="cfg-resub" class="secondary">Re-subscribe UPnP events</button>
  </div>
  <details><summary>What this does</summary>
    <p class="muted">When UPnP events arrive from a Sonos player (volume change, pause, station change), the bridge updates its in-memory state cache. If a webhook URL is configured, the bridge POSTs a small JSON payload to that URL so the HomeServer can update KNX outputs without polling. The Sonos Cloud Control API is supported as an optional <em>fallback</em> only — local SOAP is preferred and works on all current and 2026 firmware.</p>
  </details>
</section>

<section>
  <h2>Live event stream</h2>
  <pre id="event-log" style="height: 12em; overflow-y: auto; background: #0d1117; color: #c9d1d9; padding: 0.5rem; border-radius: 6px; font-size: 0.8rem; margin: 0;"></pre>
  <p class="muted" style="margin-top: 0.5rem;">Streams events via Server-Sent Events from <code>${escapeHtml(bridgeUrl)}/events</code>.</p>
</section>

<section>
  <h2>HomeServer integration</h2>
  <p class="muted">Add this URL to the HomeServer homepage or debug page so the configuration is one click away:</p>
  <pre style="background: #0d1117; color: #c9d1d9; padding: 0.5rem; border-radius: 6px; margin: 0;"><code id="hs-url">${escapeHtml(bridgeUrl)}</code></pre>
  <p class="muted" style="margin-top: 0.5rem;">See <code>homeserver/HOMEPAGE.md</code> for an iframe snippet to embed this page inside the HomeServer visualisation.</p>
</section>

</main>
<div id="toast" class="toast"></div>

<script>
const BASE = location.origin;
function toast(msg, err) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.className = 'toast' + (err ? ' error' : '');
  t.style.display = 'block';
  setTimeout(() => { t.style.display = 'none'; }, 2500);
}
async function api(method, path, body) {
  const opts = { method, headers: { 'Content-Type': 'application/json' } };
  if (body) opts.body = JSON.stringify(body);
  const res = await fetch(BASE + path, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.message || ('HTTP ' + res.status));
  return data;
}
async function refreshPlayers() {
  try {
    const { players, states } = await api('GET', '/api/players/full');
    const tbody = document.querySelector('#players-table tbody');
    tbody.innerHTML = '';
    for (const p of players) {
      const st = states[p.name] || {};
      const online = st.online !== false;
      const tr = document.createElement('tr');
      tr.innerHTML =
        '<td>' + escapeHtml(p.name) + '</td>' +
        '<td>' + escapeHtml(p.host) + '</td>' +
        '<td><span class="pill ' + (online ? (st.state === 'PLAYING' ? 'playing' : 'online') : 'offline') + '">' +
        escapeHtml(online ? (st.state || 'unknown') : 'offline') + '</span></td>' +
        '<td>' + (st.volume != null ? st.volume : '-') + (st.mute ? ' 🔇' : '') + '</td>' +
        '<td>' + escapeHtml((st.track && (st.track.title || st.track.streamContent)) || '') + '</td>' +
        '<td class="row">' +
        '<button data-act="play" data-p="' + escapeAttr(p.name) + '">▶</button>' +
        '<button data-act="pause" data-p="' + escapeAttr(p.name) + '" class="secondary">⏸</button>' +
        '<button data-act="del" data-p="' + escapeAttr(p.name) + '" class="danger">✕</button>' +
        '</td>';
      tbody.appendChild(tr);
    }
    document.getElementById('players-state').textContent = players.length + ' player(s) configured.';
  } catch (e) { toast(e.message, true); }
}
async function refreshStations() {
  try {
    const { stations } = await api('GET', '/api/stations');
    const tbody = document.querySelector('#stations-table tbody');
    tbody.innerHTML = '';
    for (const s of stations) {
      const tr = document.createElement('tr');
      tr.innerHTML =
        '<td>' + s.index + '</td>' +
        '<td>' + escapeHtml(s.name) + '</td>' +
        '<td><code>' + escapeHtml(s.streamUri) + '</code></td>' +
        '<td><button data-sact="del" data-idx="' + s.index + '" class="danger">✕</button></td>';
      tbody.appendChild(tr);
    }
  } catch (e) { toast(e.message, true); }
}
async function refreshConfig() {
  try {
    const { config } = await api('GET', '/api/config');
    document.getElementById('wh-url').value = (config.webhook && config.webhook.url) || '';
    document.getElementById('wh-auth').value = (config.webhook && config.webhook.authHeader) || '';
    document.getElementById('cloud-toggle').checked = !!(config.cloud && config.cloud.enabled);
    document.getElementById('cloud-id').value = (config.cloud && config.cloud.clientId) || '';
    document.getElementById('cloud-secret').value = (config.cloud && config.cloud.clientSecret) || '';
  } catch (e) { toast(e.message, true); }
}
document.addEventListener('click', async (ev) => {
  const t = ev.target;
  if (t.tagName !== 'BUTTON') return;
  try {
    if (t.dataset.act === 'play')  { await api('POST', '/players/' + encodeURIComponent(t.dataset.p) + '/play'); refreshPlayers(); }
    if (t.dataset.act === 'pause') { await api('POST', '/players/' + encodeURIComponent(t.dataset.p) + '/pause'); refreshPlayers(); }
    if (t.dataset.act === 'del')   {
      if (!confirm('Remove player ' + t.dataset.p + '?')) return;
      await api('DELETE', '/api/players/' + encodeURIComponent(t.dataset.p)); refreshPlayers();
    }
    if (t.dataset.sact === 'del')  {
      await api('DELETE', '/api/stations/' + encodeURIComponent(t.dataset.idx)); refreshStations();
    }
  } catch (e) { toast(e.message, true); }
});
document.getElementById('np-add').addEventListener('click', async () => {
  const name = document.getElementById('np-name').value.trim();
  const host = document.getElementById('np-host').value.trim();
  if (!name || !host) return toast('name and host required', true);
  try {
    await api('POST', '/api/players', { name, host });
    document.getElementById('np-name').value = '';
    document.getElementById('np-host').value = '';
    refreshPlayers();
    toast('Player added');
  } catch (e) { toast(e.message, true); }
});
document.getElementById('np-discover').addEventListener('click', async () => {
  try {
    const r = await api('GET', '/players/discover');
    if (!r.discovered.length) return toast('No players found on LAN');
    toast('Found ' + r.discovered.length + ' player(s)');
  } catch (e) { toast(e.message, true); }
});
document.getElementById('ns-add').addEventListener('click', async () => {
  const index = parseInt(document.getElementById('ns-idx').value, 10);
  const name = document.getElementById('ns-name').value.trim();
  const streamUri = document.getElementById('ns-uri').value.trim();
  if (!index || !name || !streamUri) return toast('index, name and URI required', true);
  try {
    await api('POST', '/api/stations', { index, name, streamUri });
    document.getElementById('ns-idx').value = '';
    document.getElementById('ns-name').value = '';
    document.getElementById('ns-uri').value = '';
    refreshStations();
    toast('Station added');
  } catch (e) { toast(e.message, true); }
});
document.getElementById('cfg-save').addEventListener('click', async () => {
  try {
    await api('PUT', '/api/config', {
      webhook: {
        url: document.getElementById('wh-url').value.trim(),
        authHeader: document.getElementById('wh-auth').value.trim()
      },
      cloud: {
        enabled: document.getElementById('cloud-toggle').checked,
        clientId: document.getElementById('cloud-id').value.trim(),
        clientSecret: document.getElementById('cloud-secret').value.trim()
      }
    });
    toast('Configuration saved');
  } catch (e) { toast(e.message, true); }
});
document.getElementById('cfg-test-webhook').addEventListener('click', async () => {
  try { const r = await api('POST', '/api/webhook/test'); toast(r.ok ? 'Webhook OK' : 'Webhook failed', !r.ok); }
  catch (e) { toast(e.message, true); }
});
document.getElementById('cfg-resub').addEventListener('click', async () => {
  try { await api('POST', '/api/events/resubscribe'); toast('Re-subscribed'); }
  catch (e) { toast(e.message, true); }
});

function escapeHtml(s) {
  return String(s == null ? '' : s)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}
function escapeAttr(s) { return escapeHtml(s); }

function openEventStream() {
  const log = document.getElementById('event-log');
  const es = new EventSource(BASE + '/events');
  es.onmessage = (ev) => {
    const line = new Date().toLocaleTimeString() + '  ' + ev.data + '\\n';
    log.textContent += line;
    log.scrollTop = log.scrollHeight;
    if (ev.data.indexOf('"event":"state"') !== -1) refreshPlayers();
  };
  es.onerror = () => { /* browser auto-reconnects */ };
}

refreshPlayers();
refreshStations();
refreshConfig();
openEventStream();
setInterval(refreshPlayers, 15000);
</script>
</body>
</html>`;
}

function escapeHtml(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

module.exports = { adminHtml };
