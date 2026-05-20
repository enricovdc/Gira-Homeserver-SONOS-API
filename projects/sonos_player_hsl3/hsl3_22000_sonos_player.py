"""LBS 22000 - Sonos Player.

Controls a single Sonos player via the local UPnP/SOAP API on port 1400.
Handles play/pause/stop/next/prev, volume, mute, radio station playback,
status polling, and UPnP event subscription (with NOTIFY callback handling).

Compatible with Sonos firmware 2024+ / 2026: uses metadata-free DIDL-Lite
and the x-rincon-mp3radio direct-broadcast scheme, with a fallback ladder
for players that reject our SetAVTransportURI payload.

Inputs / outputs / store / timer keys must match config.json.
"""

import re
import socket
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests


# ---------------------------------------------------------------------------
# SOAP envelopes (metadata-free for 2024+ firmware compatibility)
# ---------------------------------------------------------------------------

ENV_PLAY = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
    's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
    '<s:Body><u:Play xmlns:u="urn:schemas-upnp-org:service:AVTransport:1">'
    '<InstanceID>0</InstanceID><Speed>1</Speed></u:Play></s:Body></s:Envelope>'
)
ENV_PAUSE = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
    '<s:Body><u:Pause xmlns:u="urn:schemas-upnp-org:service:AVTransport:1">'
    '<InstanceID>0</InstanceID></u:Pause></s:Body></s:Envelope>'
)
ENV_STOP = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
    '<s:Body><u:Stop xmlns:u="urn:schemas-upnp-org:service:AVTransport:1">'
    '<InstanceID>0</InstanceID></u:Stop></s:Body></s:Envelope>'
)
ENV_NEXT = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
    '<s:Body><u:Next xmlns:u="urn:schemas-upnp-org:service:AVTransport:1">'
    '<InstanceID>0</InstanceID></u:Next></s:Body></s:Envelope>'
)
ENV_PREV = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
    '<s:Body><u:Previous xmlns:u="urn:schemas-upnp-org:service:AVTransport:1">'
    '<InstanceID>0</InstanceID></u:Previous></s:Body></s:Envelope>'
)
ENV_SET_VOLUME = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
    '<s:Body><u:SetVolume xmlns:u="urn:schemas-upnp-org:service:RenderingControl:1">'
    "<InstanceID>0</InstanceID><Channel>Master</Channel>"
    "<DesiredVolume>{level}</DesiredVolume></u:SetVolume></s:Body></s:Envelope>"
)
ENV_GET_VOLUME = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
    '<s:Body><u:GetVolume xmlns:u="urn:schemas-upnp-org:service:RenderingControl:1">'
    "<InstanceID>0</InstanceID><Channel>Master</Channel></u:GetVolume></s:Body></s:Envelope>"
)
ENV_SET_MUTE = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
    '<s:Body><u:SetMute xmlns:u="urn:schemas-upnp-org:service:RenderingControl:1">'
    "<InstanceID>0</InstanceID><Channel>Master</Channel>"
    "<DesiredMute>{mute}</DesiredMute></u:SetMute></s:Body></s:Envelope>"
)
ENV_GET_MUTE = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
    '<s:Body><u:GetMute xmlns:u="urn:schemas-upnp-org:service:RenderingControl:1">'
    "<InstanceID>0</InstanceID><Channel>Master</Channel></u:GetMute></s:Body></s:Envelope>"
)
ENV_GET_TRANSPORT = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
    '<s:Body><u:GetTransportInfo xmlns:u="urn:schemas-upnp-org:service:AVTransport:1">'
    "<InstanceID>0</InstanceID></u:GetTransportInfo></s:Body></s:Envelope>"
)
ENV_GET_POSITION = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
    '<s:Body><u:GetPositionInfo xmlns:u="urn:schemas-upnp-org:service:AVTransport:1">'
    "<InstanceID>0</InstanceID></u:GetPositionInfo></s:Body></s:Envelope>"
)
# GetMediaInfo returns CurrentURI / CurrentURIMetaData — what was passed
# to the last SetAVTransportURI call. Distinct from GetPositionInfo's
# TrackURI: for queue playback CurrentURI is x-rincon-queue:..., TrackURI
# is the current queue item. Restoring to the queue (CurrentURI) is what
# resumes playback in the same source after a sound notification.
ENV_GET_MEDIA = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
    '<s:Body><u:GetMediaInfo xmlns:u="urn:schemas-upnp-org:service:AVTransport:1">'
    "<InstanceID>0</InstanceID></u:GetMediaInfo></s:Body></s:Envelope>"
)
ENV_SEEK_REL_TIME = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
    '<s:Body><u:Seek xmlns:u="urn:schemas-upnp-org:service:AVTransport:1">'
    "<InstanceID>0</InstanceID><Unit>REL_TIME</Unit>"
    "<Target>{pos}</Target></u:Seek></s:Body></s:Envelope>"
)
# Sonos S2 native announcement. Plays the clip on top of whatever's
# already playing — Sonos handles ducking + automatic resume.
# ClipType=CUSTOM with a StreamUrl tells the player to fetch and play
# our arbitrary audio file. AppId is a reverse-DNS identifier the
# player uses internally to track outstanding clips; the exact string
# doesn't affect playback. Priority=HIGH ensures the clip overrides
# any lower-priority audio.
ENV_LOAD_AUDIO_CLIP = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
    '<s:Body><u:LoadAudioClip xmlns:u="urn:schemas-sonos-com:service:AudioClip:1">'
    "<InstanceID>0</InstanceID>"
    "<AppId>com.gira.homeserver-sonos</AppId>"
    "<Name>{name}</Name>"
    "<ClipType>CUSTOM</ClipType>"
    "<Priority>HIGH</Priority>"
    "<StreamUrl>{url}</StreamUrl>"
    "</u:LoadAudioClip></s:Body></s:Envelope>"
)
ENV_SET_URI = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
    '<s:Body><u:SetAVTransportURI xmlns:u="urn:schemas-upnp-org:service:AVTransport:1">'
    "<InstanceID>0</InstanceID><CurrentURI>{uri}</CurrentURI>"
    "<CurrentURIMetaData>{meta}</CurrentURIMetaData></u:SetAVTransportURI>"
    "</s:Body></s:Envelope>"
)
ENV_SET_PLAY_MODE = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
    '<s:Body><u:SetPlayMode xmlns:u="urn:schemas-upnp-org:service:AVTransport:1">'
    "<InstanceID>0</InstanceID><NewPlayMode>{mode}</NewPlayMode>"
    "</u:SetPlayMode></s:Body></s:Envelope>"
)
ENV_GET_TRANSPORT_SETTINGS = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
    '<s:Body><u:GetTransportSettings xmlns:u="urn:schemas-upnp-org:service:AVTransport:1">'
    "<InstanceID>0</InstanceID></u:GetTransportSettings></s:Body></s:Envelope>"
)
# CurrentTransportActions is Sonos's "what transport buttons are currently
# available". Comma-separated list — typically "Play, Stop" for a radio
# stream, "Play, Stop, Pause, Seek, Next, Previous" for queue playback.
# Drives the *Allowed outputs so a Gira visualisation can grey out
# buttons that the player would reject right now.
ENV_GET_CURRENT_TRANSPORT_ACTIONS = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
    '<s:Body><u:GetCurrentTransportActions xmlns:u="urn:schemas-upnp-org:service:AVTransport:1">'
    "<InstanceID>0</InstanceID></u:GetCurrentTransportActions></s:Body></s:Envelope>"
)

# BecomeCoordinatorOfStandaloneGroup — ungroups the player from whatever
# zone group it's currently in, making it a standalone coordinator.
ENV_STANDALONE = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
    '<s:Body><u:BecomeCoordinatorOfStandaloneGroup xmlns:u="urn:schemas-upnp-org:service:AVTransport:1">'
    "<InstanceID>0</InstanceID>"
    "</u:BecomeCoordinatorOfStandaloneGroup></s:Body></s:Envelope>"
)


# Queue management — needed to play containers (Spotify / Apple Music
# playlists, Sonos saved queues). For these, SetAVTransportURI with the
# raw container URI is NOT a valid transport target; Sonos requires:
#   RemoveAllTracksFromQueue → AddURIToQueue → SetAVTransportURI(queue) → Play
ENV_REMOVE_QUEUE = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
    '<s:Body><u:RemoveAllTracksFromQueue xmlns:u="urn:schemas-upnp-org:service:AVTransport:1">'
    "<InstanceID>0</InstanceID></u:RemoveAllTracksFromQueue></s:Body></s:Envelope>"
)
ENV_ADD_URI = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
    '<s:Body><u:AddURIToQueue xmlns:u="urn:schemas-upnp-org:service:AVTransport:1">'
    "<InstanceID>0</InstanceID>"
    "<EnqueuedURI>{uri}</EnqueuedURI>"
    "<EnqueuedURIMetaData>{meta}</EnqueuedURIMetaData>"
    "<DesiredFirstTrackNumberEnqueued>0</DesiredFirstTrackNumberEnqueued>"
    "<EnqueueAsNext>0</EnqueueAsNext>"
    "</u:AddURIToQueue></s:Body></s:Envelope>"
)


# Container URI schemes that require queue-and-play (not direct
# SetAVTransportURI). Anything not matching these is treated as a direct
# stream / single-track URI and gets the existing direct-play path.
CONTAINER_URI_PREFIXES = (
    "x-rincon-cpcontainer:",   # Spotify / Apple Music / Amazon playlists & albums
    "file:///jffs/settings/savedqueues.rsq",  # Sonos saved queues (SQ:N)
    "x-rincon-playlist:",      # Internal Sonos playlists
)


def _is_container_uri(uri):
    if not uri:
        return False
    return any(uri.startswith(p) for p in CONTAINER_URI_PREFIXES)


def _is_group_join_uri(uri):
    """``x-rincon:RINCON_xxx`` — the URI scheme that joins a player to
    another player's zone group. Distinct from ``x-rincon-stream:`` /
    ``x-rincon-mp3radio:`` / ``x-rincon-cpcontainer:`` / ``x-rincon-queue:``
    / ``x-rincon-playlist:`` which look similar but mean entirely
    different things — those all start with ``x-rincon-`` (hyphen),
    only the bare ``x-rincon:`` (colon) is the group-join scheme.
    Used by `_action_start_radio` to skip the Play step: slaves inherit
    the master's transport state, calling Play would just error."""
    if not uri:
        return False
    return uri.startswith("x-rincon:") and not uri.startswith("x-rincon-")


