#!/usr/bin/env python3
"""Test the HSL3 LogicModule classes with a stubbed Hsl3Framework.

These tests are CI-friendly per the SDK's testing guidance: the framework
stub mirrors the real API (set_output / set_timer / set_store / get_logger /
create_debug_section / run_in_context), and HTTP calls are monkeypatched so
nothing reaches a real Sonos player.
"""

import importlib.util
import json
import os
import sys
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLAYER_PY = ROOT / "projects" / "sonos_player_hsl3" / "hsl3_22000_sonos_player.py"
ADMIN_PY = ROOT / "projects" / "sonos_admin_hsl3" / "hsl3_22001_sonos_admin.py"


def load_module(path: Path, mod_name: str):
    spec = importlib.util.spec_from_file_location(mod_name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Framework stub mirroring the Hsl3Framework surface used by our modules.
# ---------------------------------------------------------------------------

class StubSlot:
    def __init__(self, value, changed=False):
        self.value = value
        self.changed = changed


class StubSlots(dict):
    def get(self, key, default=None):
        return super().get(key, default)

    def value(self, key):
        s = super().get(key)
        return s.value if s else None

    def changed(self, key):  # noqa: A003 — shadow built-in to match SDK shape
        s = super().get(key)
        return s.changed if s else False

    def keys(self):
        return list(super().keys())


class StubDebug:
    def __init__(self):
        self.fields = {}
        self.counters = {}
        self.logs = []

    def set(self, name, value):
        self.fields[name] = value

    def inc(self, name, value=1):
        self.counters[name] = self.counters.get(name, 0) + value

    def avg(self, name, value):
        self.fields[name] = value

    def timestamp(self, name, value=None):
        self.fields[name] = value or "now"

    def log(self, msg):
        self.logs.append(msg)

    def exception(self, msg):
        self.logs.append("EXC: " + msg)


class StubLogger:
    def __init__(self):
        self.records = []

    def info(self, msg, *args):    self.records.append(("info", msg % args if args else msg))
    def warning(self, msg, *args): self.records.append(("warn", msg % args if args else msg))
    def error(self, msg, *args):   self.records.append(("err",  msg % args if args else msg))
    def exception(self, msg, *args): self.records.append(("exc", msg % args if args else msg))


class StubFramework:
    def __init__(self):
        self.outputs = {}
        self.stores = {}
        self.timers = {}
        self.stores = {}
        self._debug = StubDebug()
        self._logger = StubLogger()
        # When True, run_in_context dispatches synchronously so tests can
        # assert results without waiting on threads.
        self.sync = True

    # --- SDK surface ---
    def set_output(self, key, value):
        if value is None:
            raise ValueError("set_output value must not be None")
        if isinstance(value, str):
            raise ValueError("set_output value must be bytes/int/float, not str")
        self.outputs[key] = value

    def set_store(self, key, value):
        if value is None:
            raise ValueError("set_store value must not be None")
        self.stores[key] = value

    def set_timer(self, key, seconds):
        self.timers[key] = seconds

    def get_logger(self, **_kw):
        return self._logger

    def create_debug_section(self):
        return self._debug

    def run_in_context(self, callback, params):
        if self.sync:
            callback(*params)
        else:
            t = threading.Thread(target=callback, args=params, daemon=True)
            t.start()

    def get_instance_id(self):
        return 1

    def get_module_id(self):
        return 22000

    def get_context_id(self):
        return "sonos.bridge"


def make_player_inputs(host="10.0.0.1", **overrides):
    base = {
        "Host":         StubSlot(host),
        "Play":         StubSlot(0),
        "Pause":        StubSlot(0),
        "Stop":         StubSlot(0),
        "Next":         StubSlot(0),
        "Prev":         StubSlot(0),
        "SetVolume":    StubSlot(0),
        "VolUp":        StubSlot(0),
        "VolDown":      StubSlot(0),
        "SetMute":      StubSlot(0),
        "MuteToggle":   StubSlot(0),
        "SetShuffle":   StubSlot(0),
        "SetRepeat":    StubSlot(0),
        "StartRadio":   StubSlot(0),
        "StartRadioName": StubSlot(""),
        "GroupPreset":  StubSlot(0),
        "GroupPresetName": StubSlot(""),
        "Ungroup":      StubSlot(0),
        "Resubscribe":  StubSlot(0),
        "VolStep":      StubSlot(2),
        "PollInterval": StubSlot(60),
        "SubTimeout":   StubSlot(1800),
        "HttpTimeout":  StubSlot(5),
        "CallbackBase": StubSlot(""),
    }
    base.update({k: (v if isinstance(v, StubSlot) else StubSlot(v))
                 for k, v in overrides.items()})
    return StubSlots(base)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSonosPlayerHelpers(unittest.TestCase):
    """Pure helper functions — no framework needed."""

    @classmethod
    def setUpClass(cls):
        # Load with requests stubbed so import does not hit the network.
        sys.modules.setdefault("requests", _make_requests_stub())
        cls.mod = load_module(PLAYER_PY, "sonos_player_22000")

    def test_normalize_radio_uri(self):
        n = self.mod.normalize_radio_uri
        self.assertEqual(n("http://x.com/r.mp3"),  "x-rincon-mp3radio://x.com/r.mp3")
        self.assertEqual(n("https://x.com/r.mp3"), "x-rincon-mp3radio://x.com/r.mp3")
        self.assertEqual(n("x-rincon-stream://abc"), "x-rincon-stream://abc")
        self.assertEqual(n(""), "")

    def test_unescape_xml(self):
        u = self.mod.unescape_xml
        self.assertEqual(u("a &amp; b &lt; c &gt; d"), "a & b < c > d")

    def test_extract_response_field(self):
        body = "<u:GetVolumeResponse><CurrentVolume>33</CurrentVolume></u:GetVolumeResponse>"
        self.assertEqual(self.mod.extract_response_field(body, "CurrentVolume"), "33")
        self.assertIsNone(self.mod.extract_response_field(body, "Missing"))

    def test_extract_soap_fault_code(self):
        body = "<UPnPError><errorCode>714</errorCode><errorDescription>x</errorDescription></UPnPError>"
        self.assertEqual(self.mod.extract_soap_fault_code(body), "714")
        self.assertIsNone(self.mod.extract_soap_fault_code("<ok/>"))

    def test_parse_notify_avtransport(self):
        sample = """<?xml version="1.0"?>
<e:propertyset xmlns:e="urn:schemas-upnp-org:event-1-0">
<e:property><LastChange>&lt;Event&gt;&lt;InstanceID val=&quot;0&quot;&gt;
&lt;TransportState val=&quot;PLAYING&quot;/&gt;
&lt;CurrentTrackURI val=&quot;x-rincon-mp3radio://s&quot;/&gt;
&lt;CurrentTrackMetaData val=&quot;&amp;lt;dc:title&amp;gt;Foo&amp;lt;/dc:title&amp;gt;&quot;/&gt;
&lt;/InstanceID&gt;&lt;/Event&gt;</LastChange></e:property></e:propertyset>"""
        r = self.mod.parse_notify(sample)
        self.assertEqual(r["state"], "PLAYING")
        self.assertEqual(r["trackUri"], "x-rincon-mp3radio://s")
        self.assertEqual(r["title"], "Foo")

    def test_parse_notify_volume(self):
        sample = """<e:propertyset><e:property><LastChange>&lt;Event&gt;&lt;InstanceID val=&quot;0&quot;&gt;
&lt;Volume channel=&quot;Master&quot; val=&quot;42&quot;/&gt;
&lt;Mute channel=&quot;Master&quot; val=&quot;1&quot;/&gt;
&lt;/InstanceID&gt;&lt;/Event&gt;</LastChange></e:property></e:propertyset>"""
        r = self.mod.parse_notify(sample)
        self.assertEqual(r["volume"], 42)
        self.assertTrue(r["mute"])

    def test_to_iso_bytes_encodes(self):
        self.assertEqual(self.mod.to_iso_bytes("Foo"), b"Foo")
        self.assertEqual(self.mod.to_iso_bytes(None), b"")

    def test_to_str_decodes(self):
        self.assertEqual(self.mod.to_str(b"Foo"), "Foo")
        self.assertEqual(self.mod.to_str(None), "")

    def test_play_mode_to_flags(self):
        f = self.mod.play_mode_to_flags
        self.assertEqual(f("NORMAL"),             (False, False))
        self.assertEqual(f("REPEAT_ALL"),         (False, True))
        self.assertEqual(f("REPEAT_ONE"),         (False, True))
        self.assertEqual(f("SHUFFLE_NOREPEAT"),   (True,  False))
        self.assertEqual(f("SHUFFLE"),            (True,  True))
        self.assertEqual(f("SHUFFLE_REPEAT_ONE"), (True,  True))
        self.assertEqual(f(""),                   (False, False))
        self.assertEqual(f(None),                 (False, False))
        # Case-insensitive
        self.assertEqual(f("shuffle"),            (True,  True))

    def test_flags_to_play_mode(self):
        f = self.mod.flags_to_play_mode
        self.assertEqual(f(False, False), "NORMAL")
        self.assertEqual(f(False, True),  "REPEAT_ALL")
        self.assertEqual(f(True,  False), "SHUFFLE_NOREPEAT")
        self.assertEqual(f(True,  True),  "SHUFFLE")

    def test_extract_group_master_uuid(self):
        g = self.mod.extract_group_master_uuid
        self.assertEqual(g("x-rincon:RINCON_AABBCC"), "RINCON_AABBCC")
        # Slave URIs with query/fragment must still extract clean UUID
        self.assertEqual(g("x-rincon:RINCON_AA?x=1"), "RINCON_AA")
        self.assertEqual(g("x-rincon:RINCON_AA#0"),   "RINCON_AA")
        # Non-slave URIs return empty
        self.assertEqual(g("x-sonosapi-stream:s12345"), "")
        self.assertEqual(g("x-rincon-mp3radio://x"),    "")
        self.assertEqual(g(""),                          "")
        self.assertEqual(g(None),                        "")

    def test_parse_notify_extracts_play_mode_album_albumart(self):
        sample = """<e:propertyset><e:property><LastChange>&lt;Event&gt;&lt;InstanceID val=&quot;0&quot;&gt;
&lt;CurrentPlayMode val=&quot;SHUFFLE&quot;/&gt;
&lt;CurrentTrackURI val=&quot;x-rincon:RINCON_MASTER&quot;/&gt;
&lt;CurrentTrackMetaData val=&quot;&amp;lt;DIDL-Lite&amp;gt;&amp;lt;item&amp;gt;&amp;lt;dc:title&amp;gt;Yesterday&amp;lt;/dc:title&amp;gt;&amp;lt;dc:creator&amp;gt;Beatles&amp;lt;/dc:creator&amp;gt;&amp;lt;upnp:album&amp;gt;Help!&amp;lt;/upnp:album&amp;gt;&amp;lt;upnp:albumArtURI&amp;gt;/getaa?u=abc&amp;lt;/upnp:albumArtURI&amp;gt;&amp;lt;/item&amp;gt;&amp;lt;/DIDL-Lite&amp;gt;&quot;/&gt;
&lt;/InstanceID&gt;&lt;/Event&gt;</LastChange></e:property></e:propertyset>"""
        r = self.mod.parse_notify(sample)
        self.assertEqual(r["playMode"],    "SHUFFLE")
        self.assertEqual(r["album"],       "Help!")
        self.assertEqual(r["albumArtURI"], "/getaa?u=abc")
        self.assertEqual(r["trackUri"],    "x-rincon:RINCON_MASTER")


class TestSonosPlayerLogicModule(unittest.TestCase):
    """LogicModule lifecycle and IO contract tests with a stub framework."""

    @classmethod
    def setUpClass(cls):
        sys.modules.setdefault("requests", _make_requests_stub())
        cls.mod = load_module(PLAYER_PY, "sonos_player_22000_lm")

    def setUp(self):
        # Reset the module-level NOTIFY listener registry between tests.
        self.mod._instances_by_host.clear()
        # Force listener "already started" so on_init doesn't try to bind.
        self.mod._listener_started = True
        self.mod._listener_port = 8081

    def test_apply_notify_writes_iso_bytes(self):
        fw = StubFramework()
        lm = self.mod.LogicModule(fw)
        lm.debug = fw.create_debug_section()
        lm._apply_notify_parsed({
            "state": "PLAYING",
            "volume": 42,
            "mute": False,
            "title": "Foo",
            "artist": "Bar",
        })
        # String outputs must be bytes per the SDK's iso-8859-15 rule.
        self.assertEqual(fw.outputs["State"], b"Playing")
        self.assertEqual(fw.outputs["Title"], b"Foo")
        self.assertEqual(fw.outputs["Artist"], b"Bar")
        # Numeric outputs are int/float.
        self.assertEqual(fw.outputs["Volume"], 42.0)
        self.assertEqual(fw.outputs["Mute"], 0)
        self.assertEqual(fw.outputs["Online"], 1)

    def test_apply_status_poll_offline(self):
        fw = StubFramework()
        lm = self.mod.LogicModule(fw)
        lm.debug = fw.create_debug_section()
        lm._apply_status_poll(False, None, None, None, "", "")
        self.assertEqual(fw.outputs["Online"], 0)

    def test_write_error_encodes_bytes(self):
        fw = StubFramework()
        lm = self.mod.LogicModule(fw)
        lm.debug = fw.create_debug_section()
        lm._write_error("PLAYER_UNREACHABLE")
        self.assertEqual(fw.outputs["LastError"], b"PLAYER_UNREACHABLE")

    def test_mark_state_writes_bytes(self):
        fw = StubFramework()
        lm = self.mod.LogicModule(fw)
        lm._mark_state("STOPPED")
        self.assertEqual(fw.outputs["State"], b"Stopped")

    def test_mark_volume_writes_float(self):
        fw = StubFramework()
        lm = self.mod.LogicModule(fw)
        lm._mark_volume(33)
        self.assertEqual(fw.outputs["Volume"], 33.0)
        self.assertIsInstance(fw.outputs["Volume"], float)

    def test_state_booleans_exclusive(self):
        """IsPlaying / IsPaused / IsStopped / IsTransitioning are
        mutually exclusive — at most one is 1 at any time."""
        fw = StubFramework()
        lm = self.mod.LogicModule(fw)
        lm.debug = fw.create_debug_section()
        lm._mark_state("PLAYING")
        self.assertEqual(fw.outputs["IsPlaying"], 1)
        self.assertEqual(fw.outputs["IsPaused"], 0)
        self.assertEqual(fw.outputs["IsStopped"], 0)
        self.assertEqual(fw.outputs["IsTransitioning"], 0)
        lm._mark_state("PAUSED_PLAYBACK")
        self.assertEqual(fw.outputs["IsPlaying"], 0)
        self.assertEqual(fw.outputs["IsPaused"], 1)
        self.assertEqual(fw.outputs["IsStopped"], 0)
        lm._mark_state("STOPPED")
        self.assertEqual(fw.outputs["IsPaused"], 0)
        self.assertEqual(fw.outputs["IsStopped"], 1)
        lm._mark_state("TRANSITIONING")
        self.assertEqual(fw.outputs["IsStopped"], 0)
        self.assertEqual(fw.outputs["IsTransitioning"], 1)

    def test_mark_play_mode_writes_shuffle_repeat(self):
        fw = StubFramework()
        lm = self.mod.LogicModule(fw)
        lm._mark_play_mode(True, False)
        self.assertEqual(fw.outputs["ShuffleState"], 1)
        self.assertEqual(fw.outputs["RepeatState"], 0)
        lm._mark_play_mode(False, True)
        self.assertEqual(fw.outputs["ShuffleState"], 0)
        self.assertEqual(fw.outputs["RepeatState"], 1)

    def test_set_play_mode_sends_correct_envelope(self):
        """The (shuffle, repeat) pair must compose into a Sonos PlayMode
        value, then go out as a SetPlayMode SOAP call."""
        fw = StubFramework()
        lm = self.mod.LogicModule(fw)
        lm.debug = fw.create_debug_section()
        lm._host = "10.0.0.1"
        calls = []
        lm._soap = lambda s, a, e: (calls.append((a, e)) or (True, "", ""))
        lm._action_set_play_mode(True, True)
        self.assertEqual(calls[-1][0], "SetPlayMode")
        self.assertIn("<NewPlayMode>SHUFFLE</NewPlayMode>", calls[-1][1])
        lm._action_set_play_mode(False, True)
        self.assertIn("<NewPlayMode>REPEAT_ALL</NewPlayMode>", calls[-1][1])
        lm._action_set_play_mode(False, False)
        self.assertIn("<NewPlayMode>NORMAL</NewPlayMode>", calls[-1][1])

    def test_apply_notify_publishes_album_and_group_info(self):
        fw = StubFramework()
        lm = self.mod.LogicModule(fw)
        lm.debug = fw.create_debug_section()
        lm._host = "10.0.0.5"
        lm._apply_notify_parsed({
            "state":       "PLAYING",
            "playMode":    "SHUFFLE",
            "album":       "Abbey Road",
            "albumArtURI": "/getaa?u=xyz",
            "trackUri":    "x-rincon:RINCON_MASTER",
            "title":       "Come Together",
            "artist":      "Beatles",
        })
        self.assertEqual(fw.outputs["Album"], b"Abbey Road")
        # Album art made absolute against this player's IP
        self.assertEqual(fw.outputs["AlbumArtURI"], b"http://10.0.0.5:1400/getaa?u=xyz")
        # Slave because trackUri is x-rincon:
        self.assertEqual(fw.outputs["IsCoordinator"], 0)
        self.assertEqual(fw.outputs["GroupInfo"], b"RINCON_MASTER")
        # PlayMode SHUFFLE -> shuffle + repeat-all
        self.assertEqual(fw.outputs["ShuffleState"], 1)
        self.assertEqual(fw.outputs["RepeatState"], 1)
        # Discrete state booleans
        self.assertEqual(fw.outputs["IsPlaying"], 1)

    def test_apply_notify_coordinator_when_track_uri_not_xrincon(self):
        fw = StubFramework()
        lm = self.mod.LogicModule(fw)
        lm.debug = fw.create_debug_section()
        lm._host = "10.0.0.5"
        lm._apply_notify_parsed({
            "state": "PLAYING",
            "trackUri": "x-sonosapi-stream:s24939",
            "title": "Foo",
        })
        self.assertEqual(fw.outputs["IsCoordinator"], 1)
        self.assertEqual(fw.outputs["GroupInfo"], b"")

    def test_absolute_album_art_keeps_http_urls(self):
        fw = StubFramework()
        lm = self.mod.LogicModule(fw)
        lm._host = "10.0.0.5"
        self.assertEqual(lm._absolute_album_art("/getaa?u=a"),
                         "http://10.0.0.5:1400/getaa?u=a")
        # External URL stays intact
        self.assertEqual(lm._absolute_album_art("https://i.scdn.co/foo.jpg"),
                         "https://i.scdn.co/foo.jpg")
        self.assertEqual(lm._absolute_album_art(""), "")


# ---------------------------------------------------------------------------
# Minimal `requests` stub so module import works in CI even without the
# requests library installed. Tests never actually issue HTTP.
# ---------------------------------------------------------------------------

def _make_requests_stub():
    import types
    mod = types.ModuleType("requests")
    class _Exc:
        class RequestException(Exception): pass
    mod.exceptions = _Exc
    def post(*a, **k): raise _Exc.RequestException("stub")
    def get(*a, **k):  raise _Exc.RequestException("stub")
    def request(*a, **k): raise _Exc.RequestException("stub")
    mod.post = post
    mod.get = get
    mod.request = request
    return mod


class TestSonosAdmin(unittest.TestCase):
    """LBS 22001 admin module: registry CRUD, MAC normalization, helpers."""

    @classmethod
    def setUpClass(cls):
        sys.modules.setdefault("requests", _make_requests_stub())
        cls.mod = load_module(ADMIN_PY, "sonos_admin_22001")

    def setUp(self):
        # Fresh registry per test.
        with self.mod._registry_lock:
            self.mod._players.clear()
            self.mod._stations.clear()
            for k in list(self.mod._cloud.keys()):
                self.mod._cloud[k] = "" if isinstance(self.mod._cloud[k], str) else 0

    def test_norm_mac(self):
        n = self.mod._norm_mac
        self.assertEqual(n("00:0E:58:AB:CD:EF"), "00:0e:58:ab:cd:ef")
        self.assertEqual(n("000e58abcdef"),       "00:0e:58:ab:cd:ef")
        self.assertEqual(n("00-0E-58-AB-CD-EF"),  "00:0e:58:ab:cd:ef")
        self.assertEqual(n(""), "")
        self.assertEqual(n("not-a-mac"), "")
        self.assertEqual(n("00:0E:58:AB:CD"), "")   # too short

    def test_is_ip(self):
        ip = self.mod._is_ip
        self.assertTrue(ip("192.168.1.50"))
        self.assertTrue(ip("10.0.0.1"))
        self.assertFalse(ip("256.0.0.1"))
        self.assertFalse(ip("1.2.3"))
        self.assertFalse(ip("not.an.ip.4"))
        self.assertFalse(ip(""))

    def test_resolve_host_for_ip_literal_passes_through(self):
        self.assertEqual(self.mod.resolve_host("192.168.1.50"), "192.168.1.50")
        self.assertEqual(self.mod.resolve_host(""), "")

    def test_resolve_host_by_name_in_registry(self):
        with self.mod._registry_lock:
            self.mod._players["x"] = {
                "id": "x", "name": "livingroom", "ip": "10.0.0.42",
                "mac": "", "uuid": "", "model": "", "source": "manual",
            }
        self.assertEqual(self.mod.resolve_host("livingroom"), "10.0.0.42")
        self.assertEqual(self.mod.resolve_host("LIVINGROOM"), "10.0.0.42")
        self.assertEqual(self.mod.resolve_host("missing"), "")

    def test_resolve_host_by_mac_in_registry(self):
        with self.mod._registry_lock:
            self.mod._players["x"] = {
                "id": "x", "name": "lr", "zoneName": "Living Room",
                "ip": "10.0.0.42", "mac": "00:0e:58:ab:cd:ef",
                "uuid": "", "model": "", "source": "manual",
            }
        self.assertEqual(self.mod.resolve_host("00:0E:58:AB:CD:EF"), "10.0.0.42")

    def test_resolve_host_by_uuid_in_registry(self):
        """UUID (RINCON_xxx) is the recommended Host value; admin must
        resolve it to the current IP."""
        with self.mod._registry_lock:
            self.mod._players["x"] = {
                "id": "x", "name": "", "zoneName": "Bedroom",
                "ip": "10.0.0.99", "mac": "", "uuid": "RINCON_AABBCC112233",
                "model": "", "source": "ssdp",
            }
        self.assertEqual(self.mod.resolve_host("RINCON_AABBCC112233"), "10.0.0.99")

    def test_player_record_carries_zone_name(self):
        """SSDP-discovered and manually-added records must carry zoneName
        so the Admin UI can display it."""
        with self.mod._registry_lock:
            self.mod._players["x"] = {
                "id": "x", "name": "", "zoneName": "Kitchen",
                "ip": "10.0.0.5", "mac": "", "uuid": "RINCON_DEAD",
                "model": "PLAY:1", "source": "ssdp",
            }
        fw = StubFramework()
        lm = self.mod.LogicModule(fw)
        lm.debug = fw.create_debug_section()
        listing = lm.api_list_players()
        self.assertEqual(len(listing["players"]), 1)
        self.assertEqual(listing["players"][0]["zoneName"], "Kitchen")
        self.assertEqual(listing["players"][0]["uuid"], "RINCON_DEAD")

    def test_parse_didl_items_extracts_favorites(self):
        """The Browse(FV:2) response wraps DIDL-Lite items. The parser must
        pull out title, URI, music-service metadata, and classify by type."""
        sample = (
            '<DIDL-Lite xmlns:dc="http://purl.org/dc/elements/1.1/" '
            'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/" '
            'xmlns:r="urn:schemas-rinconnetworks-com:metadata-1-0/" '
            'xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/">'
            '<item id="FV:2/0" parentID="FV:2" restricted="true">'
            '<dc:title>BBC Radio 1</dc:title>'
            '<upnp:class>object.itemobject.item.sonos-favorite</upnp:class>'
            '<res protocolInfo="x-sonosapi-stream:*:audio/x-rincon-mp3radio:*">'
            'x-sonosapi-stream:s24939?sid=254&amp;flags=8224&amp;sn=0</res>'
            '<r:resMD>&lt;DIDL-Lite&gt;&lt;item&gt;&lt;dc:title&gt;BBC Radio 1'
            '&lt;/dc:title&gt;&lt;upnp:class&gt;object.item.audioItem.audioBroadcast'
            '&lt;/upnp:class&gt;&lt;desc id=&quot;cdudn&quot;&gt;'
            'SA_RINCON65031_X_#Svc65031-0&lt;/desc&gt;&lt;/item&gt;&lt;/DIDL-Lite&gt;'
            '</r:resMD>'
            '</item>'
            '<item id="FV:2/1" parentID="FV:2" restricted="true">'
            '<dc:title>Local Stream</dc:title>'
            '<upnp:class>object.item.audioItem.audioBroadcast</upnp:class>'
            '<res>x-rincon-mp3radio://stream.example.com/r1.mp3</res>'
            '</item>'
            '</DIDL-Lite>'
        )
        items = self.mod._parse_didl_items(sample)
        self.assertEqual(len(items), 2)
        # First item: TuneIn-bound favorite with full metadata
        self.assertEqual(items[0]["title"], "BBC Radio 1")
        self.assertEqual(items[0]["type"], "radio")
        self.assertIn("x-sonosapi-stream:s24939", items[0]["uri"])
        # & should be decoded back to its literal form
        self.assertIn("&", items[0]["uri"])
        # The metadata must survive the round-trip with the SMAPI binding
        self.assertIn("SA_RINCON65031", items[0]["metadata"])
        self.assertIn("audioBroadcast", items[0]["metadata"])
        # Second item: direct stream, no metadata
        self.assertEqual(items[1]["title"], "Local Stream")
        self.assertEqual(items[1]["type"], "radio")
        self.assertEqual(items[1]["metadata"], "")
        self.assertEqual(items[1]["uri"], "x-rincon-mp3radio://stream.example.com/r1.mp3")

    def test_classify_recognises_x_rincon_stream_as_source(self):
        """Line-in URIs from a Sonos Connect:Amp / Port / Five / Beam
        live at x-rincon-stream:<that-player's-UUID>. The classifier
        must report 'source' so the UI groups them apart from radio."""
        self.assertEqual(self.mod._classify("", "x-rincon-stream:RINCON_AABBCC"), "source")
        self.assertEqual(self.mod._classify("object.item.audioItem.audioInput", "anything"), "source")

    def test_api_player_favorites_prepends_line_in_source(self):
        """When AI: returns nothing (older firmware) the favorites
        endpoint falls back to a synthetic Line-In entry built from the
        player's UUID so cross-room source routing still works."""
        fw = StubFramework()
        lm = self.mod.LogicModule(fw)
        lm.debug = fw.create_debug_section()
        with self.mod._registry_lock:
            self.mod._players["lr"] = {
                "id": "lr", "name": "lr", "zoneName": "Living Room",
                "ip": "10.0.0.50", "mac": "", "uuid": "RINCON_AABBCC",
                "model": "Connect:Amp", "source": "ssdp",
            }
        # Stub the SOAP browse so both FV:2 and AI: return nothing.
        original = self.mod.browse_content
        try:
            self.mod.browse_content = lambda *a, **kw: []
            r = lm.api_player_favorites("lr")
        finally:
            self.mod.browse_content = original
        self.assertEqual(len(r["favorites"]), 1)
        first = r["favorites"][0]
        self.assertEqual(first["type"], "source")
        self.assertEqual(first["title"], "Line-In (Living Room)")
        self.assertEqual(first["uri"], "x-rincon-stream:RINCON_AABBCC")

    def test_api_player_favorites_includes_real_ai_browse(self):
        """When AI: returns real audio-input items (Line-In on Connect,
        Bluetooth on Era 100, TV on Beam, …) they're each tagged
        type=source, the zone name is appended to the title, and the
        synthetic fallback is NOT added. The order is sources first,
        then FV:2 favorites."""
        fw = StubFramework()
        lm = self.mod.LogicModule(fw)
        lm.debug = fw.create_debug_section()
        with self.mod._registry_lock:
            self.mod._players["era"] = {
                "id": "era", "name": "era", "zoneName": "Living Room",
                "ip": "10.0.0.50", "mac": "", "uuid": "RINCON_ERA",
                "model": "Era 100", "source": "ssdp",
            }
        original = self.mod.browse_content
        try:
            # browse_content(ip, object_id, ...) — return different
            # lists for FV:2 vs AI: so we can verify both reach the
            # response.
            def fake_browse(ip, object_id="FV:2", **_kw):
                if object_id == "AI:":
                    return [
                        {"title": "Line-In", "class": "object.item.audioItem.audioBroadcast",
                         "uri": "x-rincon-stream:RINCON_ERA", "metadata": "", "type": "radio"},
                        {"title": "Bluetooth", "class": "object.item.audioItem.audioInput",
                         "uri": "x-sonos-bt:RINCON_ERA", "metadata": "", "type": "source"},
                    ]
                if object_id == "FV:2":
                    return [
                        {"title": "BBC R1", "class": "object.itemobject.item.sonos-favorite",
                         "uri": "x-sonosapi-stream:s1", "metadata": "<DIDL/>", "type": "radio"},
                    ]
                return []
            self.mod.browse_content = fake_browse
            r = lm.api_player_favorites("era")
        finally:
            self.mod.browse_content = original
        self.assertEqual(len(r["favorites"]), 3)
        # First two are sources (with zone name appended) and BOTH must
        # be tagged source even though one had upnp class audioBroadcast.
        self.assertEqual(r["favorites"][0]["title"], "Line-In (Living Room)")
        self.assertEqual(r["favorites"][0]["type"], "source")
        self.assertEqual(r["favorites"][1]["title"], "Bluetooth (Living Room)")
        self.assertEqual(r["favorites"][1]["type"], "source")
        # Third is the radio favorite, untouched.
        self.assertEqual(r["favorites"][2]["title"], "BBC R1")
        self.assertEqual(r["favorites"][2]["type"], "radio")

    def test_api_add_station_captures_optional_type(self):
        """When the integrator adds a favorite as a preset the type tag
        (source/radio/playlist/track) is captured so the Presets table
        can render it. Existing callers that omit type don't break;
        record just has type=""."""
        fw = StubFramework()
        lm = self.mod.LogicModule(fw)
        lm.debug = fw.create_debug_section()
        r = lm.api_add_station({"name": "BT", "uri": "x-sonos-bt:X",
                                "metadata": "", "type": "source"})
        self.assertEqual(r["station"]["type"], "source")
        r2 = lm.api_add_station({"name": "Manual", "uri": "http://x"})
        self.assertEqual(r2["station"]["type"], "")

    def test_parse_didl_items_extracts_spotify_playlist_favorite(self):
        """Sonos favorites for Spotify playlists are wrapped in the
        generic sonos-favorite class — the actual playlistContainer
        class lives inside the resMD. The parser must peek there so
        the playlist gets classified as 'playlist' (not 'other'),
        otherwise the Admin UI used to display only radio favorites."""
        sample = (
            '<DIDL-Lite xmlns:dc="http://purl.org/dc/elements/1.1/" '
            'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/" '
            'xmlns:r="urn:schemas-rinconnetworks-com:metadata-1-0/" '
            'xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/">'
            '<item id="FV:2/9" parentID="FV:2" restricted="true">'
            '<dc:title>My Spotify Playlist</dc:title>'
            '<upnp:class>object.itemobject.item.sonos-favorite</upnp:class>'
            '<res protocolInfo="x-rincon-cpcontainer:*:*:*">'
            'x-rincon-cpcontainer:1006206cspotify%3aplaylist%3aXYZ?sid=9&amp;flags=8300&amp;sn=1</res>'
            '<r:resMD>&lt;DIDL-Lite&gt;&lt;item&gt;&lt;upnp:class&gt;'
            'object.container.playlistContainer&lt;/upnp:class&gt;'
            '&lt;desc id=&quot;cdudn&quot;&gt;SA_RINCON3079_X_#Svc3079-0&lt;/desc&gt;'
            '&lt;/item&gt;&lt;/DIDL-Lite&gt;</r:resMD>'
            '</item></DIDL-Lite>'
        )
        items = self.mod._parse_didl_items(sample)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "My Spotify Playlist")
        # The inner class must drive the type classification.
        self.assertEqual(items[0]["type"], "playlist")
        self.assertIn("SA_RINCON3079", items[0]["metadata"])

    def test_classify_recognises_cpcontainer_uri_as_playlist(self):
        """Even when the favorite wrapper has no inner class info, a
        cpcontainer URI scheme must classify as playlist (Spotify /
        Apple Music / Amazon Music playlists and albums all use this)."""
        self.assertEqual(self.mod._classify("", "x-rincon-cpcontainer:1006206cspotify"), "playlist")
        self.assertEqual(self.mod._classify("object.container.playlistContainer", "anything"), "playlist")
        self.assertEqual(self.mod._classify("", "x-sonosapi-stream:s12345"), "radio")
        self.assertEqual(self.mod._classify("", "file:///jffs/settings/savedqueues.rsq#3"), "playlist")

    def test_parse_didl_items_extracts_sq_containers(self):
        """SQ: browse returns <container> blocks for Sonos Playlists
        (user-saved queues). They're not items so the original parser
        would skip them entirely. New container path must include them."""
        sample = (
            '<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" '
            'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/">'
            '<container id="SQ:3" parentID="SQ:" restricted="true">'
            '<dc:title>Saturday Morning</dc:title>'
            '<upnp:class>object.container.playlistContainer</upnp:class>'
            '</container></DIDL-Lite>'
        )
        items = self.mod._parse_didl_items(sample)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "Saturday Morning")
        self.assertEqual(items[0]["type"], "playlist")

    def test_publish_counters_async_uses_run_in_context(self):
        """Regression for the 'Method can't be called outside context thread'
        error: any state-mutating API method must marshal counter
        re-publishing back to node context via run_in_context."""
        fw = StubFramework()
        called = []
        # Intercept run_in_context to confirm the wrap path is taken.
        orig = fw.run_in_context
        def spy(cb, params):
            called.append(cb.__name__ if hasattr(cb, '__name__') else 'callable')
            orig(cb, params)
        fw.run_in_context = spy
        lm = self.mod.LogicModule(fw)
        lm.debug = fw.create_debug_section()
        lm._publish_counters_async()
        self.assertEqual(called[-1], '_publish_counters')

    def test_get_station_returns_metadata(self):
        """LBS 22000 uses get_station() to retrieve the full record."""
        with self.mod._registry_lock:
            self.mod._stations["s1"] = {
                "id": "s1", "name": "BBC R1",
                "uri": "x-sonosapi-stream:s12345?sid=254",
                "metadata": "<DIDL-Lite>...SMAPI...</DIDL-Lite>",
            }
        rec = self.mod.get_station("BBC R1")
        self.assertEqual(rec["uri"], "x-sonosapi-stream:s12345?sid=254")
        self.assertIn("SMAPI", rec["metadata"])
        # Numeric index lookup also returns metadata.
        rec2 = self.mod.get_station(1)
        self.assertEqual(rec2["name"], "BBC R1")

    def test_get_station_uri_shim_unchanged(self):
        """Backwards-compat shim still returns just the URI string."""
        with self.mod._registry_lock:
            self.mod._stations["s1"] = {
                "id": "s1", "name": "X", "uri": "http://x", "metadata": "<meta/>"
            }
        self.assertEqual(self.mod.get_station_uri("X"), "http://x")
        self.assertEqual(self.mod.get_station_uri("missing"), "")

    def test_api_player_favorites_unknown_player(self):
        fw = StubFramework()
        lm = self.mod.LogicModule(fw)
        lm.debug = fw.create_debug_section()
        with self.assertRaises(ValueError):
            lm.api_player_favorites("nope")

    def test_api_add_station_carries_metadata(self):
        fw = StubFramework()
        lm = self.mod.LogicModule(fw)
        lm.debug = fw.create_debug_section()
        r = lm.api_add_station({
            "name": "Spotify Playlist",
            "uri": "x-rincon-cpcontainer:1004206cspotify:playlist:abc",
            "metadata": "<DIDL-Lite>...spotify-binding...</DIDL-Lite>",
        })
        self.assertEqual(r["station"]["metadata"], "<DIDL-Lite>...spotify-binding...</DIDL-Lite>")
        with self.mod._registry_lock:
            self.assertIn("metadata", self.mod._stations[r["station"]["id"]])

    def test_fetch_device_info_parses_roomname_and_model(self):
        """The new _fetch_device_info helper extracts both <roomName> and
        <modelName> from a Sonos device description XML — needed so the
        Admin UI can show 'Living Room' instead of just an IP."""
        # Stub requests.get to return a synthetic XML.
        original = self.mod.requests.get
        class _Resp:
            text = ("<?xml version='1.0'?><root>"
                    "<device><modelName>PLAY:5</modelName>"
                    "<roomName>Living Room</roomName>"
                    "<UDN>uuid:RINCON_AA</UDN></device></root>")
        try:
            self.mod.requests.get = lambda *a, **kw: _Resp()
            info = self.mod._fetch_device_info("http://10.0.0.1:1400/xml/device_description.xml")
            self.assertEqual(info["model"], "PLAY:5")
            self.assertEqual(info["zoneName"], "Living Room")
        finally:
            self.mod.requests.get = original

    def test_get_station_uri_by_name(self):
        with self.mod._registry_lock:
            self.mod._stations["a"] = {"id": "a", "name": "Foo", "uri": "http://x"}
            self.mod._stations["b"] = {"id": "b", "name": "Bar", "uri": "http://y"}
        self.assertEqual(self.mod.get_station_uri("Foo"), "http://x")
        self.assertEqual(self.mod.get_station_uri("foo"), "http://x")
        self.assertEqual(self.mod.get_station_uri("missing"), "")

    def test_get_station_uri_by_index(self):
        with self.mod._registry_lock:
            self.mod._stations["a"] = {"id": "a", "name": "Bar", "uri": "http://y"}
            self.mod._stations["b"] = {"id": "b", "name": "Foo", "uri": "http://x"}
        # Sorted by name (case-insensitive): Bar=1, Foo=2.
        self.assertEqual(self.mod.get_station_uri(1), "http://y")
        self.assertEqual(self.mod.get_station_uri(2), "http://x")
        self.assertEqual(self.mod.get_station_uri(99), "")

    def test_admin_api_add_player_requires_ip_or_mac(self):
        fw = StubFramework()
        lm = self.mod.LogicModule(fw)
        lm.debug = fw.create_debug_section()
        with self.assertRaises(ValueError):
            lm.api_add_player({"name": "lr"})
        with self.assertRaises(ValueError):
            lm.api_add_player({"name": "lr", "ip": "999.0.0.1"})

    def test_admin_api_add_remove_player(self):
        fw = StubFramework()
        lm = self.mod.LogicModule(fw)
        lm.debug = fw.create_debug_section()
        r = lm.api_add_player({"name": "kitchen", "ip": "10.0.0.5", "mac": "00:0e:58:ab:cd:ef"})
        self.assertEqual(r["player"]["ip"], "10.0.0.5")
        self.assertEqual(r["player"]["mac"], "00:0e:58:ab:cd:ef")
        self.assertEqual(r["player"]["source"], "manual")
        with self.mod._registry_lock:
            self.assertEqual(len(self.mod._players), 1)
        lm.api_remove_player(r["player"]["id"])
        with self.mod._registry_lock:
            self.assertEqual(len(self.mod._players), 0)

    def test_admin_api_station_crud(self):
        fw = StubFramework()
        lm = self.mod.LogicModule(fw)
        lm.debug = fw.create_debug_section()
        r = lm.api_add_station({"name": "Radio 1", "uri": "http://r1"})
        sid = r["station"]["id"]
        listed = lm.api_list_stations()
        self.assertEqual(len(listed["stations"]), 1)
        lm.api_update_station(sid, {"name": "Radio One"})
        with self.mod._registry_lock:
            self.assertEqual(self.mod._stations[sid]["name"], "Radio One")
        lm.api_remove_station(sid)
        with self.mod._registry_lock:
            self.assertEqual(len(self.mod._stations), 0)

    def test_persist_and_load_round_trip(self):
        """Registries serialise to retentive stores and restore on load.
        Survives a 'restart' (clear in-memory, build a stub store with
        the bytes that were set, call _load_persisted)."""
        fw = StubFramework()
        lm = self.mod.LogicModule(fw)
        lm.debug = fw.create_debug_section()
        # Set up state.
        with self.mod._registry_lock:
            self.mod._players["p1"] = {"id": "p1", "name": "lr",
                "zoneName": "Living Room", "ip": "10.0.0.1", "mac": "",
                "uuid": "RINCON_LR", "model": "PLAY:5", "source": "ssdp"}
            self.mod._stations["s1"] = {"id": "s1", "name": "BBC R1",
                "uri": "http://r1", "metadata": "<DIDL/>"}
            self.mod._groups["g1"] = {"id": "g1", "name": "All",
                "master": "p1", "members": []}
            self.mod._cloud["clientId"] = "abc"
            self.mod._cloud["clientSecret"] = "topsecret"
        # Persist (runs synchronously in test because run_in_context is sync).
        lm._persist()
        # Captured store bytes.
        self.assertIn("PersistedPlayers", fw.stores)
        self.assertIn("PersistedStations", fw.stores)
        self.assertIn("PersistedGroups", fw.stores)
        self.assertIn("PersistedCloud", fw.stores)
        # Each value is iso-8859-15 bytes containing JSON.
        self.assertIsInstance(fw.stores["PersistedPlayers"], bytes)
        # Wipe registries to simulate a fresh start.
        with self.mod._registry_lock:
            self.mod._players.clear()
            self.mod._stations.clear()
            self.mod._groups.clear()
            for k in list(self.mod._cloud.keys()):
                if isinstance(self.mod._cloud[k], str):
                    self.mod._cloud[k] = ""
        # Build a stub store object that returns the captured bytes.
        class _Slot:
            def __init__(self, v): self.value = v
        class _Store(dict):
            def __getitem__(self, k): return _Slot(fw.stores.get(k, b""))
        lm._load_persisted(_Store())
        # Registries are back.
        with self.mod._registry_lock:
            self.assertIn("p1", self.mod._players)
            self.assertEqual(self.mod._players["p1"]["zoneName"], "Living Room")
            self.assertIn("s1", self.mod._stations)
            self.assertEqual(self.mod._stations["s1"]["metadata"], "<DIDL/>")
            self.assertIn("g1", self.mod._groups)
            self.assertEqual(self.mod._groups["g1"]["master"], "p1")
            self.assertEqual(self.mod._cloud["clientId"], "abc")
            self.assertEqual(self.mod._cloud["clientSecret"], "topsecret")

    def test_load_persisted_tolerates_garbage(self):
        """Malformed JSON must NOT crash on_init — registries start empty
        and the next discovery / UI add re-populates them."""
        class _Slot:
            def __init__(self, v): self.value = v
        class _Store:
            def __init__(self, d): self._d = d
            def __getitem__(self, k): return _Slot(self._d.get(k, b""))
        fw = StubFramework()
        lm = self.mod.LogicModule(fw)
        lm.debug = fw.create_debug_section()
        with self.mod._registry_lock:
            self.mod._players.clear()
            self.mod._stations.clear()
            self.mod._groups.clear()
        # Mix of empty, garbage, and one valid blob.
        valid_player = json.dumps([{"id": "x", "name": "X", "zoneName": "X",
            "ip": "1.1.1.1", "mac": "", "uuid": "U", "model": "", "source": "ssdp"}])
        store = _Store({
            "PersistedPlayers":  valid_player.encode("iso-8859-15"),
            "PersistedStations": b"not json {{{{",
            "PersistedGroups":   b"",
            "PersistedCloud":    b"\xff\xfe garbage",
        })
        lm._load_persisted(store)  # must not raise
        with self.mod._registry_lock:
            self.assertEqual(len(self.mod._players), 1)
            self.assertEqual(len(self.mod._stations), 0)
            self.assertEqual(len(self.mod._groups), 0)

    def test_admin_group_crud_and_lookup(self):
        """Group presets: add, list, get by index, get by name,
        update master/members, delete. Master is auto-stripped from
        members on add. Unknown player ids are rejected."""
        with self.mod._registry_lock:
            self.mod._players["a"] = {"id": "a", "name": "Living Room",
                "zoneName": "Living Room", "ip": "10.0.0.10", "mac": "",
                "uuid": "RINCON_AA", "model": "PLAY:5", "source": "ssdp"}
            self.mod._players["b"] = {"id": "b", "name": "Kitchen",
                "zoneName": "Kitchen", "ip": "10.0.0.11", "mac": "",
                "uuid": "RINCON_BB", "model": "One", "source": "ssdp"}
            self.mod._players["c"] = {"id": "c", "name": "Bedroom",
                "zoneName": "Bedroom", "ip": "10.0.0.12", "mac": "",
                "uuid": "RINCON_CC", "model": "One", "source": "ssdp"}
        fw = StubFramework()
        lm = self.mod.LogicModule(fw)
        lm.debug = fw.create_debug_section()

        # Unknown player id is rejected.
        with self.assertRaises(ValueError):
            lm.api_add_group({"name": "Bad", "master": "missing", "members": []})
        with self.assertRaises(ValueError):
            lm.api_add_group({"name": "Bad", "master": "a", "members": ["unknown"]})
        with self.assertRaises(ValueError):
            lm.api_add_group({"master": "a"})  # no name
        with self.assertRaises(ValueError):
            lm.api_add_group({"name": "G"})    # no master

        # Add — master is removed from members automatically.
        r = lm.api_add_group({
            "name": "Whole Home",
            "master": "a",
            "members": ["a", "b", "c"],
        })
        self.assertEqual(r["group"]["master"], "a")
        self.assertEqual(sorted(r["group"]["members"]), ["b", "c"])
        gid = r["group"]["id"]

        # Lookup by index and by name.
        rec = self.mod.get_group(1)
        self.assertEqual(rec["name"], "Whole Home")
        rec = self.mod.get_group("whole home")
        self.assertEqual(rec["id"], gid)
        self.assertIsNone(self.mod.get_group("nope"))
        self.assertIsNone(self.mod.get_group(99))

        # Update master to b, members to {a, c} — must drop b from
        # members again automatically since it's the new master.
        lm.api_update_group(gid, {"master": "b", "members": ["a", "b", "c"]})
        with self.mod._registry_lock:
            self.assertEqual(self.mod._groups[gid]["master"], "b")
            self.assertEqual(sorted(self.mod._groups[gid]["members"]), ["a", "c"])

        # Update with unknown player id is rejected.
        with self.assertRaises(ValueError):
            lm.api_update_group(gid, {"master": "missing"})
        with self.assertRaises(ValueError):
            lm.api_update_group(gid, {"members": ["a", "missing"]})

        # Delete.
        lm.api_remove_group(gid)
        with self.assertRaises(ValueError):
            lm.api_remove_group(gid)
        with self.mod._registry_lock:
            self.assertEqual(len(self.mod._groups), 0)

    def test_admin_cloud_get_does_not_leak_secret(self):
        fw = StubFramework()
        lm = self.mod.LogicModule(fw)
        lm.debug = fw.create_debug_section()
        lm.api_set_cloud({"clientId": "abc", "clientSecret": "topsecret",
                          "redirectBase": "http://hs:8080"})
        r = lm.api_get_cloud()
        self.assertEqual(r["cloud"]["clientId"], "abc")
        self.assertNotIn("clientSecret", r["cloud"])
        self.assertTrue(r["cloud"]["clientSecretSet"])

    def test_admin_post_discovery_publishes_discover_outputs(self):
        """KNX-friendly outputs (formerly LBS 22001 Discover): DiscoveredPlayers
        as newline-separated ip;uuid;model and LastDiscoveryCount as a number."""
        fw = StubFramework()
        lm = self.mod.LogicModule(fw)
        lm.debug = fw.create_debug_section()
        discovered = [
            {"ip": "192.168.1.10", "uuid": "RINCON_AA", "model": "PLAY:5"},
            {"ip": "192.168.1.11", "uuid": "RINCON_BB", "model": "Beam"},
        ]
        lm._post_discovery(discovered)
        self.assertIn(b"192.168.1.10;RINCON_AA;PLAY:5", fw.outputs["DiscoveredPlayers"])
        self.assertIn(b"192.168.1.11;RINCON_BB;Beam",  fw.outputs["DiscoveredPlayers"])
        self.assertEqual(fw.outputs["LastDiscoveryCount"], 2.0)

    def test_admin_post_discovery_empty_scan(self):
        fw = StubFramework()
        lm = self.mod.LogicModule(fw)
        lm.debug = fw.create_debug_section()
        lm._post_discovery([])
        self.assertEqual(fw.outputs["DiscoveredPlayers"], b"")
        self.assertEqual(fw.outputs["LastDiscoveryCount"], 0.0)

    def test_admin_player_defaults_get_set_persist(self):
        """Player tunable defaults (PollInterval, SubTimeout, HttpTimeout,
        CallbackBase) are settable via api_set_player_defaults, readable
        via api_get_player_defaults and get_player_defaults(), and round-
        trip through the retentive store."""
        fw = StubFramework()
        lm = self.mod.LogicModule(fw)
        lm.debug = fw.create_debug_section()
        # Reset to the init values so the test is hermetic.
        with self.mod._registry_lock:
            self.mod._player_defaults.update({
                "pollInterval": 60, "subTimeout": 1800,
                "httpTimeout": 5, "callbackBase": "",
            })
        # Set new defaults via the API.
        r = lm.api_set_player_defaults({
            "pollInterval": 30,
            "subTimeout":   600,
            "httpTimeout":  8,
            "callbackBase": "http://hs:8082/",
        })
        self.assertEqual(r["defaults"]["pollInterval"], 30)
        self.assertEqual(r["defaults"]["subTimeout"],   600)
        self.assertEqual(r["defaults"]["httpTimeout"],  8)
        # Trailing slash stripped
        self.assertEqual(r["defaults"]["callbackBase"], "http://hs:8082")

        # api_get_player_defaults sees the new values.
        g = lm.api_get_player_defaults()
        self.assertEqual(g["defaults"]["pollInterval"], 30)

        # Module-level helper returns the same snapshot.
        snap = self.mod.get_player_defaults()
        self.assertEqual(snap["pollInterval"], 30)
        self.assertEqual(snap["callbackBase"], "http://hs:8082")

        # Persistence — _sync_async should have written the dict via _persist.
        self.assertIn("PersistedPlayerDefaults", fw.stores)
        # Wipe and reload through _load_persisted; values must come back.
        with self.mod._registry_lock:
            self.mod._player_defaults.update({
                "pollInterval": 60, "subTimeout": 1800,
                "httpTimeout": 5, "callbackBase": "",
            })
        class _Slot:
            def __init__(self, v): self.value = v
        class _Store(dict):
            def __getitem__(self, k): return _Slot(fw.stores.get(k, b""))
        lm._load_persisted(_Store())
        self.assertEqual(self.mod._player_defaults["pollInterval"], 30)
        self.assertEqual(self.mod._player_defaults["callbackBase"], "http://hs:8082")

    def test_admin_player_defaults_clamps_lower_bounds(self):
        """Bad UI values can't break the integration — the same lower
        bounds the Player module enforces are applied at the API layer."""
        fw = StubFramework()
        lm = self.mod.LogicModule(fw)
        lm.debug = fw.create_debug_section()
        lm.api_set_player_defaults({"pollInterval": 1, "subTimeout": 1, "httpTimeout": 0})
        d = self.mod.get_player_defaults()
        # 0 means "use default value" → falls back to module init (60/1800/5)
        # but values like 1 get clamped up to the floor.
        self.assertGreaterEqual(d["pollInterval"], 10)
        self.assertGreaterEqual(d["subTimeout"], 60)
        self.assertGreaterEqual(d["httpTimeout"], 2)

    def test_admin_oauth_start_requires_full_config(self):
        fw = StubFramework()
        lm = self.mod.LogicModule(fw)
        lm.debug = fw.create_debug_section()
        self.assertIsNone(lm.oauth_start_params())
        lm.api_set_cloud({"clientId": "abc", "clientSecret": "s",
                          "redirectBase": "http://hs:8080"})
        params = lm.oauth_start_params()
        self.assertEqual(params["client_id"], "abc")
        self.assertEqual(params["redirect_uri"], "http://hs:8080/oauth/callback")
        self.assertEqual(params["response_type"], "code")


