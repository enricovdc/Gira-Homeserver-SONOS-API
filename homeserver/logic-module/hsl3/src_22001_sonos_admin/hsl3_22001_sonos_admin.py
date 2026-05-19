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


def get_player_record(spec):
    """Return the full registry record for a player identified by IP,
    MAC, UUID, or custom name (case-insensitive). Returns ``None`` when
    Admin doesn't know the player yet. LBS 22000 calls this to surface
    ``zoneName``, ``model`` and other discovery-derived metadata on its
    outputs."""
    if not spec:
        return None
    spec = str(spec).strip()
    if not spec:
        return None
    norm = _norm_mac(spec)
    with _registry_lock:
        if _is_ip(spec):
            for rec in _players.values():
                if rec.get("ip") == spec:
                    return dict(rec)
            return None
        if norm:
            for rec in _players.values():
                if rec.get("mac") == norm:
                    return dict(rec)
            return None
        lower = spec.lower()
        for rec in _players.values():
            if rec.get("name", "").lower() == lower or rec.get("uuid") == spec:
                return dict(rec)
    return None


def get_station_uri(index_or_name):
    """Backwards-compat shim. Prefer get_station() which returns the full
    record including the DIDL-Lite metadata required for Sonos cloud
    favorites (TuneIn, Spotify, Apple Music). Returns '' when not found."""
    rec = get_station(index_or_name)
    return rec["uri"] if rec else ""


def get_station(index_or_name):
    """LBS 22000 calls this to fetch the full station record. Returns None
    when not found. The returned dict has the keys ``id``, ``name``,
    ``uri`` and ``metadata`` — the last carries the DIDL-Lite XML
    captured from a Sonos favorite so cloud-service items play correctly
    (their music-service binding lives in the metadata, not in the URI)."""
    if index_or_name is None:
        return None
    key = str(index_or_name).strip()
    with _registry_lock:
        # Numeric: index into sorted list.
        if key.isdigit():
            idx = int(key)
            sorted_stations = sorted(_stations.values(), key=lambda s: s["name"].lower())
            if 1 <= idx <= len(sorted_stations):
                return dict(sorted_stations[idx - 1])
            return None
        # Name match (case-insensitive).
        for rec in _stations.values():
            if rec["name"].lower() == key.lower():
                return dict(rec)
    return None


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
            info = _fetch_device_info(location) if location else {"model": "", "zoneName": ""}
            found[ip] = {"ip": ip, "uuid": uuid, "model": info["model"], "zoneName": info["zoneName"]}
        return list(found.values())
    finally:
        try:
            sock.close()
        except Exception:
            pass


def _fetch_device_info(location):
    """Pull the player's friendly room name + model from its UPnP device
    description XML. The Sonos app's "Zone name" is exposed as
    <roomName>...</roomName> in /xml/device_description.xml. Returns an
    empty dict on failure so discovery still works for offline players."""
    try:
        resp = requests.get(location, timeout=2)
        text = resp.text
    except Exception:
        return {"model": "", "zoneName": ""}
    model_m = re.search(r"<modelName>([^<]+)</modelName>", text)
    room_m = re.search(r"<roomName>([^<]+)</roomName>", text)
    return {
        "model": model_m.group(1) if model_m else "",
        "zoneName": room_m.group(1) if room_m else "",
    }


def scan_and_merge(timeout_sec=4):
    """Run an SSDP scan; merge results into the registry. Manually-added
    players keep their `source = "manual"` flag and are not overwritten.
    Returns the raw scan list."""
    discovered = _ssdp_scan(timeout_sec)
    arp = _read_arp_table()
    now = time.time()
    with _registry_lock:
        for d in discovered:
            mac = arp.get(d["ip"], "") or _resolve_mac_for_ip(d["ip"])
            rec = {
                "id": d.get("uuid") or mac or d["ip"],
                "name": "",
                "zoneName": d.get("zoneName", ""),
                "ip": d["ip"],
                "mac": mac,
                "uuid": d["uuid"],
                "model": d["model"],
                "source": "ssdp",
                "lastSeen": now,
            }
            existing = None
            for pid, p in _players.items():
                if (d["uuid"] and p.get("uuid") == d["uuid"]) or \
                   (mac and p.get("mac") == mac) or \
                   p.get("ip") == d["ip"]:
                    existing = pid
                    break
            if existing:
                # Refresh IP (DHCP may have renumbered), keep custom name + source.
                _players[existing]["ip"] = d["ip"]
                if mac:
                    _players[existing]["mac"] = mac
                if d["uuid"] and not _players[existing].get("uuid"):
                    _players[existing]["uuid"] = d["uuid"]
                if d["model"]:
                    _players[existing]["model"] = d["model"]
                if d.get("zoneName"):
                    # roomName tracks renames in the Sonos app — always refresh.
                    _players[existing]["zoneName"] = d["zoneName"]
                _players[existing]["lastSeen"] = now
            else:
                _players[rec["id"]] = rec
    return discovered