# Sonos extends the standard UPnP transport-state alphabet with a
# ZPSTR_-prefixed family (BUFFERING, CONNECTING, PLAYING_TV, …). We
# strip the prefix AND title-case the whole value so the State output
# reads as "Playing" / "Paused" / "Buffering" rather than the raw
# ALL_CAPS shouting. A small explicit map covers the most common
# states so they get nicer forms (PAUSED_PLAYBACK -> "Paused" rather
# than "Paused playback", NO_MEDIA_PRESENT -> "No media", etc.); any
# unknown value falls back to "<First-letter-capital> <rest lower>".
_STATE_FRIENDLY = {
    "PLAYING":          "Playing",
    "PAUSED_PLAYBACK":  "Paused",
    "STOPPED":          "Stopped",
    "TRANSITIONING":    "Transitioning",
    "NO_MEDIA_PRESENT": "No media",
    "BUFFERING":        "Buffering",
    "CONNECTING":       "Connecting",
    "PLAYING_TV":       "Playing TV",
    "PLAYING_LINE_IN":  "Playing line-in",
}


def _normalize_state(raw):
    if not raw:
        return ""
    s = str(raw).strip()
    if s.startswith("ZPSTR_"):
        s = s[len("ZPSTR_"):]
    if s in _STATE_FRIENDLY:
        return _STATE_FRIENDLY[s]
    # Generic ALL_CAPS_WITH_UNDERSCORES -> "Title cased sentence" so
    # any future Sonos state we don't know about still looks friendly.
    return s.replace("_", " ").lower().capitalize()


def _friendly_title(raw):
    """Most track titles are real metadata ("Yesterday", "Hey Jude") and
    we want them through unchanged. But occasionally Sonos leaks a
    transport-state marker (ZPSTR_BUFFERING) into the <dc:title> field
    while a stream is connecting. Translate those the same way the
    State output is translated so the Title output stays readable."""
    if not raw:
        return ""
    s = str(raw).strip()
    if s.startswith("ZPSTR_") or s in _STATE_FRIENDLY:
        return _normalize_state(s)
    return s


# Sonos PlayMode alphabet. We expose only shuffle (bool) + repeat-all
# (bool) on the input side because that's the user-facing pair; on the
# output side we surface ShuffleState / RepeatState the same way. The
# six raw modes Sonos accepts are these — decompose and recompose
# routines below map (shuffle, repeat) <-> raw mode.
_PLAY_MODE_TO_FLAGS = {
    "NORMAL":              (False, False),
    "REPEAT_ALL":          (False, True),
    "REPEAT_ONE":          (False, True),   # surfaced as RepeatState=1
    "SHUFFLE_NOREPEAT":    (True,  False),
    "SHUFFLE":             (True,  True),   # Sonos: shuffle + repeat-all
    "SHUFFLE_REPEAT_ONE":  (True,  True),
}


def play_mode_to_flags(mode):
    if not mode:
        return (False, False)
    return _PLAY_MODE_TO_FLAGS.get(str(mode).strip().upper(), (False, False))


def flags_to_play_mode(shuffle, repeat):
    """Compose a Sonos PlayMode from the two boolean flags we expose."""
    if shuffle and repeat:
        return "SHUFFLE"
    if shuffle:
        return "SHUFFLE_NOREPEAT"
    if repeat:
        return "REPEAT_ALL"
    return "NORMAL"


def parse_transport_actions(raw):
    """Sonos exposes a comma-separated "what transport actions are
    currently allowed" list — like "Play, Stop, Pause, Seek, Next,
    Previous" for queue playback, or just "Play, Stop" for a radio
    stream. Returns a dict with the booleans we surface as the *Allowed
    outputs.

    Shuffle/Repeat are not in the upstream list because they are
    settings rather than transport actions. We approximate "allowed"
    for them as "queue playback is in effect" — i.e. Next is allowed.
    That matches the user expectation: you can't shuffle a radio
    stream, but any queued source is shufflable / repeatable."""
    if raw is None:
        return {
            "play": False, "pause": False, "stop": False,
            "next": False, "prev": False,
            "shuffle": False, "repeat": False,
        }
    tokens = {t.strip().lower() for t in str(raw).split(",") if t.strip()}
    next_allowed = "next" in tokens
    return {
        "play":    "play"     in tokens,
        "pause":   "pause"    in tokens,
        "stop":    "stop"     in tokens,
        "next":    next_allowed,
        "prev":    "previous" in tokens,
        # Shuffle / Repeat make sense only when there's a queue to
        # navigate. Use Next-allowed as the proxy.
        "shuffle": next_allowed,
        "repeat":  next_allowed,
    }


_DEFAULT_ALLOWED = parse_transport_actions(None)


def extract_group_master_uuid(track_uri):
    """When a player is a *member* of a Sonos zone group its current
    transport URI is "x-rincon:RINCON_<master-uuid>". Returns the
    master's UUID, or '' for coordinator / standalone players."""
    if not track_uri:
        return ""
    if track_uri.startswith("x-rincon:"):
        rest = track_uri[len("x-rincon:"):]
        # Strip query / fragment in case Sonos appends one.
        for sep in ("?", "#"):
            i = rest.find(sep)
            if i >= 0:
                rest = rest[:i]
        return rest
    return ""


def _xml_escape(s):
    """Escape a string for inclusion as XML element content. The URI and
    metadata both go inside elements (not attributes) so this is enough."""
    if not s:
        return ""
    return (s.replace("&", "&amp;")
             .replace("<", "&lt;")
             .replace(">", "&gt;")
             .replace('"', "&quot;")
             .replace("'", "&apos;"))


def _admin_player_record(spec):
    """Ask the Admin LBS for the full registry record for a player
    identified by spec (IP / MAC / UUID / custom name). Returns None
    when Admin isn't loaded or doesn't know this player."""
    if not spec:
        return None
    for mod_name, mod in list(sys.modules.items()):
        if mod is None:
            continue
        if "sonos_admin" in mod_name or "hsl3_22001" in mod_name:
            fn = getattr(mod, "get_player_record", None)
            if callable(fn):
                try:
                    rec = fn(spec)
                    if rec:
                        return rec
                except Exception:
                    pass
    return None


def _lookup_group_via_admin(spec):
    """Ask the Admin LBS for a group preset record. Returns a dict
    {id, name, master, members} or None when Admin isn't loaded or the
    group doesn't exist. Used by LBS 22000 when its GroupPreset input
    fires to look up which players form the group."""
    if spec is None:
        return None
    for mod_name, mod in list(sys.modules.items()):
        if mod is None:
            continue
        if "sonos_admin" in mod_name or "hsl3_22001" in mod_name:
            fn = getattr(mod, "get_group", None)
            if callable(fn):
                try:
                    rec = fn(spec)
                    if rec:
                        return rec
                except Exception:
                    pass
    return None


def _admin_player_tunables(spec):
    """Effective per-player tunables (PollInterval / SubTimeout /
    HttpTimeout / CallbackBase). Layers any per-player overrides on
    top of the project-wide defaults. Falls back to an empty dict
    when the Admin LBS isn't on the canvas — `_reload_config` then
    uses the hard-coded floors (60 / 1800 / 5)."""
    for mod_name, mod in list(sys.modules.items()):
        if mod is None:
            continue
        if "sonos_admin" in mod_name or "hsl3_22001" in mod_name:
            fn = getattr(mod, "get_player_tunables", None)
            if callable(fn):
                try:
                    d = fn(spec)
                    if isinstance(d, dict):
                        return d
                except Exception:
                    pass
    return {}


def _admin_player_defaults():
    """Pull the Admin LBS's project-wide player tunable defaults.
    Kept for tests that exercise the global-fallback layer directly;
    runtime configuration goes through ``_admin_player_tunables``."""
    for mod_name, mod in list(sys.modules.items()):
        if mod is None:
            continue
        if "sonos_admin" in mod_name or "hsl3_22001" in mod_name:
            fn = getattr(mod, "get_player_defaults", None)
            if callable(fn):
                try:
                    d = fn()
                    if isinstance(d, dict):
                        return d
                except Exception:
                    pass
    return {}


def _lookup_station_via_admin(idx_or_name):
    """If the Sonos Admin LBS is loaded, ask it for the full station
    record (uri + metadata + name + index). Returns None when Admin
    isn't present or doesn't know the station."""
    for mod_name, mod in list(sys.modules.items()):
        if mod is None:
            continue
        if "sonos_admin" in mod_name or "hsl3_22001" in mod_name:
            fn = getattr(mod, "get_station", None)
            if callable(fn):
                try:
                    rec = fn(idx_or_name)
                    if rec and rec.get("uri"):
                        return rec
                except Exception:
                    pass
    return None


def _admin_sound_record(idx_or_name):
    """Resolve a sound index/name into ``{"name", "url"}`` so the
    Player can build a native AudioClip envelope (which carries both
    the StreamUrl and a display Name) without two Admin calls. Returns
    None when Admin isn't loaded, the sound doesn't exist, or the
    Admin HTTP server hasn't bound a port yet."""
    for mod_name, mod in list(sys.modules.items()):
        if mod is None:
            continue
        if "sonos_admin" in mod_name or "hsl3_22001" in mod_name:
            get_url = getattr(mod, "get_sound_url", None)
            get_rec = getattr(mod, "get_sound", None)
            ref = getattr(mod, "_admin_instance_ref", None)
            if not (callable(get_url) and callable(get_rec) and ref):
                continue
            try:
                inst = ref.get("instance")
                if inst is None:
                    continue
                port = getattr(inst, "listener_port", 0) or 0
                lan_ip = _get_local_lan_ip()
                url = get_url(idx_or_name, lan_ip, port)
                if not url:
                    continue
                rec = get_rec(idx_or_name) or {}
                return {"name": rec.get("name", ""), "url": url}
            except Exception:
                pass
    return None


def _admin_station_count():
    """Total number of presets in the Admin library. Returns 0 when
    Admin isn't loaded or has nothing yet — `PresetNextPrev` then
    surfaces NO_PRESETS instead of dividing by zero."""
    for mod_name, mod in list(sys.modules.items()):
        if mod is None:
            continue
        if "sonos_admin" in mod_name or "hsl3_22001" in mod_name:
            fn = getattr(mod, "get_station_count", None)
            if callable(fn):
                try:
                    n = fn()
                    if isinstance(n, int):
                        return n
                except Exception:
                    pass
    return 0

