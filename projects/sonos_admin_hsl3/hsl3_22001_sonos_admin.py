"""LBS 22001 - Sonos Admin.

Singleton (one instance per HomeServer) that starts a small HTTP server
on port 8080 and serves a single-page web UI for managing the Sonos
integration:

  - Discovered + manually-added players (identified by IP and/or MAC).
  - A library of playable presets (radio stations, favorites,
    playlists, Sonos saved queues, and line-in sources).
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

# Per-player preset slots are a fixed-size sparse list. Slots 1..10 are
# reserved for the player's own presets (configured under each player
# card); global presets occupy indices 11..(10 + len(_stations)) so the
# index of any given global preset is identical across every player —
# wiring StartRadio = 14 to a KNX button picks the same global preset
# regardless of which player block receives it. Per-player slots can be
# sparse: a player with only slots 1 + 4 configured leaves 2/3/5/.../10
# empty (lookups return None, PresetNextPrev skips them).
PER_PLAYER_PRESET_SLOTS = 10

_registry_lock = threading.RLock()
_players = {}     # id(str) -> {"id","name","ip","mac","uuid","model","source",
                  #              "presets": [{slot,name,uri,metadata,type}, ...] }
_stations = {}    # id(str) -> {"id","name","uri","metadata"}  (global presets)
_groups = {}      # id(str) -> {"id","name","master","members"} (master/members are player ids)
_sounds = {}      # id(str) -> {"id","name","filename","source","size",
                  #              "_data" (raw bytes, excluded from JSON)}
_cloud = {
    "clientId": "",
    "clientSecret": "",
    "redirectBase": "",
    "accessToken": "",
    "refreshToken": "",
    "expiresAt": 0,
}
# Defaults the Sonos Player block falls back to when its own tunable
# inputs are left at the init value (0 / empty). Persisted across HS
# restarts via the PersistedPlayerDefaults store. Edited from the
# Admin web UI's "Player Defaults" section.
_player_defaults = {
    "pollInterval":      60,    # seconds
    "subTimeout":        1800,  # seconds
    "httpTimeout":       5,     # seconds
    "callbackBase":      "",    # empty = auto-detect from LAN IP
    # 0 = text outputs (Title, Artist, Album, ActiveStationName,
    # ZoneName, GroupInfo, LastError) emit their full value as-is.
    # Non-zero = anything longer than this character count scrolls
    # marquee-style at 1 char/second so a visualisation tile with a
    # narrow text field still shows the whole content.
    "marqueeMaxLength":  0,
}
_admin_instance_ref = {"instance": None}   # Wrapped in dict so swap is atomic.


def get_player_defaults():
    """Return a snapshot copy of the project-wide player tunable
    defaults. LBS 22000 + LBS 22002 layer per-player overrides on
    top of these (see ``get_player_tunables``)."""
    with _registry_lock:
        return dict(_player_defaults)


def get_player_tunables(spec):
    """Effective tunables for the player identified by ``spec``
    (IP / MAC / UUID / name). Per-player overrides stored on the
    player record win; missing or 0-valued overrides fall through
    to the project-wide defaults. Always returns a complete dict —
    never raises. The Player and Sound Enhancement LBS read their
    PollInterval / SubTimeout / HttpTimeout / CallbackBase from
    here so the integrator doesn't have to wire those inputs."""
    out = dict(_player_defaults)
    rec = get_player_record(spec) or {}
    # Numeric overrides — 0 / negative / unparseable means "no
    # override", fall through to the project default.
    for key in ("pollInterval", "subTimeout", "httpTimeout", "marqueeMaxLength"):
        try:
            v = int(rec.get(key) or 0)
        except (TypeError, ValueError):
            v = 0
        if v > 0:
            out[key] = v
    # Callback URL — empty string also means "no override".
    cb = rec.get("callbackBase")
    if isinstance(cb, str) and cb.strip():
        out["callbackBase"] = cb.strip().rstrip("/")
    return out


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


def get_group(index_or_name):
    """Return a group preset by alphabetical index (1..N) or by name
    (case-insensitive). The record is a dict:

        {
          "id":       <internal id>,
          "name":     <group label>,
          "master":   <player-id of the coordinator>,
          "members":  [<player-id>, <player-id>, ...]   (excludes master)
        }

    LBS 22000 calls this when a Player block's GroupPreset input fires;
    it then resolves each player-id to a current IP / UUID via
    get_player_record() and issues the SetAVTransportURI sequence."""
    if index_or_name is None:
        return None
    key = str(index_or_name).strip()
    if not key:
        return None
    with _registry_lock:
        if key.isdigit():
            idx = int(key)
            sorted_groups = sorted(_groups.values(), key=lambda g: g["name"].lower())
            if 1 <= idx <= len(sorted_groups):
                return dict(sorted_groups[idx - 1])
            return None
        for rec in _groups.values():
            if rec["name"].lower() == key.lower():
                return dict(rec)
    return None


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


def get_station_uri(index_or_name, player_spec=None):
    """Backwards-compat shim. Prefer get_station() which returns the full
    record including the DIDL-Lite metadata required for Sonos cloud
    favorites (TuneIn, Spotify, Apple Music). Returns '' when not found."""
    rec = get_station(index_or_name, player_spec)
    return rec["uri"] if rec else ""


def _player_preset_list(rec):
    """Normalised list of {slot, name, uri, metadata, type} dicts for a
    player record. Filters obvious garbage so a malformed retentive
    store can't break preset dispatch."""
    out = []
    for p in (rec.get("presets") or []):
        if not isinstance(p, dict):
            continue
        try:
            slot = int(p.get("slot") or 0)
        except (TypeError, ValueError):
            continue
        if not (1 <= slot <= PER_PLAYER_PRESET_SLOTS):
            continue
        name = (p.get("name") or "").strip()
        uri = (p.get("uri") or "").strip()
        if not name or not uri:
            continue
        out.append({
            "slot":     slot,
            "name":     name,
            "uri":      uri,
            "metadata": p.get("metadata") or "",
            "type":     (p.get("type") or "").strip(),
        })
    # Sort by slot so the same slot order is exposed to every consumer.
    out.sort(key=lambda p: p["slot"])
    return out


def get_station(index_or_name, player_spec=None):
    """LBS 22000 calls this to fetch the full preset record. Returns None
    when not found. The returned dict has the keys ``id``, ``name``,
    ``uri``, ``metadata``, ``type``, ``index`` and ``scope``.

    When ``player_spec`` is supplied (IP / MAC / UUID / name of a known
    player), per-player presets occupy indices 1..10 and the global
    library starts at index 11 — so ``StartRadio = 14`` always means
    "the third global preset" on every player. Name lookups search the
    player's own presets first, then fall back to global. When
    ``player_spec`` is None, indices map directly to the global library
    1..N (the legacy behaviour, kept for Admin-internal callers and
    older tests).

    ``scope`` is "player" or "global" so a caller can tell which list
    the match came from without re-resolving by index."""
    if index_or_name is None:
        return None
    key = str(index_or_name).strip()
    if not key:
        return None
    with _registry_lock:
        sorted_stations = sorted(_stations.values(), key=lambda s: s["name"].lower())
        player_presets = []
        if player_spec:
            prec = get_player_record(player_spec) or {}
            player_presets = _player_preset_list(prec)
        # Numeric lookup ---------------------------------------------------
        if key.isdigit():
            idx = int(key)
            if player_spec:
                # Slots 1..10 → per-player.
                if 1 <= idx <= PER_PLAYER_PRESET_SLOTS:
                    for p in player_presets:
                        if p["slot"] == idx:
                            return {"id":       "p_{}_{}".format(prec.get("id", ""), idx),
                                    "name":     p["name"],
                                    "uri":      p["uri"],
                                    "metadata": p["metadata"],
                                    "type":     p["type"],
                                    "index":    idx,
                                    "scope":    "player"}
                    return None
                # ≥11 → global at offset (idx - 10).
                gidx = idx - PER_PLAYER_PRESET_SLOTS
                if 1 <= gidx <= len(sorted_stations):
                    rec = dict(sorted_stations[gidx - 1])
                    rec["index"] = idx
                    rec["scope"] = "global"
                    return rec
                return None
            # Legacy global-only mapping.
            if 1 <= idx <= len(sorted_stations):
                rec = dict(sorted_stations[idx - 1])
                rec["index"] = idx
                rec["scope"] = "global"
                return rec
            return None
        # Name lookup (case-insensitive) ----------------------------------
        lower = key.lower()
        for p in player_presets:
            if p["name"].lower() == lower:
                return {"id":       "p_{}_{}".format(prec.get("id", ""), p["slot"]),
                        "name":     p["name"],
                        "uri":      p["uri"],
                        "metadata": p["metadata"],
                        "type":     p["type"],
                        "index":    p["slot"],
                        "scope":    "player"}
        for i, rec in enumerate(sorted_stations, start=1):
            if rec["name"].lower() == lower:
                r = dict(rec)
                r["index"] = (PER_PLAYER_PRESET_SLOTS + i) if player_spec else i
                r["scope"] = "global"
                return r
    return None


def get_station_count(player_spec=None):
    """Highest valid preset index for the given player. PresetNextPrev
    uses this together with get_station_indices() to wrap around. When
    ``player_spec`` is given the count is ``PER_PLAYER_PRESET_SLOTS +
    len(global)``; legacy callers (player_spec=None) get the raw global
    count for backward compatibility."""
    with _registry_lock:
        if player_spec:
            return PER_PLAYER_PRESET_SLOTS + len(_stations)
        return len(_stations)


def get_station_indices(player_spec=None):
    """Ordered list of every CONFIGURED preset index for the given
    player — used by LBS 22000's PresetNextPrev so the cycle skips
    empty per-player slots. With ``player_spec`` set: configured
    per-player slots (1..10) come first, then every global preset
    starting at 11. Without ``player_spec``: 1..len(global)."""
    with _registry_lock:
        gcount = len(_stations)
        if not player_spec:
            return list(range(1, gcount + 1))
        prec = get_player_record(player_spec) or {}
        player_slots = sorted(p["slot"] for p in _player_preset_list(prec))
        global_idxs = list(range(PER_PLAYER_PRESET_SLOTS + 1,
                                 PER_PLAYER_PRESET_SLOTS + 1 + gcount))
        return player_slots + global_idxs