def discover_and_merge(timeout_sec=4):
    """Backwards-compat wrapper kept so older callers see an int count."""
    return len(scan_and_merge(timeout_sec))


# ---------------------------------------------------------------------------
# ContentDirectory#Browse — used to walk a player's saved favorites,
# playlists, and other content. The user already curated this list in the
# Sonos app; surfacing it in the Admin UI is much friendlier than asking
# the integrator to paste raw stream URIs.
# ---------------------------------------------------------------------------

ENV_BROWSE = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
    '<s:Body><u:Browse xmlns:u="urn:schemas-upnp-org:service:ContentDirectory:1">'
    "<ObjectID>{object_id}</ObjectID>"
    "<BrowseFlag>BrowseDirectChildren</BrowseFlag>"
    "<Filter>*</Filter>"
    "<StartingIndex>{start}</StartingIndex>"
    "<RequestedCount>{count}</RequestedCount>"
    "<SortCriteria></SortCriteria>"
    "</u:Browse></s:Body></s:Envelope>"
)


def _unescape_xml(s):
    """XML-entity decode. Sonos wraps Browse responses in double XML
    escaping — call this twice for the inner DIDL-Lite payload."""
    if s is None:
        return ""
    return (s.replace("&lt;", "<")
             .replace("&gt;", ">")
             .replace("&quot;", '"')
             .replace("&apos;", "'")
             .replace("&amp;", "&"))


def _extract_tag(xml, tag):
    """Return the inner text of <tag>...</tag> (handles namespaced tags)
    or '' when not present. Does NOT XML-unescape — callers decide."""
    m = re.search(r"<{t}[^>]*>([\s\S]*?)</{t}>".format(t=re.escape(tag)), xml)
    return m.group(1) if m else ""


def _parse_didl_items(didl_xml):
    """Pull out every <item> AND <container> block from a DIDL-Lite envelope.

    Sonos favorites are wrapped in the generic class
    ``object.itemobject.item.sonos-favorite`` — to know what the
    favorite actually IS (radio / playlist / album / track) we have to
    peek at the inner ``<upnp:class>`` carried inside ``<r:resMD>``.
    For each item we capture title, both class strings, the playback
    URI, and the music-service metadata verbatim. resMD must reach the
    player UNCHANGED — it carries the cdudn music-service binding.

    Sonos Playlists (``SQ:`` browse) appear as ``<container>`` blocks
    instead of ``<item>``; we extract them with the same structure
    minus resMD (built synthetically so the player can still queue
    the playlist URI)."""
    items = []
    # Items first (favorites, tracks, radio).
    for m in re.finditer(r"<item\b[^>]*>([\s\S]*?)</item>", didl_xml):
        inner = m.group(1)
        title = _unescape_xml(_extract_tag(inner, "dc:title"))
        outer_class = _extract_tag(inner, "upnp:class")
        res = _unescape_xml(_extract_tag(inner, "res"))
        res_md_raw = _extract_tag(inner, "r:resMD")
        res_md = _unescape_xml(res_md_raw) if res_md_raw else ""
        # Peek inside resMD for the wrapped class. A Sonos favorite has
        # outer class "...sonos-favorite" and the real type lives in the
        # inner DIDL-Lite, e.g. "object.container.playlistContainer" for
        # a Spotify playlist favorite.
        inner_class = ""
        if res_md:
            mi = re.search(r"<upnp:class>([^<]+)</upnp:class>", res_md)
            if mi:
                inner_class = mi.group(1)
        effective_class = inner_class or outer_class
        if not (title and res):
            continue
        items.append({
            "title": title,
            "class": effective_class,
            "uri": res,
            "metadata": res_md,
            "type": _classify(effective_class, res),
        })
    # Containers (Sonos Playlists from SQ: browse).
    for m in re.finditer(r"<container\b[^>]*id=\"([^\"]+)\"[^>]*>([\s\S]*?)</container>", didl_xml):
        cid, inner = m.group(1), m.group(2)
        title = _unescape_xml(_extract_tag(inner, "dc:title"))
        upnp_class = _extract_tag(inner, "upnp:class") or "object.container.playlistContainer"
        # SQ: containers carry a queue URI of the form `file:///jffs/settings/savedqueues.rsq#NN`
        # accessible via x-rincon-queue:RINCON_UUID#0 — easier to use SetAVTransportURI directly
        # with the container ID via x-rincon-cpcontainer:.
        # For most SQ:NN entries Sonos accepts SetAVTransportURI with the SQ:NN URI directly.
        uri = "file:///jffs/settings/savedqueues.rsq#" + cid.split(":")[-1] if cid.startswith("SQ:") else cid
        if not title:
            continue
        items.append({
            "title": title,
            "class": upnp_class,
            "uri": uri,
            "metadata": "",
            "type": "playlist",
        })
    return items


