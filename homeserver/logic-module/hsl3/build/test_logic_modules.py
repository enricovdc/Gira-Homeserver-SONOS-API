#!/usr/bin/env python3
"""Test the HSL3 LogicModule classes with a stubbed Hsl3Framework.

These tests are CI-friendly per the SDK's testing guidance: the framework
stub mirrors the real API (set_output / set_timer / set_store / get_logger /
create_debug_section / run_in_context), and HTTP calls are monkeypatched so
nothing reaches a real Sonos player.
"""

import importlib.util
import os
import sys
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
PLAYER_PY = ROOT / "homeserver" / "logic-module" / "hsl3" / "src_22000_sonos_player" / "hsl3_22000_sonos_player.py"
DISCOVER_PY = ROOT / "homeserver" / "logic-module" / "hsl3" / "src_22001_sonos_discover" / "hsl3_22001_sonos_discover.py"


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
        "StartRadio":   StubSlot(0),
        "Resubscribe":  StubSlot(0),
        "VolStep":      StubSlot(2),
        "PollInterval": StubSlot(60),
        "SubTimeout":   StubSlot(1800),
        "HttpTimeout":  StubSlot(5),
        "CallbackBase": StubSlot(""),
    }
    for i in range(1, 9):
        base["Station{}Uri".format(i)] = StubSlot("")
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
        self.assertEqual(fw.outputs["State"], b"PLAYING")
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
        self.assertEqual(fw.outputs["State"], b"STOPPED")

    def test_mark_volume_writes_float(self):
        fw = StubFramework()
        lm = self.mod.LogicModule(fw)
        lm._mark_volume(33)
        self.assertEqual(fw.outputs["Volume"], 33.0)
        self.assertIsInstance(fw.outputs["Volume"], float)


class TestSonosDiscover(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.modules.setdefault("requests", _make_requests_stub())
        cls.mod = load_module(DISCOVER_PY, "sonos_discover_22001")

    def test_handle_result_encodes_bytes(self):
        fw = StubFramework()
        lm = self.mod.LogicModule(fw)
        lm.debug = fw.create_debug_section()
        players = [("192.168.1.10", "RINCON_AA", "PLAY:5"),
                   ("192.168.1.11", "RINCON_BB", "Beam")]
        lm._handle_result(players)
        self.assertIn(b"192.168.1.10;RINCON_AA;PLAY:5", fw.outputs["Result"])
        self.assertEqual(fw.outputs["Count"], 2.0)

    def test_looks_like_sonos(self):
        self.assertTrue(self.mod.looks_like_sonos({"SERVER": "Linux/3.x Sonos/123"}))
        self.assertTrue(self.mod.looks_like_sonos({"USN": "uuid:RINCON_AABBCC"}))
        self.assertFalse(self.mod.looks_like_sonos({"SERVER": "Foo/1.0"}))


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
