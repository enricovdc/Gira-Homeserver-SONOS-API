"""LBS 22002 - Sonos Sound Enhancement.

Optional per-player companion to LBS 22000. Adds the sound-tuning
controls that aren't worth carrying on every Player block: bass /
treble, loudness, soundbar EQ (NightMode + DialogMode), crossfade,
and the sleep timer. Drop one instance onto the canvas for any
speaker that needs the extras; leave it off for kitchens and
bedrooms that just want play/pause/volume.

Pure polling — no UPnP subscriptions. These settings don't change
second-by-second; refreshing them on the same Tick cadence as the
Player block (default 60 s) is plenty. Keeps the module small and
avoids a second NOTIFY listener on top of the one LBS 22000 already
runs.
"""

import re
import sys
import threading

import requests


# ---------------------------------------------------------------------------
# SOAP envelopes — all use existing AVTransport and RenderingControl paths,
# same as LBS 22000. No new services involved.
# ---------------------------------------------------------------------------

ENV_GET_BASS = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
    '<s:Body><u:GetBass xmlns:u="urn:schemas-upnp-org:service:RenderingControl:1">'
    "<InstanceID>0</InstanceID></u:GetBass></s:Body></s:Envelope>"
)
ENV_SET_BASS = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
    '<s:Body><u:SetBass xmlns:u="urn:schemas-upnp-org:service:RenderingControl:1">'
    "<InstanceID>0</InstanceID><DesiredBass>{val}</DesiredBass>"
    "</u:SetBass></s:Body></s:Envelope>"
)
ENV_GET_TREBLE = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
    '<s:Body><u:GetTreble xmlns:u="urn:schemas-upnp-org:service:RenderingControl:1">'
    "<InstanceID>0</InstanceID></u:GetTreble></s:Body></s:Envelope>"
)
ENV_SET_TREBLE = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
    '<s:Body><u:SetTreble xmlns:u="urn:schemas-upnp-org:service:RenderingControl:1">'
    "<InstanceID>0</InstanceID><DesiredTreble>{val}</DesiredTreble>"
    "</u:SetTreble></s:Body></s:Envelope>"
)
ENV_GET_LOUDNESS = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
    '<s:Body><u:GetLoudness xmlns:u="urn:schemas-upnp-org:service:RenderingControl:1">'
    "<InstanceID>0</InstanceID><Channel>Master</Channel></u:GetLoudness></s:Body></s:Envelope>"
)
ENV_SET_LOUDNESS = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
    '<s:Body><u:SetLoudness xmlns:u="urn:schemas-upnp-org:service:RenderingControl:1">'
    "<InstanceID>0</InstanceID><Channel>Master</Channel>"
    "<DesiredLoudness>{val}</DesiredLoudness></u:SetLoudness></s:Body></s:Envelope>"
)
# EQ is Sonos's bucket for soundbar-specific tweaks. EQType picks the
# bucket — NightMode (loudness compression for late TV) or
# DialogLevel (speech enhance). Non-soundbar players reject these
# with a SOAP fault; the module catches that and tags LastError.
ENV_GET_EQ = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
    '<s:Body><u:GetEQ xmlns:u="urn:schemas-upnp-org:service:RenderingControl:1">'
    "<InstanceID>0</InstanceID><EQType>{eq}</EQType></u:GetEQ></s:Body></s:Envelope>"
)
ENV_SET_EQ = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
    '<s:Body><u:SetEQ xmlns:u="urn:schemas-upnp-org:service:RenderingControl:1">'
    "<InstanceID>0</InstanceID><EQType>{eq}</EQType>"
    "<DesiredValue>{val}</DesiredValue></u:SetEQ></s:Body></s:Envelope>"
)
ENV_GET_CROSSFADE = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
    '<s:Body><u:GetCrossfadeMode xmlns:u="urn:schemas-upnp-org:service:AVTransport:1">'
    "<InstanceID>0</InstanceID></u:GetCrossfadeMode></s:Body></s:Envelope>"
)
ENV_SET_CROSSFADE = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
    '<s:Body><u:SetCrossfadeMode xmlns:u="urn:schemas-upnp-org:service:AVTransport:1">'
    "<InstanceID>0</InstanceID><CrossfadeMode>{val}</CrossfadeMode>"
    "</u:SetCrossfadeMode></s:Body></s:Envelope>"
)
ENV_GET_SLEEP_REMAINING = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
    '<s:Body><u:GetRemainingSleepTimerDuration '
    'xmlns:u="urn:schemas-upnp-org:service:AVTransport:1">'
    "<InstanceID>0</InstanceID></u:GetRemainingSleepTimerDuration></s:Body></s:Envelope>"
)
ENV_CONFIGURE_SLEEP = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
    '<s:Body><u:ConfigureSleepTimer xmlns:u="urn:schemas-upnp-org:service:AVTransport:1">'
    "<InstanceID>0</InstanceID>"
    "<NewSleepTimerDuration>{duration}</NewSleepTimerDuration>"
    "</u:ConfigureSleepTimer></s:Body></s:Envelope>"
)
# AVTransport SetAVTransportURI — used to switch a soundbar into TV
# input mode (URI scheme x-sonos-htastream:<UUID>:spdif) and the
# subsequent Play. The Player block has the same envelope; duplicated
# here to keep this LBS self-contained.
ENV_SET_URI = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
    '<s:Body><u:SetAVTransportURI xmlns:u="urn:schemas-upnp-org:service:AVTransport:1">'
    "<InstanceID>0</InstanceID><CurrentURI>{uri}</CurrentURI>"
    "<CurrentURIMetaData></CurrentURIMetaData></u:SetAVTransportURI>"
    "</s:Body></s:Envelope>"
)
ENV_PLAY = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
    's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
    '<s:Body><u:Play xmlns:u="urn:schemas-upnp-org:service:AVTransport:1">'
    '<InstanceID>0</InstanceID><Speed>1</Speed></u:Play></s:Body></s:Envelope>'
)
ENV_GET_MEDIA = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
    '<s:Body><u:GetMediaInfo xmlns:u="urn:schemas-upnp-org:service:AVTransport:1">'
    "<InstanceID>0</InstanceID></u:GetMediaInfo></s:Body></s:Envelope>"
)