def _classify(upnp_class, uri):
    """Friendly category label so the UI can group items. Looks at the
    UPnP class first (most reliable when the SDK-style inner class is
    present) and falls back to URI-scheme heuristics for items where the
    class is just the generic sonos-favorite wrapper."""
    c = (upnp_class or "").lower()
    if "audiobroadcast" in c:
        return "radio"
    if "playlistcontainer" in c:
        return "playlist"
    if "album.musicalbum" in c or "musicalbum" in c:
        return "album"
    if "musictrack" in c:
        return "track"
    # URI-scheme fallbacks for sonos-favorite wrappers with no inner class.
    if uri.startswith("x-rincon-mp3radio") or uri.startswith("x-sonosapi-stream"):
        return "radio"
    if uri.startswith("x-rincon-cpcontainer"):
        # Spotify / Apple Music / Amazon playlists or albums.
        return "playlist"
    if uri.startswith("x-sonos-spotify") or uri.startswith("x-sonos-http") \
            or uri.startswith("x-file-cifs"):
        return "track"
    if uri.startswith("file:///jffs"):
        return "playlist"  # Sonos saved queue
    return "other"


def browse_content(host, object_id="FV:2", start=0, count=200, timeout=5):
    """Run ContentDirectory#Browse against the player. Returns the parsed
    item list (possibly empty on errors — we never raise to the API layer
    because a slow / offline player must not crash the web UI)."""
    if not host:
        return []
    headers = {
        "Content-Type": 'text/xml; charset="utf-8"',
        "SOAPACTION": '"urn:schemas-upnp-org:service:ContentDirectory:1#Browse"',
    }
    envelope = ENV_BROWSE.format(object_id=object_id, start=start, count=count)
    try:
        resp = requests.post(
            "http://{}:1400/MediaServer/ContentDirectory/Control".format(host),
            data=envelope.encode("utf-8"),
            headers=headers,
            timeout=timeout,
        )
    except requests.exceptions.RequestException:
        return []
    if resp.status_code != 200:
        return []
    # The response Result is XML-escaped DIDL-Lite text wrapped in <Result>.
    result_match = re.search(r"<Result>([\s\S]*?)</Result>", resp.text)
    if not result_match:
        return []
    didl = _unescape_xml(result_match.group(1))
    return _parse_didl_items(didl)


# ---------------------------------------------------------------------------
# HTTP server
# ---------------------------------------------------------------------------

INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sonos Admin - Gira HomeServer</title>
<style>
/* Gira HomeServer base palette + typography, mirrored from /main-site/hs.css */
html { overflow-y: scroll; }
*, *::before, *::after { box-sizing: border-box; }
body { margin: 0; background-color: rgb(230,230,230); color: rgb(32,32,32);
       font-family: 'Univers Next W1G', 'Verdana', 'Helvetica', 'Arial', sans-serif;
       font-size: 12px; padding: 0; }