SERVICE_PATHS = {
    "AVTransport":      "/MediaRenderer/AVTransport/Control",
    "RenderingControl": "/MediaRenderer/RenderingControl/Control",
    # Sonos S2's native announcement service. Plays a clip "on top of"
    # the current source — the player ducks / pauses music, plays the
    # notification, then resumes automatically. Equivalent to Home
    # Assistant's `play_media announce: true`. Older S1 hardware
    # doesn't expose this service, in which case `_action_play_sound`
    # falls back to the snapshot/restore code path.
    "AudioClip":        "/AudioClip/Control",
}
SERVICE_TYPES = {
    "AVTransport":      "urn:schemas-upnp-org:service:AVTransport:1",
    "RenderingControl": "urn:schemas-upnp-org:service:RenderingControl:1",
    "AudioClip":        "urn:schemas-sonos-com:service:AudioClip:1",
}
EVENT_PATHS = {
    "av": "/MediaRenderer/AVTransport/Event",
    "rc": "/MediaRenderer/RenderingControl/Event",
}
META_REJECT_CODES = {"714", "716", "402", "501", "800"}

# Port for the embedded NOTIFY listener. All instances share one listener
# bound on this port. If the port is unavailable, eventing is silently
# disabled and the module falls back to timer polling.
NOTIFY_PORT_DEFAULT = 8081


# ---------------------------------------------------------------------------
# Pure helpers (no framework calls — safely testable in isolation)
# ---------------------------------------------------------------------------

def normalize_radio_uri(uri):
    """Rewrite http(s):// to x-rincon-mp3radio:// for firmware-resilient
    direct-broadcast playback. Other schemes pass through unchanged."""
    if not uri:
        return uri
    if uri.startswith("http://"):
        return "x-rincon-mp3radio://" + uri[len("http://"):]
    if uri.startswith("https://"):
        return "x-rincon-mp3radio://" + uri[len("https://"):]
    return uri


def unescape_xml(s):
    if s is None:
        return ""
    return (s.replace("&lt;", "<")
             .replace("&gt;", ">")
             .replace("&quot;", '"')
             .replace("&apos;", "'")
             .replace("&amp;", "&"))


def extract_response_field(body, field):
    m = re.search(r"<{f}>([^<]*)</{f}>".format(f=re.escape(field)), body)
    return m.group(1) if m else None


def extract_soap_fault_code(body):
    m = re.search(r"<errorCode>(\d+)</errorCode>", body)
    return m.group(1) if m else None


def parse_notify(body):
    """Pull state/volume/mute/title/etc out of a Sonos LastChange envelope."""
    if not body:
        return {}
    m = re.search(r"<LastChange>([\s\S]*?)</LastChange>", body)
    if not m:
        return {}
    inner = unescape_xml(m.group(1))
    out = {}
    t = re.search(r'<TransportState\s+val="([^"]+)"', inner)
    if t:
        out["state"] = t.group(1)
    pm = re.search(r'<CurrentPlayMode\s+val="([^"]+)"', inner)
    if pm:
        out["playMode"] = pm.group(1)
    ta = re.search(r'<CurrentTransportActions\s+val="([^"]*)"', inner)
    if ta:
        out["transportActions"] = unescape_xml(ta.group(1))
    v = re.search(r'<Volume\s+channel="Master"\s+val="(\d+)"', inner)
    if v:
        out["volume"] = int(v.group(1))
    mu = re.search(r'<Mute\s+channel="Master"\s+val="([01])"', inner)
    if mu:
        out["mute"] = mu.group(1) == "1"
    u = re.search(r'<CurrentTrackURI\s+val="([^"]*)"', inner)
    if u:
        out["trackUri"] = unescape_xml(u.group(1))
    md = re.search(r'<CurrentTrackMetaData\s+val="([^"]*)"', inner)
    if md:
        meta = unescape_xml(md.group(1))
        # Default every metadata-derived key to empty before pulling
        # individual fields. Radio streams legitimately have no
        # <dc:creator> / <upnp:album>; treating "tag absent" as
        # "value is empty" lets the caller blank stale Artist /
        # Album outputs when the track changes from a Spotify track
        # (which has those tags) to a radio stream (which doesn't).
        out["title"] = ""
        out["artist"] = ""
        out["album"] = ""
        out["albumArtURI"] = ""
        out["streamContent"] = ""
        t = re.search(r"<dc:title>([^<]*)</dc:title>", meta)
        if t:
            out["title"] = unescape_xml(t.group(1))
        a = re.search(r"<dc:creator>([^<]*)</dc:creator>", meta)
        if a:
            out["artist"] = unescape_xml(a.group(1))
        al = re.search(r"<upnp:album>([^<]*)</upnp:album>", meta)
        if al:
            out["album"] = unescape_xml(al.group(1))
        aa = re.search(r"<upnp:albumArtURI>([^<]*)</upnp:albumArtURI>", meta) \
             or re.search(r"<r:albumArtURI>([^<]*)</r:albumArtURI>", meta)
        if aa:
            out["albumArtURI"] = unescape_xml(aa.group(1))
        sc = re.search(r"<r:streamContent>([^<]*)</r:streamContent>", meta)
        if sc:
            out["streamContent"] = unescape_xml(sc.group(1))
    return out


def to_str(value):
    """Decode an HSL3 input value that may arrive as bytes or str."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        try:
            return value.decode("iso-8859-15")
        except Exception:
            return value.decode("utf-8", errors="replace")
    return str(value)


def to_iso_bytes(value):
    """Encode a Python string for an HSL3 string output."""
    if value is None:
        return b""
    if isinstance(value, bytes):
        return value
    return str(value).encode("iso-8859-15", "replace")


def _is_ip_literal(value):
    if not value:
        return False
    parts = str(value).split(".")
    if len(parts) != 4:
        return False
    try:
        return all(0 <= int(p) <= 255 for p in parts)
    except ValueError:
        return False


def resolve_host_spec(spec):
    """Map a Host input value to a usable IP address.

    Accepts an IPv4 literal (used as-is), a MAC address, a player name, or
    a Sonos UUID. The latter three are resolved by asking the Sonos Admin
    LBS (22001) via its module-level `resolve_host` function — found via
    sys.modules to avoid a hard import dependency. If no Admin module is
    loaded, only IP literals work (the old behaviour). Returns '' when
    resolution fails; the caller treats that as offline.
    """
    if not spec:
        return ""
    spec = str(spec).strip()
    if _is_ip_literal(spec):
        return spec
    # Try every loaded module whose name looks like the admin module.
    # LBS 22001 = Sonos Admin (combined Discover + Admin since v1.0).
    for mod_name, mod in list(sys.modules.items()):
        if mod is None:
            continue
        if "sonos_admin" in mod_name or "hsl3_22001" in mod_name:
            resolver = getattr(mod, "resolve_host", None)
            if callable(resolver):
                try:
                    resolved = resolver(spec)
                    if resolved:
                        return resolved
                except Exception:
                    pass
    return ""


# ---------------------------------------------------------------------------
# Shared NOTIFY HTTP listener (class-level)
# ---------------------------------------------------------------------------

_listener_lock = threading.Lock()
_listener_started = False
_listener_port = None
# Maps the player's *routing key* (raw Host input — typically a UUID like
# RINCON_xxx) to its LogicModule instance. Using the spec rather than the
# resolved IP keeps event delivery working across DHCP renumbering: the
# subscription URL embeds the spec, the listener routes by the spec, and
# the IP can change underneath without the listener's mapping going stale.
_instances_by_host = {}


class _NotifyHandler(BaseHTTPRequestHandler):
    """Handles NOTIFY callbacks. URL format: /upnp/<routing-key>/<service>."""

    def do_NOTIFY(self):  # noqa: N802 — UPnP uses this method name
        try:
            length = int(self.headers.get("Content-Length", "0") or "0")
            body = self.rfile.read(length).decode("utf-8", errors="replace") if length else ""
            parts = self.path.strip("/").split("/")
            # Expect ["upnp", "<routing-key>", "<service>"]
            if len(parts) >= 3 and parts[0] == "upnp":
                # The key may contain characters that the subscriber URL-
                # encoded (e.g. colons in a MAC). Decode before lookup.
                host = urllib.parse.unquote(parts[1])
                service = parts[2]
                instance = _instances_by_host.get(host)
                if instance is not None:
                    instance._dispatch_notify(service, body)
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()
        except Exception:
            # Never let the listener thread die on a malformed callback.
            try:
                self.send_response(500)
                self.send_header("Content-Length", "0")
                self.end_headers()
            except Exception:
                pass

    def log_message(self, format, *args):  # silence stderr noise
        return


def _ensure_listener_started(preferred_port):
    """Start the shared NOTIFY listener if not already running. Returns the
    bound port number, or None if binding failed."""
    global _listener_started, _listener_port
    with _listener_lock:
        if _listener_started:
            return _listener_port
        for port in (preferred_port, preferred_port + 1, preferred_port + 2, 0):
            try:
                server = HTTPServer(("0.0.0.0", port), _NotifyHandler)
            except OSError:
                continue
            actual_port = server.server_address[1]
            t = threading.Thread(
                target=server.serve_forever,
                name="sonos-notify-listener",
                daemon=True,
            )
            t.start()
            _listener_started = True
            _listener_port = actual_port
            return actual_port
        return None


def _get_local_lan_ip():
    """Best-effort LAN IP detection without resolving hostnames.

    Returns 127.0.0.1 if discovery fails — events will not work but control
    still does, so the module degrades gracefully."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        finally:
            s.close()
    except OSError:
        return "127.0.0.1"


# ===========================================================================
# Mandatory class
# ===========================================================================