class TestPlayerStationFromAdmin(unittest.TestCase):
    """When the Admin LBS exposes a station with metadata, the Player
    module must pass the metadata through verbatim — it's the music-
    service binding for TuneIn / Spotify / Apple Music favorites."""

    @classmethod
    def setUpClass(cls):
        sys.modules.setdefault("requests", _make_requests_stub())
        cls.admin = load_module(ADMIN_PY, "sonos_admin_22001_for_stations")
        cls.player = load_module(PLAYER_PY, "sonos_player_22000_for_stations")

    def setUp(self):
        with self.admin._registry_lock:
            self.admin._stations.clear()
            self.admin._groups.clear()
            self.admin._players.clear()

    def test_lookup_station_via_admin_returns_dict_with_metadata(self):
        with self.admin._registry_lock:
            self.admin._stations["s"] = {
                "id": "s", "name": "TuneIn", "uri": "x-sonosapi-stream:s24939",
                "metadata": "<DIDL-Lite>...SA_RINCON65031...</DIDL-Lite>",
            }
        rec = self.player._lookup_station_via_admin(1)
        self.assertEqual(rec["uri"], "x-sonosapi-stream:s24939")
        self.assertIn("SA_RINCON65031", rec["metadata"])

    def test_admin_get_player_record_by_ip_mac_uuid_name(self):
        """LBS 22000's ZoneName output sources via this helper. Must
        resolve by every identifier the integrator might use in Host."""
        with self.admin._registry_lock:
            self.admin._players["x"] = {
                "id": "x", "name": "lr", "zoneName": "Living Room",
                "ip": "10.0.0.50", "mac": "00:0e:58:ab:cd:ef",
                "uuid": "RINCON_AABBCC", "model": "PLAY:5", "source": "ssdp",
            }
        for spec in ("10.0.0.50", "00:0E:58:AB:CD:EF", "RINCON_AABBCC", "lr", "LR"):
            rec = self.admin.get_player_record(spec)
            self.assertIsNotNone(rec, spec)
            self.assertEqual(rec["zoneName"], "Living Room")
        self.assertIsNone(self.admin.get_player_record("nothing"))
        self.assertIsNone(self.admin.get_player_record(""))

    def test_player_fetches_zone_name_from_admin_registry(self):
        """Player module's _admin_player_record walks sys.modules for
        the Admin's get_player_record. With Admin loaded and a known
        player, zoneName must reach the Player as iso-8859-15 bytes."""
        with self.admin._registry_lock:
            self.admin._players["k"] = {
                "id": "k", "name": "", "zoneName": "Kitchen",
                "ip": "10.0.0.7", "mac": "", "uuid": "RINCON_KK",
                "model": "One", "source": "ssdp",
            }
        rec = self.player._admin_player_record("RINCON_KK")
        self.assertIsNotNone(rec)
        self.assertEqual(rec["zoneName"], "Kitchen")
        # And the to_iso_bytes helper converts it correctly for set_output.
        self.assertEqual(self.player.to_iso_bytes("Kitchen"), b"Kitchen")

    def test_lookup_station_by_name_case_insensitive(self):
        """StartRadioName lookup goes through _lookup_station_via_admin
        with a string. Must match case-insensitively."""
        with self.admin._registry_lock:
            self.admin._stations["a"] = {
                "id": "a", "name": "BBC Radio 1", "uri": "http://r1", "metadata": "",
            }
        self.assertEqual(self.player._lookup_station_via_admin("BBC Radio 1")["uri"], "http://r1")
        self.assertEqual(self.player._lookup_station_via_admin("bbc radio 1")["uri"], "http://r1")
        self.assertEqual(self.player._lookup_station_via_admin("BBC RADIO 1")["uri"], "http://r1")
        self.assertIsNone(self.player._lookup_station_via_admin("unknown"))

    def test_lookup_station_via_admin_returns_none_when_empty(self):
        self.assertIsNone(self.player._lookup_station_via_admin(1))

    def test_normalize_state_returns_title_case_friendly(self):
        """All transport states surface as Title-cased English. ZPSTR_
        prefix is stripped; known codes map to nicer short forms
        (PAUSED_PLAYBACK -> Paused, not 'Paused playback'); unknown
        codes fall back to 'Sentence case' so future Sonos states
        still look readable instead of ALL_CAPS."""
        n = self.player._normalize_state
        self.assertEqual(n("PLAYING"), "Playing")
        self.assertEqual(n("PAUSED_PLAYBACK"), "Paused")
        self.assertEqual(n("STOPPED"), "Stopped")
        self.assertEqual(n("TRANSITIONING"), "Transitioning")
        self.assertEqual(n("NO_MEDIA_PRESENT"), "No media")
        self.assertEqual(n("ZPSTR_BUFFERING"), "Buffering")
        self.assertEqual(n("ZPSTR_CONNECTING"), "Connecting")
        self.assertEqual(n("ZPSTR_PLAYING_TV"), "Playing TV")
        # Unknown future state — generic sentence case.
        self.assertEqual(n("SOME_NEW_THING"), "Some new thing")
        self.assertEqual(n(""), "")
        self.assertEqual(n(None), "")

    def test_friendly_title_passes_real_titles_through_unchanged(self):
        """Real track titles preserve their original casing — we don't
        want to title-case "Hey Jude" into "Hey jude" or any such
        thing. Only ZPSTR_ leaks get the cleanup treatment."""
        f = self.player._friendly_title
        # Real titles untouched.
        self.assertEqual(f("Hey Jude"), "Hey Jude")
        self.assertEqual(f("BBC Radio 1"), "BBC Radio 1")
        self.assertEqual(f("Live at Madison Square Garden"),
                         "Live at Madison Square Garden")
        self.assertEqual(f("lowercase song title"),
                         "lowercase song title")
        # ZPSTR_ leaks get the same friendly treatment as State.
        self.assertEqual(f("ZPSTR_BUFFERING"), "Buffering")
        self.assertEqual(f("ZPSTR_CONNECTING"), "Connecting")
        # Plain "PLAYING" appearing in dc:title also gets cleaned (it
        # would only happen if Sonos leaked a state into the title,
        # but cheap to handle).
        self.assertEqual(f("PLAYING"), "Playing")
        self.assertEqual(f(""), "")
        self.assertEqual(f(None), "")

    def test_is_container_uri_recognises_playlist_schemes(self):
        """The dispatch from _action_start_radio uses _is_container_uri
        to decide between direct-play and queue-and-play. Container URIs
        from Spotify (cpcontainer), Sonos saved queues (jffs), and
        x-rincon-playlist must route through the queue path."""
        c = self.player._is_container_uri
        self.assertTrue(c("x-rincon-cpcontainer:1006206cspotify:playlist:abc"))
        self.assertTrue(c("file:///jffs/settings/savedqueues.rsq#3"))
        self.assertTrue(c("x-rincon-playlist:RINCON_xxx#A:PLAYLISTS/foo"))
        # Direct streams and tracks must NOT match — they keep the
        # existing direct-play path.
        self.assertFalse(c("x-sonosapi-stream:s24939?sid=254"))
        self.assertFalse(c("x-rincon-mp3radio://stream.example.com/r1.mp3"))
        self.assertFalse(c("x-sonos-spotify:spotify:track:abc"))
        self.assertFalse(c("http://stream.example.com/r1.mp3"))
        self.assertFalse(c(""))

    def test_play_via_queue_calls_required_soap_actions(self):
        """Container playback must issue the queue-and-play SOAP sequence
        (RemoveAllTracksFromQueue → AddURIToQueue → SetAVTransportURI
        with the queue URI → Play). Direct SetAVTransportURI on a
        cpcontainer URI is a no-op on Sonos — that's why playlists
        didn't play before."""
        fw = StubFramework()
        lm = self.player.LogicModule(fw)
        lm.debug = fw.create_debug_section()
        lm._host = "10.0.0.1"
        lm._uuid = "RINCON_TESTAA"  # short-circuit the UUID resolve

        calls = []
        def fake_soap(service, action, envelope):
            calls.append((service, action, envelope))
            return (True, "", "")
        lm._soap = fake_soap

        lm._play_via_queue(
            "x-rincon-cpcontainer:1006206cspotify:playlist:abc",
            "<DIDL-Lite>...spotify metadata...</DIDL-Lite>",
            "Test Playlist",
        )
        actions = [a for (_s, a, _e) in calls]
        self.assertEqual(actions, [
            "RemoveAllTracksFromQueue",
            "AddURIToQueue",
            "SetAVTransportURI",
            "Play",
        ])
        # The SetAVTransportURI must point at the player's own queue URI.
        set_envelope = calls[2][2]
        self.assertIn("x-rincon-queue:RINCON_TESTAA#0", set_envelope)
        # The AddURIToQueue payload must carry the cpcontainer URI AND
        # the music-service metadata (XML-escaped).
        add_envelope = calls[1][2]
        self.assertIn("x-rincon-cpcontainer:1006206cspotify:playlist:abc", add_envelope)
        self.assertIn("spotify metadata", add_envelope)

    def test_group_form_sends_xrincon_to_each_member(self):
        """GroupPreset on the player triggers SetAVTransportURI with
        x-rincon:<master-UUID> on every member, leaving the master
        alone. The action issues one SOAP per member; we capture the
        host each SOAP went to and verify nothing was sent to the
        master."""
        # Seed Admin with three players and a group.
        with self.admin._registry_lock:
            self.admin._players.clear()
            self.admin._groups.clear()
            for pid, ip, uuid in (("a", "10.0.0.10", "RINCON_AA"),
                                  ("b", "10.0.0.11", "RINCON_BB"),
                                  ("c", "10.0.0.12", "RINCON_CC")):
                self.admin._players[pid] = {
                    "id": pid, "name": pid, "zoneName": pid.upper(),
                    "ip": ip, "mac": "", "uuid": uuid, "model": "",
                    "source": "ssdp",
                }
            self.admin._groups["g1"] = {
                "id": "g1", "name": "All", "master": "a", "members": ["b", "c"],
            }

        fw = StubFramework()
        lm = self.player.LogicModule(fw)
        lm.debug = fw.create_debug_section()
        # The trigger player can be ANY of them — doesn't matter; the
        # action loops over Admin's member list.
        lm._host_spec = "RINCON_BB"
        lm._host = "10.0.0.11"

        calls = []
        def fake_soap(service, action, envelope):
            # Capture (current self._host, action, payload-substring).
            calls.append((lm._host, action, envelope))
            return (True, "", "")
        lm._soap = fake_soap

        lm._action_group_form(1)  # Form by index

        # Each member got exactly one SetAVTransportURI; master did not.
        member_hosts = {h for (h, a, _) in calls if a == "SetAVTransportURI"}
        self.assertEqual(member_hosts, {"10.0.0.11", "10.0.0.12"})
        self.assertNotIn("10.0.0.10", member_hosts)
        # Every payload references the master's RINCON UUID via x-rincon:.
        for _h, _a, env in calls:
            self.assertIn("x-rincon:RINCON_AA", env)

    def test_group_form_partial_failure_reports_error(self):
        """When some members can't be joined (resolve fails or SOAP
        returns an error) the action reports GROUP_PARTIAL in
        LastError but doesn't crash the worker."""
        with self.admin._registry_lock:
            self.admin._players.clear()
            self.admin._groups.clear()
            # Master present, one member present, one member missing.
            self.admin._players["m"] = {"id": "m", "name": "M",
                "zoneName": "Master", "ip": "10.0.0.1", "mac": "",
                "uuid": "RINCON_M", "model": "", "source": "ssdp"}
            self.admin._players["x"] = {"id": "x", "name": "X",
                "zoneName": "Slave", "ip": "10.0.0.2", "mac": "",
                "uuid": "RINCON_X", "model": "", "source": "ssdp"}
            # Members include 'ghost' which isn't in the registry —
            # api_add_group would normally block this, but the Admin
            # could be edited in-place too; defensive check needed.
            self.admin._groups["g"] = {
                "id": "g", "name": "G", "master": "m", "members": ["x", "ghost"],
            }
        fw = StubFramework()
        lm = self.player.LogicModule(fw)
        lm.debug = fw.create_debug_section()
        lm._host = "10.0.0.1"
        lm._soap = lambda *a, **kw: (True, "", "")
        lm._action_group_form("G")
        self.assertIn(b"GROUP_PARTIAL", fw.outputs.get("LastError", b""))

    def test_ungroup_sends_become_standalone(self):
        """Ungroup triggers BecomeCoordinatorOfStandaloneGroup on the
        player itself (not on any other player)."""
        fw = StubFramework()
        lm = self.player.LogicModule(fw)
        lm.debug = fw.create_debug_section()
        lm._host = "10.0.0.99"
        actions = []
        lm._soap = lambda s, a, e: (actions.append(a) or (True, "", ""))
        lm._action_ungroup()
        self.assertEqual(actions, ["BecomeCoordinatorOfStandaloneGroup"])

    def test_lookup_group_via_admin_returns_record(self):
        with self.admin._registry_lock:
            self.admin._players["a"] = {"id": "a", "name": "A", "zoneName": "A",
                "ip": "1.1.1.1", "mac": "", "uuid": "U_A", "model": "",
                "source": "ssdp"}
            self.admin._groups["g"] = {
                "id": "g", "name": "Test Group", "master": "a", "members": [],
            }
        rec = self.player._lookup_group_via_admin(1)
        self.assertEqual(rec["name"], "Test Group")
        rec = self.player._lookup_group_via_admin("test group")
        self.assertEqual(rec["master"], "a")
        self.assertIsNone(self.player._lookup_group_via_admin("nothing"))

    def test_play_via_queue_aborts_without_uuid(self):
        fw = StubFramework()
        lm = self.player.LogicModule(fw)
        lm.debug = fw.create_debug_section()
        lm._host = "10.0.0.1"
        lm._uuid = ""
        # Stub _resolve_uuid to keep returning "" (admin absent + HTTP fails).
        lm._resolve_uuid = lambda: ""
        called = []
        lm._soap = lambda *a, **kw: called.append(a) or (True, "", "")
        lm._play_via_queue("x-rincon-cpcontainer:foo", "", 1)
        # No SOAP traffic emitted — we bailed early with PLAYLIST_NO_UUID.
        self.assertEqual(called, [])
        self.assertEqual(fw.outputs["LastError"], b"PLAYLIST_NO_UUID")

    def test_player_falls_back_to_admin_defaults_when_inputs_zero(self):
        """When the Player block's tunable inputs are at init (0 / empty),
        _reload_config picks them up from the Admin's _player_defaults
        dict via the get_player_defaults() module-level helper."""
        # Configure Admin-side defaults. The player helper walks
        # sys.modules and returns the first matching admin module, so to
        # keep the test hermetic we update every loaded admin module.
        for mod_name, mod in list(sys.modules.items()):
            if mod is None:
                continue
            if "sonos_admin" in mod_name and hasattr(mod, "_player_defaults"):
                with mod._registry_lock:
                    mod._player_defaults.update({
                        "pollInterval": 25,
                        "subTimeout":   900,
                        "httpTimeout":  7,
                        "callbackBase": "http://hs:8082",
                    })
        fw = StubFramework()
        lm = self.player.LogicModule(fw)
        lm.debug = fw.create_debug_section()
        inputs = make_player_inputs(
            host="10.0.0.1",
            PollInterval=0, SubTimeout=0, HttpTimeout=0, CallbackBase="",
        )
        lm._reload_config(inputs)
        self.assertEqual(lm._poll_interval_s, 25)
        self.assertEqual(lm._sub_timeout_s, 900)
        self.assertEqual(lm._http_timeout_s, 7)
        self.assertEqual(lm._callback_base, "http://hs:8082")

    def test_player_input_overrides_admin_default(self):
        """When the Player input is set (non-zero / non-empty), it wins
        over the Admin default — local override always beats global."""
        for mod_name, mod in list(sys.modules.items()):
            if mod is None:
                continue
            if "sonos_admin" in mod_name and hasattr(mod, "_player_defaults"):
                with mod._registry_lock:
                    mod._player_defaults.update({
                        "pollInterval": 25, "subTimeout": 900,
                        "httpTimeout": 7, "callbackBase": "http://hs:8082",
                    })
        fw = StubFramework()
        lm = self.player.LogicModule(fw)
        lm.debug = fw.create_debug_section()
        inputs = make_player_inputs(
            host="10.0.0.1",
            PollInterval=120, SubTimeout=3600, HttpTimeout=10,
            CallbackBase="http://override:9000",
        )
        lm._reload_config(inputs)
        self.assertEqual(lm._poll_interval_s, 120)
        self.assertEqual(lm._sub_timeout_s, 3600)
        self.assertEqual(lm._http_timeout_s, 10)
        self.assertEqual(lm._callback_base, "http://override:9000")

    def test_xml_escape_handles_uri_with_ampersand(self):
        """SetAVTransportURI URIs frequently contain & (cloud query params).
        The XML envelope embeds them in element content so they must be
        escaped to &amp; or the SOAP body is malformed."""
        escaped = self.player._xml_escape("x-sonosapi-stream:s1?sid=254&flags=8224")
        self.assertIn("&amp;", escaped)
        self.assertNotIn("&f", escaped)  # the literal & must be gone


class TestPlayerHostResolution(unittest.TestCase):
    """LBS 22000 reads the Admin registry when its Host isn't an IP."""

    @classmethod
    def setUpClass(cls):
        sys.modules.setdefault("requests", _make_requests_stub())
        # Load admin first so its globals are available to the player module.
        cls.admin = load_module(ADMIN_PY, "sonos_admin_22001_for_player")
        cls.player = load_module(PLAYER_PY, "sonos_player_22000_for_admin")

    def setUp(self):
        with self.admin._registry_lock:
            self.admin._players.clear()

    def test_ip_literal_passes_through(self):
        self.assertEqual(self.player.resolve_host_spec("192.168.1.50"), "192.168.1.50")

    def test_name_resolved_via_admin_registry(self):
        with self.admin._registry_lock:
            self.admin._players["x"] = {
                "id": "x", "name": "studio", "ip": "10.0.0.99",
                "mac": "", "uuid": "", "model": "", "source": "manual",
            }
        self.assertEqual(self.player.resolve_host_spec("studio"), "10.0.0.99")

    def test_unresolvable_returns_empty(self):
        self.assertEqual(self.player.resolve_host_spec("nope"), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