# ---------------------------------------------------------------------------
# Sounds library — short notification audio clips (doorbell, alarm, …)
# uploaded by the integrator via the web UI and triggered by the LBS
# 22000 Player block's PlaySound input. The library starts empty;
# upload any MP3/WAV via the Sounds section in the Admin UI. Audio
# bytes persist across HomeServer restarts as base64 in a retentive
# store. The LBS 22000 plays the clip and automatically restores
# whatever was playing before (preset, queue position, volume, mute).
# ---------------------------------------------------------------------------

# Hard cap on uploaded sound size. Retentive stores have a finite
# budget; 2 MB is plenty for a 10-second clip at decent quality. The
# upload form in the web UI shows the limit.
MAX_SOUND_BYTES = 2 * 1024 * 1024


def get_sound(index_or_name):
    """Return a sound record (incl. 1-based alphabetical ``index``) by
    numeric index or by case-insensitive name. None when not found."""
    if index_or_name is None:
        return None
    key = str(index_or_name).strip()
    if not key:
        return None
    with _registry_lock:
        sorted_sounds = sorted(_sounds.values(), key=lambda s: s["name"].lower())
        if key.isdigit():
            idx = int(key)
            if 1 <= idx <= len(sorted_sounds):
                rec = _public_sound(sorted_sounds[idx - 1])
                rec["index"] = idx
                return rec
            return None
        for i, rec in enumerate(sorted_sounds, start=1):
            if rec["name"].lower() == key.lower():
                r = _public_sound(rec)
                r["index"] = i
                return r
    return None


def get_sound_count():
    with _registry_lock:
        return len(_sounds)


def _public_sound(rec):
    """Strip server-internal fields (``_generator``, ``_data``) before
    returning a sound record to a caller. Keeps the JSON small and the
    base64 payload off the wire when listing."""
    out = {}
    for k in ("id", "name", "filename", "source", "size", "mime"):
        if k in rec:
            out[k] = rec[k]
    return out


def get_sound_bytes(sound_id):
    """Return the raw audio bytes for a sound id. Built-in sounds are
    generated lazily and cached. User uploads return the decoded
    payload. None when the sound id is unknown."""
    with _registry_lock:
        rec = _sounds.get(sound_id)
    if rec is None:
        return None
    return rec.get("_data")


_AUDIO_MIME_BY_EXT = {
    ".wav":  "audio/wav",
    ".mp3":  "audio/mpeg",
    ".m4a":  "audio/mp4",
    ".aac":  "audio/aac",
    ".ogg":  "audio/ogg",
    ".flac": "audio/flac",
}


def _guess_audio_mime(filename):
    if not filename:
        return "application/octet-stream"
    lower = filename.lower()
    for ext, mime in _AUDIO_MIME_BY_EXT.items():
        if lower.endswith(ext):
            return mime
    return "application/octet-stream"


def get_sound_url(index_or_name, lan_ip, port):
    """Build the http://<hs-ip>:<admin-port>/sounds/<id>/<filename> URL
    that Sonos fetches when LBS 22000 plays a sound. The id in the
    path guarantees uniqueness (two uploads with the same filename
    don't collide); the filename suffix gives Sonos the format hint it
    uses to pick a decoder. Returns '' when the sound is unknown or
    the Admin hasn't bound a port yet."""
    if not lan_ip or not port:
        return ""
    rec = get_sound(index_or_name)
    if not rec:
        return ""
    return "http://{}:{}/sounds/{}/{}".format(
        lan_ip, port, rec["id"], rec["filename"]
    )


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
    if "audioinput" in c:
        return "source"
    if "audiobroadcast" in c:
        return "radio"
    if "playlistcontainer" in c:
        return "playlist"
    if "album.musicalbum" in c or "musicalbum" in c:
        return "album"
    if "musictrack" in c:
        return "track"
    # URI-scheme fallbacks for sonos-favorite wrappers with no inner class.
    if uri.startswith("x-rincon-stream"):
        # Another player's line-in (Connect:Amp, Port, Five, Beam…).
        return "source"
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
button.small         { min-width: 0; height: 28px; font-size: 11px; padding: 0 12px; margin: 0 4px 0 0; }
/* Force action-button rows to stay on one line so the Edit + Remove
   pair next to each Group preset doesn't wrap into a column. */
#groups td:last-child, .pc-actions { white-space: nowrap; }
#groups td:last-child button.small, .pc-actions button.small {
  display: inline-block; vertical-align: middle;
}

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
/* Click-to-copy affordance on the UUID. The label below the zone name
   is the "Use as Host" mechanism — clicking the UUID copies it into
   the clipboard so the integrator can paste straight into the Sonos
   Player block's Host input. */
.player-card .pc-uuid-copy { cursor: pointer; padding: 1px 4px;
                             border-radius: 2px; transition: background 0.15s; }
