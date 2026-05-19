"""LBS 22001 - Sonos Admin.

Singleton (one instance per HomeServer) that starts a small HTTP server
on port 8080 and serves a single-page web UI for managing the Sonos
integration:

  - Discovered + manually-added players (identified by IP and/or MAC).
  - A library of radio stations (name + stream URI).
  - Sonos Cloud Control API OAuth credentials and token capture.

It runs both periodic and KNX-triggerable SSDP discovery, exposes the
latest scan result on a structured output (newline-separated
``ip;uuid;model`` list), and maintains a class-level **player registry**
that LBS 22000 (Sonos Player) consults whenever its ``Host`` input is a
friendly name or a MAC address instead of a literal IP. This makes the
configured player references survive DHCP renumbering on networks that
don't reserve addresses.

Backward compatibility: LBS 22000 still works standalone. If no Admin
instance is on the canvas, ``Host`` must be an IP, exactly as before.
When Admin is present and a player's IP changes, the next ARP refresh
picks it up and LBS 22000's next tick uses the new IP.

This module supersedes the previously-separate LBS 22001 Sonos Discover
and LBS 22002 Sonos Admin nodes. Discover's KNX-friendly outputs
(``Result``, ``Count``, ``Error``) are present here as
``DiscoveredPlayers``, ``LastDiscoveryCount`` and ``LastError``; its
``Trigger`` input is the existing ``TriggerDiscovery``; its ``Timeout``
input is the new ``DiscoveryTimeout``.
"""

import json
import re
import socket
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests


ADMIN_PORT_DEFAULT = 8080
ADMIN_PORT_MAX = 8083  # Try 8080..8083 before giving up

# Periodic SSDP refresh + ARP-table reload cadence.
DEFAULT_AUTO_DISCOVER_S = 300
ARP_PATH = "/proc/net/arp"

SSDP_HOST = "239.255.255.250"
SSDP_PORT = 1900
SSDP_TARGETS = [
    "urn:schemas-upnp-org:device:ZonePlayer:1",
    "urn:smartspeaker-audio:service:SpeakerGroup:1",
    "ssdp:all",
]

# Sonos Cloud Control API OAuth endpoints. The LBS only collects + stores
# tokens; actual cloud API calls are out of scope for this module and
# happen elsewhere if a future LBS needs the fallback.
SONOS_AUTHORIZE_URL = "https://api.sonos.com/login/v3/oauth"
SONOS_TOKEN_URL = "https://api.sonos.com/login/v3/oauth/access"


# ---------------------------------------------------------------------------
# Shared registry — class-level so LBS 22000 can read it without explicit
# inter-instance wiring. Guarded by a single lock.
# ---------------------------------------------------------------------------

_registry_lock = threading.RLock()
_players = {}     # id(str) -> {"id","name","ip","mac","uuid","model","source"}
_stations = {}    # id(str) -> {"id","name","uri"}
_cloud = {
    "clientId": "",
    "clientSecret": "",
    "redirectBase": "",
    "accessToken": "",
    "refreshToken": "",
    "expiresAt": 0,
}
_admin_instance_ref = {"instance": None}   # Wrapped in dict so swap is atomic.


def _norm_mac(mac):
    """Normalize a MAC address to lower-case colon form. Returns '' on input
    that is not a MAC."""
    if not mac:
        return ""
    s = re.sub(r"[^0-9A-Fa-f]", "", str(mac)).lower()
    if len(s) != 12:
        return ""
    return ":".join(s[i:i + 2] for i in range(0, 12, 2))


def _is_ip(value):
    if not value:
        return False
    parts = str(value).split(".")
    if len(parts) != 4:
        return False
    try:
        return all(0 <= int(p) <= 255 for p in parts)
    except ValueError:
        return False


def _player_id(rec):
    """Stable id: prefer MAC (survives DHCP), fall back to IP."""
    return rec.get("mac") or rec.get("ip") or rec.get("uuid") or "unknown"


def _read_arp_table():
    """Return {ip: mac} from /proc/net/arp. Empty on platforms without it
    (or when the file is unreadable). Skips incomplete entries (flags 0x0)."""
    out = {}
    try:
        with open(ARP_PATH) as fh:
            lines = fh.readlines()
    except OSError:
        return out
    for line in lines[1:]:
        cols = line.split()
        if len(cols) < 6:
            continue
        ip, _hwtype, flags, mac, _mask, _dev = cols[:6]
        if flags == "0x0":   # not yet resolved
            continue
        norm = _norm_mac(mac)
        if norm and _is_ip(ip):
            out[ip] = norm
    return out


def _resolve_mac_for_ip(ip):
    """Best-effort: poke the ARP cache by opening a TCP connection (RST is
    fine) then re-read /proc/net/arp. Returns '' if the MAC isn't visible."""
    if not _is_ip(ip):
        return ""
    table = _read_arp_table()
    if ip in table:
        return table[ip]
    # Force a cache entry by attempting a quick TCP connect.
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.3)
    try:
        s.connect_ex((ip, 1400))
    except OSError:
        pass
    finally:
        s.close()
    return _read_arp_table().get(ip, "")