# DeviceProperties — LED state on/off. Sonos accepts the literal
# strings "On" / "Off" (capitalised) in DesiredLEDState.
ENV_GET_LED = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
    '<s:Body><u:GetLEDState xmlns:u="urn:schemas-upnp-org:service:DeviceProperties:1">'
    "</u:GetLEDState></s:Body></s:Envelope>"
)
ENV_SET_LED = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
    '<s:Body><u:SetLEDState xmlns:u="urn:schemas-upnp-org:service:DeviceProperties:1">'
    "<DesiredLEDState>{val}</DesiredLEDState></u:SetLEDState></s:Body></s:Envelope>"
)

# GroupRenderingControl — only the group coordinator accepts these.
# Non-coordinators return a SOAP fault that surfaces as LastError.
ENV_GET_GROUP_VOLUME = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
    '<s:Body><u:GetGroupVolume xmlns:u="urn:schemas-upnp-org:service:GroupRenderingControl:1">'
    "<InstanceID>0</InstanceID></u:GetGroupVolume></s:Body></s:Envelope>"
)
ENV_SET_GROUP_VOLUME = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
    '<s:Body><u:SetGroupVolume xmlns:u="urn:schemas-upnp-org:service:GroupRenderingControl:1">'
    "<InstanceID>0</InstanceID><DesiredVolume>{val}</DesiredVolume>"
    "</u:SetGroupVolume></s:Body></s:Envelope>"
)

# RenderingControl room calibration (Trueplay). Set toggles whether
# the player applies its stored Trueplay calibration; the actual
# calibration is run from the iOS app and cannot be triggered from
# the local API.
ENV_GET_ROOM_CAL = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
    '<s:Body><u:GetRoomCalibrationStatus '
    'xmlns:u="urn:schemas-upnp-org:service:RenderingControl:1">'
    "<InstanceID>0</InstanceID></u:GetRoomCalibrationStatus></s:Body></s:Envelope>"
)
ENV_SET_ROOM_CAL = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
    '<s:Body><u:SetRoomCalibrationStatus '
    'xmlns:u="urn:schemas-upnp-org:service:RenderingControl:1">'
    "<InstanceID>0</InstanceID>"
    "<RoomCalibrationEnabled>{val}</RoomCalibrationEnabled>"
    "</u:SetRoomCalibrationStatus></s:Body></s:Envelope>"
)


SERVICE_PATHS = {
    "AVTransport":           "/MediaRenderer/AVTransport/Control",
    "RenderingControl":      "/MediaRenderer/RenderingControl/Control",
    "DeviceProperties":      "/DeviceProperties/Control",
    "GroupRenderingControl": "/MediaRenderer/GroupRenderingControl/Control",
}
SERVICE_TYPES = {
    "AVTransport":           "urn:schemas-upnp-org:service:AVTransport:1",
    "RenderingControl":      "urn:schemas-upnp-org:service:RenderingControl:1",
    "DeviceProperties":      "urn:schemas-upnp-org:service:DeviceProperties:1",
    "GroupRenderingControl": "urn:schemas-upnp-org:service:GroupRenderingControl:1",
}


# ---------------------------------------------------------------------------
# Pure helpers — testable in isolation, no framework dependency.
# ---------------------------------------------------------------------------