.player-card .pc-uuid-copy:hover { background: #BACE00; color: #202020; }
.player-card .pc-uuid-copy::before { content: "⧉  ";
                                     font-family: inherit; opacity: 0.6; }
.player-card .pc-grid { display: grid; gap: 6px 16px; align-items: center;
                        grid-template-columns: minmax(0, 1fr) minmax(0, 1fr)
                                               minmax(0, 1fr) auto; }
.player-card .pc-field { min-width: 0; }
.player-card .pc-field label { display: block; font-size: 11px; color: #808080;
                               margin-bottom: 2px; }
.player-card .pc-field input { width: 100%; }
.player-card .pc-actions { display: flex; gap: 4px; align-items: end; }
.player-card .pc-tunables, .player-card .pc-presets { margin-top: 8px; }
.player-card .pc-tunables summary, .player-card .pc-presets summary {
  cursor: pointer; color: #606060;
  font-size: 11px; user-select: none; padding: 4px 0; }
.player-card .pc-tunables summary:hover,
.player-card .pc-presets summary:hover { color: #202020; }
.player-card .pc-tunables[open] summary,
.player-card .pc-presets[open] summary { color: #202020; }
.player-card .pc-tunables .pc-grid { margin-top: 6px; }
.player-card .pc-presets .pp-table { margin-top: 4px; }
.player-card .pc-presets .pp-table th { background: #f5f5f5; }
.player-card .pc-presets .pp-table input { padding: 4px 6px; }
.player-card .pc-meta { margin-top: 6px; font-size: 11px; color: #808080;
                        display: flex; gap: 12px; flex-wrap: wrap; }
.player-card .pc-meta .pc-model { color: #505050; }
@media only screen and (max-width: 720px) {
  .player-card .pc-grid { grid-template-columns: 1fr 1fr; }
  .player-card .pc-actions { grid-column: 1 / -1; justify-content: flex-end; }
}
/* Group preset editor */
details.group-add { margin-top: 12px; padding: 8px 12px; background: #f5f5f5; border: 1px solid #e0e0e0; }
details.group-add summary { font-weight: 400; color: #505050; cursor: pointer; padding: 4px 0; }
details.group-add .row { margin-top: 6px; }
.members-pick { display: flex; flex-wrap: wrap; gap: 6px; }
.member-chip { display: inline-flex; align-items: center; gap: 4px;
               padding: 4px 10px; background: white; border: 1px solid #c0c0c0;
               font-size: 11px; cursor: pointer; user-select: none; }
.member-chip.on { background: #BACE00; border-color: #BACE00; color: #202020; }
.member-chip.master { background: #505050; border-color: #505050; color: white;
                      cursor: not-allowed; }
.member-chip.master:hover { background: #505050; }
.member-chip.master .master-tag { font-style: normal; font-size: 10px;
                                  opacity: 0.85; margin-left: 4px; }
.member-chip input { display: none; }
.group-members-display { font-size: 11px; color: #505050; }
.group-members-display .empty { color: #a0a0a0; font-style: italic; }

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
.fav-actions { display: flex; gap: 4px; flex-wrap: nowrap; }
.fav-actions button.small { white-space: nowrap; }

/* Two-column "grid" used by the Cloud section */
.grid { display: grid; grid-template-columns: 224px 1fr; gap: 6px 12px;
        margin-bottom: 8px; }
.grid > label { font-size: 12px; color: #505050; align-self: center; }

/* Footers, mirroring the HS index page (/main-site/hs.css conventions:
   light-grey upper bar with version-info+links, slightly-darker bar
   beneath with API-link helpers in muted text). */
.footer1 { display: flex; background-color: #f5f5f5; padding: 10px 30px;
           font-size: 14px; line-height: 24px; margin-top: 30px; }
.footer1 > .left  { flex: 1 1 60%; }
.footer1 > .right { flex: 0 0 40%; text-align: right; }
.footer1 a { color: #505050; text-decoration: none; }
.footer1 a:hover { color: #BACE00; }
.footer2 { background-color: rgb(230,230,230); padding: 10px 30px;
           font-size: 14px; color: #808080; }
.footer2 > a { color: inherit; text-decoration: none; margin-right: 18px; }
.footer2 > a:hover { color: #505050; }
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
       DHCP renumbering). <strong>Click the UUID</strong> below to copy it
       to the clipboard.</p>
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
    <h2>Global presets</h2>
    <p class="muted">
      Global presets are shared by every Sonos Player block — radio
      stations, Sonos favorites, Spotify / Apple Music playlists, Sonos
      saved queues, and line-in sources from a Connect:Amp / Port / Five.
      Wire one of the Sonos Player block's two preset-trigger inputs:
      write the <strong>#</strong> shown below into <code>StartRadio</code>
      (numeric), or write the <strong>Name</strong> into
      <code>StartRadioName</code> (string, case-insensitive).
      Global presets start at index <strong>11</strong> — indices 1..10
      are reserved for the <em>per-player presets</em> configured on
      each player card above. So <code>StartRadio = 14</code> always
      means "the third global preset" on every player.
      Adding or removing entries re-numbers the alphabetical index.
    </p>
    <table id="stations">
      <thead><tr>
        <th style="width:5%">#</th>
        <th style="width:9%">Type</th>
        <th style="width:24%">Name</th>
        <th>Stream URI</th>
        <th style="width:7%">Meta</th>
        <th style="width:8%">Actions</th>
      </tr></thead><tbody></tbody>
    </table>
    <div class="row">
      <input id="ns-name" placeholder="name" style="max-width: 200px">
      <input id="ns-uri"  placeholder="stream URL: http://... or x-rincon-mp3radio://...">
      <button id="ns-add">Add preset</button>
    </div>
    <p class="muted" style="margin-top:14px">
      Or create a <strong>join preset</strong>: triggering this preset
      makes the Sonos Player join an existing master speaker (it stops
      its own playback and inherits the master&apos;s). Use it to wire
      a single KNX address to "follow the kitchen" or similar.
    </p>
    <div class="row">
      <input id="nj-name" placeholder="preset name (e.g. Join Kitchen)" style="max-width: 240px">
      <select id="nj-master" style="max-width: 220px"></select>
      <button id="nj-add">Add join preset</button>
    </div>
  </section>

  <section>
    <h2>Group presets</h2>
    <p class="muted">
      Pre-defined Sonos zone groups. Each preset picks a
      <strong>master</strong> (the coordinator that keeps playing its
      content) plus one or more <strong>members</strong> that join.
      From any Sonos Player block write the <strong>#</strong> below
      into <code>GroupPreset</code> (numeric) or the <strong>Name</strong>
      into <code>GroupPresetName</code> (string) to form the group on
      demand. Write <code>Ungroup = 1</code> on a player to make it
      stand alone again.
    </p>
    <table id="groups">
      <thead><tr>
        <th style="width:6%">#</th>
        <th style="width:22%">Name</th>
        <th style="width:22%">Master</th>
        <th>Members</th>
        <th style="width:10%">Actions</th>
      </tr></thead><tbody></tbody>
    </table>
    <details class="group-add">
      <summary>Add group preset</summary>
      <div class="row">
        <input id="ng-name" placeholder="name (e.g. Whole Home)" style="max-width: 220px">
      </div>
      <div class="row">
        <label class="muted" style="width: 60px">Master:</label>
        <select id="ng-master" style="max-width: 240px"></select>
      </div>
      <div class="row">
        <label class="muted" style="width: 60px; vertical-align: top">Members:</label>
        <div id="ng-members" class="members-pick"></div>
      </div>
      <div class="row">
        <button id="ng-add">Add group preset</button>
      </div>
    </details>
  </section>

  <section>
    <h2>Sounds</h2>
    <p class="muted">Notification clips (doorbell, alarm, …) the Sonos Player block can play via the <code>PlaySound</code> input. Upload an MP3/WAV/AAC/OGG/FLAC, write the row&apos;s <strong>#</strong> into <code>PlaySound</code> on a Player block — the player snapshots its current state, plays the clip, then restores what was playing (preset, queue position, volume, mute). Library starts empty; the file lives in HomeServer retentive storage.</p>
    <table id="sounds">
      <thead><tr>
        <th style="width:5%">#</th>
        <th style="width:32%">Name</th>
        <th>Filename</th>
        <th style="width:10%">Size</th>
        <th style="width:8%">Actions</th>
      </tr></thead><tbody></tbody>
    </table>
    <div class="row">
      <input id="snd-name" placeholder="name (e.g. Doorbell)" style="max-width: 220px">
      <input id="snd-file" type="file" accept="audio/*" style="max-width: 280px">
      <button id="snd-upload">Upload sound</button>
      <span id="snd-state" class="muted"></span>
    </div>
  </section>

  <section>
    <h2>Player Defaults</h2>
    <p class="muted">Defaults the Sonos Player block falls back to when its own tunable inputs (PollInterval, SubTimeout, HttpTimeout, CallbackBase) are left at their init value. Setting them here once means you don't have to wire those inputs on every Player block.</p>
    <div class="grid">
      <label for="pd-poll">Status poll interval (seconds)</label><input id="pd-poll" type="number" min="10" step="1">
      <label for="pd-sub">UPnP subscription timeout (seconds)</label><input id="pd-sub" type="number" min="60" step="1">
      <label for="pd-http">HTTP timeout (seconds)</label><input id="pd-http" type="number" min="2" step="1">
      <label for="pd-cb">Callback base URL (leave empty for auto)</label><input id="pd-cb" type="text" placeholder="http://&lt;homeserver-ip&gt;:8081">
      <label for="pd-marq">Max text length (0 = no marquee)</label><input id="pd-marq" type="number" min="0" step="1" placeholder="0">
    </div>
    <div class="row">
      <button id="pd-save">Save defaults</button>
      <span id="pd-state" class="muted"></span>
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
  <a href="/api/player-defaults">/api/player-defaults</a>
  <a href="/api/sounds">/api/sounds</a>
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

// Cross-context clipboard copy: navigator.clipboard.writeText only
// works in a Secure Context (HTTPS or localhost). The Admin runs on
// plain HTTP over the LAN, so the modern API rejects and we'd fall
// back to a prompt() — which is the bug the user reported. The legacy
// execCommand('copy') path predates secure-context gating and works
// over HTTP back to IE10. We try the modern API first when available,
// then the legacy path, and only as a last resort show a prompt.
async function copyToClipboard(text) {
  if (window.isSecureContext && navigator.clipboard && navigator.clipboard.writeText) {
    try { await navigator.clipboard.writeText(text); return true; }
    catch (e) { /* fall through to legacy */ }
  }
  try {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.setAttribute('readonly', '');
    ta.style.position = 'fixed';
    ta.style.left = '-9999px';
    ta.style.top = '0';
    document.body.appendChild(ta);
    ta.select();
    ta.setSelectionRange(0, ta.value.length);
    const ok = document.execCommand('copy');
    document.body.removeChild(ta);
    return ok;
  } catch (e) { return false; }
}
// Maximum per-player preset slots, set from /api/players response.
// 10 by design but kept dynamic so the UI follows the server.
let _presetSlots = 10;

// Build the inner HTML for one player's per-player-preset table. Each
// row is either a configured preset (name + URI + Type + delete) or
// an empty slot with inline name + URI inputs and a Save button. The
// click + change handlers further down route the values into PUT /
// DELETE on /api/players/{pid}/presets/{slot}.
function renderPlayerPresets(p) {
  const byslot = {};
  for (const ps of (p.presets || [])) byslot[ps.slot] = ps;
  let rows = '';
  for (let n = 1; n <= _presetSlots; n++) {
    const ps = byslot[n];
    if (ps) {
      const typeBadge = ps.type
        ? '<span class="fav-type">' + esc(ps.type) + '</span>' : '<span class="muted">—</span>';
      rows +=
        '<tr>' +
          '<td><strong>' + n + '</strong></td>' +
          '<td>' + typeBadge + '</td>' +
          '<td><input data-pp-edit="' + esc(p.id) + '" data-slot="' + n + '" data-field="name" value="' + esc(ps.name) + '"></td>' +
          '<td><input data-pp-edit="' + esc(p.id) + '" data-slot="' + n + '" data-field="uri" value="' + esc(ps.uri) + '"></td>' +
          '<td><button class="danger small" data-pp-del="' + esc(p.id) + '" data-slot="' + n + '">x</button></td>' +
        '</tr>';
    } else {
      rows +=
        '<tr>' +
          '<td><strong>' + n + '</strong></td>' +
          '<td><span class="muted">—</span></td>' +
          '<td><input data-pp-new="' + esc(p.id) + '" data-slot="' + n + '" data-field="name" placeholder="(empty)"></td>' +
          '<td><input data-pp-new="' + esc(p.id) + '" data-slot="' + n + '" data-field="uri" placeholder="stream URL or favorite URI"></td>' +
          '<td><button class="small" data-pp-add="' + esc(p.id) + '" data-slot="' + n + '">Add</button></td>' +
        '</tr>';
    }
  }
  return (
    '<table class="pp-table">' +
      '<thead><tr>' +
        '<th style="width:5%">#</th>' +
        '<th style="width:9%">Type</th>' +
        '<th style="width:30%">Name</th>' +
        '<th>URI</th>' +
        '<th style="width:8%">Actions</th>' +
      '</tr></thead><tbody>' + rows + '</tbody>' +
    '</table>'
  );
}

async function refreshPlayers() {
  const r = await api('GET', '/api/players');
  if (r.presetSlots) _presetSlots = r.presetSlots;
  const presetSlots = _presetSlots;
  const list = document.getElementById('players');
  list.innerHTML = '';
  for (const p of r.players) {
    const zone = p.zoneName || '';
    const uuid = p.uuid || '';
    const card = document.createElement('div');
    card.className = 'player-card';
    // The UUID itself is the "Use as Host" affordance: click to copy
    // into the clipboard for pasting into the Sonos Player block's
    // Host input. Hover state + a small "click to copy" tooltip make
    // the interaction discoverable without taking up a button slot.
    const uuidContent = uuid
      ? '<span class="pc-uuid pc-uuid-copy" data-host="' + esc(uuid) +
        '" title="Click to copy UUID for the Sonos Player Host input">' +
        esc(uuid) + '</span>'
      : '<span class="pc-uuid"><em>no UUID yet</em></span>';
    card.innerHTML =
      '<div class="pc-head">' +
        '<div class="pc-zone">' + esc(zone) + '</div>' +
        '<span class="pill ' + esc(p.source) + '">' + esc(p.source) + '</span>' +
      '</div>' +
      '<div>' + uuidContent + '</div>' +
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
          '<button class="secondary small" data-favs="' + esc(p.id) + '" title="Browse Sonos Favorites on this player">Favorites</button>' +
          '<button class="danger small" data-del-player="' + esc(p.id) + '" title="Remove">x</button>' +
        '</div>' +
      '</div>' +
      '<div class="pc-meta">' +
        (p.model ? '<span class="pc-model">' + esc(p.model) + '</span>' : '') +
      '</div>' +
      // Per-player tunable overrides. Empty fields = use the project
      // default from the Player Defaults section. The placeholder
      // shows what each default currently is so the integrator knows
      // what they're overriding.
      '<details class="pc-tunables">' +
        '<summary>Advanced overrides</summary>' +
        '<div class="pc-grid">' +
          '<div class="pc-field">' +
            '<label>Status poll (s)</label>' +
            '<input type="number" min="10" data-edit="' + esc(p.id) + '" data-field="pollInterval" ' +
                   'value="' + esc(p.pollInterval || '') + '" placeholder="default">' +
          '</div>' +
          '<div class="pc-field">' +
            '<label>UPnP sub (s)</label>' +
            '<input type="number" min="60" data-edit="' + esc(p.id) + '" data-field="subTimeout" ' +
                   'value="' + esc(p.subTimeout || '') + '" placeholder="default">' +
          '</div>' +
          '<div class="pc-field">' +
            '<label>HTTP timeout (s)</label>' +
            '<input type="number" min="2"  data-edit="' + esc(p.id) + '" data-field="httpTimeout" ' +
                   'value="' + esc(p.httpTimeout || '') + '" placeholder="default">' +
          '</div>' +
          '<div class="pc-field">' +
            '<label>Max text length</label>' +
            '<input type="number" min="0"  data-edit="' + esc(p.id) + '" data-field="marqueeMaxLength" ' +
                   'value="' + esc(p.marqueeMaxLength || '') + '" placeholder="default" title="0 disables marquee">' +
          '</div>' +
          '<div class="pc-field" style="grid-column:1 / -1">' +
            '<label>Callback base URL</label>' +
            '<input type="text" data-edit="' + esc(p.id) + '" data-field="callbackBase" ' +
                   'value="' + esc(p.callbackBase || '') + '" placeholder="default (auto from LAN IP)">' +
          '</div>' +
        '</div>' +
      '</details>' +
      // Per-player preset slots. 1..10 are always shown; configured
      // slots carry name + URI + Type, empty slots show "(empty)".
      // The index in this card is what the integrator writes into
      // StartRadio on the Player block — and it never collides with
      // global presets because those start at 11.
      '<details class="pc-presets">' +
        '<summary>Per-player presets (slots 1&ndash;' + presetSlots + ')</summary>' +
        '<p class="muted" style="margin:4px 0 6px">' +
          'Triggering slot <strong>N</strong> on this player plays the ' +
          'preset stored in slot N. Empty slots are skipped by ' +
          '<code>PresetNextPrev</code>. Slot index is local to this ' +
          'player; the same slot number on another player can hold a ' +
          'different preset.' +
        '</p>' +
        renderPlayerPresets(p) +
      '</details>' +
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
    const ptype = s.type || '';
    tr.innerHTML =
      '<td><strong>' + (i + 1) + '</strong></td>' +
      '<td>' + (ptype ? '<span class="fav-type">' + esc(ptype) + '</span>' : '<span class="muted">—</span>') + '</td>' +
      '<td><input data-sedit="' + esc(s.id) + '" data-field="name" value="' + esc(s.name) + '"></td>' +
      '<td><input data-sedit="' + esc(s.id) + '" data-field="uri"  value="' + esc(s.uri)  + '"></td>' +
      '<td>' + (hasMeta ? '<span class="pill ssdp" title="Has music-service metadata">yes</span>' : '<span class="muted">—</span>') + '</td>' +
      '<td><button class="danger small" data-del-station="' + esc(s.id) + '">x</button></td>';
    tbody.appendChild(tr);
  });
  if (!sorted.length) {
    tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;color:#808080;padding:12px">' +
      'No presets yet. Add one above, or click <strong>Favorites</strong> on a player to import.' +
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
async function refreshPlayerDefaults() {
  const r = await api('GET', '/api/player-defaults');
  document.getElementById('pd-poll').value = r.defaults.pollInterval;
  document.getElementById('pd-sub').value = r.defaults.subTimeout;
  document.getElementById('pd-http').value = r.defaults.httpTimeout;
  document.getElementById('pd-cb').value = r.defaults.callbackBase || '';
  document.getElementById('pd-marq').value = r.defaults.marqueeMaxLength || 0;
}
function fmtSize(n) {
  if (!n) return '—';
  if (n < 1024) return n + ' B';
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB';
  return (n / 1024 / 1024).toFixed(2) + ' MB';
}
async function refreshSounds() {
  const r = await api('GET', '/api/sounds');
  const tbody = document.querySelector('#sounds tbody');
  tbody.innerHTML = '';
  for (const s of (r.sounds || [])) {
    const tr = document.createElement('tr');
    tr.innerHTML =
      '<td><strong>' + s.index + '</strong></td>' +
      '<td><input data-snd-edit="' + esc(s.id) + '" data-field="name" value="' + esc(s.name) + '"></td>' +
      '<td><code>' + esc(s.filename) + '</code></td>' +
      '<td>' + fmtSize(s.size) + '</td>' +
      '<td><button class="danger small" data-del-sound="' + esc(s.id) + '">x</button></td>';
    tbody.appendChild(tr);
  }
  if (!(r.sounds || []).length) {
    tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;color:#808080;padding:12px">' +
      'No sounds yet. Upload an MP3 or WAV below.' +
      '</td></tr>';
  }
}
async function refreshDiag() {
  const r = await api('GET', '/api/info');
  document.getElementById('diag').innerHTML =
    'Listener port: <code>' + r.listenPort + '</code> &middot; ' +
    'Players: <code>' + r.playerCount + '</code> &middot; ' +
    'Presets: <code>' + r.stationCount + '</code> &middot; ' +
    'Groups: <code>' + (r.groupCount || 0) + '</code> &middot; ' +
    'Cloud authorized: <code>' + r.cloudAuthorized + '</code>';
}
// Render the member-chip area for a group editor. ``checked`` is a Set
// of player ids the user picked as members; ``masterId`` is the
// currently-selected master and is shown with a (master) tag + locked
// checkbox so it's obvious which player coordinates the group. The
// master is implicitly always "on" — Sonos requires the coordinator to
// be part of its own group — but it can't be toggled off in this view.
function renderMemberChips(container, checked, masterId) {
  if (!container) return;
  const entries = Object.entries(_playerIndex || {});
  if (!entries.length) {
    container.innerHTML = '<span class="muted">No players yet. Scan first.</span>';
    return;
  }
  container.innerHTML = entries.map(([pid, label]) => {
    const isMaster = pid === masterId;
    const on = isMaster || checked.has(pid);
    return '<label class="member-chip' +
           (on ? ' on' : '') +
           (isMaster ? ' master' : '') +
           '" data-pid="' + esc(pid) + '">' +
           '<input type="checkbox" value="' + esc(pid) + '"' +
             (on ? ' checked' : '') +
             (isMaster ? ' disabled' : '') + '>' +
           esc(label) +
           (isMaster ? ' <em class="master-tag">(master)</em>' : '') +
           '</label>';
  }).join('');
}

function chipsCheckedSet(container) {
  const set = new Set();
  if (!container) return set;
  for (const i of container.querySelectorAll('input:checked')) set.add(i.value);
  return set;
}

// Cached so refreshGroups can label master + member cells without
// re-fetching /api/players.
let _playerIndex = {};

async function refreshGroups() {
  const r = await api('GET', '/api/groups');
  // Build the player picker options every refresh so master + members
  // dropdowns reflect the latest registry.
  const players = (await api('GET', '/api/players')).players;
  _playerIndex = {};
  for (const p of players) {
    _playerIndex[p.id] = (p.zoneName || p.name || p.ip || p.id);
  }
  const masterSel = document.getElementById('ng-master');
  masterSel.innerHTML = '<option value="">(pick a master)</option>' +
    players.map(p => '<option value="' + esc(p.id) + '">' + esc(_playerIndex[p.id]) + '</option>').join('');
  // The "Add join preset" dropdown keys off the player's UUID rather
  // than its id — the URI we build (x-rincon:RINCON_...) is the UUID
  // verbatim, and only players with a known UUID can act as a master.
  const njSel = document.getElementById('nj-master');
  if (njSel) {
    const haveUuid = players.filter(p => p.uuid);
    njSel.innerHTML = '<option value="">(pick a master speaker)</option>' +
      haveUuid.map(p =>
        '<option value="' + esc(p.uuid) + '">' +
        esc(p.zoneName || p.name || p.ip || p.uuid) + '</option>'
      ).join('');
  }
  // Render the "Add" form's member chips with no master selected yet;
  // the change-handler on #ng-master will re-render with the master
  // chip locked + tagged whenever the user picks one.
  renderMemberChips(document.getElementById('ng-members'),
                    new Set(),
                    document.getElementById('ng-master').value);

  // Now the table itself.
  const tbody = document.querySelector('#groups tbody');
  tbody.innerHTML = '';
  const sorted = [...r.groups].sort(
    (a, b) => a.name.toLowerCase().localeCompare(b.name.toLowerCase())
  );
  sorted.forEach((g, i) => {
    const tr = document.createElement('tr');
    const masterLabel = _playerIndex[g.master] || g.master || '(?)';
    const memberLabels = (g.members || []).map(id => _playerIndex[id] || id);
    const memberHtml = memberLabels.length
      ? memberLabels.map(l => esc(l)).join(', ')
      : '<span class="empty">none</span>';
    tr.innerHTML =
      '<td><strong>' + (i + 1) + '</strong></td>' +
      '<td><input data-gedit="' + esc(g.id) + '" data-field="name" value="' + esc(g.name) + '"></td>' +
      '<td>' + esc(masterLabel) + '</td>' +
      '<td class="group-members-display">' + memberHtml + '</td>' +
      '<td>' +
        '<button class="secondary small" data-gedit-open="' + esc(g.id) + '" title="Edit members">Edit</button>' +
        '<button class="danger small" data-del-group="' + esc(g.id) + '">x</button>' +
      '</td>';
    tbody.appendChild(tr);
    // Hidden editor row beneath.
    const editTr = document.createElement('tr');
    editTr.id = 'gedit-' + g.id;
    editTr.style.display = 'none';
    editTr.innerHTML = '<td colspan="5" style="background:#f5f5f5;padding:10px"></td>';
    tbody.appendChild(editTr);
  });
  if (!sorted.length) {
    tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;color:#808080;padding:12px">' +
      'No group presets yet. Scroll down to add one.</td></tr>';
  }
}

document.addEventListener('click', async (ev) => {
  const t = ev.target;
  try {
    if (t.dataset.delGroup) {
      if (!confirm('Remove group preset?')) return;
      await api('DELETE', '/api/groups/' + encodeURIComponent(t.dataset.delGroup));
      refreshGroups();
    }
    if (t.dataset.geditOpen) {
      const editTr = document.getElementById('gedit-' + t.dataset.geditOpen);
      if (!editTr) return;
      if (editTr.style.display !== 'none') {
        editTr.style.display = 'none';
        return;
      }
      const r = (await api('GET', '/api/groups')).groups;
      const g = r.find(x => x.id === t.dataset.geditOpen);
      if (!g) return;
      const cell = editTr.querySelector('td');
      const playerOpts = Object.entries(_playerIndex)
        .map(([pid, label]) =>
          '<option value="' + esc(pid) + '"' + (pid === g.master ? ' selected' : '') + '>' +
          esc(label) + '</option>').join('');
      cell.innerHTML =
        '<div class="row"><label class="muted" style="width:60px">Master:</label>' +
          '<select data-gemaster="' + esc(g.id) + '" style="max-width:240px">' + playerOpts + '</select></div>' +
        '<div class="row"><label class="muted" style="width:60px;vertical-align:top">Members:</label>' +
          '<div class="members-pick" data-gemembers="' + esc(g.id) + '"></div></div>' +
        '<div class="row"><button class="small" data-gsave="' + esc(g.id) + '">Save</button>' +
          '<button class="small secondary" data-gcancel="' + esc(g.id) + '">Cancel</button></div>';
      editTr.style.display = '';
      // Render chips through the shared helper so the master is shown
      // tagged + locked. Re-renders on master-select change below.
      renderMemberChips(cell.querySelector('[data-gemembers]'),
                        new Set(g.members || []),
                        g.master);
    }
    if (t.dataset.gcancel) {
      const editTr = document.getElementById('gedit-' + t.dataset.gcancel);
      if (editTr) editTr.style.display = 'none';
    }
    if (t.dataset.gsave) {
      const gid = t.dataset.gsave;
      const editTr = document.getElementById('gedit-' + gid);
      const master = editTr.querySelector('select[data-gemaster]').value;
      const members = [...editTr.querySelectorAll('div[data-gemembers] input:checked')].map(i => i.value);
      await api('PATCH', '/api/groups/' + encodeURIComponent(gid), { master, members });
      toast('Group saved');
      editTr.style.display = 'none';
      refreshGroups();
    }
    // Member-chip handling is done via the native <label><input> click
    // (browser toggles the checkbox + fires change) plus a 'change'
    // listener that syncs the .on class — no manual click toggling here,
    // otherwise we'd double-toggle and the chip looks unresponsive.
  } catch (e) { toast(e.message, true); }
});

// Sync the visual highlight on member chips whenever their hidden
// checkbox changes — runs for clicks on either the label or the input.
// Also re-renders the chip area when the master <select> changes so
// the "(master)" tag follows the dropdown selection.
document.addEventListener('change', (ev) => {
  const t = ev.target;
  if (t.tagName === 'INPUT' && t.type === 'checkbox') {
    const chip = t.closest('.member-chip');
    if (chip) chip.classList.toggle('on', t.checked);
  }
  if (t.id === 'ng-master') {
    // Add-form master changed: re-render the chip area below so the
    // new master gets the locked-tagged style. Preserve any chips the
    // user already ticked.
    const chips = document.getElementById('ng-members');
    renderMemberChips(chips, chipsCheckedSet(chips), t.value);
  }
  if (t.dataset && t.dataset.gemaster) {
    // Inline editor master changed: same re-render for that group's
    // chip area. The selector hangs off the same <tr> as the chips.
    const editTr = t.closest('tr');
    if (editTr) {
      const chips = editTr.querySelector('[data-gemembers]');
      renderMemberChips(chips, chipsCheckedSet(chips), t.value);
    }
  }
});

document.getElementById('ng-add').addEventListener('click', async () => {
  const name = document.getElementById('ng-name').value.trim();
  const master = document.getElementById('ng-master').value;
  const members = [...document.querySelectorAll('#ng-members input:checked')].map(i => i.value);
  if (!name) return toast('Group name required', true);
  if (!master) return toast('Pick a master player', true);
  try {
    await api('POST', '/api/groups', { name, master, members });
    document.getElementById('ng-name').value = '';
    document.getElementById('ng-master').value = '';
    document.querySelectorAll('#ng-members input').forEach(i => {
      i.checked = false; i.closest('.member-chip').classList.remove('on');
    });
    toast('Group preset added');
    refreshGroups();
  } catch (e) { toast(e.message, true); }
});

// Sync group-name inline edits like presets do.
document.addEventListener('change', async (ev) => {
  const t = ev.target;
  if (t.dataset && t.dataset.gedit && t.dataset.field) {
    try { await api('PATCH', '/api/groups/' + encodeURIComponent(t.dataset.gedit),
                    { [t.dataset.field]: t.value }); toast('Saved'); }
    catch (e) { toast(e.message, true); }
  }
});

async function refreshAll() {
  try { await refreshPlayers(); await refreshStations(); await refreshGroups();
        await refreshSounds(); await refreshPlayerDefaults();
        await refreshCloud(); await refreshDiag(); }
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
  if (t.dataset.sndEdit) {
    try { await api('PATCH', '/api/sounds/' + encodeURIComponent(t.dataset.sndEdit),
                    { [t.dataset.field]: t.value }); toast('Saved'); }
    catch (e) { toast(e.message, true); }
  }
  // Per-player preset inline edit: name OR uri changed on an already-
  // configured slot. Re-PUT the whole record (name + uri are both
  // required server-side) so a partial edit doesn't drop the other
  // field.
  if (t.dataset.ppEdit && t.dataset.slot) {
    const pid = t.dataset.ppEdit;
    const slot = t.dataset.slot;
    const row = t.closest('tr');
    const name = (row.querySelector('input[data-field="name"]').value || '').trim();
    const uri = (row.querySelector('input[data-field="uri"]').value || '').trim();
    if (!name || !uri) return;  // server would error; keep silent on partial edits
    try {
      await api('PUT', '/api/players/' + encodeURIComponent(pid) +
                       '/presets/' + encodeURIComponent(slot),
                { name, uri });
      toast('Saved');
    } catch (e) { toast(e.message, true); }
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
    if (t.dataset.delSound) {
      if (!confirm('Remove sound?')) return;
      await api('DELETE', '/api/sounds/' + encodeURIComponent(t.dataset.delSound));
      refreshAll();
    }
    // Per-player preset: add a new slot from the inline empty-row inputs.
    if (t.dataset.ppAdd && t.dataset.slot) {
      const pid = t.dataset.ppAdd;
      const slot = t.dataset.slot;
      const row = t.closest('tr');
      const name = (row.querySelector('input[data-field="name"]').value || '').trim();
      const uri = (row.querySelector('input[data-field="uri"]').value || '').trim();
      if (!name) return toast('preset name required', true);
      if (!uri) return toast('preset URI required', true);
      await api('PUT', '/api/players/' + encodeURIComponent(pid) +
                       '/presets/' + encodeURIComponent(slot),
                { name, uri });
      toast('Slot ' + slot + ' saved');
      refreshPlayers();
    }
    // Per-player preset: clear a configured slot.
    if (t.dataset.ppDel && t.dataset.slot) {
      const pid = t.dataset.ppDel;
      const slot = t.dataset.slot;
      if (!confirm('Clear preset slot ' + slot + '?')) return;
      await api('DELETE', '/api/players/' + encodeURIComponent(pid) +
                          '/presets/' + encodeURIComponent(slot));
      refreshPlayers();
    }
    if (t.dataset.host !== undefined) {
      // Copy the UUID into the clipboard. navigator.clipboard.writeText
      // requires a Secure Context (HTTPS / localhost) — the Admin runs
      // on plain HTTP over the LAN, so the modern API would reject.
      // The legacy execCommand('copy') path doesn't have that
      // restriction and works in every browser back to IE10.
      const v = t.dataset.host || '';
      if (!v) return toast('No identifier available', true);
      if (await copyToClipboard(v)) {
        toast('Copied: ' + v);
      } else {
        // Last resort if even the legacy path was blocked (e.g. by an
        // extension): show a prompt the user can copy from manually.
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
      toast('Added to global: ' + payload.name);
      refreshStations();
    }
    if (t.dataset.addFavPlayer) {
      // Add a favorite as a per-player preset on this card's player.
      // Walk slots 1..10, pick the first free one. If every slot is
      // taken surface a clear error so the user knows to delete one
      // first.
      const pid = t.dataset.pid;
      const payload = JSON.parse(decodeURIComponent(escape(atob(t.dataset.addFavPlayer))));
      const cur = await api('GET', '/api/players/' + encodeURIComponent(pid) + '/presets');
      const used = new Set((cur.presets || []).map(p => p.slot));
      const total = cur.slots || 10;
      let free = 0;
      for (let n = 1; n <= total; n++) {
        if (!used.has(n)) { free = n; break; }
      }
      if (!free) {
        toast('No free per-player slot on this player (all ' + total + ' are taken). Remove one first.', true);
        return;
      }
      await api('PUT', '/api/players/' + encodeURIComponent(pid) +
                       '/presets/' + free, payload);
      toast('Added to slot ' + free + ': ' + payload.name);
      refreshPlayers();
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

  // /api/players/<id>/favorites returns AI: sources (Line-In,
  // Bluetooth, TV, ...) intermixed with FV:2 favorites. Split them so
  // the UI shows three groups: Player sources first, then favorites,
  // then saved playlists.
  const _sources = [], _favs = [];
  for (const f of (favs.favorites || [])) {
    (f.type === 'source' ? _sources : _favs).push(f);
  }
  const all = [
    ..._sources.map(f => ({...f, group: 'Player sources'})),
    ..._favs.map(f => ({...f, group: 'Sonos Favorites'})),
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
        name: f.title, uri: f.uri, metadata: f.metadata, type: f.type || ''
      }))));
      // Two add buttons per favorite: one drops the item into the global
      // library (shared across every player, indices 11+), the other
      // assigns it to the next free slot on THIS player (indices 1..10,
      // local to this card). pid is encoded on the second button so the
      // handler knows which player to write to.
      html += '<div class="fav-row">' +
                '<span class="fav-type">' + esc(f.type || 'other') + '</span>' +
                '<span class="fav-title">' + esc(f.title) + '</span>' +
                '<span class="fav-actions">' +
                  '<button class="small" data-add-fav-station="' + payload + '" title="Add to the global preset library (shared by every player)">Add as global</button>' +
                  '<button class="small secondary" data-add-fav-player="' + payload + '" data-pid="' + esc(pid) + '" title="Add to the next free per-player slot on this player">Add as player</button>' +
                '</span>' +
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
document.getElementById('nj-add').addEventListener('click', async () => {
  const name = document.getElementById('nj-name').value.trim();
  const sel = document.getElementById('nj-master');
  const masterUuid = sel.value;
  if (!name) return toast('preset name required', true);
  if (!masterUuid) return toast('pick a master speaker', true);
  // The URI scheme that joins a master is bare x-rincon: (colon), not
  // x-rincon-stream / x-rincon-cpcontainer / etc. The Player module
  // detects this scheme and skips the Play step — slaves auto-inherit
  // the master's transport state.
  try {
    await api('POST', '/api/stations', {
      name,
      uri: 'x-rincon:' + masterUuid,
      type: 'join',
    });
    document.getElementById('nj-name').value = '';
    refreshAll();
  } catch (e) { toast(e.message, true); }
});
// Read a File as base64 in a single non-blocking step. FileReader is
// the only cross-browser way to get bytes out of a <input type="file">.
function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onerror = () => reject(new Error('file read failed'));
    r.onload = () => {
      const s = r.result || '';
      // FileReader.readAsDataURL gives us "data:audio/wav;base64,XYZ"
      const i = s.indexOf(',');
      resolve(i >= 0 ? s.slice(i + 1) : s);
    };
    r.readAsDataURL(file);
  });
}
document.getElementById('snd-upload').addEventListener('click', async () => {
  const name = document.getElementById('snd-name').value.trim();
  const fileInput = document.getElementById('snd-file');
  const file = fileInput.files && fileInput.files[0];
  if (!name) return toast('name required', true);
  if (!file)  return toast('pick a file', true);
  const stateEl = document.getElementById('snd-state');
  stateEl.textContent = 'Uploading ' + fmtSize(file.size) + '...';
  try {
    const data_b64 = await fileToBase64(file);
    await api('POST', '/api/sounds', {
      name, filename: file.name, mime: file.type || '', data_b64,
    });
    document.getElementById('snd-name').value = '';
    fileInput.value = '';
    stateEl.textContent = 'Uploaded';
    setTimeout(() => { stateEl.textContent = ''; }, 2000);
    refreshSounds();
  } catch (e) { stateEl.textContent = ''; toast(e.message, true); }
});
document.getElementById('pd-save').addEventListener('click', async () => {
  const body = {
    pollInterval: parseInt(document.getElementById('pd-poll').value, 10) || 0,
    subTimeout:   parseInt(document.getElementById('pd-sub').value,  10) || 0,
    httpTimeout:  parseInt(document.getElementById('pd-http').value, 10) || 0,
    callbackBase: document.getElementById('pd-cb').value.trim(),
    marqueeMaxLength: parseInt(document.getElementById('pd-marq').value, 10) || 0,
  };
  try {
    await api('PUT', '/api/player-defaults', body);
    document.getElementById('pd-state').textContent = 'Saved';
    setTimeout(() => { document.getElementById('pd-state').textContent = ''; }, 2000);
    refreshPlayerDefaults();
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
        '  <div class="descr">Manage Sonos players, presets, and Cloud authorization.</div>\n'
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
        # Cap a hair above MAX_SOUND_BYTES so a max-size upload still
        # fits the base64-inflated JSON body. Anything bigger is almost
        # certainly an accidental or malicious payload.
        if length > MAX_SOUND_BYTES * 2:
            return None
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
            if path == "/api/groups":
                return self._send(200, self.server.admin.api_list_groups())
            if path == "/api/cloud":
                return self._send(200, self.server.admin.api_get_cloud())
            if path == "/api/player-defaults":
                return self._send(200, self.server.admin.api_get_player_defaults())
            if path == "/api/sounds":
                return self._send(200, self.server.admin.api_list_sounds())
            # Public sound delivery — Sonos players fetch the audio from
            # this route via the URL get_sound_url() built. Decoupled
            # from /api/ so it has no Cache-Control: no-store header and
            # no JSON wrapper.
            m = re.match(r"^/sounds/([^/]+)/.+$", path)
            if m:
                return self._serve_sound_bytes(urllib.parse.unquote(m.group(1)))
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
            m = re.match(r"^/api/players/(.+)/presets$", path)
            if m:
                pid = urllib.parse.unquote(m.group(1))
                return self._send(200, self.server.admin.api_list_player_presets(pid))
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
            if path == "/api/groups":
                return self._send(200, self.server.admin.api_add_group(body))
            if path == "/api/sounds":
                return self._send(200, self.server.admin.api_add_sound(body))
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
            if path == "/api/player-defaults":
                return self._send(200, self.server.admin.api_set_player_defaults(body))
            m = re.match(r"^/api/players/(.+)/presets/(\d+)$", path)
            if m:
                pid = urllib.parse.unquote(m.group(1))
                slot = m.group(2)
                return self._send(200, self.server.admin.api_set_player_preset(pid, slot, body))
            self._err(404, "NOT_FOUND", "no such route")
        except ValueError as exc:
            self._err(400, "INVALID_ARG", str(exc))
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
            m = re.match(r"^/api/groups/(.+)$", path)
            if m:
                return self._send(200, self.server.admin.api_update_group(urllib.parse.unquote(m.group(1)), body))
            m = re.match(r"^/api/sounds/(.+)$", path)
            if m:
                return self._send(200, self.server.admin.api_update_sound(urllib.parse.unquote(m.group(1)), body))
            self._err(404, "NOT_FOUND", "no such route")
        except ValueError as exc:
            self._err(400, "INVALID_ARG", str(exc))
        except Exception as exc:  # noqa: BLE001
            self._err(500, "INTERNAL", str(exc))

    def do_DELETE(self):  # noqa: N802
        try:
            path = urllib.parse.urlparse(self.path).path
            m = re.match(r"^/api/players/(.+)/presets/(\d+)$", path)
            if m:
                pid = urllib.parse.unquote(m.group(1))
                slot = m.group(2)
                return self._send(200, self.server.admin.api_remove_player_preset(pid, slot))
            m = re.match(r"^/api/players/(.+)$", path)
            if m:
                return self._send(200, self.server.admin.api_remove_player(urllib.parse.unquote(m.group(1))))
            m = re.match(r"^/api/stations/(.+)$", path)
            if m:
                return self._send(200, self.server.admin.api_remove_station(urllib.parse.unquote(m.group(1))))
            m = re.match(r"^/api/groups/(.+)$", path)
            if m:
                return self._send(200, self.server.admin.api_remove_group(urllib.parse.unquote(m.group(1))))
            m = re.match(r"^/api/sounds/(.+)$", path)
            if m:
                return self._send(200, self.server.admin.api_remove_sound(urllib.parse.unquote(m.group(1))))
            self._err(404, "NOT_FOUND", "no such route")
        except ValueError as exc:
            self._err(400, "INVALID_ARG", str(exc))
        except Exception as exc:  # noqa: BLE001
            self._err(500, "INTERNAL", str(exc))

    def _serve_sound_bytes(self, sound_id):
        """Stream the audio bytes Sonos requested. Uses the MIME the
        upload provided so MP3/AAC/OGG decoders pick the right codec."""
        data = get_sound_bytes(sound_id)
        if data is None:
            return self._err(404, "NOT_FOUND", "no such sound")
        with _registry_lock:
            rec = _sounds.get(sound_id)
        mime = (rec or {}).get("mime", "audio/wav")
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "max-age=3600")
        self.end_headers()
        self.wfile.write(data)

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
        self.debug.set("Presets", 0)
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

        # Restore registries from HSL3 retentive stores before the first
        # tick. SSDP scans that come in later will MERGE on top — they
        # never overwrite manual entries or zone-name edits, so a
        # partially-incomplete startup scan can't lose the work the user
        # already put in.
        self._load_persisted(store)

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
            # Watchdog: if the HTTP server thread died (BaseHTTPServer
            # can exit on certain socket errors — FD exhaustion, OOM
            # inside a worker, etc.) the admin web UI becomes silently
            # unreachable. Detect the dead thread here and re-bind so
            # the user doesn't have to restart the HomeServer to get
            # the UI back. set_output runs in node context (Tick fires
            # in the right place).
            self._ensure_server_alive()

    def _ensure_server_alive(self):
        """Restart the HTTP server thread if it died. Idempotent — a
        live thread is left alone. Called from on_timer so the check
        runs on every discovery tick."""
        thread_dead = (self.server_thread is None
                       or not self.server_thread.is_alive())
        if not thread_dead:
            return
        try:
            self.logger.warning("Admin HTTP server thread died — rebinding")
        except Exception:
            pass
        # Tear down whatever's left so we don't leak the old socket.
        try:
            if self.server is not None:
                self.server.shutdown()
        except Exception:
            pass
        try:
            if self.server is not None:
                self.server.server_close()
        except Exception:
            pass
        self.server = None
        self.server_thread = None
        # Try the same port the previous bind succeeded on first; fall
        # back through the range / ephemeral.
        bound = self._start_server(self.listener_port or 0)
        if bound is None:
            try:
                self.fw.set_output("LastError", b"PORT_BIND_FAILED")
            except Exception:
                pass
            try:
                self.logger.error("Admin: rebind failed; UI still down")
            except Exception:
                pass
            return
        self.listener_port = bound
        try:
            self.fw.set_output("ListenPort", float(bound))
        except Exception:
            pass
        if self.debug is not None:
            try:
                self.debug.set("Listener port", float(bound))
                self.debug.inc("Rebinds")
            except Exception:
                pass

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
            # Wrap serve_forever so any exception that escapes (FD
            # exhaustion in accept(), OOM in a worker, anything else
            # BaseHTTPServer doesn't catch) lands in the logger and
            # the debug page instead of vanishing silently. The
            # watchdog in on_timer then rebinds.
            self.server_thread = threading.Thread(
                target=self._serve_until_dead,
                args=(server,),
                name="sonos-admin-http",
                daemon=True,
            )
            self.server_thread.start()
            return port
        return None

    def _serve_until_dead(self, server):
        """Run serve_forever and capture whatever kills it so the
        crash cause is visible — otherwise BaseHTTPServer's silent
        thread exit is impossible to diagnose."""
        try:
            server.serve_forever()
        except Exception as exc:  # noqa: BLE001
            try:
                self.logger.error(
                    "Admin HTTP server crashed: %s: %s",
                    type(exc).__name__, exc,
                )
            except Exception:
                pass
            if self.debug is not None:
                try:
                    self.debug.set(
                        "HTTP crash",
                        "{}: {}".format(type(exc).__name__, str(exc)[:120]),
                    )
                except Exception:
                    pass

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
        # Snapshot to retentive stores so a HomeServer restart inherits
        # the most recent discovery state. Discovery only ever adds /
        # refreshes records (never removes), so even a partial scan is
        # safe to persist.
        self._persist()

    def _publish_counters(self):
        with _registry_lock:
            np = len(_players)
            ns = len(_stations)
            ng = len(_groups)
            authed = bool(_cloud.get("accessToken"))
        self.fw.set_output("PlayerCount", float(np))
        self.fw.set_output("StationCount", float(ns))
        self.fw.set_output("GroupCount", float(ng))
        self.fw.set_output("CloudAuthorized", 1 if authed else 0)
        if self.debug is not None:
            self.debug.set("Players", float(np))
            self.debug.set("Presets", float(ns))
            self.debug.set("Group presets", float(ng))
            self.debug.set("Cloud authorized", "yes" if authed else "no")

    def _publish_counters_async(self):
        """Marshal _publish_counters into node context. Use this from any
        path that runs on the HTTP server thread (every REST handler) —
        calling set_output from a worker thread raises Hsl3ContextError."""
        self.fw.run_in_context(self._publish_counters, ())

    # ----- Persistence to HSL3 retentive stores ---------------------------

    def _load_persisted(self, store):
        """Restore registry state from the four retentive stores. Called
        from on_init, which is the only context that receives the
        ``store`` Hsl3Slots object. Failures (missing stores, malformed
        JSON) are swallowed so a corrupted store can never block startup
        — the registries simply start empty and the next discovery /
        UI add re-populates them. Source flag for restored SSDP entries
        is left as 'ssdp' so re-discovery merges them in place rather
        than appending a duplicate."""
        def _read_str(key):
            try:
                v = store[key].value
            except Exception:
                return ""
            if isinstance(v, bytes):
                try:
                    return v.decode("iso-8859-15", errors="replace")
                except Exception:
                    return ""
            return v or ""

        def _safe_json(s, fallback):
            if not s:
                return fallback
            try:
                return json.loads(s)
            except (ValueError, TypeError):
                return fallback

        with _registry_lock:
            for rec in _safe_json(_read_str("PersistedPlayers"), []):
                if isinstance(rec, dict) and rec.get("id"):
                    _players[rec["id"]] = rec
            for rec in _safe_json(_read_str("PersistedStations"), []):
                if isinstance(rec, dict) and rec.get("id"):
                    _stations[rec["id"]] = rec
            for rec in _safe_json(_read_str("PersistedGroups"), []):
                if isinstance(rec, dict) and rec.get("id"):
                    _groups[rec["id"]] = rec
            cloud_data = _safe_json(_read_str("PersistedCloud"), {})
            if isinstance(cloud_data, dict):
                for k in ("clientId", "clientSecret", "redirectBase",
                          "accessToken", "refreshToken"):
                    v = cloud_data.get(k)
                    if isinstance(v, str):
                        _cloud[k] = v
                exp = cloud_data.get("expiresAt")
                try:
                    if exp is not None:
                        _cloud["expiresAt"] = float(exp)
                except (TypeError, ValueError):
                    pass
            # Player tunable defaults — written from the Admin UI, read by
            # LBS 22000 as a fallback for its PollInterval / SubTimeout /
            # HttpTimeout / CallbackBase inputs.
            defaults = _safe_json(_read_str("PersistedPlayerDefaults"), {})
            if isinstance(defaults, dict):
                for k in ("pollInterval", "subTimeout", "httpTimeout"):
                    v = defaults.get(k)
                    try:
                        if v is not None:
                            _player_defaults[k] = int(v)
                    except (TypeError, ValueError):
                        pass
                cb = defaults.get("callbackBase")
                if isinstance(cb, str):
                    _player_defaults["callbackBase"] = cb
            # Uploaded sounds — the audio bytes ride along as base64 in
            # the same JSON blob so a HomeServer restart inherits both
            # the metadata AND the playable data. Built-in sounds are
            # NOT persisted; they're re-seeded from _BUILTIN_SOUNDS on
            # every on_init.
            import base64 as _b64
            for rec in _safe_json(_read_str("PersistedSounds"), []):
                if not isinstance(rec, dict) or not rec.get("id"):
                    continue
                if rec.get("source") == "builtin":
                    continue  # defensive — never restore over a builtin
                b64 = rec.pop("data_b64", "") or ""
                if not b64:
                    continue
                try:
                    data = _b64.b64decode(b64)
                except Exception:
                    continue
                rec["_data"] = data
                rec["size"] = len(data)
                _sounds[rec["id"]] = rec

    def _persist(self):
        """Serialize the four registries to retentive stores. Must run in
        node context (set_store is a node-context API). Records are
        encoded as compact JSON then iso-8859-15 bytes per the HSL3 SDK
        string-output contract."""
        with _registry_lock:
            # Build the uploaded-sounds payload: skip built-ins (they
            # re-seed on each boot) and re-encode the audio bytes as
            # base64 so the value is JSON-safe.
            import base64 as _b64
            sound_payload = []
            for rec in _sounds.values():
                if rec.get("source") == "builtin":
                    continue
                data = rec.get("_data") or b""
                sound_payload.append({
                    "id":       rec["id"],
                    "name":     rec["name"],
                    "filename": rec.get("filename", ""),
                    "source":   rec.get("source", "uploaded"),
                    "size":     len(data),
                    "mime":     rec.get("mime", "audio/wav"),
                    "data_b64": _b64.b64encode(data).decode("ascii"),
                })
            try:
                p = json.dumps(list(_players.values()), separators=(",", ":"))
                s = json.dumps(list(_stations.values()), separators=(",", ":"))
                g = json.dumps(list(_groups.values()), separators=(",", ":"))
                c = json.dumps(_cloud, separators=(",", ":"))
                d = json.dumps(_player_defaults, separators=(",", ":"))
                sounds_json = json.dumps(sound_payload, separators=(",", ":"))
            except (TypeError, ValueError) as exc:
                # Should never happen — every record is plain str/int/bool/
                # list/dict — but never let a JSON encode failure break the
                # web UI. Log and skip the persist for this cycle.
                try:
                    self.logger.warning("Persist serialization failed: %s", exc)
                except Exception:
                    pass
                return
        try:
            self.fw.set_store("PersistedPlayers",        p.encode("iso-8859-15", "replace"))
            self.fw.set_store("PersistedStations",       s.encode("iso-8859-15", "replace"))
            self.fw.set_store("PersistedGroups",         g.encode("iso-8859-15", "replace"))
            self.fw.set_store("PersistedCloud",          c.encode("iso-8859-15", "replace"))
            self.fw.set_store("PersistedPlayerDefaults", d.encode("iso-8859-15", "replace"))
            self.fw.set_store("PersistedSounds",         sounds_json.encode("iso-8859-15", "replace"))
        except Exception as exc:  # noqa: BLE001
            try:
                self.logger.warning("Persist set_store failed: %s", exc)
            except Exception:
                pass

    def _sync_async(self):
        """Convenience for HTTP-thread mutations: re-publish counters AND
        persist the registries to stores. One run_in_context call ensures
        both happen atomically in node context."""
        def _do():
            self._publish_counters()
            self._persist()
        self.fw.run_in_context(_do, ())

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
                "groupCount": len(_groups),
                "cloudAuthorized": bool(_cloud.get("accessToken")),
                "lastDiscoveryAt": (time.strftime("%Y-%m-%dT%H:%M:%S",
                                                  time.localtime(self._last_discovery_at))
                                    if self._last_discovery_at else None),
            }

    def api_list_players(self):
        with _registry_lock:
            # Emit a shallow copy of every record with a normalised
            # `presets` list so the UI can render each card's per-player
            # preset slots without an extra request per player.
            players = []
            for rec in _players.values():
                copy = dict(rec)
                copy["presets"] = _player_preset_list(rec)
                players.append(copy)
        return {
            "ok": True,
            "players": sorted(players, key=lambda p: (p.get("name") or "", p.get("ip") or "")),
            "presetSlots": PER_PLAYER_PRESET_SLOTS,
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
        self._sync_async()
        return {"ok": True, "player": rec}

    def api_remove_player(self, pid):
        with _registry_lock:
            removed = _players.pop(pid, None)
        if removed is None:
            raise ValueError("unknown player id")
        self._sync_async()
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
            # Per-player tunable overrides. Empty / 0 / negative
            # clears the override (back to the project default). The
            # validation floors mirror the Player module's clamps so
            # a bad value entered in the UI can't sneak past.
            for key, floor in (("pollInterval",     10),
                               ("subTimeout",       60),
                               ("httpTimeout",      2),
                               ("marqueeMaxLength", 4)):
                if key in body:
                    try:
                        v = int(body[key] or 0)
                    except (TypeError, ValueError):
                        raise ValueError("{} must be a number".format(key))
                    if v <= 0:
                        rec.pop(key, None)
                    else:
                        rec[key] = max(floor, v)
            if "callbackBase" in body:
                cb = (body["callbackBase"] or "").strip().rstrip("/")
                if cb:
                    rec["callbackBase"] = cb
                else:
                    rec.pop("callbackBase", None)
        self._sync_async()
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
        # Optional type tag captured from the favorite at add time
        # (radio / playlist / source / track / …). Shown in the presets
        # table; never affects playback dispatch (the Player block
        # routes by URI scheme, not by this label).
        ptype = (body.get("type") or "").strip()
        if not name or not uri:
            raise ValueError("name and uri required")
        sid = "s_" + str(int(time.time() * 1000))
        rec = {"id": sid, "name": name, "uri": uri,
               "metadata": metadata, "type": ptype}
        with _registry_lock:
            _stations[sid] = rec
        self._sync_async()
        return {"ok": True, "station": rec}

    def api_player_favorites(self, pid):
        """Return the player's Sonos Favorites (FV:2) plus its audio
        inputs (AI:). The latter is the canonical way to enumerate
        whatever audio sources the device actually exposes — Line-In on
        a Connect:Amp / Port / Five, Bluetooth on an Era 100 / Era 300
        / Move / Roam, TV input on a Beam / Arc, etc.

        Each AI: item is tagged with ``type=source`` (regardless of
        what UPnP class Sonos used) and the player's zone name is
        appended to the title so it's obvious which device the input
        belongs to when the same preset is wired across multiple
        players. Older firmware that doesn't expose AI: falls back to
        a synthetic ``Line-In`` entry built from the player's UUID so
        the integration still has *something* to bind to."""
        with _registry_lock:
            rec = _players.get(pid)
        if rec is None:
            raise ValueError("unknown player id")
        ip = rec.get("ip", "")
        if not ip:
            raise ValueError("player has no IP — run discovery first")
        favorites = browse_content(ip, "FV:2", count=200)
        sources = browse_content(ip, "AI:", count=20)

        zone = rec.get("zoneName") or rec.get("name") or rec.get("ip") or "Player"

        # AI: items vary across hardware: Sonos uses several upnp:class
        # values for inputs (audioBroadcast on legacy, audioInput on
        # newer). Force the type tag so the UI groups them as sources
        # regardless of the wire format. Tag the title with the zone so
        # cross-room source routing is unambiguous.
        for s in sources:
            base = s.get("title") or "Source"
            s["title"] = "{} ({})".format(base, zone)
            s["type"] = "source"

        # Back-compat: very old firmware doesn't expose AI:, in which
        # case fall back to the synthetic Line-In entry built from the
        # player's RINCON UUID — works for Connect:Amp / Port / Five
        # but not for any input other than the analogue line.
        if not sources and rec.get("uuid"):
            sources = [{
                "title": "Line-In ({})".format(zone),
                "class": "object.item.audioItem.audioInput",
                "uri": "x-rincon-stream:{}".format(rec["uuid"]),
                "metadata": "",
                "type": "source",
            }]

        return {"ok": True, "playerId": pid,
                "favorites": sources + favorites}

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
        self._sync_async()
        return {"ok": True}

    def api_remove_station(self, sid):
        with _registry_lock:
            removed = _stations.pop(sid, None)
        if removed is None:
            raise ValueError("unknown station id")
        self._sync_async()
        return {"ok": True}

    # ----- Per-player presets ---------------------------------------------
    #
    # Each player carries up to PER_PLAYER_PRESET_SLOTS (10) presets in
    # its own `presets` field. Slot numbers are fixed at 1..10 so a
    # given slot always means the same preset on that player. Global
    # presets keep their separate registry; they're addressed at
    # indices 11..(10 + len(_stations)) so global indices are
    # identical across every player. Together the per-player +
    # global lists feed PresetNextPrev on the Sonos Player block.

    def api_list_player_presets(self, pid):
        with _registry_lock:
            rec = _players.get(pid)
            if rec is None:
                raise ValueError("unknown player id")
            presets = _player_preset_list(rec)
        return {"ok": True,
                "playerId": pid,
                "slots": PER_PLAYER_PRESET_SLOTS,
                "presets": presets}

    def api_set_player_preset(self, pid, slot, body):
        """Upsert a per-player preset at the given slot (1..10). Body
        carries name + uri (required) plus optional metadata + type —
        same shape as the global preset record. Empty name/uri clears
        the slot (same effect as DELETE)."""
        try:
            slot = int(slot)
        except (TypeError, ValueError):
            raise ValueError("slot must be 1..{}".format(PER_PLAYER_PRESET_SLOTS))
        if not (1 <= slot <= PER_PLAYER_PRESET_SLOTS):
            raise ValueError("slot out of range 1..{}".format(PER_PLAYER_PRESET_SLOTS))
        name = (body.get("name") or "").strip()
        uri = (body.get("uri") or "").strip()
        metadata = body.get("metadata") or ""
        ptype = (body.get("type") or "").strip()
        with _registry_lock:
            rec = _players.get(pid)
            if rec is None:
                raise ValueError("unknown player id")
            presets = [p for p in (rec.get("presets") or [])
                       if isinstance(p, dict) and int(p.get("slot") or 0) != slot]
            if name and uri:
                presets.append({"slot": slot, "name": name, "uri": uri,
                                "metadata": metadata, "type": ptype})
            rec["presets"] = presets
        self._sync_async()
        return {"ok": True,
                "playerId": pid,
                "slot": slot,
                "presets": _player_preset_list(rec)}

    def api_remove_player_preset(self, pid, slot):
        try:
            slot = int(slot)
        except (TypeError, ValueError):
            raise ValueError("slot must be 1..{}".format(PER_PLAYER_PRESET_SLOTS))
        with _registry_lock:
            rec = _players.get(pid)
            if rec is None:
                raise ValueError("unknown player id")
            before = rec.get("presets") or []
            after = [p for p in before
                     if isinstance(p, dict) and int(p.get("slot") or 0) != slot]
            rec["presets"] = after
        self._sync_async()
        return {"ok": True}

    # ----- Group presets ---------------------------------------------------

    def api_list_groups(self):
        with _registry_lock:
            groups = list(_groups.values())
        return {
            "ok": True,
            "groups": sorted(groups, key=lambda g: g["name"].lower()),
        }

    def api_add_group(self, body):
        name = (body.get("name") or "").strip()
        master = (body.get("master") or "").strip()
        members_raw = body.get("members") or []
        if not name:
            raise ValueError("name required")
        if not master:
            raise ValueError("master required")
        if not isinstance(members_raw, list):
            raise ValueError("members must be a list of player ids")
        # Validate every player id exists in the registry, else the group
        # would be impossible to dispatch. Strip the master from members
        # automatically (no point sending it x-rincon:<itself>).
        with _registry_lock:
            known = set(_players.keys())
            for pid in [master] + list(members_raw):
                if pid not in known:
                    raise ValueError("unknown player id: {}".format(pid))
            members = [m for m in members_raw if m != master]
            gid = "g_" + str(int(time.time() * 1000))
            rec = {"id": gid, "name": name, "master": master, "members": members}
            _groups[gid] = rec
        self._sync_async()
        return {"ok": True, "group": rec}

    def api_update_group(self, gid, body):
        with _registry_lock:
            rec = _groups.get(gid)
            if rec is None:
                raise ValueError("unknown group id")
            known = set(_players.keys())
            if "name" in body:
                v = (body["name"] or "").strip()
                if not v:
                    raise ValueError("name cannot be empty")
                rec["name"] = v
            if "master" in body:
                m = (body["master"] or "").strip()
                if m and m not in known:
                    raise ValueError("unknown player id: {}".format(m))
                rec["master"] = m
            if "members" in body:
                v = body["members"] or []
                if not isinstance(v, list):
                    raise ValueError("members must be a list")
                for pid in v:
                    if pid not in known:
                        raise ValueError("unknown player id: {}".format(pid))
                rec["members"] = [m for m in v if m != rec.get("master")]
        self._sync_async()
        return {"ok": True, "group": rec}

    def api_remove_group(self, gid):
        with _registry_lock:
            removed = _groups.pop(gid, None)
        if removed is None:
            raise ValueError("unknown group id")
        self._sync_async()
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

    def api_get_player_defaults(self):
        with _registry_lock:
            return {"ok": True, "defaults": dict(_player_defaults)}

    def api_set_player_defaults(self, body):
        """Update the per-player tunable defaults. Each field is optional;
        missing fields keep their current value. Numeric fields are
        clamped to the same lower bounds the Player module enforces so
        a bad UI value can't break the integration."""
        with _registry_lock:
            if "pollInterval" in body:
                try:
                    _player_defaults["pollInterval"] = max(10, int(body["pollInterval"] or 0) or 60)
                except (TypeError, ValueError):
                    raise ValueError("pollInterval must be a number")
            if "subTimeout" in body:
                try:
                    _player_defaults["subTimeout"] = max(60, int(body["subTimeout"] or 0) or 1800)
                except (TypeError, ValueError):
                    raise ValueError("subTimeout must be a number")
            if "httpTimeout" in body:
                try:
                    _player_defaults["httpTimeout"] = max(2, int(body["httpTimeout"] or 0) or 5)
                except (TypeError, ValueError):
                    raise ValueError("httpTimeout must be a number")
            if "callbackBase" in body:
                _player_defaults["callbackBase"] = (body["callbackBase"] or "").strip().rstrip("/")
            if "marqueeMaxLength" in body:
                try:
                    v = int(body["marqueeMaxLength"] or 0)
                except (TypeError, ValueError):
                    raise ValueError("marqueeMaxLength must be a number")
                # 0 disables marquee entirely. Any positive value
                # enables scrolling for outputs that exceed it; floor
                # at 4 so the visible window is wide enough to read.
                _player_defaults["marqueeMaxLength"] = 0 if v <= 0 else max(4, v)
        self._sync_async()
        return {"ok": True, "defaults": dict(_player_defaults)}

    # ----- Sounds library --------------------------------------------------

    def api_list_sounds(self):
        with _registry_lock:
            sorted_sounds = sorted(_sounds.values(), key=lambda s: s["name"].lower())
        out = []
        for i, rec in enumerate(sorted_sounds, start=1):
            pub = _public_sound(rec)
            pub["index"] = i
            out.append(pub)
        return {"ok": True, "sounds": out, "maxBytes": MAX_SOUND_BYTES}

    def api_add_sound(self, body):
        """Upload a sound. ``body`` is JSON with:
            - name      (string, required)
            - filename  (string, optional — used in the public URL)
            - mime      (string, optional — defaults to audio/wav)
            - data_b64  (string, required — base64-encoded audio bytes)
        Rejects payloads bigger than MAX_SOUND_BYTES so a runaway upload
        can't blow the retentive-store budget."""
        import base64 as _b64
        name = (body.get("name") or "").strip()
        if not name:
            raise ValueError("name required")
        b64 = body.get("data_b64") or ""
        if not b64:
            raise ValueError("data_b64 required")
        try:
            data = _b64.b64decode(b64, validate=False)
        except Exception:
            raise ValueError("data_b64 is not valid base64")
        if not data:
            raise ValueError("data_b64 decoded to zero bytes")
        if len(data) > MAX_SOUND_BYTES:
            raise ValueError(
                "audio exceeds {} bytes".format(MAX_SOUND_BYTES)
            )
        filename = (body.get("filename") or "").strip() or (name + ".bin")
        mime = (body.get("mime") or "").strip() or _guess_audio_mime(filename)
        sid = "snd_" + str(int(time.time() * 1000))
        rec = {
            "id":       sid,
            "name":     name,
            "filename": filename,
            "mime":     mime,
            "source":   "uploaded",
            "size":     len(data),
            "_data":    data,
        }
        with _registry_lock:
            _sounds[sid] = rec
        self._sync_async()
        return {"ok": True, "sound": _public_sound(rec)}

    def api_update_sound(self, sid, body):
        with _registry_lock:
            rec = _sounds.get(sid)
            if rec is None:
                raise ValueError("unknown sound id")
            if "name" in body:
                v = (body["name"] or "").strip()
                if not v:
                    raise ValueError("name cannot be empty")
                rec["name"] = v
        self._sync_async()
        return {"ok": True, "sound": _public_sound(rec)}

    def api_remove_sound(self, sid):
        with _registry_lock:
            removed = _sounds.pop(sid, None)
        if removed is None:
            raise ValueError("unknown sound id")
        self._sync_async()
        return {"ok": True}

    def api_set_cloud(self, body):
        with _registry_lock:
            if "clientId" in body:
                _cloud["clientId"] = (body["clientId"] or "").strip()
            if "clientSecret" in body:
                _cloud["clientSecret"] = (body["clientSecret"] or "").strip()
            if "redirectBase" in body:
                _cloud["redirectBase"] = (body["redirectBase"] or "").rstrip("/")
        self._sync_async()
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
        # Update outputs + persist the new tokens.
        self._sync_async()
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