def _resolve_ip_for_mac(mac):
    """Look up the current IP for a known MAC via /proc/net/arp."""
    norm = _norm_mac(mac)
    if not norm:
        return ""
    for ip, m in _read_arp_table().items():
        if m == norm:
            return ip
    return ""


# ---------------------------------------------------------------------------
# Resolution function called by LBS 22000 when its Host input doesn't look
# like an IP. Exported via module globals so the player module can grab it
# without explicit imports across LBS boundaries.
# ---------------------------------------------------------------------------

def resolve_host(spec):
    """Map a Host-input value to a current IP. Accepts:

      192.168.1.50         → returned unchanged (already an IP)
      00:0e:58:ab:cd:ef    → looked up via ARP, returns current IP or ''
      livingroom           → looked up by name in the player registry
      RINCON_xxx           → looked up by uuid

    Returns '' on resolution failure (caller treats as offline).
    """
    if not spec:
        return ""
    spec = str(spec).strip()
    if _is_ip(spec):
        return spec
    norm = _norm_mac(spec)
    with _registry_lock:
        if norm:
            for rec in _players.values():
                if rec.get("mac") == norm:
                    return rec.get("ip") or _resolve_ip_for_mac(norm)
            # MAC not in registry yet — try ARP anyway.
            return _resolve_ip_for_mac(norm)
        # Try name or uuid match.
        for rec in _players.values():
            if rec.get("name", "").lower() == spec.lower():
                return rec.get("ip", "")
            if rec.get("uuid") == spec:
                return rec.get("ip", "")
    return ""


def get_station_uri(index_or_name):
    """LBS 22000 can call this to resolve a station from the central library
    instead of from its own per-instance Station<N>Uri inputs. Returns ''
    when not found."""
    if index_or_name is None:
        return ""
    key = str(index_or_name).strip()
    with _registry_lock:
        # Numeric: index into sorted list.
        if key.isdigit():
            idx = int(key)
            sorted_stations = sorted(_stations.values(), key=lambda s: s["name"].lower())
            if 1 <= idx <= len(sorted_stations):
                return sorted_stations[idx - 1]["uri"]
            return ""
        # Name match (case-insensitive).
        for rec in _stations.values():
            if rec["name"].lower() == key.lower():
                return rec["uri"]
    return ""


# ---------------------------------------------------------------------------
# SSDP discovery (same logic as LBS 22001, kept local to avoid cross-LBS
# Python imports which the SDK does not guarantee).
# ---------------------------------------------------------------------------

def _ssdp_scan(timeout_sec=4):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    sock.settimeout(0.5)
    try:
        for target in SSDP_TARGETS:
            msg = (
                "M-SEARCH * HTTP/1.1\r\n"
                "HOST: {}:{}\r\n"
                'MAN: "ssdp:discover"\r\n'
                "MX: 2\r\n"
                "ST: {}\r\n\r\n"
            ).format(SSDP_HOST, SSDP_PORT, target).encode("utf-8")
            try:
                sock.sendto(msg, (SSDP_HOST, SSDP_PORT))
            except OSError:
                pass

        deadline = time.time() + timeout_sec
        found = {}
        while time.time() < deadline:
            try:
                data, addr = sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            text = data.decode("utf-8", errors="replace")
            headers = {}
            for line in text.split("\r\n")[1:]:
                if ":" in line:
                    k, _, v = line.partition(":")
                    headers[k.strip().upper()] = v.strip()
            server = headers.get("SERVER", "").lower()
            usn = headers.get("USN", "").lower()
            if "sonos" not in server and "rincon" not in usn and "zoneplayer" not in usn:
                continue
            ip = addr[0]
            if ip in found:
                continue
            m = re.search(r"uuid:([A-Za-z0-9_-]+)", headers.get("USN", ""))
            uuid = m.group(1) if m else ""
            location = headers.get("LOCATION", "")
            model = _fetch_model(location) if location else ""
            found[ip] = {"ip": ip, "uuid": uuid, "model": model}
        return list(found.values())
    finally:
        try:
            sock.close()
        except Exception:
            pass


def _fetch_model(location):
    try:
        resp = requests.get(location, timeout=2)
        m = re.search(r"<modelName>([^<]+)</modelName>", resp.text)
        return m.group(1) if m else ""
    except Exception:
        return ""