class LogicModule:

    def __init__(self, hsl3):
        self.fw = hsl3
        self.logger = hsl3.get_logger()
        self.debug = None

        # Per-instance state — survives across on_calc / on_timer cycles.
        self._host_spec = ""
        self._host = ""
        self._sid_av = ""
        self._sid_rc = ""
        self._sub_av_exp = 0.0
        self._sub_rc_exp = 0.0
        self._last_state = ""
        self._last_volume = -1
        self._last_mute = None
        self._last_title = ""
        self._last_artist = ""
        self._last_album = ""
        self._last_album_art = ""
        self._last_zone_name = ""
        self._last_shuffle = False
        self._last_repeat = False
        self._last_group_master = ""  # raw RINCON_xxx master UUID; "" when coordinator
        # Transport-actions snapshot drives the *Allowed outputs (PlayAllowed,
        # PauseAllowed, …). Starts all-false so a not-yet-polled player shows
        # every button as unavailable rather than incorrectly allowing them.
        self._last_allowed = dict(_DEFAULT_ALLOWED)
        self._uuid = ""           # Sonos RINCON UUID — needed to build queue URI
        self._active_station = 0
        self._active_station_name = ""
        self._online = False
        self._poll_interval_s = 60
        self._renew_threshold_s = 300  # renew when < this remaining
        self._sub_timeout_s = 1800
        self._http_timeout_s = 5
        self._vol_step = 2
        self._callback_base = ""
        self._notify_port = NOTIFY_PORT_DEFAULT

    # ----- HSL3 entry points -----------------------------------------------

    def on_init(self, inputs, store):
        self.debug = self.fw.create_debug_section()
        self.debug.set("Notifies received", 0)
        self.debug.set("Status polls", 0)
        self.debug.set("Subscribe attempts", 0)
        self.debug.set("Last error", "-")

        self._reload_config(inputs)
        # Register under the spec (raw Host input) — typically a UUID — so
        # the NOTIFY routing key stays stable across IP changes.
        if self._host_spec:
            _instances_by_host[self._host_spec] = self

        # Start the NOTIFY listener (idempotent — only first call binds).
        bound = _ensure_listener_started(self._notify_port)
        if bound is not None:
            self._notify_port = bound
            self.debug.set("Listener port", float(bound))
        else:
            self.logger.warning("Could not bind NOTIFY listener; eventing disabled")
            self.debug.set("Listener port", to_iso_bytes("disabled"))

        # Best-effort synchronous ZoneName from the Admin registry so the
        # output isn't blank for the first 5 s until the Tick HTTP fetch
        # completes. Admin lookup is just dict access — safe in node
        # context. If Admin isn't loaded the output stays empty until the
        # tick worker runs the HTTP fallback.
        rec = _admin_player_record(self._host_spec)
        if rec and rec.get("zoneName"):
            self._last_zone_name = rec["zoneName"]
            self.fw.set_output("ZoneName", to_iso_bytes(self._last_zone_name))

        # First tick after a short delay so HS finishes initialising.
        self.fw.set_timer("Tick", 5)

    def on_calc(self, inputs):
        prev_spec = self._host_spec
        self._reload_config(inputs)
        if self._host_spec != prev_spec:
            # Re-register under the new routing key (the new spec value).
            if prev_spec and _instances_by_host.get(prev_spec) is self:
                _instances_by_host.pop(prev_spec, None)
            if self._host_spec:
                _instances_by_host[self._host_spec] = self
            # Invalidate subscriptions for the old spec.
            self._sid_av = ""
            self._sid_rc = ""
            self._sub_av_exp = 0.0
            self._sub_rc_exp = 0.0

        if not self._host:
            self._write_error("NO_HOST_CONFIGURED")
            return

        # Edge-triggered controls.
        if inputs["Play"].changed and inputs["Play"].value != 0:
            self._run_control_threaded(self._action_play)
        if inputs["Pause"].changed and inputs["Pause"].value != 0:
            self._run_control_threaded(self._action_pause)
        if inputs["Stop"].changed and inputs["Stop"].value != 0:
            self._run_control_threaded(self._action_stop)
        if inputs["Next"].changed and inputs["Next"].value != 0:
            self._run_control_threaded(self._action_next)
        if inputs["Prev"].changed and inputs["Prev"].value != 0:
            self._run_control_threaded(self._action_previous)

        # SetVolume / SetMute / SetShuffle / SetRepeat are commonly
        # wired to the same KNX group address as their matching status
        # output (Volume / Mute / ShuffleState / RepeatState) — Gira
        # QuadClient slider widgets do this by default. Suppress the
        # SOAP dispatch when the requested value already matches the
        # last known player state; that's the echo from our own
        # output write rather than a real user request. Combined with
        # the SBC guard on the output side this fully breaks the
        # feedback loop.
        if inputs["SetVolume"].changed:
            level = max(0, min(100, int(inputs["SetVolume"].value or 0)))
            if level != self._last_volume:
                self._run_control_threaded(lambda v=level: self._action_set_volume(v))

        if inputs["VolUp"].changed and inputs["VolUp"].value != 0:
            self._run_control_threaded(lambda: self._action_adjust_volume(+self._vol_step))
        if inputs["VolDown"].changed and inputs["VolDown"].value != 0:
            self._run_control_threaded(lambda: self._action_adjust_volume(-self._vol_step))
        # KNX DPT 1.008 "Up/Down" rocker — wire the single group address
        # straight to VolUpDown. Each write (1 = up, 0 = down) dispatches
        # one step of size VolStep. Saves the integrator a pair of helper
        # logic blocks that would otherwise route Up to VolUp and Down to
        # VolDown.
        if inputs["VolUpDown"].changed:
            delta = +self._vol_step if inputs["VolUpDown"].value else -self._vol_step
            self._run_control_threaded(lambda d=delta: self._action_adjust_volume(d))

        if inputs["SetMute"].changed:
            mute = inputs["SetMute"].value != 0
            # Loop suppression — see SetVolume comment above.
            if self._last_mute is None or bool(self._last_mute) != mute:
                self._run_control_threaded(lambda m=mute: self._action_set_mute(m))
        if inputs["MuteToggle"].changed and inputs["MuteToggle"].value != 0:
            self._run_control_threaded(self._action_toggle_mute)

        # Shuffle / Repeat-All — two booleans the user sets independently
        # but Sonos exposes only as a combined PlayMode enum. We compose
        # the new mode from the latest desired (shuffle, repeat) pair,
        # taking the current internal state for the input that didn't
        # change this cycle so toggling one doesn't accidentally reset
        # the other.
        if inputs["SetShuffle"].changed or inputs["SetRepeat"].changed:
            new_shuffle = (bool(inputs["SetShuffle"].value)
                           if inputs["SetShuffle"].changed else self._last_shuffle)
            new_repeat = (bool(inputs["SetRepeat"].value)
                          if inputs["SetRepeat"].changed else self._last_repeat)
            # Loop suppression — same idea as SetVolume above.
            if (new_shuffle != bool(self._last_shuffle)
                    or new_repeat != bool(self._last_repeat)):
                self._run_control_threaded(
                    lambda s=new_shuffle, r=new_repeat: self._action_set_play_mode(s, r)
                )

        # Start a radio station from the admin's central library OR from
        # the per-player StationNUri inputs. Two routes:
        #   - StartRadio     (number) selects by alphabetical index 1..N
        #   - StartRadioName (string) selects by name (case-insensitive)
        if inputs["StartRadio"].changed:
            idx = int(inputs["StartRadio"].value or 0)
            if idx > 0:
                self._run_control_threaded(lambda: self._action_start_radio(idx))
        if inputs["StartRadioName"].changed:
            name = to_str(inputs["StartRadioName"].value).strip()
            if name:
                self._run_control_threaded(lambda: self._action_start_radio(name))

        # Group presets — dispatch by index or name.
        if inputs["GroupPreset"].changed:
            idx = int(inputs["GroupPreset"].value or 0)
            if idx > 0:
                self._run_control_threaded(lambda: self._action_group_form(idx))
        if inputs["GroupPresetName"].changed:
            gname = to_str(inputs["GroupPresetName"].value).strip()
            if gname:
                self._run_control_threaded(lambda: self._action_group_form(gname))

        # Ungroup — make THIS player a standalone coordinator.
        if inputs["Ungroup"].changed and inputs["Ungroup"].value != 0:
            self._run_control_threaded(self._action_ungroup)

        if inputs["Resubscribe"].changed and inputs["Resubscribe"].value != 0:
            self._sid_av = ""
            self._sid_rc = ""
            self._sub_av_exp = 0.0
            self._sub_rc_exp = 0.0
            self._run_control_threaded(self._maintain_subscriptions)

        # Value-driven toggles. Unlike the rising-edge Play / Pause /
        # Next / Prev / StartRadio inputs above, these dispatch on
        # *any* change of value — pattern-matched to a single KNX 1-bit
        # group address. Each value carries its own action so the input
        # works equally well for "0 = pause / 1 = play" buttons in a
        # visualisation and for two-state KNX switches.
        if inputs["PlayPause"].changed:
            if inputs["PlayPause"].value:
                self._run_control_threaded(self._action_play)
            else:
                self._run_control_threaded(self._action_pause)
        if inputs["NextPrev"].changed:
            if inputs["NextPrev"].value:
                self._run_control_threaded(self._action_next)
            else:
                self._run_control_threaded(self._action_previous)
        if inputs["PresetNextPrev"].changed:
            direction = 1 if inputs["PresetNextPrev"].value else -1
            self._run_control_threaded(
                lambda d=direction: self._action_step_preset(d)
            )

        # PlaySound — write the alphabetical index of a sound in the
        # Admin library. Sound playback snapshots the current state,
        # plays the clip via Sonos's standard SetAVTransportURI + Play,
        # then restores whatever was playing before (preset / queue /
        # radio, position, volume, mute, transport state).
        if inputs["PlaySound"].changed:
            idx = int(inputs["PlaySound"].value or 0)
            if idx > 0:
                self._run_control_threaded(
                    lambda i=idx: self._action_play_sound(i)
                )

    def on_timer(self, timer):
        if timer["Tick"].changed:
            # Re-arm immediately so we never miss a tick.
            self.fw.set_timer("Tick", self._poll_interval_s)
            self._run_control_threaded(self._tick_work)

    # ----- Public API used by the shared NOTIFY listener -------------------

    def _dispatch_notify(self, service, body):
        """Called from the listener thread when a NOTIFY arrives for this host."""
        parsed = parse_notify(body)
        if not parsed:
            return
        self.fw.run_in_context(self._apply_notify_parsed, (parsed,))

    def _apply_notify_parsed(self, parsed):
        """Runs in node context — safe to call set_output."""
        if self.debug is not None:
            self.debug.inc("Notifies received")
        self._online = True
        if "state" in parsed:
            self._last_state = _normalize_state(parsed["state"])
        if "playMode" in parsed:
            self._last_shuffle, self._last_repeat = play_mode_to_flags(parsed["playMode"])
        if "volume" in parsed:
            self._last_volume = parsed["volume"]
        if "mute" in parsed:
            self._last_mute = parsed["mute"]
        # parse_notify guarantees that when a CurrentTrackMetaData
        # block was found, the title / artist / album / albumArtURI
        # keys are all present (possibly empty). Always overwrite
        # so the previous track's Artist doesn't leak when going
        # from a Spotify track (rich metadata) to a radio stream
        # (no <dc:creator>/<upnp:album>). streamContent overrides
        # title for radio playback when it's non-empty.
        if "title" in parsed:
            self._last_title = _friendly_title(parsed["title"])
        if "streamContent" in parsed and parsed["streamContent"]:
            self._last_title = _friendly_title(parsed["streamContent"])
        if "artist" in parsed:
            self._last_artist = parsed["artist"]
        if "album" in parsed:
            self._last_album = parsed["album"]
        if "albumArtURI" in parsed:
            self._last_album_art = self._absolute_album_art(parsed["albumArtURI"])
        if "trackUri" in parsed:
            self._last_group_master = extract_group_master_uuid(parsed["trackUri"])
        if "transportActions" in parsed:
            self._last_allowed = parse_transport_actions(parsed["transportActions"])
        self._publish_outputs()

    # ----- Config / input reading ------------------------------------------

    def _reload_config(self, inputs):
        self._host_spec = to_str(inputs["Host"].value).strip()
        # Resolve the spec (IP, MAC, name, UUID) to a current IP via the
        # admin registry. Falls back to using the spec literally if it
        # looks like an IP (admin not present).
        resolved = resolve_host_spec(self._host_spec)
        self._host = resolved or (self._host_spec if _is_ip_literal(self._host_spec) else "")
        self._vol_step = max(1, int(inputs["VolStep"].value or 2))

        # Tunables (PollInterval / SubTimeout / HttpTimeout /
        # CallbackBase) live entirely in the Admin: a project-wide
        # default plus an optional per-player override stored on the
        # player record. The Player block has no inputs for them —
        # the integrator edits the values in the Admin web UI.
        tunables = _admin_player_tunables(self._host_spec)
        self._poll_interval_s = max(10, int(tunables.get("pollInterval") or 60))
        self._sub_timeout_s   = max(60, int(tunables.get("subTimeout")   or 1800))
        self._renew_threshold_s = max(30, self._sub_timeout_s // 6)
        self._http_timeout_s  = max(2,  int(tunables.get("httpTimeout")  or 5))
        cb = (tunables.get("callbackBase") or "").strip().rstrip("/")
        if not cb:
            cb = "http://{}:{}".format(_get_local_lan_ip(), self._notify_port)
        self._callback_base = cb

    # ----- Action wrappers -------------------------------------------------

    def _run_control_threaded(self, fn):
        """Run a control action in a thread so on_calc does not block on HTTP."""
        t = threading.Thread(target=self._run_safely, args=(fn,), daemon=True)
        t.start()

    def _run_safely(self, fn):
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 — log everything
            try:
                self.logger.exception("Control action failed: %s", exc)
            except Exception:
                pass
            self.fw.run_in_context(self._write_error, ("EXCEPTION: {}".format(exc),))

    # ----- HTTP / SOAP -----------------------------------------------------

    def _soap(self, service, action, envelope):
        url = "http://{}:1400{}".format(self._host, SERVICE_PATHS[service])
        headers = {
            "Content-Type": 'text/xml; charset="utf-8"',
            "SOAPACTION": '"{}#{}"'.format(SERVICE_TYPES[service], action),
        }
        try:
            resp = requests.post(url, data=envelope.encode("utf-8"),
                                 headers=headers, timeout=self._http_timeout_s)
        except requests.exceptions.RequestException as e:
            return False, "", "UNREACHABLE"
        if 200 <= resp.status_code < 300:
            return True, resp.text, ""
        code = extract_soap_fault_code(resp.text) or "HTTP_{}".format(resp.status_code)
        return False, resp.text, code

    def _upnp(self, method, path, headers):
        """SUBSCRIBE / UNSUBSCRIBE need a custom HTTP method that `requests`
        supports via the lower-level Session.request() call."""
        url = "http://{}:1400{}".format(self._host, path)
        try:
            resp = requests.request(method, url, headers=headers,
                                    timeout=self._http_timeout_s)
        except requests.exceptions.RequestException:
            return None, {}
        return resp.status_code, {k.lower(): v for k, v in resp.headers.items()}

    # ----- Control actions (run in worker threads) --------------------------

    def _action_play(self):
        ok, _body, err = self._soap("AVTransport", "Play", ENV_PLAY)
        if ok:
            self.fw.run_in_context(self._mark_state, ("PLAYING",))
        else:
            self.fw.run_in_context(self._write_error, (err,))

    def _action_pause(self):
        ok, _body, err = self._soap("AVTransport", "Pause", ENV_PAUSE)
        if ok:
            self.fw.run_in_context(self._mark_state, ("PAUSED_PLAYBACK",))
        else:
            self.fw.run_in_context(self._write_error, (err,))

    def _action_stop(self):
        ok, _body, err = self._soap("AVTransport", "Stop", ENV_STOP)
        if ok:
            self.fw.run_in_context(self._mark_state, ("STOPPED",))
        else:
            self.fw.run_in_context(self._write_error, (err,))

    def _action_next(self):
        ok, _body, err = self._soap("AVTransport", "Next", ENV_NEXT)
        if not ok:
            self.fw.run_in_context(self._write_error, (err,))

    def _action_previous(self):
        ok, _body, err = self._soap("AVTransport", "Previous", ENV_PREV)
        if not ok:
            self.fw.run_in_context(self._write_error, (err,))

    def _action_set_volume(self, level):
        level = max(0, min(100, int(level)))
        ok, _body, err = self._soap(
            "RenderingControl", "SetVolume", ENV_SET_VOLUME.format(level=level)
        )
        if ok:
            self.fw.run_in_context(self._mark_volume, (level,))
        else:
            self.fw.run_in_context(self._write_error, (err,))

    def _get_volume_blocking(self):
        ok, body, _err = self._soap("RenderingControl", "GetVolume", ENV_GET_VOLUME)
        if not ok:
            return None
        raw = extract_response_field(body, "CurrentVolume")
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None

    def _action_adjust_volume(self, delta):
        current = self._get_volume_blocking()
        if current is None:
            self.fw.run_in_context(self._write_error, ("GET_VOLUME_FAILED",))
            return
        self._action_set_volume(current + delta)

    def _action_set_mute(self, mute):
        ok, _body, err = self._soap(
            "RenderingControl", "SetMute", ENV_SET_MUTE.format(mute="1" if mute else "0")
        )
        if ok:
            self.fw.run_in_context(self._mark_mute, (bool(mute),))
        else:
            self.fw.run_in_context(self._write_error, (err,))

    def _action_toggle_mute(self):
        ok, body, err = self._soap("RenderingControl", "GetMute", ENV_GET_MUTE)
        if not ok:
            self.fw.run_in_context(self._write_error, (err,))
            return
        current = extract_response_field(body, "CurrentMute") == "1"
        self._action_set_mute(not current)

    def _action_set_play_mode(self, shuffle, repeat):
        mode = flags_to_play_mode(bool(shuffle), bool(repeat))
        envelope = ENV_SET_PLAY_MODE.format(mode=mode)
        ok, _body, err = self._soap("AVTransport", "SetPlayMode", envelope)
        if ok:
            self.fw.run_in_context(self._mark_play_mode, (bool(shuffle), bool(repeat)))
        else:
            self.fw.run_in_context(self._write_error, (err or "SET_PLAY_MODE_FAILED",))

    def _get_play_mode_blocking(self):
        """Read CurrentPlayMode from GetTransportSettings. Returns the
        raw mode string ("NORMAL" / "REPEAT_ALL" / …) or None on failure."""
        ok, body, _err = self._soap(
            "AVTransport", "GetTransportSettings", ENV_GET_TRANSPORT_SETTINGS
        )
        if not ok:
            return None
        return extract_response_field(body, "PlayMode")

    def _get_transport_actions_blocking(self):
        """Read CurrentTransportActions. Returns the raw comma-separated
        string ("Play, Stop, Pause, Seek, Next, Previous") or None on
        failure."""
        ok, body, _err = self._soap(
            "AVTransport", "GetCurrentTransportActions",
            ENV_GET_CURRENT_TRANSPORT_ACTIONS,
        )
        if not ok:
            return None
        return extract_response_field(body, "Actions")

    def _play_via_queue(self, uri, metadata, active_idx, active_name=""):
        """Queue-and-play path for container URIs (playlist / album /
        Sonos saved queue). The standard Sonos sequence is:

            RemoveAllTracksFromQueue       (start with an empty queue)
            AddURIToQueue(uri, metadata)   (load the container)
            SetAVTransportURI(queue_uri)   (switch transport to queue)
            Play

        queue_uri is "x-rincon-queue:<this-player's-UUID>#0" — Sonos
        won't accept a relative form, so we need the player's UUID.
        Cached in self._uuid (fetched from Admin or device XML)."""
        uuid = self._resolve_uuid()
        if not uuid:
            self.fw.run_in_context(self._write_error, ("PLAYLIST_NO_UUID",))
            return

        # If this player is currently a slave (its transport follows
        # another player via x-rincon:UUID), queue operations are
        # either rejected or silently no-op — the slave doesn't own
        # the playing queue. Detach first via
        # BecomeCoordinatorOfStandaloneGroup so the queue dance below
        # operates on this player's own (empty) queue and the
        # subsequent SetAVTransportURI(x-rincon-queue:UUID#0) switch
        # actually plays. Idempotent on non-slaves, so safe to call
        # unconditionally; we gate on _last_group_master only to skip
        # the wasted SOAP when we're already standalone.
        if self._last_group_master:
            self._soap(
                "AVTransport", "BecomeCoordinatorOfStandaloneGroup",
                ENV_STANDALONE,
            )
            # Optimistic clear so a chained second action this cycle
            # doesn't try to detach again. The next NOTIFY confirms.
            self._last_group_master = ""

        # Step 1: clear the existing queue. Best-effort — some Sonos
        # firmware variants return an empty 200 even on a previously
        # empty queue, so we don't treat a 'fault' here as fatal.
        self._soap("AVTransport", "RemoveAllTracksFromQueue", ENV_REMOVE_QUEUE)

        # Step 2: enqueue the container with its metadata. For
        # cloud-service containers (cpcontainer) the metadata carries the
        # music-service binding — without it Sonos can't resolve the URI.
        env_add = ENV_ADD_URI \
            .replace("{uri}", _xml_escape(uri)) \
            .replace("{meta}", _xml_escape(metadata))
        ok, _b, err = self._soap("AVTransport", "AddURIToQueue", env_add)
        if not ok:
            self.fw.run_in_context(self._write_error, (err or "ADD_QUEUE_FAILED",))
            return

        # Step 3: switch transport to the player's queue.
        queue_uri = "x-rincon-queue:{}#0".format(uuid)
        env_switch = ENV_SET_URI \
            .replace("{uri}", _xml_escape(queue_uri)) \
            .replace("{meta}", "")
        ok, _b, err = self._soap("AVTransport", "SetAVTransportURI", env_switch)
        if not ok:
            self.fw.run_in_context(self._write_error, (err or "QUEUE_TRANSPORT_FAILED",))
            return

        # Step 4: play.
        ok, _b, err = self._soap("AVTransport", "Play", ENV_PLAY)
        if not ok:
            self.fw.run_in_context(self._write_error, (err or "PLAY_FAILED",))
            return
        self.fw.run_in_context(self._mark_active_station, (active_idx, active_name))

    # ----- Group preset actions --------------------------------------------

    def _action_group_form(self, spec):
        """Form a Sonos zone group from a predefined preset. The Admin
        block holds the master + members list; we resolve every player
        to an IP and the master to a UUID, then issue
        SetAVTransportURI(x-rincon:<master-uuid>) on each member. The
        master needs no action — its current content becomes the group's
        content. Members joining stop their own playback and mirror the
        master."""
        group = _lookup_group_via_admin(spec)
        if not group:
            self.fw.run_in_context(self._write_error, ("GROUP_NOT_FOUND: {}".format(spec),))
            return
        master_id = group.get("master") or ""
        members = group.get("members") or []
        master_rec = _admin_player_record(master_id)
        if not master_rec:
            self.fw.run_in_context(self._write_error, ("GROUP_MASTER_UNKNOWN: {}".format(master_id),))
            return
        master_uuid = master_rec.get("uuid", "")
        if not master_uuid:
            self.fw.run_in_context(self._write_error, ("GROUP_MASTER_NO_UUID",))
            return
        join_uri = "x-rincon:{}".format(master_uuid)
        env_join = ENV_SET_URI.replace("{uri}", _xml_escape(join_uri)).replace("{meta}", "")
        failed = []
        for member_id in members:
            mrec = _admin_player_record(member_id)
            if not mrec or not mrec.get("ip"):
                failed.append(member_id)
                continue
            # Issue SetAVTransportURI directly on the member by
            # temporarily swapping our host for the SOAP call.
            saved_host = self._host
            try:
                self._host = mrec["ip"]
                ok, _b, err = self._soap("AVTransport", "SetAVTransportURI", env_join)
            finally:
                self._host = saved_host
            if not ok:
                failed.append(member_id)
        if failed:
            self.fw.run_in_context(
                self._write_error,
                ("GROUP_PARTIAL: {} members not joined".format(len(failed)),),
            )

    def _action_ungroup(self):
        """Break THIS player out of whatever zone group it's in. Sonos's
        BecomeCoordinatorOfStandaloneGroup detaches the player from the
        current group. If the player is already standalone it's a no-op."""
        ok, _b, err = self._soap(
            "AVTransport", "BecomeCoordinatorOfStandaloneGroup", ENV_STANDALONE
        )
        if not ok:
            self.fw.run_in_context(self._write_error, (err or "UNGROUP_FAILED",))

    def _action_start_radio(self, spec):
        """Start a preset identified either by a positive integer
        (alphabetical index into the Admin preset library) OR by the
        preset name (case-insensitive lookup in the same library).

        The Admin block (LBS 22001) MUST be on the canvas for preset
        playback to work — its registry is the single source of truth
        for URIs and the DIDL-Lite metadata required for Sonos cloud
        favorites (TuneIn, Spotify, …). Errors surface as
        PRESET_NOT_FOUND on the LastError output.
        """
        # The Admin block's preset library is the single source of
        # truth — there are no per-player preset slots anymore. If no
        # match exists in the library (admin not loaded, or unknown
        # spec) we surface PRESET_NOT_FOUND.
        admin_rec = _lookup_station_via_admin(spec)
        if not admin_rec or not admin_rec.get("uri"):
            self.fw.run_in_context(
                self._write_error,
                ("PRESET_NOT_FOUND: {}".format(spec),),
            )
            return
        uri = admin_rec["uri"]
        metadata = admin_rec.get("metadata") or ""
        # ``index`` and ``name`` come from Admin's get_station so the
        # ActiveStation + ActiveStationName outputs are stable whether
        # the caller used the numeric index or the preset name.
        active_idx = int(admin_rec.get("index") or 0)
        active_name = admin_rec.get("name", "") or ""
        # Optimistic feedback: post "Loading: <name>" before the SOAP
        # dispatch starts so the visualisation moves immediately when
        # the integrator triggers PresetNextPrev. Preset playback
        # involves multi-step SOAP (join URIs do per-member calls,
        # queue playback does Remove + Add + SwitchTransport + Play),
        # and without this hook ActiveStationName would otherwise
        # only update after the whole dispatch completed.
        self.fw.run_in_context(self._mark_active_station_loading,
                               (active_idx, active_name))

        # Group-join preset: the URI is "x-rincon:RINCON_<master>". Sending
        # it via SetAVTransportURI makes this player a slave of the
        # master's zone group; the slave then auto-inherits the master's
        # transport state, so we deliberately DON'T issue Play afterwards.
        if _is_group_join_uri(uri):
            envelope = ENV_SET_URI.replace("{uri}", _xml_escape(uri)) \
                                  .replace("{meta}", "")
            ok, _b, err = self._soap("AVTransport", "SetAVTransportURI", envelope)
            if ok:
                # Optimistic update — the AVTransport NOTIFY confirming
                # the slave state can take up to a second. If the user
                # immediately triggers a playlist preset after this
                # join, _play_via_queue would otherwise see
                # _last_group_master still empty and skip the
                # standalone-detach step (and the queue dance would
                # silently fail because the slave doesn't own its
                # transport). Update the state now from the URI's
                # master UUID so the next action sees the truth.
                self._last_group_master = uri[len("x-rincon:"):]
                self.fw.run_in_context(self._mark_active_station, (active_idx, active_name))
            else:
                self.fw.run_in_context(self._write_error, (err or "JOIN_FAILED",))
            return

        # Containers (Spotify/Apple playlists, Sonos saved queues, …)
        # cannot be SetAVTransportURI'd directly — they must be added to
        # the player's queue first, then the transport switches to the
        # queue URI. Branch here.
        if _is_container_uri(uri):
            self._play_via_queue(uri, metadata, active_idx, active_name)
            return

        if metadata:
            # Cloud-bound favorite: the music-service binding (TuneIn,
            # Spotify…) lives inside the metadata. Do NOT try the empty-
            # metadata fallback — Sonos would lose the service binding.
            envelope = ENV_SET_URI.replace("{uri}", _xml_escape(uri)) \
                                  .replace("{meta}", _xml_escape(metadata))
            ok, _b, err = self._soap("AVTransport", "SetAVTransportURI", envelope)
            if ok:
                play_ok, _b2, play_err = self._soap("AVTransport", "Play", ENV_PLAY)
                if play_ok:
                    self.fw.run_in_context(self._mark_active_station, (active_idx, active_name))
                else:
                    self.fw.run_in_context(self._write_error, (play_err,))
                return
            self.fw.run_in_context(self._write_error, (err or "RADIO_START_FAILED",))
            return

        # Direct stream (manual URI): the original firmware-2026-resilient
        # fallback ladder — empty metadata first, then raw URI.
        for candidate in (normalize_radio_uri(uri), uri):
            envelope = ENV_SET_URI.replace("{uri}", _xml_escape(candidate)) \
                                  .replace("{meta}", "")
            ok, _body, err = self._soap("AVTransport", "SetAVTransportURI", envelope)
            if ok:
                play_ok, _b, play_err = self._soap("AVTransport", "Play", ENV_PLAY)
                if play_ok:
                    self.fw.run_in_context(self._mark_active_station, (active_idx, active_name))
                else:
                    self.fw.run_in_context(self._write_error, (play_err,))
                return
            if err not in META_REJECT_CODES:
                break
        self.fw.run_in_context(self._write_error, ("RADIO_START_FAILED",))

    def _action_step_preset(self, direction):
        """Step one position through the Admin preset library and start
        the resulting preset. ``direction`` is +1 (next) or -1 (prev).
        Wraps at both ends so a single 1-bit KNX address can cycle
        through the whole library. NO_PRESETS surfaces on LastError when
        the library is empty or Admin isn't loaded."""
        count = _admin_station_count()
        if count <= 0:
            self.fw.run_in_context(self._write_error, ("NO_PRESETS",))
            return
        current = self._active_station
        if direction > 0:
            next_idx = 1 if current <= 0 else (current % count) + 1
        else:
            next_idx = count if current <= 1 else current - 1
        self._action_start_radio(next_idx)

    # ----- Sound notifications ---------------------------------------------

    def _action_play_sound(self, spec):
        """Play a notification clip from the Admin sounds library —
        equivalent to Home Assistant's Sonos ``announce: true``.

        Modern Sonos firmware (S2) exposes a native announcement
        service (``AudioClip.LoadAudioClip``) that plays the clip on
        top of whatever is playing and resumes automatically when the
        clip ends — no snapshot needed, no audible interruption. This
        is the primary path.

        Older S1 hardware doesn't have the AudioClip service and
        returns a SOAP fault. In that case we fall back to the
        snapshot/restore pattern: capture CurrentURI + metadata +
        position + transport state + Volume + Mute, play the clip via
        ``SetAVTransportURI`` + ``Play``, poll ``TransportState`` until
        ``STOPPED``, then restore everything.

        Runs in the worker thread spawned by `_run_control_threaded` so
        the polling loop in the fallback path never blocks node context.
        """
        sound = _admin_sound_record(spec)
        if not sound or not sound.get("url"):
            self.fw.run_in_context(
                self._write_error,
                ("SOUND_NOT_FOUND: {}".format(spec),),
            )
            return
        url = sound["url"]
        name = sound.get("name") or "clip"

        # Primary path — native Sonos announcement. AudioClip absent on
        # S1 hardware returns HTTP 404 or a SOAP fault; both surface as
        # ok=False from _soap, triggering the fallback below.
        env_clip = ENV_LOAD_AUDIO_CLIP \
            .replace("{name}", _xml_escape(name)) \
            .replace("{url}",  _xml_escape(url))
        ok, _b, _err = self._soap("AudioClip", "LoadAudioClip", env_clip)
        if ok:
            # The service handles ducking + auto-resume — we're done.
            return

        # Fallback for S1 / older firmware: snapshot, play, restore.
        snapshot = self._snapshot_transport()
        env_set = ENV_SET_URI.replace("{uri}", _xml_escape(url)).replace("{meta}", "")
        ok, _b, err = self._soap("AVTransport", "SetAVTransportURI", env_set)
        if not ok:
            self.fw.run_in_context(self._write_error, (err or "SOUND_SETURI_FAILED",))
            return
        ok, _b, err = self._soap("AVTransport", "Play", ENV_PLAY)
        if not ok:
            self.fw.run_in_context(self._write_error, (err or "SOUND_PLAY_FAILED",))
            self._restore_transport(snapshot)
            return
        # Sonos transitions to STOPPED at end-of-file for SetAVTransportURI'd clips.
        self._wait_until_stopped(timeout_s=120)
        self._restore_transport(snapshot)

    def _snapshot_transport(self):
        """Capture everything `_restore_transport` needs to put the
        player back into the source it was using. All fields default to
        safe empty values so a player that's idle restores to idle."""
        snap = {
            "currentUri": "", "currentUriMeta": "",
            "trackUri": "", "relTime": "0:00:00",
            "state": "STOPPED", "volume": None, "mute": None,
        }
        ok, body, _err = self._soap("AVTransport", "GetMediaInfo", ENV_GET_MEDIA)
        if ok:
            snap["currentUri"]     = extract_response_field(body, "CurrentURI") or ""
            snap["currentUriMeta"] = extract_response_field(body, "CurrentURIMetaData") or ""
        ok, body, _err = self._soap("AVTransport", "GetPositionInfo", ENV_GET_POSITION)
        if ok:
            snap["trackUri"] = extract_response_field(body, "TrackURI") or ""
            snap["relTime"]  = extract_response_field(body, "RelTime")  or "0:00:00"
        ok, body, _err = self._soap("AVTransport", "GetTransportInfo", ENV_GET_TRANSPORT)
        if ok:
            snap["state"] = extract_response_field(body, "CurrentTransportState") or "STOPPED"
        v = self._get_volume_blocking()
        if v is not None:
            snap["volume"] = v
        ok, body, _err = self._soap("RenderingControl", "GetMute", ENV_GET_MUTE)
        if ok:
            snap["mute"] = (extract_response_field(body, "CurrentMute") == "1")
        return snap

    def _wait_until_stopped(self, timeout_s=120, poll_interval_s=1.0):
        """Poll TransportState until the clip ends or the timeout
        expires. The clip's actual length is unknown to us — Sonos
        flips to STOPPED at EOF for direct SetAVTransportURI sources,
        which is the unambiguous signal that playback finished."""
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            ok, body, _err = self._soap(
                "AVTransport", "GetTransportInfo", ENV_GET_TRANSPORT
            )
            if not ok:
                # Network blip — keep trying until the timeout.
                time.sleep(poll_interval_s)
                continue
            state = extract_response_field(body, "CurrentTransportState") or ""
            if state == "STOPPED":
                return
            time.sleep(poll_interval_s)

    def _restore_transport(self, snap):
        """Re-establish the player's pre-sound state. Best-effort: each
        step is independent and a failure doesn't abort the others.
        Volume / Mute are restored even when there was nothing playing
        so the next manual playback inherits the user's level."""
        # Restore CurrentURI (queue, radio, file, or whatever it was).
        if snap.get("currentUri"):
            env = ENV_SET_URI \
                .replace("{uri}",  _xml_escape(snap["currentUri"])) \
                .replace("{meta}", _xml_escape(snap.get("currentUriMeta") or ""))
            self._soap("AVTransport", "SetAVTransportURI", env)

        # Seek back to where playback was. Radio streams reject Seek
        # with UPnP error 711 ("transport is not seekable") — that's
        # expected and harmless; we ignore the return code.
        rel = snap.get("relTime") or "0:00:00"
        if rel and rel != "0:00:00" and rel != "NOT_IMPLEMENTED":
            self._soap("AVTransport", "Seek", ENV_SEEK_REL_TIME.format(pos=rel))

        # Restore Volume + Mute.
        if snap.get("volume") is not None:
            self._soap(
                "RenderingControl", "SetVolume",
                ENV_SET_VOLUME.format(level=int(snap["volume"])),
            )
        if snap.get("mute") is not None:
            self._soap(
                "RenderingControl", "SetMute",
                ENV_SET_MUTE.format(mute="1" if snap["mute"] else "0"),
            )

        # Resume playback only if the snapshot was actively playing.
        # Paused / Stopped snapshots stay in their original state.
        if snap.get("state") == "PLAYING":
            self._soap("AVTransport", "Play", ENV_PLAY)

    # ----- Periodic tick ----------------------------------------------------

    def _fetch_device_xml(self):
        """One-shot GET of the player's UPnP device description. Cached
        only at the call site (callers cache the parsed result). Runs in
        a worker thread — never call from node context."""
        if not self._host:
            return ""
        try:
            resp = requests.get(
                "http://{}:1400/xml/device_description.xml".format(self._host),
                timeout=self._http_timeout_s,
            )
            return resp.text
        except Exception:
            return ""

    def _fetch_zone_name(self):
        """Return the player's Sonos Zone Name ("Living Room"). Tries the
        Admin registry first (cheap, no HTTP); falls back to fetching
        device_description.xml when Admin isn't present. Returns '' on
        all errors so an offline player just blanks the output."""
        rec = _admin_player_record(self._host_spec) or _admin_player_record(self._host)
        if rec and rec.get("zoneName"):
            return rec["zoneName"]
        xml = self._fetch_device_xml()
        m = re.search(r"<roomName>([^<]+)</roomName>", xml) if xml else None
        return m.group(1) if m else ""

    def _resolve_uuid(self):
        """Return the player's RINCON UUID — required when building the
        queue URI for playlist playback. Admin registry first (cheap),
        falls back to <UDN>uuid:RINCON_xxx</UDN> from the device XML.
        Caches in self._uuid so subsequent calls are free."""
        if self._uuid:
            return self._uuid
        rec = _admin_player_record(self._host_spec) or _admin_player_record(self._host)
        if rec and rec.get("uuid"):
            self._uuid = rec["uuid"]
            return self._uuid
        xml = self._fetch_device_xml()
        if xml:
            m = re.search(r"<UDN>uuid:([A-Za-z0-9_-]+)</UDN>", xml)
            if m:
                self._uuid = m.group(1)
        return self._uuid

    def _tick_work(self):
        # Re-resolve the host on each tick so DHCP renumbering is picked up
        # automatically when the admin registry refreshes.
        if self._host_spec:
            resolved = resolve_host_spec(self._host_spec)
            if resolved:
                self._host = resolved
        if not self._host:
            return

        # Refresh the player's Sonos Zone Name. Cheap (one GET); also
        # picks up renames the user made in the Sonos app.
        zone = self._fetch_zone_name()
        if zone and zone != self._last_zone_name:
            self._last_zone_name = zone
            self.fw.run_in_context(self._publish_zone_name, ())

        # Status poll fallback (cheap on LAN).
        ok, body, _err = self._soap("AVTransport", "GetTransportInfo", ENV_GET_TRANSPORT)
        if ok:
            state = extract_response_field(body, "CurrentTransportState")
            vol = self._get_volume_blocking()
            mute_ok, mute_body, _err = self._soap("RenderingControl", "GetMute", ENV_GET_MUTE)
            mute = (extract_response_field(mute_body, "CurrentMute") == "1") if mute_ok else None
            play_mode = self._get_play_mode_blocking()
            actions = self._get_transport_actions_blocking()
            pos_ok, pos_body, _err = self._soap("AVTransport", "GetPositionInfo", ENV_GET_POSITION)
            title = artist = album = album_art = track_uri = ""
            if pos_ok:
                track_uri = extract_response_field(pos_body, "TrackURI") or ""
                meta_raw = extract_response_field(pos_body, "TrackMetaData") or ""
                meta = unescape_xml(meta_raw)
                t = re.search(r"<dc:title>([^<]*)</dc:title>", meta)
                if t:
                    title = unescape_xml(t.group(1))
                a = re.search(r"<dc:creator>([^<]*)</dc:creator>", meta)
                if a:
                    artist = unescape_xml(a.group(1))
                al = re.search(r"<upnp:album>([^<]*)</upnp:album>", meta)
                if al:
                    album = unescape_xml(al.group(1))
                aa = (re.search(r"<upnp:albumArtURI>([^<]*)</upnp:albumArtURI>", meta)
                      or re.search(r"<r:albumArtURI>([^<]*)</r:albumArtURI>", meta))
                if aa:
                    album_art = unescape_xml(aa.group(1))
                sc = re.search(r"<r:streamContent>([^<]*)</r:streamContent>", meta)
                if sc:
                    title = unescape_xml(sc.group(1)) or title
            self.fw.run_in_context(
                self._apply_status_poll,
                (True, state, vol, mute, title, artist, album, album_art,
                 play_mode, track_uri, actions),
            )
        else:
            self.fw.run_in_context(
                self._apply_status_poll,
                (False, None, None, None, "", "", "", "", None, "", None),
            )

        self._maintain_subscriptions()

    def _maintain_subscriptions(self):
        if not self._callback_base or not self._host_spec:
            return
        now = time.time()
        # URL-encode the routing key (typically a UUID; may also be a MAC
        # with colons). The NotifyHandler URL-decodes before lookup.
        rk = urllib.parse.quote(self._host_spec, safe="")
        cb_av = "{}/upnp/{}/av".format(self._callback_base, rk)
        cb_rc = "{}/upnp/{}/rc".format(self._callback_base, rk)

        if not self._sid_av or (self._sub_av_exp - now) < self._renew_threshold_s:
            self._subscribe_or_renew("av", "AVTransport", cb_av)
        if not self._sid_rc or (self._sub_rc_exp - now) < self._renew_threshold_s:
            self._subscribe_or_renew("rc", "RenderingControl", cb_rc)

    def _subscribe_or_renew(self, short, service_name, callback_url):
        path = EVENT_PATHS[short]
        sid_attr = "_sid_" + short
        exp_attr = "_sub_" + short + "_exp"
        existing_sid = getattr(self, sid_attr)

        if existing_sid:
            # Renew with existing SID.
            status, headers = self._upnp(
                "SUBSCRIBE", path,
                {"SID": existing_sid, "TIMEOUT": "Second-{}".format(self._sub_timeout_s)},
            )
            if status == 412 or status is None or status >= 400:
                setattr(self, sid_attr, "")
                setattr(self, exp_attr, 0.0)
                existing_sid = ""
            else:
                new_sid = headers.get("sid", existing_sid)
                granted = self._sub_timeout_s
                m = re.search(r"Second-(\d+)", headers.get("timeout", ""))
                if m:
                    granted = int(m.group(1))
                setattr(self, sid_attr, new_sid)
                setattr(self, exp_attr, time.time() + granted)
                self.fw.run_in_context(self._publish_sub_state, ())
                return

        # Initial SUBSCRIBE.
        if self.debug is not None:
            self.fw.run_in_context(self._inc_debug, ("Subscribe attempts",))
        status, headers = self._upnp(
            "SUBSCRIBE", path,
            {
                "CALLBACK": "<{}>".format(callback_url),
                "NT": "upnp:event",
                "TIMEOUT": "Second-{}".format(self._sub_timeout_s),
            },
        )
        if status is None or status >= 400:
            self.fw.run_in_context(self._write_error, ("SUBSCRIBE_FAILED",))
            return
        new_sid = headers.get("sid", "")
        granted = self._sub_timeout_s
        m = re.search(r"Second-(\d+)", headers.get("timeout", ""))
        if m:
            granted = int(m.group(1))
        setattr(self, sid_attr, new_sid)
        setattr(self, exp_attr, time.time() + granted)
        self.fw.run_in_context(self._publish_sub_state, ())

    # ----- Output marshalling (runs in node context) -----------------------

    def _absolute_album_art(self, uri):
        """Sonos returns album-art URIs as relative paths (/getaa?...).
        Prepend http://<host>:1400 so the integrator can drop the URI
        straight into a Gira visualisation tile."""
        if not uri:
            return ""
        if uri.startswith("http://") or uri.startswith("https://"):
            return uri
        if uri.startswith("/") and self._host:
            return "http://{}:1400{}".format(self._host, uri)
        return uri

    def _group_info_string(self):
        """Human-friendly group info string.
        - Coordinator / standalone: empty (use IsCoordinator=1 as signal)
        - Slave: master's ZoneName when admin knows it, else the raw UUID.
        """
        if not self._last_group_master:
            return ""
        rec = _admin_player_record(self._last_group_master)
        if rec and rec.get("zoneName"):
            return rec["zoneName"]
        return self._last_group_master

    def _publish_outputs(self):
        self.fw.set_output("Online", 1 if self._online else 0)
        self.fw.set_output("State", to_iso_bytes(self._last_state))
        # Discrete state booleans — exactly one is 1 at any time when
        # we have a known state (all 0 when state hasn't been read yet).
        self._publish_state_flags()
        self._publish_allowed_flags()
        if self._last_volume >= 0:
            self.fw.set_output("Volume", float(self._last_volume))
        if self._last_mute is not None:
            self.fw.set_output("Mute", 1 if self._last_mute else 0)
        self.fw.set_output("Title", to_iso_bytes(self._last_title))
        self.fw.set_output("Artist", to_iso_bytes(self._last_artist))
        self.fw.set_output("Album", to_iso_bytes(self._last_album))
        self.fw.set_output("AlbumArtURI", to_iso_bytes(self._last_album_art))
        self.fw.set_output("ShuffleState", 1 if self._last_shuffle else 0)
        self.fw.set_output("RepeatState", 1 if self._last_repeat else 0)
        self.fw.set_output("GroupInfo", to_iso_bytes(self._group_info_string()))
        self.fw.set_output("IsCoordinator", 0 if self._last_group_master else 1)
        self.fw.set_output("ActiveStation", float(self._active_station))
        self.fw.set_output("ActiveStationName", to_iso_bytes(self._active_station_name))
        self.fw.set_output("ZoneName", to_iso_bytes(self._last_zone_name))

    def _publish_state_flags(self):
        s = self._last_state
        self.fw.set_output("IsPlaying", 1 if s == "Playing" else 0)
        self.fw.set_output("IsPaused", 1 if s == "Paused" else 0)
        self.fw.set_output("IsStopped", 1 if s == "Stopped" else 0)
        self.fw.set_output("IsTransitioning", 1 if s == "Transitioning" else 0)

    def _publish_allowed_flags(self):
        a = self._last_allowed
        self.fw.set_output("PlayAllowed",    1 if a["play"]    else 0)
        self.fw.set_output("PauseAllowed",   1 if a["pause"]   else 0)
        self.fw.set_output("StopAllowed",    1 if a["stop"]    else 0)
        self.fw.set_output("NextAllowed",    1 if a["next"]    else 0)
        self.fw.set_output("PrevAllowed",    1 if a["prev"]    else 0)
        self.fw.set_output("ShuffleAllowed", 1 if a["shuffle"] else 0)
        self.fw.set_output("RepeatAllowed",  1 if a["repeat"]  else 0)

    def _publish_zone_name(self):
        self.fw.set_output("ZoneName", to_iso_bytes(self._last_zone_name))

    def _publish_sub_state(self):
        now = time.time()
        subscribed = bool(self._sid_av and self._sid_rc
                          and self._sub_av_exp > now and self._sub_rc_exp > now)
        self.fw.set_output("Subscribed", 1 if subscribed else 0)
        if self.debug is not None:
            self.debug.set("Subscription av exp", float(max(0, self._sub_av_exp - now)))
            self.debug.set("Subscription rc exp", float(max(0, self._sub_rc_exp - now)))

    def _apply_status_poll(self, online, state, volume, mute, title, artist,
                           album="", album_art="", play_mode=None, track_uri="",
                           actions=None):
        if self.debug is not None:
            self.debug.inc("Status polls")
            self.debug.timestamp("Last poll")
        self._online = online
        if state:
            self._last_state = _normalize_state(state)
        if volume is not None:
            self._last_volume = volume
        if mute is not None:
            self._last_mute = mute
        # Track metadata: always overwrite when we got an online poll
        # back, regardless of whether the individual field was empty.
        # Radio streams have no <dc:creator>/<upnp:album>, so a stale
        # Artist / Album from the previous Spotify track would
        # otherwise stay on the output. An offline poll (online=False
        # passes empty strings) keeps the last known values so a
        # transient network glitch doesn't blank the display.
        if online:
            self._last_title = _friendly_title(title)
            self._last_artist = artist
            self._last_album = album
            self._last_album_art = self._absolute_album_art(album_art)
        if play_mode:
            self._last_shuffle, self._last_repeat = play_mode_to_flags(play_mode)
        if track_uri or online:
            # Refresh group membership only when we actually had a poll;
            # offline players keep their last known state.
            self._last_group_master = extract_group_master_uuid(track_uri)
        if actions is not None:
            self._last_allowed = parse_transport_actions(actions)
        elif not online:
            # Lost contact — every transport action becomes unavailable
            # so the visualisation greys out instead of inviting clicks
            # that would just write to LastError.
            self._last_allowed = dict(_DEFAULT_ALLOWED)
        self._publish_outputs()
        self._publish_sub_state()

    def _mark_state(self, state):
        new = _normalize_state(state)
        if new == self._last_state:
            return
        self._last_state = new
        self.fw.set_output("State", to_iso_bytes(self._last_state))
        self._publish_state_flags()

    def _mark_play_mode(self, shuffle, repeat):
        s = bool(shuffle)
        r = bool(repeat)
        if s != bool(self._last_shuffle):
            self._last_shuffle = s
            self.fw.set_output("ShuffleState", 1 if s else 0)
        if r != bool(self._last_repeat):
            self._last_repeat = r
            self.fw.set_output("RepeatState", 1 if r else 0)

    def _mark_volume(self, level):
        # Send-by-change. Without this guard, every NOTIFY / poll
        # re-broadcasts the same value to the KNX bus, which then
        # echoes back to a SetVolume input wired to the same GA and
        # we never stop dispatching SOAP. See on_calc's matching
        # input-side suppression which catches the same echo on the
        # other end.
        level = int(level)
        if level == self._last_volume:
            return
        self._last_volume = level
        self.fw.set_output("Volume", float(level))

    def _mark_mute(self, mute):
        m = bool(mute)
        if self._last_mute is not None and bool(self._last_mute) == m:
            return
        self._last_mute = m
        self.fw.set_output("Mute", 1 if m else 0)

    def _mark_active_station(self, idx, name=""):
        """Update the ActiveStation + ActiveStationName outputs after a
        successful preset start. ``idx`` is the 1-based alphabetical
        position in the Admin library (0 = unknown); ``name`` is the
        preset's display name."""
        try:
            idx = int(idx)
        except (TypeError, ValueError):
            idx = 0
        self._active_station = idx
        self._active_station_name = name or ""
        self.fw.set_output("ActiveStation", float(idx))
        self.fw.set_output("ActiveStationName", to_iso_bytes(self._active_station_name))

    def _mark_active_station_loading(self, idx, name):
        """Optimistic in-flight update for the ActiveStationName output.
        Called the moment the integrator triggers a preset, before any
        SOAP completes. Prefixed with "Loading: " so the visualisation
        can show the change-in-progress; the prefix is dropped on
        success via _mark_active_station. On failure the prefix stays
        until the next preset is started — LastError carries the why."""
        try:
            idx = int(idx)
        except (TypeError, ValueError):
            idx = 0
        self._active_station = idx
        label = "Loading: " + (name or "")
        self._active_station_name = label
        self.fw.set_output("ActiveStation", float(idx))
        self.fw.set_output("ActiveStationName", to_iso_bytes(label))

    def _write_error(self, code):
        if self.debug is not None:
            self.debug.set("Last error", to_iso_bytes(str(code)))
        self.fw.set_output("LastError", to_iso_bytes(str(code)))

    def _inc_debug(self, key):
        if self.debug is not None:
            self.debug.inc(key)
