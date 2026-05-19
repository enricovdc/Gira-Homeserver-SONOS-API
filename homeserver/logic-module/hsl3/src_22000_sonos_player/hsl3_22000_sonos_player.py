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
ENV_SET_URI = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
    '<s:Body><u:SetAVTransportURI xmlns:u="urn:schemas-upnp-org:service:AVTransport:1">'
    "<InstanceID>0</InstanceID><CurrentURI>{uri}</CurrentURI>"
    "<CurrentURIMetaData>{meta}</CurrentURIMetaData></u:SetAVTransportURI>"
    "</s:Body></s:Envelope>"
)


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


def _lookup_station_via_admin(idx_or_name):
    """If the Sonos Admin LBS is loaded, ask it for the full station
    record (uri + metadata + name). Returns None when Admin isn't
    present or doesn't know the station."""
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

SERVICE_PATHS = {
    "AVTransport":      "/MediaRenderer/AVTransport/Control",
    "RenderingControl": "/MediaRenderer/RenderingControl/Control",
}
SERVICE_TYPES = {
    "AVTransport":      "urn:schemas-upnp-org:service:AVTransport:1",
    "RenderingControl": "urn:schemas-upnp-org:service:RenderingControl:1",
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
        t = re.search(r"<dc:title>([^<]*)</dc:title>", meta)
        if t:
            out["title"] = unescape_xml(t.group(1))
        a = re.search(r"<dc:creator>([^<]*)</dc:creator>", meta)
        if a:
            out["artist"] = unescape_xml(a.group(1))
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
        self._active_station = 0
        self._online = False
        self._stations = {}  # idx -> uri
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

        if inputs["SetVolume"].changed:
            level = int(inputs["SetVolume"].value or 0)
            self._run_control_threaded(lambda: self._action_set_volume(level))

        if inputs["VolUp"].changed and inputs["VolUp"].value != 0:
            self._run_control_threaded(lambda: self._action_adjust_volume(+self._vol_step))
        if inputs["VolDown"].changed and inputs["VolDown"].value != 0:
            self._run_control_threaded(lambda: self._action_adjust_volume(-self._vol_step))

        if inputs["SetMute"].changed:
            mute = inputs["SetMute"].value != 0
            self._run_control_threaded(lambda: self._action_set_mute(mute))
        if inputs["MuteToggle"].changed and inputs["MuteToggle"].value != 0:
            self._run_control_threaded(self._action_toggle_mute)

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

        if inputs["Resubscribe"].changed and inputs["Resubscribe"].value != 0:
            self._sid_av = ""
            self._sid_rc = ""
            self._sub_av_exp = 0.0
            self._sub_rc_exp = 0.0
            self._run_control_threaded(self._maintain_subscriptions)

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
            self._last_state = parsed["state"]
        if "volume" in parsed:
            self._last_volume = parsed["volume"]
        if "mute" in parsed:
            self._last_mute = parsed["mute"]
        if "title" in parsed and parsed["title"]:
            self._last_title = parsed["title"]
        if "streamContent" in parsed and parsed["streamContent"]:
            self._last_title = parsed["streamContent"]
        if "artist" in parsed and parsed["artist"]:
            self._last_artist = parsed["artist"]
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
        self._poll_interval_s = max(10, int(inputs["PollInterval"].value or 60))
        self._sub_timeout_s = max(60, int(inputs["SubTimeout"].value or 1800))
        self._renew_threshold_s = max(30, self._sub_timeout_s // 6)
        self._http_timeout_s = max(2, int(inputs["HttpTimeout"].value or 5))
        cb = to_str(inputs["CallbackBase"].value).strip().rstrip("/")
        if not cb:
            cb = "http://{}:{}".format(_get_local_lan_ip(), self._notify_port)
        self._callback_base = cb
        for i in range(1, 9):
            self._stations[i] = to_str(inputs["Station{}Uri".format(i)].value).strip()

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

    def _action_start_radio(self, spec):
        """Start a station identified either by a positive integer
        (alphabetical index into the Admin station library, or 1..8
        into the per-player StationNUri inputs as fallback) OR by the
        station's name string (case-insensitive lookup in the Admin
        library).

        Resolution order: Admin's global station library first (carries
        the DIDL-Lite metadata needed for Sonos cloud favorites like
        TuneIn and Spotify), falling back to the per-player StationNUri
        input when no Admin block is present and the spec is an int.
        """
        admin_rec = _lookup_station_via_admin(spec)
        if admin_rec:
            uri = admin_rec.get("uri") or ""
            metadata = admin_rec.get("metadata") or ""
            spec_label = spec  # for the error message + active-station marker
        else:
            # Fall back to the per-player input slot when spec is an int.
            uri = ""
            metadata = ""
            if isinstance(spec, int) or (isinstance(spec, str) and spec.isdigit()):
                idx = int(spec)
                uri = self._stations.get(idx, "") or ""
            spec_label = spec
        if not uri:
            self.fw.run_in_context(self._write_error, ("STATION_NOT_FOUND: {}".format(spec_label),))
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
                    self.fw.run_in_context(self._mark_active_station, (spec,))
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
                    self.fw.run_in_context(self._mark_active_station, (spec,))
                else:
                    self.fw.run_in_context(self._write_error, (play_err,))
                return
            if err not in META_REJECT_CODES:
                break
        self.fw.run_in_context(self._write_error, ("RADIO_START_FAILED",))

    # ----- Periodic tick ----------------------------------------------------

    def _tick_work(self):
        # Re-resolve the host on each tick so DHCP renumbering is picked up
        # automatically when the admin registry refreshes.
        if self._host_spec:
            resolved = resolve_host_spec(self._host_spec)
            if resolved:
                self._host = resolved
        if not self._host:
            return

        # Status poll fallback (cheap on LAN).
        ok, body, _err = self._soap("AVTransport", "GetTransportInfo", ENV_GET_TRANSPORT)
        if ok:
            state = extract_response_field(body, "CurrentTransportState")
            vol = self._get_volume_blocking()
            mute_ok, mute_body, _err = self._soap("RenderingControl", "GetMute", ENV_GET_MUTE)
            mute = (extract_response_field(mute_body, "CurrentMute") == "1") if mute_ok else None
            pos_ok, pos_body, _err = self._soap("AVTransport", "GetPositionInfo", ENV_GET_POSITION)
            title = artist = ""
            if pos_ok:
                meta_raw = extract_response_field(pos_body, "TrackMetaData") or ""
                meta = unescape_xml(meta_raw)
                t = re.search(r"<dc:title>([^<]*)</dc:title>", meta)
                if t:
                    title = unescape_xml(t.group(1))
                a = re.search(r"<dc:creator>([^<]*)</dc:creator>", meta)
                if a:
                    artist = unescape_xml(a.group(1))
                sc = re.search(r"<r:streamContent>([^<]*)</r:streamContent>", meta)
                if sc:
                    title = unescape_xml(sc.group(1)) or title
            self.fw.run_in_context(
                self._apply_status_poll,
                (True, state, vol, mute, title, artist),
            )
        else:
            self.fw.run_in_context(
                self._apply_status_poll,
                (False, None, None, None, "", ""),
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

    def _publish_outputs(self):
        self.fw.set_output("Online", 1 if self._online else 0)
        self.fw.set_output("State", to_iso_bytes(self._last_state))
        if self._last_volume >= 0:
            self.fw.set_output("Volume", float(self._last_volume))
        if self._last_mute is not None:
            self.fw.set_output("Mute", 1 if self._last_mute else 0)
        self.fw.set_output("Title", to_iso_bytes(self._last_title))
        self.fw.set_output("Artist", to_iso_bytes(self._last_artist))
        self.fw.set_output("ActiveStation", float(self._active_station))

    def _publish_sub_state(self):
        now = time.time()
        subscribed = bool(self._sid_av and self._sid_rc
                          and self._sub_av_exp > now and self._sub_rc_exp > now)
        self.fw.set_output("Subscribed", 1 if subscribed else 0)
        if self.debug is not None:
            self.debug.set("Subscription av exp", float(max(0, self._sub_av_exp - now)))
            self.debug.set("Subscription rc exp", float(max(0, self._sub_rc_exp - now)))

    def _apply_status_poll(self, online, state, volume, mute, title, artist):
        if self.debug is not None:
            self.debug.inc("Status polls")
            self.debug.timestamp("Last poll")
        self._online = online
        if state:
            self._last_state = state
        if volume is not None:
            self._last_volume = volume
        if mute is not None:
            self._last_mute = mute
        if title:
            self._last_title = title
        if artist:
            self._last_artist = artist
        self._publish_outputs()
        self._publish_sub_state()

    def _mark_state(self, state):
        self._last_state = state
        self.fw.set_output("State", to_iso_bytes(state))

    def _mark_volume(self, level):
        self._last_volume = level
        self.fw.set_output("Volume", float(level))

    def _mark_mute(self, mute):
        self._last_mute = mute
        self.fw.set_output("Mute", 1 if mute else 0)

    def _mark_active_station(self, spec):
        """Update the ActiveStation output. Accepts either an int index
        (preferred) or a station-name string; in the latter case the
        output records 0 because the alphabetical index would be
        meaningful only in conjunction with the Admin library state."""
        if isinstance(spec, int):
            idx = spec
        else:
            s = str(spec).strip()
            idx = int(s) if s.isdigit() else 0
        self._active_station = idx
        self.fw.set_output("ActiveStation", float(idx))

    def _write_error(self, code):
        if self.debug is not None:
            self.debug.set("Last error", to_iso_bytes(str(code)))
        self.fw.set_output("LastError", to_iso_bytes(str(code)))

    def _inc_debug(self, key):
        if self.debug is not None:
            self.debug.inc(key)