h1 { font-weight: normal; font-size: 39px; margin: 0; }
h2 { font-weight: 400; font-size: 18px; margin: 20px 0; }
h3 { font-weight: 400; font-size: 14px; margin: 0 0 12px; }
p  { font-size: 12px; }
a  { color: #969696; text-decoration: none; }

.container { position: relative; left: 0; width: 939px; margin: 0 auto; text-align: left; }
@media only screen and (max-width: 1023px) {
  .container { width: auto; }
}
.content { background-color: white; min-width: 0; overflow-x: hidden; }
.header  { margin: 0 30px; border-bottom: 1px solid #c0c0c0;
           margin-bottom: 30px; padding: 37px 0; }
.header > img { display: inline-block; width: 120px; height: 30px;
                margin-right: 20px; vertical-align: middle; }
.header > h1  { display: inline-block; vertical-align: middle; }
.block { padding: 0 30px; }

/* Section ("group") cards in the body */
section { margin-bottom: 30px; }
section > h2 { border-bottom: 1px solid #c0c0c0; padding-bottom: 8px; }
section > .muted { color: #808080; font-size: 12px; margin: 0 0 12px; }

/* Tables */
table { width: 100%; border-collapse: collapse; font-size: 12px;
        border-top: 1px solid #c0c0c0; border-bottom: 1px solid #c0c0c0; }
th, td { text-align: left; padding: 8px 6px; vertical-align: middle; }
th { background: #f5f5f5; font-weight: 400; border-bottom: 1px solid #c0c0c0;
     color: #505050; }
tr:not(:last-child) > td { border-bottom: 1px solid #e8e8e8; }

/* Form controls */
input[type=text], input[type=url], input[type=number] {
  width: 100%; padding: 6px 8px; border: 1px solid #c0c0c0;
  font: inherit; box-sizing: border-box; background: white;
}
input:focus { outline: 1px solid #BACE00; }

/* Gira buttons: pill-shaped, dark gray with green hover */
button {
  border: 0; border-radius: 25px; min-width: 100px; height: 36px;
  margin: 6px 8px 6px 0; padding: 0 18px;
  background-color: #505050; color: white; cursor: pointer;
  font: inherit; font-size: 12px;
}
button:hover    { background-color: #BACE00; color: #323232; }
button:disabled { background-color: #d0d0d0; color: #505050; cursor: not-allowed; }
button.danger   { background-color: #a83232; min-width: 36px; padding: 0 10px; }
button.danger:hover  { background-color: #BACE00; color: #323232; }
button.secondary     { background-color: #707070; }
button.small         { min-width: 80px; height: 28px; font-size: 11px; padding: 0 12px; margin: 0 4px 0 0; }

/* Inline form row under each table */
.row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-top: 12px; }

/* Source indicator (replaces the old colored pills) */
.pill { display: inline-block; padding: 2px 10px; font-size: 11px;
        font-weight: 400; border: 1px solid #c0c0c0; color: #505050;
        background: white; }
.pill.ssdp   { border-color: #BACE00; color: #5a6a00; }
.pill.manual { border-color: #707070; color: #404040; }

/* Toast: subtle Gira-styled bottom-right banner */
.toast { position: fixed; right: 30px; bottom: 30px; background: #505050;
         color: white; padding: 10px 20px; box-shadow: 0 2px 8px rgba(0,0,0,0.25);
         display: none; font-size: 12px; }
.toast.error { background: #a83232; }

code { background: #f5f5f5; padding: 1px 6px; border: 1px solid #e8e8e8;
       font-family: ui-monospace, 'Courier New', monospace; font-size: 0.85em;
       word-break: break-all; }

/* Player card list: one block per Sonos player, laid out so the wide
   UUID can wrap inside its container instead of pushing the table
   beyond the 939px Gira frame. */
.player-list { border-top: 1px solid #c0c0c0; }
.player-card { border-bottom: 1px solid #c0c0c0; padding: 12px 0; }
.player-card .pc-head { display: flex; align-items: baseline; gap: 12px;
                        margin-bottom: 6px; flex-wrap: wrap; }
.player-card .pc-zone { font-size: 14px; font-weight: 400; color: #202020;
                        flex: 1 1 auto; min-width: 0; }
.player-card .pc-zone:empty::before { content: "(no Sonos zone name)";
                                      color: #a0a0a0; font-style: italic; }
.player-card .pc-uuid { font-family: ui-monospace, 'Courier New', monospace;
                        font-size: 11px; color: #707070; word-break: break-all; }
.player-card .pc-grid { display: grid; gap: 6px 16px; align-items: center;
                        grid-template-columns: minmax(0, 1fr) minmax(0, 1fr)
                                               minmax(0, 1fr) auto; }
.player-card .pc-field { min-width: 0; }
.player-card .pc-field label { display: block; font-size: 11px; color: #808080;
                               margin-bottom: 2px; }
.player-card .pc-field input { width: 100%; }
.player-card .pc-actions { display: flex; gap: 4px; align-items: end; }
.player-card .pc-meta { margin-top: 6px; font-size: 11px; color: #808080;
                        display: flex; gap: 12px; flex-wrap: wrap; }
.player-card .pc-meta .pc-model { color: #505050; }
@media only screen and (max-width: 720px) {
  .player-card .pc-grid { grid-template-columns: 1fr 1fr; }
  .player-card .pc-actions { grid-column: 1 / -1; justify-content: flex-end; }
}
/* Inline favorites pane, shown when "Favorites" is clicked on a card */
.pc-favs:empty { display: none; }
.pc-favs { margin-top: 10px; padding: 8px 12px; background: #f5f5f5;
           border: 1px solid #e0e0e0; }
.favs-head { font-size: 11px; color: #505050; margin-bottom: 6px;
             padding-bottom: 4px; border-bottom: 1px solid #e0e0e0; }
.favs-head .muted { font-weight: normal; color: #808080; }
.favs-empty { font-size: 12px; color: #808080; font-style: italic; padding: 4px 0; }
.fav-row { display: grid; grid-template-columns: 60px 1fr auto;
           gap: 10px; align-items: center; padding: 4px 0;
           border-bottom: 1px solid #ececec; }
.fav-row:last-child { border-bottom: 0; }
.fav-type { font-size: 10px; text-transform: uppercase; color: #707070;
            letter-spacing: 0.5px; }
.fav-title { font-size: 12px; color: #202020; word-break: break-word; }

/* Two-column "grid" used by the Cloud section */
.grid { display: grid; grid-template-columns: 224px 1fr; gap: 6px 12px;
        margin-bottom: 8px; }
.grid > label { font-size: 12px; color: #505050; align-self: center; }

/* Footers, mirroring the HS index page */
.footer1 { display: flex; background-color: #f5f5f5; padding: 10px 30px;
           font-size: 14px; height: 24px; line-height: 24px; }
.footer1 > .left  { display: inline; width: 60%; }
.footer1 > .right { display: inline; width: 40%; text-align: right; }
.footer2 { background-color: rgb(230,230,230); padding: 10px 30px;
           font-size: 12px; color: #808080; }
.footer2 > a { color: inherit; text-decoration: none; margin-right: 18px; }
</style>
</head>
<body>
<div class="container">
<div class="content">
<div class="header">
  <h1>Sonos Admin</h1>
</div>
<div class="block">
  <section>
    <h2>Players</h2>
    <div id="players-state" class="muted">Loading...</div>
    <p class="muted">For the Sonos Player block's <code>Host</code> input,
       prefer the <strong>UUID</strong> (stable across firmware updates and
       DHCP renumbering). Click the <em>Use as Host</em> button on a row to
       copy a value to the clipboard.</p>
    <div id="players" class="player-list"></div>
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
    <p class="muted">
      Wire one of the Sonos Player block's two station-trigger inputs:
      write the <strong>#</strong> shown below into <code>StartRadio</code>
      (numeric), or write the <strong>Name</strong> into
      <code>StartRadioName</code> (string, case-insensitive).
      Adding or removing stations re-numbers the index list alphabetically.
    </p>
    <table id="stations">
      <thead><tr>
        <th style="width:6%">#</th>
        <th style="width:24%">Name</th>
        <th>Stream URI</th>
        <th style="width:8%">Meta</th>
        <th style="width:10%">Actions</th>
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
</div>
<div class="footer1">
  <div class="left">Sonos Admin &middot; LBS 22001</div>
  <div class="right"><a href="/info" target="_blank">/info</a></div>
</div>
<div class="footer2">
  <a href="/api/players">/api/players</a>
  <a href="/api/stations">/api/stations</a>
  <a href="/api/cloud">/api/cloud</a>
  <a href="/tile.html">/tile.html</a>
</div>
</div>
</div>
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
  const list = document.getElementById('players');
  list.innerHTML = '';
  for (const p of r.players) {
    const zone = p.zoneName || '';
    const uuid = p.uuid || '';
    // The "Use as Host" button copies the most stable identifier available
    // (UUID > MAC > IP) into the clipboard for pasting into the Sonos
    // Player block's Host input.
    const hostValue = uuid || p.mac || p.ip || '';
    const card = document.createElement('div');
    card.className = 'player-card';
    card.innerHTML =
      '<div class="pc-head">' +
        '<div class="pc-zone">' + esc(zone) + '</div>' +
        '<span class="pill ' + esc(p.source) + '">' + esc(p.source) + '</span>' +
      '</div>' +
      '<div class="pc-uuid">' + (uuid ? esc(uuid) : '<em>no UUID yet</em>') + '</div>' +
      '<div class="pc-grid" style="margin-top:8px">' +
        '<div class="pc-field">' +
          '<label>Custom name</label>' +
          '<input data-edit="' + esc(p.id) + '" data-field="name" value="' + esc(p.name) + '" placeholder="(none)">' +
        '</div>' +
        '<div class="pc-field">' +
          '<label>IP</label>' +
          '<input data-edit="' + esc(p.id) + '" data-field="ip" value="' + esc(p.ip) + '">' +
        '</div>' +
        '<div class="pc-field">' +
          '<label>MAC</label>' +
          '<input data-edit="' + esc(p.id) + '" data-field="mac" value="' + esc(p.mac) + '">' +
        '</div>' +
        '<div class="pc-actions">' +
          '<button class="secondary small" data-host="' + esc(hostValue) + '" title="Copy Host value">Use as Host</button>' +
          '<button class="secondary small" data-favs="' + esc(p.id) + '" title="Browse Sonos Favorites on this player">Favorites</button>' +
          '<button class="danger small" data-del-player="' + esc(p.id) + '" title="Remove">x</button>' +
        '</div>' +
      '</div>' +
      '<div class="pc-meta">' +
        (p.model ? '<span class="pc-model">' + esc(p.model) + '</span>' : '') +
      '</div>' +
      '<div class="pc-favs" id="favs-' + esc(p.id) + '"></div>';
    list.appendChild(card);
  }
  if (r.players.length === 0) {
    list.innerHTML = '<div class="player-card" style="text-align:center;color:#808080">' +
      'No players yet. Click <strong>Scan now (SSDP)</strong> or add one manually below.' +
      '</div>';
  }
  document.getElementById('players-state').textContent =
    r.players.length + ' player(s); last scan ' + (r.lastDiscoveryAt || 'never') + '.';
}
async function refreshStations() {
  const r = await api('GET', '/api/stations');
  const tbody = document.querySelector('#stations tbody');
  tbody.innerHTML = '';
  // The Admin sorts stations alphabetically by name (case-insensitive)
  // before exposing them to LBS 22000's get_station(idx). Renders the
  // same order here so the # column matches what the Player block sees.
  const sorted = [...r.stations].sort(
    (a, b) => a.name.toLowerCase().localeCompare(b.name.toLowerCase())
  );
  sorted.forEach((s, i) => {
    const tr = document.createElement('tr');
    const hasMeta = !!(s.metadata && s.metadata.length > 0);
    tr.innerHTML =
      '<td><strong>' + (i + 1) + '</strong></td>' +
      '<td><input data-sedit="' + esc(s.id) + '" data-field="name" value="' + esc(s.name) + '"></td>' +
      '<td><input data-sedit="' + esc(s.id) + '" data-field="uri"  value="' + esc(s.uri)  + '"></td>' +
      '<td>' + (hasMeta ? '<span class="pill ssdp" title="Has music-service metadata">yes</span>' : '<span class="muted">—</span>') + '</td>' +
      '<td><button class="danger small" data-del-station="' + esc(s.id) + '">x</button></td>';
    tbody.appendChild(tr);
  });
  if (!sorted.length) {
    tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;color:#808080;padding:12px">' +
      'No stations yet. Add one above, or click <strong>Favorites</strong> on a player to import.' +
      '</td></tr>';
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
    if (t.dataset.host !== undefined) {
      // Copy the player's UUID/MAC/IP to the clipboard so the integrator
      // can paste it directly into the Sonos Player block's Host input.
      const v = t.dataset.host || '';
      if (!v) return toast('No identifier available', true);
      try { await navigator.clipboard.writeText(v); toast('Copied: ' + v); }
      catch (e) {
        // Older browsers without clipboard API: show the value so the user
        // can copy it manually.
        prompt('Copy this value into the Sonos Player Host input:', v);
      }
    }
    if (t.dataset.favs) {
      await toggleFavorites(t.dataset.favs, t);
    }
    if (t.dataset.addFavStation) {
      // Encoded payload {title, uri, metadata} (base64-JSON, URI-safe).
      const payload = JSON.parse(decodeURIComponent(escape(atob(t.dataset.addFavStation))));
      await api('POST', '/api/stations', payload);
      toast('Added: ' + payload.name);
      refreshStations();
    }
  } catch (e) { toast(e.message, true); }
});

async function toggleFavorites(pid, btn) {
  const container = document.getElementById('favs-' + pid);
  if (!container) return;
  if (container.dataset.open === '1') {
    container.innerHTML = '';
    container.dataset.open = '0';
    btn.textContent = 'Favorites';
    return;
  }
  btn.textContent = 'Loading...';
  // Fetch Sonos Favorites (FV:2) AND Sonos Playlists (SQ:) in parallel.
  // Favorites carries cloud-bound items the user explicitly saved;
  // Playlists carries the Sonos queues a user saved (which include
  // Spotify / Apple Music playlists that aren't favorited but are
  // queued).
  let favs, pls;
  try {
    [favs, pls] = await Promise.all([
      api('GET', '/api/players/' + encodeURIComponent(pid) + '/favorites'),
      api('GET', '/api/players/' + encodeURIComponent(pid) + '/playlists').catch(() => ({playlists: []})),
    ]);
  } catch (e) { btn.textContent = 'Favorites'; toast(e.message, true); return; }
  btn.textContent = 'Hide';
  container.dataset.open = '1';

  const all = [
    ...(favs.favorites || []).map(f => ({...f, group: 'Sonos Favorites'})),
    ...(pls.playlists || []).map(f => ({...f, group: 'Sonos Playlists'})),
  ];

  if (!all.length) {
    container.innerHTML = '<div class="favs-empty">' +
      'Nothing here yet. In the Sonos app: long-press a station, playlist, or ' +
      'album and choose "Add to Sonos Favourites" to make it appear here.' +
      '</div>';
    return;
  }

  // Group rendering: each source becomes its own header + rows.
  let html = '';
  const byGroup = {};
  for (const item of all) (byGroup[item.group] ||= []).push(item);
  for (const groupName of Object.keys(byGroup)) {
    const list = byGroup[groupName];
    html += '<div class="favs-head">' + esc(groupName) +
            ' <span class="muted">(' + list.length + ')</span></div>';
    for (const f of list) {
      // Encode the whole record so the Add click handler can POST it back
      // unchanged (preserving the music-service metadata verbatim).
      const payload = btoa(unescape(encodeURIComponent(JSON.stringify({
        name: f.title, uri: f.uri, metadata: f.metadata
      }))));
      html += '<div class="fav-row">' +
                '<span class="fav-type">' + esc(f.type || 'other') + '</span>' +
                '<span class="fav-title">' + esc(f.title) + '</span>' +
                '<button class="small" data-add-fav-station="' + payload + '">Add to stations</button>' +
              '</div>';
    }
  }
  container.innerHTML = html;
}
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


def _icon_svg():
    """A small Gira-grey speaker icon used in the tile fragment. Inline SVG
    so the integrator doesn't have to copy a separate image file into the
    HomeServer's /main-site/ tree."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 44" width="24" height="44">'
        '<rect x="2" y="2" width="20" height="40" rx="2" '
        'fill="none" stroke="#505050" stroke-width="1.5"/>'
        '<circle cx="12" cy="14" r="3.5" fill="none" stroke="#505050" stroke-width="1.5"/>'
        '<circle cx="12" cy="30" r="6" fill="none" stroke="#505050" stroke-width="1.5"/>'
        '<circle cx="12" cy="30" r="2" fill="#505050"/>'
        '</svg>'
    )


def _tile_fragment(base_url):
    """Return the HTML <a class="box"> tile a Gira integrator drops into
    /main-site/index.html alongside the existing tiles. Same structure as
    the built-in 'Manage Sonos Favorites' tile so the styling is automatic."""
    return (
        '<a id="config_sonos_admin" class="box" href="{url}" target="_blank">\n'
        '  <img class="box_img" src="{url}/icon.svg" />\n'
        '  <div class="title">Sonos Admin</div>\n'
        '  <div class="descr">Manage Sonos players, radio stations, and Cloud authorization.</div>\n'
        '  <div class="url">{url}</div>\n'
        '  <div class="link">&gt; call-up</div>\n'
        '</a>\n'
    ).format(url=base_url.rstrip("/"))


def _tile_page(base_url):
    """A complete, standalone Gira-styled page that consists of a single tile
    pointing at the Admin URL. Useful for iframe-embedding in the HomeServer
    visualisation or as a quick visual check that the styling works."""
    url = base_url.rstrip("/")
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<title>Sonos Admin tile</title>"
        "<style>"
        "html{overflow-y:scroll}"
        "body{margin:0;background:#e6e6e6;color:#202020;"
        "font-family:'Univers Next W1G','Verdana','Helvetica','Arial',sans-serif;font-size:12px}"
        ".container{width:939px;margin:0 auto}"
        "@media (max-width:1023px){.container{width:auto}}"
        ".content{background:white}"
        ".header{margin:0 30px;border-bottom:1px solid #c0c0c0;padding:37px 0;margin-bottom:30px}"
        ".header h1{margin:0;font-weight:normal;font-size:39px}"
        ".block{padding:0 30px 30px}"
        ".box_list{position:relative;display:flex;margin-bottom:30px;"
        "border-top:1px solid #c0c0c0;border-bottom:1px solid #c0c0c0;width:auto}"
        ".box{display:flex;width:293px;height:277px;position:relative;font-size:14px;"
        "text-align:left;cursor:pointer;border-right:1px solid #c0c0c0;overflow:hidden;"
        "color:#000;text-decoration:none}"
        ".box:hover{background:#f0f0f0}"
        ".box_img{position:absolute;left:10px;top:10px;width:24px;height:44px}"
        ".box .title{position:absolute;left:10px;top:69px;width:180px;height:21px;"
        "font-weight:400;color:#000}"
        ".box .descr{position:absolute;left:10px;top:118px;width:calc(100% - 20px);"
        "height:66px;font-size:12px;color:#ADABB1}"
        ".box .url{position:absolute;left:10px;top:178px;width:265px;font-size:12px;"
        "color:#ADABB1;overflow:hidden}"
        ".box .link{position:absolute;left:10px;top:246px;font-size:12px;color:#969696}"
        "</style></head><body>"
        "<div class=\"container\"><div class=\"content\">"
        "<div class=\"header\"><h1>Sonos</h1></div>"
        "<div class=\"block\"><div class=\"box_list\">"
        + _tile_fragment(url) +
        "</div></div></div></div></body></html>"
    )


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
            if path == "/tile.html":
                # Standalone Gira-styled "single tile" page — useful for
                # iframe-embedding from the HomeServer default page when the
                # integrator can't modify the main HTML.
                base = self._public_origin()
                return self._send(200, _tile_page(base), "text/html; charset=utf-8")
            if path == "/tile.fragment":
                # Bare <a class="box"> HTML for pasting into the HomeServer's
                # /main-site/index.html template.
                base = self._public_origin()
                return self._send(200, _tile_fragment(base), "text/html; charset=utf-8")
            if path == "/icon.svg":
                return self._send(200, _icon_svg(), "image/svg+xml")
            m = re.match(r"^/api/players/(.+)/favorites$", path)
            if m:
                pid = urllib.parse.unquote(m.group(1))
                return self._send(200, self.server.admin.api_player_favorites(pid))
            m = re.match(r"^/api/players/(.+)/playlists$", path)
            if m:
                pid = urllib.parse.unquote(m.group(1))
                return self._send(200, self.server.admin.api_player_playlists(pid))
            self._err(404, "NOT_FOUND", "no such route")
        except ValueError as exc:
            # Domain-validation errors (unknown player id, missing IP, …)
            # are caller-visible 400s, not server-side 500s.
            self._err(400, "INVALID_ARG", str(exc))
        except Exception as exc:  # noqa: BLE001
            self._err(500, "INTERNAL", str(exc))

    def _public_origin(self):
        """Best-effort public URL for this Admin instance, used in the tile
        snippet so the rendered fragment points back at the right HS-IP."""
        host_hdr = self.headers.get("Host", "")
        if host_hdr:
            return "http://" + host_hdr
        return "http://{}:{}".format(_get_local_lan_ip(), self.server.admin.listener_port)

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

    def _publish_counters_async(self):
        """Marshal _publish_counters into node context. Use this from any
        path that runs on the HTTP server thread (every REST handler) —
        calling set_output from a worker thread raises Hsl3ContextError."""
        self.fw.run_in_context(self._publish_counters, ())

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
        # Best-effort fetch of UUID + zoneName + model directly from the
        # player so manually-added entries are still recognizable in the UI
        # before the next SSDP scan completes.
        uuid = ""
        zone_name = ""
        model = ""
        if ip:
            info = _fetch_device_info("http://{}:1400/xml/device_description.xml".format(ip))
            model = info.get("model", "")
            zone_name = info.get("zoneName", "")
            # UUID is in the same XML as <UDN>uuid:RINCON_xxx</UDN>.
            try:
                resp = requests.get("http://{}:1400/xml/device_description.xml".format(ip), timeout=2)
                m = re.search(r"<UDN>uuid:([A-Za-z0-9_-]+)</UDN>", resp.text)
                if m:
                    uuid = m.group(1)
            except Exception:
                pass
        rec = {
            "id": uuid or mac or ip,
            "name": name,
            "zoneName": zone_name,
            "ip": ip,
            "mac": mac,
            "uuid": uuid,
            "model": model,
            "source": "manual",
            "lastSeen": time.time(),
        }
        with _registry_lock:
            _players[rec["id"]] = rec
        self._publish_counters_async()
        return {"ok": True, "player": rec}

    def api_remove_player(self, pid):
        with _registry_lock:
            removed = _players.pop(pid, None)
        if removed is None:
            raise ValueError("unknown player id")
        self._publish_counters_async()
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
        metadata = body.get("metadata") or ""
        if not name or not uri:
            raise ValueError("name and uri required")
        sid = "s_" + str(int(time.time() * 1000))
        rec = {"id": sid, "name": name, "uri": uri, "metadata": metadata}
        with _registry_lock:
            _stations[sid] = rec
        self._publish_counters_async()
        return {"ok": True, "station": rec}

    def api_player_favorites(self, pid):
        """Return the player's Sonos Favorites (the FV:2 container in
        UPnP ContentDirectory). Each item carries the playback URI and
        the music-service metadata required for cloud favorites."""
        with _registry_lock:
            rec = _players.get(pid)
        if rec is None:
            raise ValueError("unknown player id")
        ip = rec.get("ip", "")
        if not ip:
            raise ValueError("player has no IP — run discovery first")
        items = browse_content(ip, "FV:2", count=200)
        return {"ok": True, "playerId": pid, "favorites": items}

    def api_player_playlists(self, pid):
        """Return the player's saved Sonos Playlists (SQ:)."""
        with _registry_lock:
            rec = _players.get(pid)
        if rec is None:
            raise ValueError("unknown player id")
        ip = rec.get("ip", "")
        if not ip:
            raise ValueError("player has no IP — run discovery first")
        items = browse_content(ip, "SQ:", count=200)
        return {"ok": True, "playerId": pid, "playlists": items}

    def api_update_station(self, sid, body):
        with _registry_lock:
            rec = _stations.get(sid)
            if rec is None:
                raise ValueError("unknown station id")
            if "name" in body:
                rec["name"] = (body["name"] or "").strip()
            if "metadata" in body:
                rec["metadata"] = body["metadata"] or ""
            if "uri" in body:
                rec["uri"] = (body["uri"] or "").strip()
        return {"ok": True}

    def api_remove_station(self, sid):
        with _registry_lock:
            removed = _stations.pop(sid, None)
        if removed is None:
            raise ValueError("unknown station id")
        self._publish_counters_async()
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