def to_str(value):
    if value is None:
        return ""
    if isinstance(value, bytes):
        try:
            return value.decode("iso-8859-15")
        except Exception:
            return value.decode("utf-8", errors="replace")
    return str(value)


def to_iso_bytes(value):
    if value is None:
        return b""
    if isinstance(value, bytes):
        return value
    return str(value).encode("iso-8859-15", "replace")


def extract_response_field(body, field):
    m = re.search(r"<{f}>([^<]*)</{f}>".format(f=re.escape(field)), body)
    return m.group(1) if m else None


def extract_soap_fault_code(body):
    m = re.search(r"<errorCode>(\d+)</errorCode>", body)
    return m.group(1) if m else None


def minutes_to_duration(minutes):
    """Convert integer minutes to Sonos's HH:MM:SS string. Empty
    string cancels the sleep timer per the AVTransport contract."""
    try:
        m = int(minutes)
    except (TypeError, ValueError):
        return ""
    if m <= 0:
        return ""
    h, rem = divmod(m, 60)
    return "{:d}:{:02d}:00".format(h, rem)


def duration_to_seconds(s):
    """Parse Sonos's HH:MM:SS duration into total seconds. Returns 0
    for empty / missing values (= no sleep timer active)."""
    if not s:
        return 0
    parts = s.strip().split(":")
    if len(parts) != 3:
        return 0
    try:
        h, m, sec = int(parts[0]), int(parts[1]), int(parts[2])
    except ValueError:
        return 0
    return h * 3600 + m * 60 + sec


def clamp(value, lo, hi):
    try:
        v = int(value)
    except (TypeError, ValueError):
        v = 0
    return max(lo, min(hi, v))


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


# ---------------------------------------------------------------------------
# Cross-LBS lookups — discover the Admin module's helpers via sys.modules.
# Same pattern LBS 22000 uses to find the Admin.
# ---------------------------------------------------------------------------

def resolve_host_spec(spec):
    """Map a Host input value (IP / MAC / name / UUID) to a usable
    IP. Asks the Admin LBS's resolve_host when present; otherwise
    only IP literals work."""
    if not spec:
        return ""
    spec = str(spec).strip()
    if _is_ip_literal(spec):
        return spec
    for mod_name, mod in list(sys.modules.items()):
        if mod is None:
            continue
        if "sonos_admin" in mod_name or "hsl3_22001" in mod_name:
            fn = getattr(mod, "resolve_host", None)
            if callable(fn):
                try:
                    r = fn(spec)
                    if r:
                        return r
                except Exception:
                    pass
    return ""


def _admin_player_record(spec):
    """Return the Admin's full player record (with ``uuid`` field) for
    the given Host spec, or None when Admin isn't loaded. Needed to
    build the TV-stream URI (`x-sonos-htastream:<UUID>:spdif`) and to
    show "this player is a slave" when GroupVolume calls are
    rejected."""
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


def _admin_player_tunables(spec):
    """Effective per-player tunables — same lookup pattern LBS 22000
    uses. Returns ``{}`` when Admin isn't loaded; `_reload_config`
    then uses hard-coded floors."""
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
    """Pull the Admin's project-wide defaults — kept for tests that
    exercise the global-fallback layer directly."""
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


# ===========================================================================
# Mandatory class
# ===========================================================================