def scan_and_merge(timeout_sec=4):
    """Run an SSDP scan; merge results into the registry. Manually-added
    players keep their `source = "manual"` flag and are not overwritten.
    Returns the raw scan list (each item: {ip, uuid, model})."""
    discovered = _ssdp_scan(timeout_sec)
    arp = _read_arp_table()
    now = time.time()
    with _registry_lock:
        for d in discovered:
            mac = arp.get(d["ip"], "") or _resolve_mac_for_ip(d["ip"])
            rec = {
                "id": mac or d["ip"],
                "name": "",
                "ip": d["ip"],
                "mac": mac,
                "uuid": d["uuid"],
                "model": d["model"],
                "source": "ssdp",
                "lastSeen": now,
            }
            existing = None
            for pid, p in _players.items():
                if (mac and p.get("mac") == mac) or p.get("ip") == d["ip"] or (d["uuid"] and p.get("uuid") == d["uuid"]):
                    existing = pid
                    break
            if existing:
                # Refresh IP (DHCP may have renumbered), keep name + source.
                _players[existing]["ip"] = d["ip"]
                if mac:
                    _players[existing]["mac"] = mac
                if d["uuid"] and not _players[existing].get("uuid"):
                    _players[existing]["uuid"] = d["uuid"]
                if d["model"] and not _players[existing].get("model"):
                    _players[existing]["model"] = d["model"]
                _players[existing]["lastSeen"] = now
            else:
                _players[rec["id"]] = rec
    return discovered


def discover_and_merge(timeout_sec=4):
    """Backwards-compat wrapper kept so older callers see an int count."""
    return len(scan_and_merge(timeout_sec))


# ---------------------------------------------------------------------------
# HTTP server
# ---------------------------------------------------------------------------

INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sonos Admin</title>
<style>
:root { font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif; }
body { margin: 0; background: #f5f6f8; color: #1a1f2c; }
header { background: #1f6feb; color: #fff; padding: 1rem 1.5rem; }
header h1 { margin: 0; font-size: 1.2rem; }
main { max-width: 980px; margin: 1.25rem auto; padding: 0 1rem; }
section { background: #fff; border: 1px solid #d7dce3; border-radius: 8px; margin-bottom: 1rem; padding: 1rem 1.25rem; }
section h2 { margin: 0 0 0.5rem; font-size: 1rem; }
table { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
th, td { text-align: left; padding: 0.4rem 0.5rem; border-bottom: 1px solid #eef0f3; vertical-align: middle; }
th { background: #f7f8fa; font-weight: 600; }
input[type=text], input[type=url] { width: 100%; padding: 0.35rem 0.5rem; border: 1px solid #cdd3dc; border-radius: 4px; font: inherit; box-sizing: border-box; }
button { font: inherit; padding: 0.4rem 0.75rem; background: #1f6feb; color: #fff; border: 0; border-radius: 4px; cursor: pointer; }
button.secondary { background: #6b7280; }
button.danger { background: #c0392b; }
.row { display: flex; gap: 0.5rem; align-items: center; flex-wrap: wrap; margin-top: 0.5rem; }
.muted { color: #6b7280; font-size: 0.85rem; }
.pill { display: inline-block; padding: 2px 8px; border-radius: 99px; font-size: 0.75rem; font-weight: 600; }
.pill.ssdp { background: #def7ec; color: #03543e; }
.pill.manual { background: #e1effe; color: #1e429f; }
.toast { position: fixed; right: 1rem; bottom: 1rem; background: #1f6feb; color: #fff; padding: 0.6rem 1rem; border-radius: 6px; box-shadow: 0 2px 6px rgba(0,0,0,0.2); display: none; }
.toast.error { background: #c0392b; }
code { background: #f1f3f6; padding: 1px 5px; border-radius: 3px; font-size: 0.85em; }
.grid { display: grid; grid-template-columns: 200px 1fr; gap: 0.5rem 1rem; }
</style>
</head>
<body>
<header><h1>Sonos Admin</h1></header>
<main>
  <section>
    <h2>Players</h2>
    <div id="players-state" class="muted">Loading...</div>
    <table id="players">
      <thead><tr>
        <th style="width:18%">Name</th><th style="width:18%">IP</th>
        <th style="width:24%">MAC</th><th style="width:14%">Model</th>
        <th style="width:12%">Source</th><th style="width:14%">Actions</th>
      </tr></thead><tbody></tbody>
    </table>
    <div class="row">
      <input id="np-name" placeholder="name (e.g. livingroom)" style="max-width: 180px">
      <input id="np-ip"   placeholder="IP (optional if MAC given)" style="max-width: 160px">
      <input id="np-mac"  placeholder="MAC (recommended for DHCP)" style="max-width: 200px">
      <button id="np-add">Add player</button>
      <button id="np-scan" class="secondary">Scan now (SSDP)</button>
    </div>
  </section>

  <section>
    <h2>Radio stations</h2>
    <table id="stations">
      <thead><tr>
        <th style="width:25%">Name</th><th>Stream URI</th><th style="width:12%">Actions</th>
      </tr></thead><tbody></tbody>
    </table>
    <div class="row">
      <input id="ns-name" placeholder="name" style="max-width: 200px">
      <input id="ns-uri"  placeholder="stream URL: http://... or x-rincon-mp3radio://...">
      <button id="ns-add">Add station</button>
    </div>
  </section>

  <section>
    <h2>Sonos Cloud (optional)</h2>
    <p class="muted">Configure OAuth credentials so a future LBS can use the Sonos Cloud Control API as a fallback when local SOAP is unavailable. Local SOAP remains the primary path.</p>
    <div class="grid">
      <label for="c-id">Client ID</label><input id="c-id" type="text">
      <label for="c-secret">Client secret</label><input id="c-secret" type="text">
      <label for="c-redirect">Redirect base (this Admin URL)</label><input id="c-redirect" type="text">
    </div>
    <div class="row">
      <button id="c-save">Save credentials</button>
      <button id="c-authorize" class="secondary">Authorize with Sonos</button>
      <span id="c-state" class="muted"></span>
    </div>
  </section>

  <section>
    <h2>Diagnostics</h2>
    <div class="muted" id="diag">Loading...</div>
  </section>
</main>
<div id="toast" class="toast"></div>

<script>
const BASE = location.origin;
function toast(msg, err) {
  const t = document.getElementById('toast');
  t.textContent = msg; t.className = 'toast' + (err ? ' error' : '');
  t.style.display = 'block'; setTimeout(() => t.style.display = 'none', 2500);
}
async function api(method, path, body) {
  const opts = { method, headers: { 'Content-Type': 'application/json' } };
  if (body) opts.body = JSON.stringify(body);
  const res = await fetch(BASE + path, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.message || ('HTTP ' + res.status));
  return data;
}
function esc(s) {
  return String(s == null ? '' : s).replace(/&/g,'&amp;').replace(/</g,'&lt;')
    .replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}
async function refreshPlayers() {
  const r = await api('GET', '/api/players');
  const tbody = document.querySelector('#players tbody');
  tbody.innerHTML = '';
  for (const p of r.players) {
    const tr = document.createElement('tr');
    tr.innerHTML =
      '<td><input data-edit="' + esc(p.id) + '" data-field="name" value="' + esc(p.name) + '"></td>' +
      '<td><input data-edit="' + esc(p.id) + '" data-field="ip"   value="' + esc(p.ip)   + '"></td>' +
      '<td><input data-edit="' + esc(p.id) + '" data-field="mac"  value="' + esc(p.mac)  + '"></td>' +
      '<td>' + esc(p.model || '') + '</td>' +
      '<td><span class="pill ' + esc(p.source) + '">' + esc(p.source) + '</span></td>' +
      '<td><button class="danger" data-del-player="' + esc(p.id) + '">Remove</button></td>';
    tbody.appendChild(tr);
  }
  document.getElementById('players-state').textContent =
    r.players.length + ' player(s); last scan ' + (r.lastDiscoveryAt || 'never') + '.';
}
async function refreshStations() {
  const r = await api('GET', '/api/stations');
  const tbody = document.querySelector('#stations tbody');
  tbody.innerHTML = '';
  for (const s of r.stations) {
    const tr = document.createElement('tr');
    tr.innerHTML =
      '<td><input data-sedit="' + esc(s.id) + '" data-field="name" value="' + esc(s.name) + '"></td>' +
      '<td><input data-sedit="' + esc(s.id) + '" data-field="uri"  value="' + esc(s.uri)  + '"></td>' +
      '<td><button class="danger" data-del-station="' + esc(s.id) + '">Remove</button></td>';
    tbody.appendChild(tr);
  }
}
async function refreshCloud() {
  const r = await api('GET', '/api/cloud');
  document.getElementById('c-id').value = r.cloud.clientId || '';
  document.getElementById('c-secret').value = (r.cloud.clientSecretSet ? '(set)' : '');
  document.getElementById('c-redirect').value = r.cloud.redirectBase || location.origin;
  document.getElementById('c-state').textContent =
    r.cloud.authorized ? 'Authorized; token expires in ' + r.cloud.expiresIn + 's' : 'Not authorized';
}
async function refreshDiag() {
  const r = await api('GET', '/api/info');
  document.getElementById('diag').innerHTML =
    'Listener port: <code>' + r.listenPort + '</code> &middot; ' +
    'Players: <code>' + r.playerCount + '</code> &middot; ' +
    'Stations: <code>' + r.stationCount + '</code> &middot; ' +
    'Cloud authorized: <code>' + r.cloudAuthorized + '</code>';
}
async function refreshAll() {
  try { await refreshPlayers(); await refreshStations(); await refreshCloud(); await refreshDiag(); }
  catch (e) { toast(e.message, true); }
}
document.addEventListener('change', async (ev) => {
  const t = ev.target;
  if (t.dataset.edit) {
    try { await api('PATCH', '/api/players/' + encodeURIComponent(t.dataset.edit),
                    { [t.dataset.field]: t.value }); toast('Saved'); }
    catch (e) { toast(e.message, true); }
  }
  if (t.dataset.sedit) {
    try { await api('PATCH', '/api/stations/' + encodeURIComponent(t.dataset.sedit),
                    { [t.dataset.field]: t.value }); toast('Saved'); }
    catch (e) { toast(e.message, true); }
  }
});
document.addEventListener('click', async (ev) => {
  const t = ev.target;
  try {
    if (t.dataset.delPlayer) {
      if (!confirm('Remove player?')) return;
      await api('DELETE', '/api/players/' + encodeURIComponent(t.dataset.delPlayer));
      refreshAll();
    }
    if (t.dataset.delStation) {
      await api('DELETE', '/api/stations/' + encodeURIComponent(t.dataset.delStation));
      refreshAll();
    }
  } catch (e) { toast(e.message, true); }
});
document.getElementById('np-add').addEventListener('click', async () => {
  const name = document.getElementById('np-name').value.trim();
  const ip = document.getElementById('np-ip').value.trim();
  const mac = document.getElementById('np-mac').value.trim();
  if (!ip && !mac) return toast('Provide IP or MAC', true);
  try {
    await api('POST', '/api/players', { name, ip, mac });
    document.getElementById('np-name').value = '';
    document.getElementById('np-ip').value = '';
    document.getElementById('np-mac').value = '';
    refreshAll();
  } catch (e) { toast(e.message, true); }
});
document.getElementById('np-scan').addEventListener('click', async () => {
  try { const r = await api('POST', '/api/players/discover'); toast('Found ' + r.discovered + ' players'); refreshAll(); }
  catch (e) { toast(e.message, true); }
});
document.getElementById('ns-add').addEventListener('click', async () => {
  const name = document.getElementById('ns-name').value.trim();
  const uri = document.getElementById('ns-uri').value.trim();
  if (!name || !uri) return toast('name and URI required', true);
  try {
    await api('POST', '/api/stations', { name, uri });
    document.getElementById('ns-name').value = '';
    document.getElementById('ns-uri').value = '';
    refreshAll();
  } catch (e) { toast(e.message, true); }
});
document.getElementById('c-save').addEventListener('click', async () => {
  const body = {
    clientId: document.getElementById('c-id').value.trim(),
    redirectBase: document.getElementById('c-redirect').value.trim(),
  };
  const sec = document.getElementById('c-secret').value.trim();
  if (sec && sec !== '(set)') body.clientSecret = sec;
  try { await api('PUT', '/api/cloud', body); toast('Saved'); refreshAll(); }
  catch (e) { toast(e.message, true); }
});
document.getElementById('c-authorize').addEventListener('click', () => {
  location.href = '/oauth/start';
});

refreshAll();
setInterval(refreshAll, 15000);
</script>
</body>
</html>
"""


class _AdminHandler(BaseHTTPRequestHandler):
    """Routes HTTP requests for the admin UI."""

    server_version = "SonosAdmin/1.0"

    def _send(self, status, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        length = int(self.headers.get("Content-Length", "0") or "0")
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None

    def _err(self, status, code, message):
        self._send(status, {"ok": False, "error": code, "message": message})

    def log_message(self, format, *args):
        return  # silence stderr noise

    # --- Routing -----------------------------------------------------------
    def do_GET(self):  # noqa: N802
        try:
            path = urllib.parse.urlparse(self.path).path
            if path in ("/", "/index.html"):
                return self._send(200, INDEX_HTML, "text/html; charset=utf-8")
            if path == "/api/info":
                return self._send(200, self.server.admin.api_info())
            if path == "/api/players":
                return self._send(200, self.server.admin.api_list_players())
            if path == "/api/stations":
                return self._send(200, self.server.admin.api_list_stations())
            if path == "/api/cloud":
                return self._send(200, self.server.admin.api_get_cloud())
            if path == "/oauth/start":
                return self._oauth_start()
            if path == "/oauth/callback":
                return self._oauth_callback()
            self._err(404, "NOT_FOUND", "no such route")
        except Exception as exc:  # noqa: BLE001
            self._err(500, "INTERNAL", str(exc))

    def do_POST(self):  # noqa: N802
        try:
            path = urllib.parse.urlparse(self.path).path
            body = self._read_json()
            if body is None:
                return self._err(400, "BAD_JSON", "request body is not valid JSON")
            if path == "/api/players":
                return self._send(200, self.server.admin.api_add_player(body))
            if path == "/api/players/discover":
                return self._send(200, self.server.admin.api_discover_now())
            if path == "/api/stations":
                return self._send(200, self.server.admin.api_add_station(body))
            self._err(404, "NOT_FOUND", "no such route")
        except ValueError as exc:
            self._err(400, "INVALID_ARG", str(exc))
        except Exception as exc:  # noqa: BLE001
            self._err(500, "INTERNAL", str(exc))

    def do_PUT(self):  # noqa: N802
        try:
            path = urllib.parse.urlparse(self.path).path
            body = self._read_json()
            if body is None:
                return self._err(400, "BAD_JSON", "request body is not valid JSON")
            if path == "/api/cloud":
                return self._send(200, self.server.admin.api_set_cloud(body))
            self._err(404, "NOT_FOUND", "no such route")
        except Exception as exc:  # noqa: BLE001
            self._err(500, "INTERNAL", str(exc))

    def do_PATCH(self):  # noqa: N802
        try:
            path = urllib.parse.urlparse(self.path).path
            body = self._read_json()
            if body is None:
                return self._err(400, "BAD_JSON", "request body is not valid JSON")
            m = re.match(r"^/api/players/(.+)$", path)
            if m:
                return self._send(200, self.server.admin.api_update_player(urllib.parse.unquote(m.group(1)), body))
            m = re.match(r"^/api/stations/(.+)$", path)
            if m:
                return self._send(200, self.server.admin.api_update_station(urllib.parse.unquote(m.group(1)), body))
            self._err(404, "NOT_FOUND", "no such route")
        except ValueError as exc:
            self._err(400, "INVALID_ARG", str(exc))
        except Exception as exc:  # noqa: BLE001
            self._err(500, "INTERNAL", str(exc))

    def do_DELETE(self):  # noqa: N802
        try:
            path = urllib.parse.urlparse(self.path).path
            m = re.match(r"^/api/players/(.+)$", path)
            if m:
                return self._send(200, self.server.admin.api_remove_player(urllib.parse.unquote(m.group(1))))
            m = re.match(r"^/api/stations/(.+)$", path)
            if m:
                return self._send(200, self.server.admin.api_remove_station(urllib.parse.unquote(m.group(1))))
            self._err(404, "NOT_FOUND", "no such route")
        except Exception as exc:  # noqa: BLE001
            self._err(500, "INTERNAL", str(exc))

    # --- OAuth helpers -----------------------------------------------------
    def _oauth_start(self):
        admin = self.server.admin
        params = admin.oauth_start_params()
        if not params:
            return self._err(400, "OAUTH_NOT_CONFIGURED",
                             "set clientId, clientSecret, redirectBase first")
        url = SONOS_AUTHORIZE_URL + "?" + urllib.parse.urlencode(params)
        self.send_response(302)
        self.send_header("Location", url)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _oauth_callback(self):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        code = (q.get("code") or [""])[0]
        if not code:
            return self._send(400, "<h1>OAuth error</h1><p>No code in callback.</p>",
                              "text/html; charset=utf-8")
        ok, msg = self.server.admin.oauth_exchange(code)
        body = "<h1>{}</h1><p>{}</p><p><a href=\"/\">Back to admin</a></p>".format(
            "Authorized" if ok else "OAuth error", msg
        )
        self._send(200 if ok else 400, body, "text/html; charset=utf-8")


# ---------------------------------------------------------------------------
# LogicModule
# ---------------------------------------------------------------------------

class LogicModule:

    def __init__(self, hsl3):
        self.fw = hsl3
        self.logger = hsl3.get_logger()
        self.debug = None
        self.server = None
        self.listener_port = 0
        self.server_thread = None
        self._auto_discover_s = DEFAULT_AUTO_DISCOVER_S
        self._discovery_timeout = 4
        self._last_discovery_at = 0.0
        self._last_discovered = []   # list of {ip, uuid, model}

    # ----- HSL3 entry points -----------------------------------------------

    def on_init(self, inputs, store):
        self.debug = self.fw.create_debug_section()
        self.debug.set("Listener port", 0)
        self.debug.set("Players", 0)
        self.debug.set("Stations", 0)
        self.debug.set("Cloud authorized", "no")
        self.debug.set("Last discovery", "-")

        # Read configuration inputs.
        port = int(inputs["HttpPort"].value or ADMIN_PORT_DEFAULT)
        self._auto_discover_s = max(60, int(inputs["AutoDiscoverInterval"].value or DEFAULT_AUTO_DISCOVER_S))
        self._discovery_timeout = max(1, int(inputs["DiscoveryTimeout"].value or 4))
        # Pre-seed cloud config from inputs (the UI can override at runtime).
        with _registry_lock:
            _cloud["clientId"] = self._decode(inputs["CloudClientId"].value)
            secret = self._decode(inputs["CloudClientSecret"].value)
            if secret:
                _cloud["clientSecret"] = secret
            _cloud["redirectBase"] = self._decode(inputs["OAuthRedirectBase"].value)

        # Start HTTP server.
        bound = self._start_server(port)
        if bound is None:
            self.fw.set_output("LastError", b"PORT_BIND_FAILED")
            self.logger.error("Admin: could not bind HTTP port; UI disabled")
            return
        self.listener_port = bound
        self.fw.set_output("ListenPort", float(bound))
        self.debug.set("Listener port", float(bound))

        # Register this instance for cross-LBS access.
        _admin_instance_ref["instance"] = self

        # Arm the discovery tick.
        self.fw.set_timer("Tick", 10)
        self._publish_counters()

    def on_calc(self, inputs):
        # Allow the integrator to re-tune at runtime; HTTP server stays bound.
        self._auto_discover_s = max(60, int(inputs["AutoDiscoverInterval"].value or DEFAULT_AUTO_DISCOVER_S))
        self._discovery_timeout = max(1, int(inputs["DiscoveryTimeout"].value or 4))
        if inputs["TriggerDiscovery"].changed and inputs["TriggerDiscovery"].value:
            self._spawn_discovery()

    def on_timer(self, timer):
        if timer["Tick"].changed:
            self.fw.set_timer("Tick", self._auto_discover_s)
            self._spawn_discovery()

    # ----- HTTP server lifecycle ------------------------------------------

    def _start_server(self, preferred_port):
        # Try the preferred port first, then the default fallback range, then
        # an OS-assigned ephemeral port as a last resort.
        candidates = []
        if preferred_port:
            candidates.append(preferred_port)
        for p in range(ADMIN_PORT_DEFAULT, ADMIN_PORT_MAX + 1):
            if p != preferred_port:
                candidates.append(p)
        candidates.append(0)  # ephemeral
        for port in candidates:
            try:
                server = HTTPServer(("0.0.0.0", port), _AdminHandler)
            except OSError:
                continue
            server.admin = self
            self.server = server
            self.server_thread = threading.Thread(
                target=server.serve_forever,
                name="sonos-admin-http",
                daemon=True,
            )
            self.server_thread.start()
            return port
        return None

    # ----- Discovery -------------------------------------------------------

    def _spawn_discovery(self):
        threading.Thread(target=self._run_discovery, daemon=True).start()

    def _run_discovery(self):
        try:
            discovered = scan_and_merge(self._discovery_timeout)
            self._last_discovery_at = time.time()
            self._last_discovered = discovered
            self.fw.run_in_context(self._post_discovery, (discovered,))
        except Exception as exc:  # noqa: BLE001
            self.fw.run_in_context(self._write_error, ("DISCOVERY: " + str(exc),))

    def _post_discovery(self, discovered):
        n = len(discovered)
        if self.debug is not None:
            self.debug.set("Last discovery", time.strftime("%H:%M:%S"))
            self.debug.set("Discovered", float(n))
        # KNX-friendly outputs: newline-separated ip;uuid;model + count.
        text = "\n".join("{};{};{}".format(d["ip"], d["uuid"], d["model"]) for d in discovered)
        self.fw.set_output("DiscoveredPlayers", text.encode("iso-8859-15", "replace"))
        self.fw.set_output("LastDiscoveryCount", float(n))
        self._publish_counters()

    def _publish_counters(self):
        with _registry_lock:
            np = len(_players)
            ns = len(_stations)
            authed = bool(_cloud.get("accessToken"))
        self.fw.set_output("PlayerCount", float(np))
        self.fw.set_output("StationCount", float(ns))
        self.fw.set_output("CloudAuthorized", 1 if authed else 0)
        if self.debug is not None:
            self.debug.set("Players", float(np))
            self.debug.set("Stations", float(ns))
            self.debug.set("Cloud authorized", "yes" if authed else "no")

    def _write_error(self, msg):
        self.fw.set_output("LastError", msg.encode("iso-8859-15", "replace"))

    def _decode(self, value):
        if value is None:
            return ""
        if isinstance(value, bytes):
            return value.decode("iso-8859-15", errors="replace")
        return str(value)

    # ----- HTTP-handler-facing API ----------------------------------------

    def api_info(self):
        with _registry_lock:
            return {
                "ok": True,
                "listenPort": self.listener_port,
                "playerCount": len(_players),
                "stationCount": len(_stations),
                "cloudAuthorized": bool(_cloud.get("accessToken")),
                "lastDiscoveryAt": (time.strftime("%Y-%m-%dT%H:%M:%S",
                                                  time.localtime(self._last_discovery_at))
                                    if self._last_discovery_at else None),
            }

    def api_list_players(self):
        with _registry_lock:
            players = list(_players.values())
        return {
            "ok": True,
            "players": sorted(players, key=lambda p: (p.get("name") or "", p.get("ip") or "")),
            "lastDiscoveryAt": (time.strftime("%Y-%m-%dT%H:%M:%S",
                                              time.localtime(self._last_discovery_at))
                                if self._last_discovery_at else None),
        }

    def api_list_stations(self):
        with _registry_lock:
            stations = list(_stations.values())
        return {"ok": True, "stations": sorted(stations, key=lambda s: s["name"].lower())}

    def api_add_player(self, body):
        name = (body.get("name") or "").strip()
        ip = (body.get("ip") or "").strip()
        mac = _norm_mac(body.get("mac") or "")
        if not ip and not mac:
            raise ValueError("provide ip and/or mac")
        if ip and not _is_ip(ip):
            raise ValueError("invalid IP")
        # If only MAC given, try to resolve current IP.
        if not ip and mac:
            ip = _resolve_ip_for_mac(mac)
        # If only IP given, try to resolve MAC for DHCP-resilience.
        if ip and not mac:
            mac = _resolve_mac_for_ip(ip)
        rec = {
            "id": mac or ip,
            "name": name,
            "ip": ip,
            "mac": mac,
            "uuid": "",
            "model": "",
            "source": "manual",
            "lastSeen": time.time(),
        }
        with _registry_lock:
            _players[rec["id"]] = rec
        self._publish_counters()
        return {"ok": True, "player": rec}

    def api_remove_player(self, pid):
        with _registry_lock:
            removed = _players.pop(pid, None)
        if removed is None:
            raise ValueError("unknown player id")
        self._publish_counters()
        return {"ok": True}

    def api_update_player(self, pid, body):
        with _registry_lock:
            rec = _players.get(pid)
            if rec is None:
                raise ValueError("unknown player id")
            if "name" in body:
                rec["name"] = (body["name"] or "").strip()
            if "ip" in body:
                v = (body["ip"] or "").strip()
                if v and not _is_ip(v):
                    raise ValueError("invalid IP")
                rec["ip"] = v
            if "mac" in body:
                v = _norm_mac(body["mac"] or "")
                if body["mac"] and not v:
                    raise ValueError("invalid MAC")
                rec["mac"] = v
        return {"ok": True}

    def api_discover_now(self):
        discovered = scan_and_merge(self._discovery_timeout)
        self._last_discovery_at = time.time()
        self._last_discovered = discovered
        # Mirror the same outputs the periodic scan publishes so KNX-side
        # consumers see the result regardless of who triggered the scan.
        self.fw.run_in_context(self._post_discovery, (discovered,))
        return {"ok": True, "discovered": len(discovered)}

    def api_add_station(self, body):
        name = (body.get("name") or "").strip()
        uri = (body.get("uri") or "").strip()
        if not name or not uri:
            raise ValueError("name and uri required")
        sid = "s_" + str(int(time.time() * 1000))
        rec = {"id": sid, "name": name, "uri": uri}
        with _registry_lock:
            _stations[sid] = rec
        self._publish_counters()
        return {"ok": True, "station": rec}

    def api_update_station(self, sid, body):
        with _registry_lock:
            rec = _stations.get(sid)
            if rec is None:
                raise ValueError("unknown station id")
            if "name" in body:
                rec["name"] = (body["name"] or "").strip()
            if "uri" in body:
                rec["uri"] = (body["uri"] or "").strip()
        return {"ok": True}

    def api_remove_station(self, sid):
        with _registry_lock:
            removed = _stations.pop(sid, None)
        if removed is None:
            raise ValueError("unknown station id")
        self._publish_counters()
        return {"ok": True}

    def api_get_cloud(self):
        with _registry_lock:
            now = time.time()
            expires_in = max(0, int(_cloud.get("expiresAt", 0) - now)) if _cloud.get("accessToken") else 0
            return {
                "ok": True,
                "cloud": {
                    "clientId": _cloud.get("clientId", ""),
                    "clientSecretSet": bool(_cloud.get("clientSecret")),
                    "redirectBase": _cloud.get("redirectBase", ""),
                    "authorized": bool(_cloud.get("accessToken")),
                    "expiresIn": expires_in,
                },
            }

    def api_set_cloud(self, body):
        with _registry_lock:
            if "clientId" in body:
                _cloud["clientId"] = (body["clientId"] or "").strip()
            if "clientSecret" in body:
                _cloud["clientSecret"] = (body["clientSecret"] or "").strip()
            if "redirectBase" in body:
                _cloud["redirectBase"] = (body["redirectBase"] or "").rstrip("/")
        return {"ok": True}

    def oauth_start_params(self):
        with _registry_lock:
            if not (_cloud.get("clientId") and _cloud.get("clientSecret") and _cloud.get("redirectBase")):
                return None
            return {
                "client_id": _cloud["clientId"],
                "response_type": "code",
                "state": "sonos-admin",
                "scope": "playback-control-all",
                "redirect_uri": _cloud["redirectBase"].rstrip("/") + "/oauth/callback",
            }

    def oauth_exchange(self, code):
        with _registry_lock:
            client_id = _cloud.get("clientId", "")
            client_secret = _cloud.get("clientSecret", "")
            redirect_uri = _cloud.get("redirectBase", "").rstrip("/") + "/oauth/callback"
        if not (client_id and client_secret and redirect_uri):
            return False, "cloud not configured"
        try:
            resp = requests.post(
                SONOS_TOKEN_URL,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                },
                auth=(client_id, client_secret),
                timeout=10,
            )
        except requests.exceptions.RequestException as exc:
            return False, "network error: {}".format(exc)
        if resp.status_code >= 400:
            return False, "Sonos rejected: HTTP {} {}".format(resp.status_code, resp.text[:200])
        try:
            data = resp.json()
        except ValueError:
            return False, "Sonos response not JSON"
        access = data.get("access_token", "")
        refresh = data.get("refresh_token", "")
        expires_in = int(data.get("expires_in", 0))
        if not access:
            return False, "no access_token in response"
        with _registry_lock:
            _cloud["accessToken"] = access
            _cloud["refreshToken"] = refresh
            _cloud["expiresAt"] = time.time() + expires_in
        # Update outputs.
        self.fw.run_in_context(self._publish_counters, ())
        return True, "Sonos Cloud authorized for {} seconds".format(expires_in)


# ---------------------------------------------------------------------------
# Public helpers other LBS modules can call (via direct attribute access on
# this module's globals).
# ---------------------------------------------------------------------------

__all__ = [
    "LogicModule",
    "resolve_host",
    "get_station_uri",
    "discover_and_merge",
]