class LogicModule:

    def __init__(self, hsl3):
        self.fw = hsl3
        self.logger = hsl3.get_logger()
        self.debug = None

        self._host_spec = ""
        self._host = ""
        self._poll_interval_s = 60
        self._http_timeout_s = 5
        self._online = False

        # Last-applied values for the value-driven inputs. Used to
        # decide whether a new on_calc dispatch is needed.
        self._last_bass = None
        self._last_treble = None
        self._last_loudness = None
        self._last_night = None
        self._last_dialog = None
        self._last_crossfade = None
        self._last_sleep_remaining = -1
        self._last_tv_mode = None
        self._last_led = None
        self._last_battery_pct = None
        self._last_battery_charging = None
        self._last_group_volume = None
        self._last_surround_enable = None
        self._last_surround_level = None
        self._last_sub_enable = None
        self._last_sub_gain = None
        self._last_trueplay = None
        self._last_trueplay_avail = None
        self._uuid = ""

    # ----- HSL3 entry points -----------------------------------------------

    def on_init(self, inputs, store):
        self.debug = self.fw.create_debug_section()
        self.debug.set("Polls", 0)
        self.debug.set("Last error", "-")
        self._reload_config(inputs)
        # First tick shortly after startup so outputs populate quickly.
        self.fw.set_timer("Tick", 5)

    def on_calc(self, inputs):
        self._reload_config(inputs)
        if not self._host:
            self._write_error("NO_HOST_CONFIGURED")
            return

        # Each input dispatches on any change of value. Bass / Treble
        # are signed integers clamped to -10..10; the others are
        # 0/1 booleans; sleep timer is minutes (0 cancels).
        if inputs["SetBass"].changed:
            val = clamp(inputs["SetBass"].value, -10, 10)
            self._run_threaded(lambda v=val: self._action_set_bass(v))
        if inputs["SetTreble"].changed:
            val = clamp(inputs["SetTreble"].value, -10, 10)
            self._run_threaded(lambda v=val: self._action_set_treble(v))
        if inputs["SetLoudness"].changed:
            on = 1 if inputs["SetLoudness"].value else 0
            self._run_threaded(lambda v=on: self._action_set_loudness(v))
        if inputs["SetNightMode"].changed:
            on = 1 if inputs["SetNightMode"].value else 0
            self._run_threaded(
                lambda v=on: self._action_set_eq("NightMode", v, "NIGHTMODE_UNSUPPORTED")
            )
        if inputs["SetDialogMode"].changed:
            on = 1 if inputs["SetDialogMode"].value else 0
            self._run_threaded(
                lambda v=on: self._action_set_eq("DialogLevel", v, "DIALOGMODE_UNSUPPORTED")
            )
        if inputs["SetCrossfade"].changed:
            on = 1 if inputs["SetCrossfade"].value else 0
            self._run_threaded(lambda v=on: self._action_set_crossfade(v))
        if inputs["SetSleepTimer"].changed:
            mins = int(inputs["SetSleepTimer"].value or 0)
            self._run_threaded(lambda m=mins: self._action_set_sleep_timer(m))
        # TV input — rising edge only. Engaging the soundbar's
        # physical TV input is a "now" action; reverting to music is
        # handled by triggering a music preset on LBS 22000.
        if inputs["SetTVMode"].changed and inputs["SetTVMode"].value != 0:
            self._run_threaded(self._action_switch_tv_mode)
        if inputs["SetLED"].changed:
            on = 1 if inputs["SetLED"].value else 0
            self._run_threaded(lambda v=on: self._action_set_led(v))
        if inputs["SetGroupVolume"].changed:
            v = clamp(inputs["SetGroupVolume"].value, 0, 100)
            self._run_threaded(lambda x=v: self._action_set_group_volume(x))
        if inputs["SetSurroundEnable"].changed:
            on = 1 if inputs["SetSurroundEnable"].value else 0
            self._run_threaded(lambda v=on:
                self._action_set_eq("SurroundEnable", v, "SURROUND_UNSUPPORTED"))
        if inputs["SetSurroundLevel"].changed:
            v = clamp(inputs["SetSurroundLevel"].value, -15, 15)
            self._run_threaded(lambda x=v:
                self._action_set_eq("SurroundLevel", x, "SURROUND_UNSUPPORTED"))
        if inputs["SetSubEnable"].changed:
            on = 1 if inputs["SetSubEnable"].value else 0
            self._run_threaded(lambda v=on:
                self._action_set_eq("SubEnable", v, "SUB_UNSUPPORTED"))
        if inputs["SetSubGain"].changed:
            v = clamp(inputs["SetSubGain"].value, -15, 15)
            self._run_threaded(lambda x=v:
                self._action_set_eq("SubGain", x, "SUB_UNSUPPORTED"))
        if inputs["SetTrueplay"].changed:
            on = 1 if inputs["SetTrueplay"].value else 0
            self._run_threaded(lambda v=on: self._action_set_trueplay(v))

    def on_timer(self, timer):
        if timer["Tick"].changed:
            self.fw.set_timer("Tick", self._poll_interval_s)
            self._run_threaded(self._tick_work)

    # ----- Config / input reading ------------------------------------------

    def _reload_config(self, inputs):
        self._host_spec = to_str(inputs["Host"].value).strip()
        resolved = resolve_host_spec(self._host_spec)
        self._host = resolved or (self._host_spec if _is_ip_literal(self._host_spec) else "")
        # Tunables come entirely from the Admin (project default
        # plus optional per-player override). No tunable inputs on
        # this block — same model as LBS 22000.
        tunables = _admin_player_tunables(self._host_spec)
        self._poll_interval_s = max(10, int(tunables.get("pollInterval") or 60))
        self._http_timeout_s  = max(2,  int(tunables.get("httpTimeout")  or 5))

    # ----- Worker thread plumbing ------------------------------------------

    def _run_threaded(self, fn):
        t = threading.Thread(target=self._run_safely, args=(fn,), daemon=True)
        t.start()

    def _run_safely(self, fn):
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            try:
                self.logger.exception("Sound action failed: %s", exc)
            except Exception:
                pass
            self.fw.run_in_context(self._write_error, ("EXCEPTION: {}".format(exc),))

    def _soap(self, service, action, envelope):
        url = "http://{}:1400{}".format(self._host, SERVICE_PATHS[service])
        headers = {
            "Content-Type": 'text/xml; charset="utf-8"',
            "SOAPACTION": '"{}#{}"'.format(SERVICE_TYPES[service], action),
        }
        try:
            resp = requests.post(url, data=envelope.encode("utf-8"),
                                 headers=headers, timeout=self._http_timeout_s)
        except requests.exceptions.RequestException:
            return False, "", "UNREACHABLE"
        if 200 <= resp.status_code < 300:
            return True, resp.text, ""
        code = extract_soap_fault_code(resp.text) or "HTTP_{}".format(resp.status_code)
        return False, resp.text, code

    # ----- Actions (run in worker threads) ---------------------------------

    def _action_set_bass(self, val):
        ok, _b, err = self._soap("RenderingControl", "SetBass", ENV_SET_BASS.format(val=val))
        if ok:
            self.fw.run_in_context(self._mark_bass, (val,))
        else:
            self.fw.run_in_context(self._write_error, (err or "SET_BASS_FAILED",))

    def _action_set_treble(self, val):
        ok, _b, err = self._soap("RenderingControl", "SetTreble", ENV_SET_TREBLE.format(val=val))
        if ok:
            self.fw.run_in_context(self._mark_treble, (val,))
        else:
            self.fw.run_in_context(self._write_error, (err or "SET_TREBLE_FAILED",))

    def _action_set_loudness(self, on):
        ok, _b, err = self._soap("RenderingControl", "SetLoudness", ENV_SET_LOUDNESS.format(val=on))
        if ok:
            self.fw.run_in_context(self._mark_loudness, (on,))
        else:
            self.fw.run_in_context(self._write_error, (err or "SET_LOUDNESS_FAILED",))

    def _action_set_eq(self, eq_type, val, unsupported_label):
        """Soundbar / sub EQ knob — every variant (NightMode,
        DialogLevel, SurroundEnable, SurroundLevel, SubEnable,
        SubGain) goes through the same RenderingControl#SetEQ action,
        keyed on EQType. Players that don't support the requested
        bucket reject with a SOAP fault; we surface a tagged
        LastError instead of pretending it worked so the integrator
        can spot the misconfigured wiring."""
        env = ENV_SET_EQ.format(eq=eq_type, val=val)
        ok, _b, err = self._soap("RenderingControl", "SetEQ", env)
        if not ok:
            self.fw.run_in_context(self._write_error, (unsupported_label,))
            return
        # Reflect the change on the corresponding output. Lookup by
        # EQType keeps the action body free of per-knob branches.
        marker = {
            "NightMode":      self._mark_night,
            "DialogLevel":    self._mark_dialog,
            "SurroundEnable": self._mark_surround_enable,
            "SurroundLevel":  self._mark_surround_level,
            "SubEnable":      self._mark_sub_enable,
            "SubGain":        self._mark_sub_gain,
        }.get(eq_type)
        if marker is not None:
            self.fw.run_in_context(marker, (val,))

    def _action_set_crossfade(self, on):
        ok, _b, err = self._soap("AVTransport", "SetCrossfadeMode",
                                 ENV_SET_CROSSFADE.format(val=on))
        if ok:
            self.fw.run_in_context(self._mark_crossfade, (on,))
        else:
            self.fw.run_in_context(self._write_error, (err or "SET_CROSSFADE_FAILED",))

    def _action_switch_tv_mode(self):
        """Engage the soundbar's physical TV input. URI is
        ``x-sonos-htastream:<player-UUID>:spdif`` — Sonos accepts this
        on any HT-capable speaker (Beam / Arc / Playbar / Playbase /
        Ray). Non-soundbar players reject with HT_NOT_SUPPORTED or a
        SOAP fault; we surface NO_HT_STREAM on LastError. The reverse
        ("back to music") is achieved by triggering a music preset
        on LBS 22000 — Sonos has no "leave TV mode" action."""
        uuid = self._resolve_uuid()
        if not uuid:
            self.fw.run_in_context(self._write_error, ("TV_NO_UUID",))
            return
        # The UUID is ASCII-safe (RINCON_xxx) so no XML escaping is
        # needed in the SetAVTransportURI envelope.
        ht_uri = "x-sonos-htastream:{}:spdif".format(uuid)
        env = ENV_SET_URI.replace("{uri}", ht_uri)
        ok, _b, err = self._soap("AVTransport", "SetAVTransportURI", env)
        if not ok:
            self.fw.run_in_context(self._write_error, (err or "NO_HT_STREAM",))
            return
        ok, _b, _err = self._soap("AVTransport", "Play", ENV_PLAY)
        if ok:
            self.fw.run_in_context(self._mark_tv_mode, (1,))

    def _action_set_led(self, on):
        env = ENV_SET_LED.format(val="On" if on else "Off")
        ok, _b, err = self._soap("DeviceProperties", "SetLEDState", env)
        if ok:
            self.fw.run_in_context(self._mark_led, (1 if on else 0,))
        else:
            self.fw.run_in_context(self._write_error, (err or "SET_LED_FAILED",))

    def _action_set_group_volume(self, val):
        """SetGroupVolume only works on the group coordinator. Slaves
        return a SOAP fault — we tag that as GROUP_NOT_COORDINATOR so
        the integrator knows to wire SetGroupVolume to the matching
        coordinator's LBS 22002 (or to use this player as the master
        of a group preset on LBS 22000)."""
        env = ENV_SET_GROUP_VOLUME.format(val=int(val))
        ok, _b, err = self._soap("GroupRenderingControl", "SetGroupVolume", env)
        if ok:
            self.fw.run_in_context(self._mark_group_volume, (int(val),))
        elif err and err == "701":
            # 701 = "Transition not available" — Sonos returns this
            # when called on a non-coordinator.
            self.fw.run_in_context(self._write_error, ("GROUP_NOT_COORDINATOR",))
        else:
            self.fw.run_in_context(self._write_error, (err or "SET_GROUP_VOLUME_FAILED",))

    def _action_set_trueplay(self, on):
        env = ENV_SET_ROOM_CAL.format(val=int(on))
        ok, _b, err = self._soap("RenderingControl", "SetRoomCalibrationStatus", env)
        if ok:
            self.fw.run_in_context(self._mark_trueplay, (1 if on else 0,))
        else:
            self.fw.run_in_context(self._write_error, (err or "TRUEPLAY_UNAVAILABLE",))

    def _resolve_uuid(self):
        """Return the player's RINCON UUID. Caches the lookup so
        repeat TV-mode switches don't re-poll the Admin."""
        if self._uuid:
            return self._uuid
        rec = _admin_player_record(self._host_spec) or _admin_player_record(self._host)
        if rec and rec.get("uuid"):
            self._uuid = rec["uuid"]
        return self._uuid

    def _action_set_sleep_timer(self, minutes):
        duration = minutes_to_duration(minutes)
        env = ENV_CONFIGURE_SLEEP.replace("{duration}", duration)
        ok, _b, err = self._soap("AVTransport", "ConfigureSleepTimer", env)
        if ok:
            # Don't update the remaining output here — the next Tick
            # poll will read the actual value Sonos accepted (it may
            # round to a different granularity).
            self.fw.run_in_context(self._publish_sleep_dispatched, ())
        else:
            self.fw.run_in_context(self._write_error, (err or "SET_SLEEP_FAILED",))

    # ----- Periodic poll ---------------------------------------------------

    def _tick_work(self):
        # Re-resolve the host on every Tick so DHCP renumbering picked
        # up by the Admin registry takes effect here too.
        if self._host_spec:
            resolved = resolve_host_spec(self._host_spec)
            if resolved:
                self._host = resolved
        if not self._host:
            return

        any_ok = False
        # Bass / Treble — RenderingControl scalar getters.
        ok, body, _err = self._soap("RenderingControl", "GetBass", ENV_GET_BASS)
        if ok:
            any_ok = True
            try:
                v = int(extract_response_field(body, "CurrentBass") or 0)
                self.fw.run_in_context(self._mark_bass, (v,))
            except ValueError:
                pass
        ok, body, _err = self._soap("RenderingControl", "GetTreble", ENV_GET_TREBLE)
        if ok:
            any_ok = True
            try:
                v = int(extract_response_field(body, "CurrentTreble") or 0)
                self.fw.run_in_context(self._mark_treble, (v,))
            except ValueError:
                pass
        # Loudness — channel-keyed boolean.
        ok, body, _err = self._soap("RenderingControl", "GetLoudness", ENV_GET_LOUDNESS)
        if ok:
            any_ok = True
            v = 1 if (extract_response_field(body, "CurrentLoudness") == "1") else 0
            self.fw.run_in_context(self._mark_loudness, (v,))
        # Soundbar EQs — silently skip when not supported (returned by
        # non-soundbar models with a SOAP fault). No LastError on poll
        # failures since the integrator already knows from the
        # NIGHTMODE_UNSUPPORTED tag on the explicit Set call.
        ok, body, _err = self._soap("RenderingControl", "GetEQ",
                                    ENV_GET_EQ.format(eq="NightMode"))
        if ok:
            v = 1 if (extract_response_field(body, "CurrentValue") == "1") else 0
            self.fw.run_in_context(self._mark_night, (v,))
        ok, body, _err = self._soap("RenderingControl", "GetEQ",
                                    ENV_GET_EQ.format(eq="DialogLevel"))
        if ok:
            v = 1 if (extract_response_field(body, "CurrentValue") == "1") else 0
            self.fw.run_in_context(self._mark_dialog, (v,))
        # Crossfade — AVTransport boolean.
        ok, body, _err = self._soap("AVTransport", "GetCrossfadeMode", ENV_GET_CROSSFADE)
        if ok:
            any_ok = True
            v = 1 if (extract_response_field(body, "CrossfadeMode") == "1") else 0
            self.fw.run_in_context(self._mark_crossfade, (v,))
        # Sleep timer remaining — duration string, 0 when no timer
        # active. Polled on every Tick so the countdown moves visibly.
        ok, body, _err = self._soap(
            "AVTransport", "GetRemainingSleepTimerDuration", ENV_GET_SLEEP_REMAINING
        )
        if ok:
            any_ok = True
            raw = extract_response_field(body, "RemainingSleepTimerDuration") or ""
            self.fw.run_in_context(self._mark_sleep_remaining, (duration_to_seconds(raw),))

        # TV mode — derive from CurrentURI (GetMediaInfo). Soundbars
        # report x-sonos-htastream:... when their physical input is
        # engaged; everything else is music.
        ok, body, _err = self._soap("AVTransport", "GetMediaInfo", ENV_GET_MEDIA)
        if ok:
            cur_uri = extract_response_field(body, "CurrentURI") or ""
            on = 1 if cur_uri.startswith("x-sonos-htastream:") else 0
            self.fw.run_in_context(self._mark_tv_mode, (on,))

        # LED state — "On"/"Off" string in CurrentLEDState.
        ok, body, _err = self._soap("DeviceProperties", "GetLEDState", ENV_GET_LED)
        if ok:
            v = 1 if (extract_response_field(body, "CurrentLEDState") or "").strip() == "On" else 0
            self.fw.run_in_context(self._mark_led, (v,))

        # Battery — only present on portable speakers (Move / Roam).
        # Non-portable hardware returns 404 at /status/batterystatus
        # which we treat as "no battery" and leave outputs at 0.
        pct, charging = self._fetch_battery_status()
        if pct is not None:
            self.fw.run_in_context(self._mark_battery, (pct, charging))

        # Group volume — works only on the coordinator. Slaves get a
        # SOAP fault; we silently skip the output update so a slave
        # block doesn't flood LastError every Tick.
        ok, body, _err = self._soap("GroupRenderingControl",
                                    "GetGroupVolume", ENV_GET_GROUP_VOLUME)
        if ok:
            try:
                v = int(extract_response_field(body, "CurrentVolume") or 0)
                self.fw.run_in_context(self._mark_group_volume, (v,))
            except ValueError:
                pass

        # Surround / Sub EQ — GetEQ returns CurrentValue 0/1 for
        # *Enable buckets, -15..15 for *Level/*Gain buckets. Same
        # silent-skip approach as the NightMode poll above: a SOAP
        # fault means the player doesn't have the bucket, no point
        # flooding LastError.
        for eq_type, marker in (
            ("SurroundEnable", self._mark_surround_enable),
            ("SurroundLevel",  self._mark_surround_level),
            ("SubEnable",      self._mark_sub_enable),
            ("SubGain",        self._mark_sub_gain),
        ):
            ok, body, _err = self._soap("RenderingControl", "GetEQ",
                                        ENV_GET_EQ.format(eq=eq_type))
            if ok:
                try:
                    v = int(extract_response_field(body, "CurrentValue") or 0)
                    self.fw.run_in_context(marker, (v,))
                except ValueError:
                    pass

        # Trueplay — RoomCalibrationEnabled (applied or not) +
        # RoomCalibrationAvailable (whether a calibration profile
        # exists at all on the player).
        ok, body, _err = self._soap("RenderingControl",
                                    "GetRoomCalibrationStatus", ENV_GET_ROOM_CAL)
        if ok:
            en = 1 if (extract_response_field(body, "RoomCalibrationEnabled") == "1") else 0
            av = 1 if (extract_response_field(body, "RoomCalibrationAvailable") == "1") else 0
            self.fw.run_in_context(self._mark_trueplay, (en,))
            self.fw.run_in_context(self._mark_trueplay_avail, (av,))

        # Online flag — set when at least one SOAP responded. A
        # totally unreachable player has every call timing out.
        self.fw.run_in_context(self._mark_online, (any_ok,))
        if self.debug is not None:
            self.fw.run_in_context(self._inc_poll, ())

    # ----- Output marshalling (runs in node context) -----------------------

    def _mark_online(self, online):
        if online != self._online:
            self._online = bool(online)
            self.fw.set_output("Online", 1 if self._online else 0)

    def _mark_bass(self, val):
        v = int(val)
        if v != self._last_bass:
            self._last_bass = v
            self.fw.set_output("Bass", float(v))

    def _mark_treble(self, val):
        v = int(val)
        if v != self._last_treble:
            self._last_treble = v
            self.fw.set_output("Treble", float(v))

    def _mark_loudness(self, on):
        v = 1 if on else 0
        if v != self._last_loudness:
            self._last_loudness = v
            self.fw.set_output("Loudness", v)

    def _mark_night(self, on):
        v = 1 if on else 0
        if v != self._last_night:
            self._last_night = v
            self.fw.set_output("NightMode", v)

    def _mark_dialog(self, on):
        v = 1 if on else 0
        if v != self._last_dialog:
            self._last_dialog = v
            self.fw.set_output("DialogMode", v)

    def _mark_crossfade(self, on):
        v = 1 if on else 0
        if v != self._last_crossfade:
            self._last_crossfade = v
            self.fw.set_output("Crossfade", v)

    def _fetch_battery_status(self):
        """Sonos exposes battery info at a plain HTTP endpoint, not
        SOAP. Returns (percent, charging_bool) or (None, None) for
        non-portable hardware (which returns 404)."""
        if not self._host:
            return None, None
        try:
            resp = requests.get(
                "http://{}:1400/status/batterystatus".format(self._host),
                timeout=self._http_timeout_s,
            )
        except requests.exceptions.RequestException:
            return None, None
        if resp.status_code != 200:
            return None, None
        # The response is XML with <Data name="Level">85</Data> and
        # <Data name="PowerSource">BATTERY|CHARGING|...</Data>.
        body = resp.text or ""
        m_pct = re.search(r'<Data name="Level">([^<]*)</Data>', body)
        m_src = re.search(r'<Data name="PowerSource">([^<]*)</Data>', body)
        if not m_pct:
            return None, None
        try:
            pct = int(m_pct.group(1))
        except ValueError:
            return None, None
        src = (m_src.group(1).strip().upper() if m_src else "")
        charging = src in ("CHARGING", "USB_POWER", "MAINS")
        return pct, charging

    def _mark_tv_mode(self, on):
        v = 1 if on else 0
        if v != self._last_tv_mode:
            self._last_tv_mode = v
            self.fw.set_output("TVMode", v)

    def _mark_led(self, on):
        v = 1 if on else 0
        if v != self._last_led:
            self._last_led = v
            self.fw.set_output("LED", v)

    def _mark_battery(self, pct, charging):
        p = max(0, min(100, int(pct)))
        if p != self._last_battery_pct:
            self._last_battery_pct = p
            self.fw.set_output("BatteryPercent", float(p))
        c = 1 if charging else 0
        if c != self._last_battery_charging:
            self._last_battery_charging = c
            self.fw.set_output("BatteryCharging", c)

    def _mark_group_volume(self, val):
        v = max(0, min(100, int(val)))
        if v != self._last_group_volume:
            self._last_group_volume = v
            self.fw.set_output("GroupVolume", float(v))

    def _mark_surround_enable(self, val):
        v = 1 if val else 0
        if v != self._last_surround_enable:
            self._last_surround_enable = v
            self.fw.set_output("SurroundEnable", v)

    def _mark_surround_level(self, val):
        v = int(val)
        if v != self._last_surround_level:
            self._last_surround_level = v
            self.fw.set_output("SurroundLevel", float(v))

    def _mark_sub_enable(self, val):
        v = 1 if val else 0
        if v != self._last_sub_enable:
            self._last_sub_enable = v
            self.fw.set_output("SubEnable", v)

    def _mark_sub_gain(self, val):
        v = int(val)
        if v != self._last_sub_gain:
            self._last_sub_gain = v
            self.fw.set_output("SubGain", float(v))

    def _mark_trueplay(self, val):
        v = 1 if val else 0
        if v != self._last_trueplay:
            self._last_trueplay = v
            self.fw.set_output("Trueplay", v)

    def _mark_trueplay_avail(self, val):
        v = 1 if val else 0
        if v != self._last_trueplay_avail:
            self._last_trueplay_avail = v
            self.fw.set_output("TrueplayAvailable", v)

    def _mark_sleep_remaining(self, seconds):
        s = max(0, int(seconds))
        if s != self._last_sleep_remaining:
            self._last_sleep_remaining = s
            self.fw.set_output("SleepTimerRemaining", float(s))

    def _publish_sleep_dispatched(self):
        # Nothing to publish — the next Tick re-reads the canonical value.
        # Hook kept so SetSleepTimer has a node-context callback for the
        # debug counter / future audit logging.
        if self.debug is not None:
            self.debug.timestamp("Sleep dispatched")

    def _write_error(self, code):
        if self.debug is not None:
            self.debug.set("Last error", to_iso_bytes(str(code)))
        self.fw.set_output("LastError", to_iso_bytes(str(code)))

    def _inc_poll(self):
        if self.debug is not None:
            self.debug.inc("Polls")
            self.debug.timestamp("Last poll")
