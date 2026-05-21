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
SOUND_PY = ROOT / "projects" / "sonos_sound_hsl3" / "hsl3_22002_sonos_sound.py"


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
        # Mirror the real HSL3 framework's type check: debug values must
        # be int / float / str. Passing bytes (or anything else) raises
        # at runtime; reproduce that here so regressions are caught in CI
        # instead of only on the HomeServer.
        if not isinstance(value, (int, float, str)):
            raise ValueError(
                "Value not int, float or str (got {})".format(type(value).__name__)
            )
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
        "VolUpDown":    StubSlot(0),
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
        "PlayPause":    StubSlot(0),
        "NextPrev":     StubSlot(0),
        "PresetNextPrev": StubSlot(0),
        "PlaySound":    StubSlot(0),
        "VolStep":      StubSlot(2),
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

    def test_parse_transport_actions(self):
        p = self.mod.parse_transport_actions
        # Queue playback — everything allowed
        full = p("Play, Stop, Pause, Seek, Next, Previous")
        self.assertTrue(full["play"]); self.assertTrue(full["pause"])
        self.assertTrue(full["stop"]); self.assertTrue(full["next"])
        self.assertTrue(full["prev"])
        # Shuffle / Repeat track Next-allowed (heuristic for "queue playback")
        self.assertTrue(full["shuffle"]); self.assertTrue(full["repeat"])
        # Radio stream — only Play, Stop available; no queue navigation
        radio = p("Play, Stop")
        self.assertTrue(radio["play"]); self.assertTrue(radio["stop"])
        self.assertFalse(radio["pause"])
        self.assertFalse(radio["next"]); self.assertFalse(radio["prev"])
        self.assertFalse(radio["shuffle"]); self.assertFalse(radio["repeat"])
        # Case insensitive, whitespace-tolerant
        weird = p("PLAY,stop , next")
        self.assertTrue(weird["play"]); self.assertTrue(weird["stop"])
        self.assertTrue(weird["next"]); self.assertTrue(weird["shuffle"])
        # None / empty → everything denied
        denied = p(None)
        self.assertEqual(set(denied.values()), {False})
        denied2 = p("")
        self.assertEqual(set(denied2.values()), {False})

    def test_parse_notify_extracts_transport_actions(self):
        sample = """<e:propertyset><e:property><LastChange>&lt;Event&gt;&lt;InstanceID val=&quot;0&quot;&gt;
&lt;CurrentTransportActions val=&quot;Play, Stop, Pause, Seek, Next, Previous&quot;/&gt;
&lt;/InstanceID&gt;&lt;/Event&gt;</LastChange></e:property></e:propertyset>"""
        r = self.mod.parse_notify(sample)
        self.assertEqual(r["transportActions"], "Play, Stop, Pause, Seek, Next, Previous")

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

    def test_volume_setvolume_loopback_terminates(self):
        """Wiring Volume output back to SetVolume input (common Gira
        QuadClient pattern: single GA for both) used to loop forever.
        The output had no SBC and the input dispatched SOAP regardless
        of whether the requested level matched the player's current
        state. Now BOTH guards are in place — verify with a synthetic
        loop simulation."""
        fw = StubFramework()
        lm = self.mod.LogicModule(fw)
        lm.debug = fw.create_debug_section()
        lm._host = "10.0.0.1"
        soap_calls = []
        lm._soap = lambda s, a, e: (soap_calls.append((a, e)) or (True, "<CurrentVolume>50</CurrentVolume>", ""))
        # Run actions inline so the output write happens before the
        # next loop iteration.
        lm._run_control_threaded = lambda fn: fn()
        # Step 1: integrator sets a new volume via KNX (50, different
        # from init -1). SetVolume input fires; SOAP dispatches.
        ins = make_player_inputs(host="10.0.0.1")
        ins["SetVolume"] = StubSlot(50, changed=True)
        lm.on_calc(ins)
        self.assertEqual(len(soap_calls), 1)
        self.assertEqual(soap_calls[0][0], "SetVolume")
        self.assertEqual(fw.outputs["Volume"], 50.0)
        # Step 2: KNX echoes the output value back into the input GA.
        # Input-side suppression should now see level == _last_volume
        # and skip the redundant SOAP. The loop terminates here.
        soap_calls.clear()
        ins = make_player_inputs(host="10.0.0.1")
        ins["SetVolume"] = StubSlot(50, changed=True)
        lm.on_calc(ins)
        self.assertEqual(soap_calls, [],
            "expected no SOAP — the input value matches our last "
            "known state; this is a KNX feedback echo, not a real change")

    def test_setmute_loopback_terminates(self):
        fw = StubFramework()
        lm = self.mod.LogicModule(fw)
        lm.debug = fw.create_debug_section()
        lm._host = "10.0.0.1"
        soap_calls = []
        lm._soap = lambda s, a, e: (soap_calls.append(a) or (True, "<CurrentMute>1</CurrentMute>", ""))
        lm._run_control_threaded = lambda fn: fn()
        ins = make_player_inputs(host="10.0.0.1")
        ins["SetMute"] = StubSlot(1, changed=True)
        lm.on_calc(ins)
        self.assertIn("SetMute", soap_calls)
        # Echo: same value back.
        soap_calls.clear()
        ins = make_player_inputs(host="10.0.0.1")
        ins["SetMute"] = StubSlot(1, changed=True)
        lm.on_calc(ins)
        self.assertEqual(soap_calls, [])

    def test_mark_volume_is_send_by_change(self):
        """Volume output writes only when the level changed from the
        last published value. Same NOTIFY arriving twice should only
        produce one KNX broadcast."""
        fw = StubFramework()
        lm = self.mod.LogicModule(fw)
        lm._mark_volume(50)
        self.assertEqual(fw.outputs["Volume"], 50.0)
        # Same level — output should NOT be re-emitted.
        fw.outputs.clear()
        lm._mark_volume(50)
        self.assertNotIn("Volume", fw.outputs)
        # Different level — emits again.
        lm._mark_volume(40)
        self.assertEqual(fw.outputs["Volume"], 40.0)

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
        """SBC-aware: each flag only emits when it changes from the
        last published value. Init is (False, False) so the False
        side of the first call shouldn't reach the output map at all."""
        fw = StubFramework()
        lm = self.mod.LogicModule(fw)
        lm._mark_play_mode(True, False)
        self.assertEqual(fw.outputs["ShuffleState"], 1)
        self.assertNotIn("RepeatState", fw.outputs)  # unchanged from init
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

    def test_apply_notify_publishes_allowed_flags(self):
        """CurrentTransportActions in the NOTIFY drives the *Allowed
        outputs. Queue playback string allows everything; a radio
        stream's short list denies pause / next / prev / shuffle /
        repeat."""
        fw = StubFramework()
        lm = self.mod.LogicModule(fw)
        lm.debug = fw.create_debug_section()
        lm._host = "10.0.0.5"
        lm._apply_notify_parsed({
            "state": "PLAYING",
            "transportActions": "Play, Stop, Pause, Seek, Next, Previous",
        })
        self.assertEqual(fw.outputs["PlayAllowed"], 1)
        self.assertEqual(fw.outputs["PauseAllowed"], 1)
        self.assertEqual(fw.outputs["StopAllowed"], 1)
        self.assertEqual(fw.outputs["NextAllowed"], 1)
        self.assertEqual(fw.outputs["PrevAllowed"], 1)
        self.assertEqual(fw.outputs["ShuffleAllowed"], 1)
        self.assertEqual(fw.outputs["RepeatAllowed"], 1)

        # Radio stream: only play + stop available; queue navigation
        # and shuffle / repeat must all be unavailable.
        lm._apply_notify_parsed({
            "state": "PLAYING",
            "transportActions": "Play, Stop",
        })
        self.assertEqual(fw.outputs["PlayAllowed"], 1)
        self.assertEqual(fw.outputs["StopAllowed"], 1)
        self.assertEqual(fw.outputs["PauseAllowed"], 0)
        self.assertEqual(fw.outputs["NextAllowed"], 0)
        self.assertEqual(fw.outputs["PrevAllowed"], 0)
        self.assertEqual(fw.outputs["ShuffleAllowed"], 0)
        self.assertEqual(fw.outputs["RepeatAllowed"], 0)

    def test_playpause_input_dispatches_by_value(self):
        """PlayPause value 1 fires Play; value 0 fires Pause. Drives a
        single KNX 1-bit GA without two separate buttons."""
        fw = StubFramework()
        lm = self.mod.LogicModule(fw)
        lm.debug = fw.create_debug_section()
        actions = []
        lm._action_play = lambda: actions.append("play")
        lm._action_pause = lambda: actions.append("pause")
        # Run inline rather than spawning threads.
        lm._run_control_threaded = lambda fn: fn()
        lm._host = "10.0.0.1"
        # Value 1 — should dispatch play.
        ins = make_player_inputs(host="10.0.0.1")
        ins["PlayPause"] = StubSlot(1, changed=True)
        lm.on_calc(ins)
        self.assertEqual(actions, ["play"])
        # Value 0 — should dispatch pause.
        actions.clear()
        ins = make_player_inputs(host="10.0.0.1")
        ins["PlayPause"] = StubSlot(0, changed=True)
        lm.on_calc(ins)
        self.assertEqual(actions, ["pause"])

    def test_volupdown_dispatches_by_value(self):
        """VolUpDown takes a DPT 1.008 rocker straight: 1 = up by
        VolStep, 0 = down by VolStep. Avoids the two-branch helper
        logic block that used to be required."""
        fw = StubFramework()
        lm = self.mod.LogicModule(fw)
        lm.debug = fw.create_debug_section()
        deltas = []
        lm._action_adjust_volume = lambda d: deltas.append(d)
        lm._run_control_threaded = lambda fn: fn()
        lm._host = "10.0.0.1"
        # VolStep is reloaded from inputs on every on_calc, so set it
        # via the slot map rather than via the attribute.
        ins = make_player_inputs(host="10.0.0.1", VolStep=3)
        ins["VolUpDown"] = StubSlot(1, changed=True)
        lm.on_calc(ins)
        self.assertEqual(deltas, [+3])
        deltas.clear()
        ins = make_player_inputs(host="10.0.0.1", VolStep=3)
        ins["VolUpDown"] = StubSlot(0, changed=True)
        lm.on_calc(ins)
        self.assertEqual(deltas, [-3])

    def test_nextprev_input_dispatches_by_value(self):
        fw = StubFramework()
        lm = self.mod.LogicModule(fw)
        lm.debug = fw.create_debug_section()
        actions = []
        lm._action_next = lambda: actions.append("next")
        lm._action_previous = lambda: actions.append("prev")
        lm._run_control_threaded = lambda fn: fn()
        lm._host = "10.0.0.1"
        ins = make_player_inputs(host="10.0.0.1")
        ins["NextPrev"] = StubSlot(1, changed=True)
        lm.on_calc(ins)
        self.assertEqual(actions, ["next"])
        actions.clear()
        ins = make_player_inputs(host="10.0.0.1")
        ins["NextPrev"] = StubSlot(0, changed=True)
        lm.on_calc(ins)
        self.assertEqual(actions, ["prev"])

    def test_marquee_disabled_emits_full_text(self):
        """maxLength = 0 disables marquee — long strings pass through
        as-is, matching the pre-marquee behaviour."""
        fw = StubFramework()
        lm = self.mod.LogicModule(fw)
        lm._max_text_length = 0
        long = "Bohemian Rhapsody (Remastered 2011) - Queen"
        lm._publish_text("Title", long)
        self.assertEqual(fw.outputs["Title"], long.encode("iso-8859-15"))

    def test_marquee_short_text_passes_through_unchanged(self):
        fw = StubFramework()
        lm = self.mod.LogicModule(fw)
        lm._max_text_length = 12
        lm._publish_text("Title", "Yesterday")
        self.assertEqual(fw.outputs["Title"], b"Yesterday")

    def test_marquee_long_text_truncates_to_window(self):
        """First view is the leading max_len chars of the text."""
        fw = StubFramework()
        lm = self.mod.LogicModule(fw)
        lm._max_text_length = 10
        lm._publish_text("Title", "Bohemian Rhapsody")
        # 10-char window starting at position 0.
        self.assertEqual(fw.outputs["Title"], b"Bohemian R")

    def test_marquee_tick_advances_position(self):
        """Each Marquee tick shifts the scroll window one character
        to the right; after enough ticks the window wraps."""
        fw = StubFramework()
        lm = self.mod.LogicModule(fw)
        lm._max_text_length = 10
        lm._publish_text("Title", "Bohemian Rhapsody")
        lm._marquee_tick()
        self.assertEqual(fw.outputs["Title"], b"ohemian Rh")
        lm._marquee_tick()
        self.assertEqual(fw.outputs["Title"], b"hemian Rha")

    def test_marquee_resets_position_when_text_changes(self):
        """When the underlying text changes (new track), the scroll
        position must reset to 0 so the visualisation reads from the
        start of the new title."""
        fw = StubFramework()
        lm = self.mod.LogicModule(fw)
        lm._max_text_length = 8
        lm._publish_text("Title", "First Track Long")
        lm._marquee_tick()
        lm._marquee_tick()
        # New track arrives — position must reset.
        lm._publish_text("Title", "Second Track Even Longer")
        self.assertEqual(fw.outputs["Title"], b"Second T")
        self.assertEqual(lm._marquee_state["Title"]["pos"], 0)

    def test_marquee_view_wraps_through_separator(self):
        """The view cycles past the end of the text via the three-
        space separator and reads back to the beginning. Only kicks
        in when the text is actually longer than the window."""
        view = self.mod.LogicModule._compute_marquee_view
        # "Foobar" + "   " = "Foobar   " (length 9). max_len=5 makes
        # this text scroll. pos=6 should read "   Fo" — three
        # separator chars then the start of the text again.
        self.assertEqual(view("Foobar", 6, 5), "   Fo")
        # pos = 9 (length of cycled string) wraps back to pos 0.
        self.assertEqual(view("Foobar", 9, 5), view("Foobar", 0, 5))

    def test_marquee_tick_idle_when_disabled(self):
        """Marquee tick is a no-op when no text exceeds the limit
        or maxLength is 0 — no outputs touched."""
        fw = StubFramework()
        lm = self.mod.LogicModule(fw)
        lm._max_text_length = 0
        lm._publish_text("Title", "Anything Goes Here Long Or Short")
        fw.outputs.clear()
        lm._marquee_tick()
        self.assertEqual(fw.outputs, {})

    def test_mark_active_station_publishes_name(self):
        """_mark_active_station writes both ActiveStation (index) and
        ActiveStationName (string) so visualisations can display the
        preset's friendly name without re-reading the Admin library."""
        fw = StubFramework()
        lm = self.mod.LogicModule(fw)
        lm.debug = fw.create_debug_section()
        lm._mark_active_station(3, "BBC Radio 1")
        self.assertEqual(fw.outputs["ActiveStation"], 3.0)
        self.assertEqual(fw.outputs["ActiveStationName"], b"BBC Radio 1")

    def test_status_poll_offline_clears_allowed_flags(self):
        """Lost contact must zero every *Allowed output so the
        visualisation doesn't keep inviting clicks the player would
        reject."""
        fw = StubFramework()
        lm = self.mod.LogicModule(fw)
        lm.debug = fw.create_debug_section()
        lm._host = "10.0.0.5"
        # Seed with queue playback so the *Allowed bits start at 1.
        lm._apply_notify_parsed({
            "state": "PLAYING",
            "transportActions": "Play, Stop, Pause, Seek, Next, Previous",
        })
        self.assertEqual(fw.outputs["PlayAllowed"], 1)
        # Now go offline.
        lm._apply_status_poll(False, None, None, None, "", "")
        for k in ("PlayAllowed", "PauseAllowed", "StopAllowed",
                  "NextAllowed", "PrevAllowed",
                  "ShuffleAllowed", "RepeatAllowed"):
            self.assertEqual(fw.outputs[k], 0, k)

    def test_notify_clears_artist_album_on_track_change(self):
        """When playback moves from a track with artist/album (Spotify)
        to a radio stream (no <dc:creator>/<upnp:album>), the new
        track's metadata block is present but those tags are absent.
        The parser must surface them as empty strings and the LBS
        must overwrite the outputs — otherwise the Spotify artist
        leaks into the radio display."""
        fw = StubFramework()
        lm = self.mod.LogicModule(fw)
        lm.debug = fw.create_debug_section()
        lm._host = "10.0.0.5"
        # Step 1: Spotify track with rich metadata.
        lm._apply_notify_parsed({
            "state": "PLAYING",
            "title": "Yesterday",
            "artist": "The Beatles",
            "album": "Help!",
            "trackUri": "x-sonos-spotify:track:abc",
        })
        self.assertEqual(fw.outputs["Artist"], b"The Beatles")
        self.assertEqual(fw.outputs["Album"], b"Help!")
        # Step 2: switch to a radio stream — metadata block present
        # but no creator / album tags. parse_notify defaults those to
        # empty strings; the LBS must overwrite, not preserve.
        lm._apply_notify_parsed({
            "state": "PLAYING",
            "title": "",
            "artist": "",
            "album": "",
            "streamContent": "BBC Radio 1",
            "trackUri": "x-sonosapi-stream:s12345",
        })
        self.assertEqual(fw.outputs["Artist"], b"")
        self.assertEqual(fw.outputs["Album"], b"")
        # Title comes from streamContent for radio.
        self.assertEqual(fw.outputs["Title"], b"BBC Radio 1")

    def test_parse_notify_defaults_metadata_keys_to_empty(self):
        """When parse_notify sees a CurrentTrackMetaData block but
        the inner tags are absent (radio streams), it must surface
        artist / album / albumArtURI / streamContent as empty strings
        rather than omit them — that's what lets the LBS distinguish
        "track changed, no artist" from "no metadata in this notify"."""
        sample = (
            '<e:propertyset><e:property><LastChange>&lt;Event&gt;'
            '&lt;InstanceID val=&quot;0&quot;&gt;'
            '&lt;CurrentTrackMetaData val=&quot;'
            '&amp;lt;DIDL-Lite&amp;gt;&amp;lt;item&amp;gt;'
            '&amp;lt;upnp:class&amp;gt;object.item.audioItem.audioBroadcast&amp;lt;/upnp:class&amp;gt;'
            '&amp;lt;/item&amp;gt;&amp;lt;/DIDL-Lite&amp;gt;'
            '&quot;/&gt;'
            '&lt;/InstanceID&gt;&lt;/Event&gt;'
            '</LastChange></e:property></e:propertyset>'
        )
        r = self.mod.parse_notify(sample)
        # Metadata block was present → every metadata-derived key
        # surfaces, even empty.
        self.assertEqual(r["title"],         "")
        self.assertEqual(r["artist"],        "")
        self.assertEqual(r["album"],         "")
        self.assertEqual(r["albumArtURI"],   "")
        self.assertEqual(r["streamContent"], "")

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
        # The player helpers (_lookup_station_via_admin /
        # _admin_station_count / _admin_player_record) walk sys.modules
        # to find an admin module and return the first match. Other
        # test classes leave sibling admin modules loaded, so clearing
        # only self.admin would still leak state through those siblings.
        # Reset every loaded admin module for a hermetic test.
        for mod_name, mod in list(sys.modules.items()):
            if mod is None:
                continue
            if "sonos_admin" in mod_name and hasattr(mod, "_stations"):
                with mod._registry_lock:
                    mod._stations.clear()
                    mod._groups.clear()
                    mod._players.clear()

    def test_action_start_radio_skips_soap_when_superseded(self):
        """Fast Next/Prev presses race: an older preset worker is
        still running when on_calc bumps _preset_version for the next
        press. The older worker must detect the bump and bail without
        firing SOAP. Reproduces the user's reported case: a slow Join
        preset stuck in flight while the user pushes Next."""
        with self.admin._registry_lock:
            self.admin._stations.clear()
            self.admin._stations["s"] = {
                "id": "s", "name": "BBC Radio 1",
                "uri": "http://stream.bbc.co.uk/r1.mp3",
                "metadata": "",
            }
        fw = StubFramework()
        lm = self.player.LogicModule(fw)
        lm.debug = fw.create_debug_section()
        lm._host = "10.0.0.5"
        # We're worker version 1 — but the test simulates that a
        # newer press (version 2) has already bumped the counter
        # while we were queued. The worker must check on entry and
        # bail without any SOAP traffic.
        lm._preset_version = 2
        calls = []
        lm._soap = lambda s, a, e: (calls.append(a) or (True, "", ""))
        lm._action_start_radio(1, version=1)  # stale dispatch
        self.assertEqual(calls, [],
            "stale preset action must not fire SOAP")
        # And the LastError is not touched either — the newer
        # action will surface its own outcome.
        self.assertNotIn("LastError", fw.outputs)

    def test_action_start_radio_skips_mark_when_superseded_post_soap(self):
        """Even when the SOAP was already in flight before the newer
        press bumped the version, the post-SOAP _mark_active_station
        write must be suppressed so the visualisation doesn't briefly
        flip back to the superseded preset's name."""
        with self.admin._registry_lock:
            self.admin._stations.clear()
            self.admin._stations["s"] = {
                "id": "s", "name": "BBC Radio 1",
                "uri": "http://stream.bbc.co.uk/r1.mp3",
                "metadata": "",
            }
        fw = StubFramework()
        lm = self.player.LogicModule(fw)
        lm.debug = fw.create_debug_section()
        lm._host = "10.0.0.5"
        lm._preset_version = 1
        # Custom _soap that bumps the version mid-flight to simulate
        # a newer press landing while our SetAVTransportURI was
        # already out on the wire.
        def fake_soap(service, action, env):
            if action == "SetAVTransportURI":
                lm._preset_version = 2  # newer press arrives
            return (True, "", "")
        lm._soap = fake_soap
        lm._action_start_radio(1, version=1)
        # The optimistic "Loading: BBC Radio 1" landed (before the
        # SOAP), but the post-SOAP _mark_active_station that would
        # drop the prefix to "BBC Radio 1" must have been suppressed
        # — the newer preset will fill in its own clean name.
        self.assertEqual(fw.outputs.get("ActiveStationName"),
                         b"Loading: BBC Radio 1",
                         "expected the Loading prefix to still be on the "
                         "output (post-SOAP clean mark was suppressed)")

    def test_action_start_radio_publishes_loading_then_resolved(self):
        """Multi-step preset dispatch (join URIs, queue playback) can
        take seconds; ActiveStationName must move immediately to
        "Loading: <name>" so the visualisation reflects the change
        in flight. On success the prefix is dropped."""
        with self.admin._registry_lock:
            self.admin._stations.clear()
            self.admin._stations["s"] = {
                "id": "s", "name": "BBC Radio 1",
                "uri": "http://stream.example.com/r1.mp3",
                "metadata": "",
            }
        fw = StubFramework()
        lm = self.player.LogicModule(fw)
        lm.debug = fw.create_debug_section()
        lm._host = "10.0.0.5"
        # Capture every output write in order so we can see the
        # loading prefix landed BEFORE the final name.
        seen_names = []
        original_set_output = fw.set_output
        def capture(key, value):
            if key == "ActiveStationName":
                seen_names.append(value)
            return original_set_output(key, value)
        fw.set_output = capture
        lm._soap = lambda s, a, e: (True, "", "")
        lm._action_start_radio(1)
        # The loading prefix appears first, then the clean name.
        self.assertTrue(any(v == b"Loading: BBC Radio 1" for v in seen_names),
                        "expected a 'Loading: BBC Radio 1' write; got {}".format(seen_names))
        # And the final value is the clean name.
        self.assertEqual(fw.outputs["ActiveStationName"], b"BBC Radio 1")

    def test_lookup_station_via_admin_returns_dict_with_metadata(self):
        with self.admin._registry_lock:
            self.admin._stations["s"] = {
                "id": "s", "name": "TuneIn", "uri": "x-sonosapi-stream:s24939",
                "metadata": "<DIDL-Lite>...SA_RINCON65031...</DIDL-Lite>",
            }
        rec = self.player._lookup_station_via_admin(1)
        self.assertEqual(rec["uri"], "x-sonosapi-stream:s24939")
        self.assertIn("SA_RINCON65031", rec["metadata"])
        # Admin must also stamp the alphabetical index so the Player
        # can publish ActiveStation regardless of whether the lookup
        # was by name or index.
        self.assertEqual(rec["index"], 1)

    def test_admin_get_station_count_and_index(self):
        """get_station_count() reflects the registry size; get_station
        returns the right 1-based alphabetical index for name lookups."""
        with self.admin._registry_lock:
            self.admin._stations["a"] = {"id": "a", "name": "Charlie", "uri": "u1"}
            self.admin._stations["b"] = {"id": "b", "name": "alpha",   "uri": "u2"}
            self.admin._stations["c"] = {"id": "c", "name": "Bravo",   "uri": "u3"}
        self.assertEqual(self.admin.get_station_count(), 3)
        # Sorted alphabetically (case-insensitive): alpha=1, Bravo=2, Charlie=3
        self.assertEqual(self.admin.get_station("alpha")["index"], 1)
        self.assertEqual(self.admin.get_station("BRAVO")["index"], 2)
        self.assertEqual(self.admin.get_station("Charlie")["index"], 3)
        self.assertEqual(self.admin.get_station(2)["name"], "Bravo")

    def test_action_step_preset_wraps_forward_and_backward(self):
        """PresetNextPrev cycles through the library and wraps at both
        ends so a single 1-bit KNX address can browse the whole library."""
        # Sync the seed across every loaded admin module: the player's
        # _admin_station_count helper walks sys.modules and returns the
        # first match, so an unseeded sibling admin would shadow the
        # one we set up here.
        for mod_name, mod in list(sys.modules.items()):
            if mod is None:
                continue
            if "sonos_admin" in mod_name and hasattr(mod, "_stations"):
                with mod._registry_lock:
                    mod._stations.clear()
                    for i, n in enumerate(("Alpha", "Bravo", "Charlie"), start=1):
                        mod._stations[str(i)] = {
                            "id": str(i), "name": n,
                            "uri": "u" + str(i), "metadata": "",
                        }
        fw = StubFramework()
        lm = self.player.LogicModule(fw)
        lm.debug = fw.create_debug_section()
        # _action_start_radio fans out to SOAP — short-circuit it and
        # just record which index it was called with.
        calls = []
        lm._action_start_radio = lambda idx, version=None: calls.append(idx)
        # First "next" from a never-started player → preset 1.
        lm._active_station = 0
        lm._action_step_preset(+1)
        self.assertEqual(calls[-1], 1)
        # Advance: 1 → 2 → 3 → wrap to 1.
        lm._active_station = 1
        lm._action_step_preset(+1); self.assertEqual(calls[-1], 2)
        lm._active_station = 2
        lm._action_step_preset(+1); self.assertEqual(calls[-1], 3)
        lm._active_station = 3
        lm._action_step_preset(+1); self.assertEqual(calls[-1], 1)
        # Backward: 1 → wrap to 3, then 3 → 2 → 1.
        lm._active_station = 1
        lm._action_step_preset(-1); self.assertEqual(calls[-1], 3)
        lm._active_station = 3
        lm._action_step_preset(-1); self.assertEqual(calls[-1], 2)
        # First "prev" from a never-started player → wraps to last preset.
        lm._active_station = 0
        lm._action_step_preset(-1); self.assertEqual(calls[-1], 3)

    def test_per_player_preset_lookup_isolated_from_global(self):
        """With a player_spec, slots 1..10 are per-player; global presets
        start at index 11. Same numeric index points at a different
        preset on each player."""
        adm = self.admin
        with adm._registry_lock:
            adm._stations.clear()
            adm._players.clear()
            adm._stations["g1"] = {"id": "g1", "name": "Globalo",
                                   "uri": "http://glob/u1", "metadata": ""}
            # Two players, each with its own slot 1.
            adm._players["pA"] = {"id": "pA", "name": "kitchen", "uuid": "RINCON_AA",
                                  "ip": "10.0.0.10", "mac": "aa:aa:aa:aa:aa:aa",
                                  "source": "manual",
                                  "presets": [{"slot": 1, "name": "Kitchen Mix",
                                               "uri": "http://A/u1", "metadata": "",
                                               "type": "playlist"}]}
            adm._players["pB"] = {"id": "pB", "name": "office", "uuid": "RINCON_BB",
                                  "ip": "10.0.0.11", "mac": "bb:bb:bb:bb:bb:bb",
                                  "source": "manual",
                                  "presets": [{"slot": 1, "name": "Office Focus",
                                               "uri": "http://B/u1", "metadata": "",
                                               "type": "playlist"}]}
        # Index 1 with player_spec="kitchen" → Kitchen Mix.
        rec = adm.get_station(1, "kitchen")
        self.assertEqual(rec["name"], "Kitchen Mix")
        self.assertEqual(rec["scope"], "player")
        self.assertEqual(rec["index"], 1)
        # Same index 1 on the other player → different preset.
        rec = adm.get_station(1, "office")
        self.assertEqual(rec["name"], "Office Focus")
        # Index 11 (10 + 1) lands on the first global preset.
        rec = adm.get_station(11, "kitchen")
        self.assertEqual(rec["name"], "Globalo")
        self.assertEqual(rec["scope"], "global")
        self.assertEqual(rec["index"], 11)
        # Index 2 (an unconfigured per-player slot) returns None — the
        # player has no preset there, so it must NOT fall through to a
        # global match.
        self.assertIsNone(adm.get_station(2, "kitchen"))
        # Without player_spec, legacy semantics: index 1 = first global.
        rec = adm.get_station(1)
        self.assertEqual(rec["name"], "Globalo")
        self.assertEqual(rec["index"], 1)

    def test_per_player_preset_name_lookup_prefers_player(self):
        """A name match in the player's own list wins over a global
        of the same name, so an integrator can override a global by
        the same name on one specific player."""
        adm = self.admin
        with adm._registry_lock:
            adm._stations.clear()
            adm._players.clear()
            adm._stations["g1"] = {"id": "g1", "name": "Favourite",
                                   "uri": "http://glob/u", "metadata": ""}
            adm._players["pA"] = {"id": "pA", "name": "kitchen", "uuid": "RINCON_AA",
                                  "ip": "10.0.0.10", "source": "manual",
                                  "presets": [{"slot": 3, "name": "Favourite",
                                               "uri": "http://A/v", "metadata": "",
                                               "type": "radio"}]}
        rec = adm.get_station("Favourite", "kitchen")
        self.assertEqual(rec["uri"], "http://A/v")
        self.assertEqual(rec["scope"], "player")
        self.assertEqual(rec["index"], 3)
        # Player without the per-player preset falls back to global.
        rec = adm.get_station("Favourite", "unknown-host")
        self.assertEqual(rec["uri"], "http://glob/u")
        self.assertEqual(rec["scope"], "global")

    def test_get_station_indices_skips_empty_player_slots(self):
        """PresetNextPrev iterates over CONFIGURED slots only — empty
        per-player slots in the middle of 1..10 are skipped."""
        adm = self.admin
        with adm._registry_lock:
            adm._stations.clear()
            adm._players.clear()
            adm._stations["g1"] = {"id": "g1", "name": "Alpha",
                                   "uri": "u1", "metadata": ""}
            adm._stations["g2"] = {"id": "g2", "name": "Bravo",
                                   "uri": "u2", "metadata": ""}
            adm._players["pA"] = {"id": "pA", "name": "kitchen", "uuid": "RINCON_AA",
                                  "ip": "10.0.0.10", "source": "manual",
                                  "presets": [
                                      {"slot": 1, "name": "S1", "uri": "u",
                                       "metadata": "", "type": ""},
                                      {"slot": 4, "name": "S4", "uri": "u",
                                       "metadata": "", "type": ""},
                                  ]}
        idxs = adm.get_station_indices("kitchen")
        # Configured per-player slots, then globals 11 + 12.
        self.assertEqual(idxs, [1, 4, 11, 12])

    def test_action_step_preset_cycles_combined_list(self):
        """With both per-player and global presets configured, the
        Next/Prev cycle hits every configured per-player slot first
        and then every global, skipping empty player slots."""
        adm = self.admin
        for mod_name, mod in list(sys.modules.items()):
            if mod is None:
                continue
            if "sonos_admin" in mod_name and hasattr(mod, "_stations"):
                with mod._registry_lock:
                    mod._stations.clear()
                    mod._players.clear()
                    mod._stations["g1"] = {"id": "g1", "name": "Alpha",
                                           "uri": "u1", "metadata": ""}
                    mod._stations["g2"] = {"id": "g2", "name": "Bravo",
                                           "uri": "u2", "metadata": ""}
                    mod._players["pA"] = {
                        "id": "pA", "name": "kitchen", "uuid": "RINCON_AA",
                        "ip": "10.0.0.10", "source": "manual",
                        "presets": [
                            {"slot": 1, "name": "S1", "uri": "u",
                             "metadata": "", "type": ""},
                            {"slot": 4, "name": "S4", "uri": "u",
                             "metadata": "", "type": ""},
                        ],
                    }
        fw = StubFramework()
        lm = self.player.LogicModule(fw)
        lm.debug = fw.create_debug_section()
        lm._host_spec = "kitchen"
        calls = []
        lm._action_start_radio = lambda idx, version=None: calls.append(idx)
        # Configured indices: [1, 4, 11, 12].
        lm._active_station = 0
        lm._action_step_preset(+1); self.assertEqual(calls[-1], 1)
        lm._active_station = 1
        lm._action_step_preset(+1); self.assertEqual(calls[-1], 4)
        lm._active_station = 4
        lm._action_step_preset(+1); self.assertEqual(calls[-1], 11)
        lm._active_station = 11
        lm._action_step_preset(+1); self.assertEqual(calls[-1], 12)
        # Wrap forward: 12 → 1.
        lm._active_station = 12
        lm._action_step_preset(+1); self.assertEqual(calls[-1], 1)
        # Wrap backward: 1 → 12.
        lm._active_station = 1
        lm._action_step_preset(-1); self.assertEqual(calls[-1], 12)
        # Step over the empty slot 2/3: 4 → 1 (previous).
        lm._active_station = 4
        lm._action_step_preset(-1); self.assertEqual(calls[-1], 1)

    def test_api_set_and_remove_player_preset(self):
        """REST CRUD for per-player presets — verifies the store-round-
        tripped record matches the body, and DELETE clears the slot."""
        adm = self.admin
        with adm._registry_lock:
            adm._players.clear()
            adm._players["pA"] = {"id": "pA", "name": "kitchen",
                                  "uuid": "RINCON_AA", "ip": "10.0.0.10",
                                  "source": "manual"}
        fw = StubFramework()
        lm = adm.LogicModule(fw)
        lm.fw = fw
        # Upsert slot 3.
        resp = lm.api_set_player_preset("pA", 3, {
            "name": "Radio One", "uri": "http://r1", "type": "radio",
            "metadata": "<DIDL/>",
        })
        self.assertTrue(resp["ok"])
        self.assertEqual(resp["slot"], 3)
        with adm._registry_lock:
            stored = adm._players["pA"].get("presets") or []
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]["slot"], 3)
        self.assertEqual(stored[0]["uri"], "http://r1")
        # Out-of-range slot raises.
        with self.assertRaises(ValueError):
            lm.api_set_player_preset("pA", 11, {"name": "x", "uri": "x"})
        with self.assertRaises(ValueError):
            lm.api_set_player_preset("pA", 0, {"name": "x", "uri": "x"})
        # Delete.
        resp = lm.api_remove_player_preset("pA", 3)
        self.assertTrue(resp["ok"])
        with adm._registry_lock:
            stored = adm._players["pA"].get("presets") or []
        self.assertEqual(stored, [])

    def test_action_play_sound_uses_native_audioclip_when_available(self):
        """The primary path on modern S2 firmware is the native
        AudioClip service — single SOAP call, Sonos handles ducking +
        auto-resume internally. Verifies the Player only dispatches
        LoadAudioClip when the service accepts the call, and skips
        the snapshot / restore SOAP traffic entirely."""
        upload_bytes = b"RIFF\x24\x00\x00\x00WAVEfmt fake"
        for mod_name, mod in list(sys.modules.items()):
            if mod is None:
                continue
            if "sonos_admin" in mod_name and hasattr(mod, "_sounds"):
                with mod._registry_lock:
                    mod._sounds.clear()
                    mod._sounds["snd_1"] = {
                        "id": "snd_1", "name": "Doorbell",
                        "filename": "doorbell.wav",
                        "mime": "audio/wav", "source": "uploaded",
                        "size": len(upload_bytes),
                        "_data": upload_bytes,
                    }
                ref = getattr(mod, "_admin_instance_ref", None)
                if ref is not None:
                    class _FakeAdmin:
                        listener_port = 8080
                    ref["instance"] = _FakeAdmin()
        fw = StubFramework()
        lm = self.player.LogicModule(fw)
        lm.debug = fw.create_debug_section()
        lm._host = "10.0.0.5"
        calls = []
        def fake_soap(service, action, envelope):
            calls.append((service, action, envelope))
            return (True, "", "")
        lm._soap = fake_soap
        # No real polling required — native path doesn't poll.
        original_sleep = self.player.time.sleep
        self.player.time.sleep = lambda *_a, **_kw: None
        try:
            lm._action_play_sound(1)
        finally:
            self.player.time.sleep = original_sleep
        services = [s for (s, _a, _e) in calls]
        actions = [a for (_s, a, _e) in calls]
        # Exactly one SOAP call — to AudioClip.LoadAudioClip.
        self.assertEqual(actions, ["LoadAudioClip"])
        self.assertEqual(services, ["AudioClip"])
        # And the envelope carries the sound URL + display name.
        env = calls[0][2]
        self.assertIn("doorbell.wav", env)
        self.assertIn(":8080/", env)
        self.assertIn("<Name>Doorbell</Name>", env)
        self.assertIn("<ClipType>CUSTOM</ClipType>", env)
        # NO snapshot/restore SOAP issued.
        self.assertNotIn("GetMediaInfo", actions)
        self.assertNotIn("SetAVTransportURI", actions)

    def test_action_play_sound_falls_back_to_snapshot_restore_on_s1(self):
        """Older S1 firmware doesn't expose AudioClip — the service
        responds with HTTP 404 / SOAP fault. The Player must then
        snapshot, play via SetAVTransportURI + Play, poll for
        STOPPED, and restore."""
        # Seed an Admin sound in every loaded admin module so
        # _admin_sound_url resolves.
        upload_bytes = b"RIFF\x24\x00\x00\x00WAVEfmt fake-bytes"
        for mod_name, mod in list(sys.modules.items()):
            if mod is None:
                continue
            if "sonos_admin" in mod_name and hasattr(mod, "_sounds"):
                with mod._registry_lock:
                    mod._sounds.clear()
                    mod._sounds["snd_1"] = {
                        "id": "snd_1", "name": "Doorbell",
                        "filename": "doorbell.wav",
                        "mime": "audio/wav", "source": "uploaded",
                        "size": len(upload_bytes),
                        "_data": upload_bytes,
                    }
                ref = getattr(mod, "_admin_instance_ref", None)
                if ref is not None:
                    class _FakeAdmin:
                        listener_port = 8080
                    ref["instance"] = _FakeAdmin()

        fw = StubFramework()
        lm = self.player.LogicModule(fw)
        lm.debug = fw.create_debug_section()
        lm._host = "10.0.0.5"

        # Build a SOAP stub that returns a believable snapshot then
        # signals STOPPED on subsequent GetTransportInfo polls.
        calls = []
        poll_count = {"n": 0}
        def fake_soap(service, action, envelope):
            calls.append((service, action, envelope))
            # Simulate S1 hardware: AudioClip service rejected.
            if action == "LoadAudioClip":
                return (False, "<UPnPError><errorCode>401</errorCode></UPnPError>", "401")
            if action == "GetMediaInfo":
                return (True,
                        "<CurrentURI>x-sonosapi-stream:s24939</CurrentURI>"
                        "<CurrentURIMetaData>&lt;DIDL/&gt;</CurrentURIMetaData>",
                        "")
            if action == "GetPositionInfo":
                return (True,
                        "<TrackURI>x-sonosapi-stream:s24939</TrackURI>"
                        "<RelTime>0:00:00</RelTime>", "")
            if action == "GetTransportInfo":
                poll_count["n"] += 1
                # First call (snapshot) returns PLAYING; subsequent
                # polls (waiting for sound to end) return STOPPED.
                state = "PLAYING" if poll_count["n"] == 1 else "STOPPED"
                return (True,
                        "<CurrentTransportState>{}</CurrentTransportState>".format(state),
                        "")
            if action == "GetVolume":
                return (True, "<CurrentVolume>42</CurrentVolume>", "")
            if action == "GetMute":
                return (True, "<CurrentMute>0</CurrentMute>", "")
            # Everything else (SetAVTransportURI / Play / Seek /
            # SetVolume / SetMute) succeeds silently.
            return (True, "", "")
        lm._soap = fake_soap
        # Don't actually sleep in the test poll loop.
        original_sleep = self.player.time.sleep
        self.player.time.sleep = lambda *_a, **_kw: None
        try:
            lm._action_play_sound(1)
        finally:
            self.player.time.sleep = original_sleep

        actions = [a for (_s, a, _e) in calls]
        # Snapshot collected the right metadata
        for needed in ("GetMediaInfo", "GetPositionInfo", "GetTransportInfo",
                       "GetVolume", "GetMute"):
            self.assertIn(needed, actions, needed)
        # The sound URL was dispatched and Play issued
        seturi_envs = [e for (_s, a, e) in calls if a == "SetAVTransportURI"]
        self.assertTrue(any("doorbell.wav" in e and ":8080/" in e
                            for e in seturi_envs),
                        "expected the sound URL (path /sounds/.../doorbell.wav on port 8080) in one of the SetAVTransportURI calls; got: {}".format(seturi_envs))
        # After playback the original CurrentURI was restored AND Play
        # was re-issued (snapshot state was PLAYING).
        self.assertTrue(any("x-sonosapi-stream:s24939" in e
                            for e in seturi_envs),
                        "expected the snapshotted CurrentURI in a SetAVTransportURI restore call")
        # Volume + Mute restored
        self.assertTrue(any(a == "SetVolume" for (_s, a, _e) in calls))
        self.assertTrue(any(a == "SetMute" for (_s, a, _e) in calls))
        # And the last Play (after restore) is present
        play_count = sum(1 for (_s, a, _e) in calls if a == "Play")
        self.assertGreaterEqual(play_count, 2,
                                "expected at least two Play actions: one for sound, one for restore")

    def test_action_play_sound_unknown_writes_error(self):
        """Unknown sound (empty library or out-of-range index) surfaces
        SOUND_NOT_FOUND on LastError without dispatching any SOAP."""
        for mod_name, mod in list(sys.modules.items()):
            if mod is None:
                continue
            if "sonos_admin" in mod_name and hasattr(mod, "_sounds"):
                with mod._registry_lock:
                    mod._sounds.clear()
        fw = StubFramework()
        lm = self.player.LogicModule(fw)
        lm.debug = fw.create_debug_section()
        lm._host = "10.0.0.5"
        calls = []
        lm._soap = lambda *a, **kw: (calls.append(a) or (True, "", ""))
        lm._action_play_sound(1)
        self.assertEqual(calls, [])
        self.assertIn(b"SOUND_NOT_FOUND", fw.outputs.get("LastError", b""))

    def test_admin_api_add_sound_round_trip(self):
        """Upload via api_add_sound, list, then remove. Audio bytes
        survive a persist/load round-trip through the retentive store."""
        import base64 as b64
        fw = StubFramework()
        lm = self.admin.LogicModule(fw)
        lm.debug = fw.create_debug_section()
        for mod_name, mod in list(sys.modules.items()):
            if mod is None:
                continue
            if "sonos_admin" in mod_name and hasattr(mod, "_sounds"):
                with mod._registry_lock:
                    mod._sounds.clear()
        payload = b"\x52\x49\x46\x46\x10\x00\x00\x00WAVEfmt synthetic"
        r = lm.api_add_sound({
            "name": "Doorbell",
            "filename": "doorbell.wav",
            "mime": "audio/wav",
            "data_b64": b64.b64encode(payload).decode("ascii"),
        })
        self.assertEqual(r["sound"]["name"], "Doorbell")
        self.assertEqual(r["sound"]["size"], len(payload))
        self.assertEqual(r["sound"]["source"], "uploaded")
        # data_b64 must NOT round-trip through the public listing
        listing = lm.api_list_sounds()
        self.assertEqual(len(listing["sounds"]), 1)
        self.assertNotIn("data_b64", listing["sounds"][0])
        # Module-level get_sound_bytes returns the bytes
        sid = r["sound"]["id"]
        self.assertEqual(self.admin.get_sound_bytes(sid), payload)
        # The publish/persist happened in node context
        self.assertIn("PersistedSounds", fw.stores)
        # Round-trip through the retentive store
        with self.admin._registry_lock:
            self.admin._sounds.clear()
        class _Slot:
            def __init__(self, v): self.value = v
        class _Store(dict):
            def __getitem__(self, k): return _Slot(fw.stores.get(k, b""))
        lm._load_persisted(_Store())
        self.assertEqual(self.admin.get_sound_bytes(sid), payload)
        # Remove
        lm.api_remove_sound(sid)
        with self.admin._registry_lock:
            self.assertEqual(len(self.admin._sounds), 0)

    def test_admin_api_add_sound_rejects_oversized(self):
        fw = StubFramework()
        lm = self.admin.LogicModule(fw)
        lm.debug = fw.create_debug_section()
        import base64 as b64
        too_big = b"\x00" * (self.admin.MAX_SOUND_BYTES + 1)
        with self.assertRaises(ValueError):
            lm.api_add_sound({
                "name": "Too big",
                "data_b64": b64.b64encode(too_big).decode("ascii"),
            })

    def test_action_step_preset_empty_library_writes_error(self):
        for mod_name, mod in list(sys.modules.items()):
            if mod is None:
                continue
            if "sonos_admin" in mod_name and hasattr(mod, "_stations"):
                with mod._registry_lock:
                    mod._stations.clear()
        fw = StubFramework()
        lm = self.player.LogicModule(fw)
        lm.debug = fw.create_debug_section()
        lm._action_step_preset(+1)
        self.assertEqual(fw.outputs["LastError"], b"NO_PRESETS")

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

    def test_is_url_like_title(self):
        """Detection of URI-shaped strings Sonos sometimes leaks
        into dc:title or streamContent. Real titles must not match."""
        c = self.player._is_url_like_title
        # URL-shaped — should be flagged
        self.assertTrue(c("http://stream.example.com/r.mp3"))
        self.assertTrue(c("https://icecast.example.com/live"))
        self.assertTrue(c("x-rincon-mp3radio://stream"))
        self.assertTrue(c("x-sonosapi-stream:s24939?sid=254"))
        self.assertTrue(c("x-sonos-spotify:spotify:track:abc"))
        self.assertTrue(c("x-rincon:RINCON_AABBCC"))
        self.assertTrue(c("file:///jffs/settings/savedqueues.rsq"))
        # Real titles — must not be flagged
        self.assertFalse(c("Bohemian Rhapsody"))
        self.assertFalse(c("BBC Radio 1"))
        self.assertFalse(c("Live at Madison Square Garden"))
        self.assertFalse(c(""))
        self.assertFalse(c(None))

    def test_resolve_title_prefers_stream_content(self):
        """streamContent wins when present and human-readable —
        that's the ICY 'now playing' label radio sends."""
        fw = StubFramework()
        lm = self.player.LogicModule(fw)
        lm._active_station_name = "BBC Radio 1"
        # streamContent says "Beatles - Yesterday", dc:title empty
        self.assertEqual(lm._resolve_title("", "Beatles - Yesterday"),
                         "Beatles - Yesterday")
        # streamContent says "Beatles - Yesterday", dc:title also set
        # — streamContent still wins.
        self.assertEqual(lm._resolve_title("Some Title", "Beatles - Yesterday"),
                         "Beatles - Yesterday")

    def test_resolve_title_falls_back_to_preset_when_url(self):
        """When Sonos leaks the stream URL into dc:title or
        streamContent (typical right after connect), the LBS must
        fall back to the active preset name — the visualisation
        should show 'BBC Radio 1' not 'http://stream.bbc.co.uk/…'."""
        fw = StubFramework()
        lm = self.player.LogicModule(fw)
        lm._active_station_name = "BBC Radio 1"
        # Both fields URL-shaped → preset name
        self.assertEqual(
            lm._resolve_title("x-rincon-mp3radio://stream",
                              "http://stream.bbc.co.uk/r1.mp3"),
            "BBC Radio 1",
        )
        # streamContent URL, dc:title real → dc:title wins
        self.assertEqual(
            lm._resolve_title("BBC Radio 1 Live",
                              "x-rincon-mp3radio://stream"),
            "BBC Radio 1 Live",
        )
        # Both empty → preset name
        self.assertEqual(lm._resolve_title("", ""), "BBC Radio 1")

    def test_resolve_title_strips_loading_prefix_from_preset(self):
        """The 'Loading: ' marquee-style prefix that
        _mark_active_station_loading writes must NOT appear in the
        title fallback — the user wants the bare preset name."""
        fw = StubFramework()
        lm = self.player.LogicModule(fw)
        lm._active_station_name = "Loading: BBC Radio 1"
        self.assertEqual(lm._resolve_title("", ""), "BBC Radio 1")

    def test_resolve_title_empty_when_no_metadata_no_preset(self):
        """No real title anywhere and no preset → empty string,
        rather than letting a URL through."""
        fw = StubFramework()
        lm = self.player.LogicModule(fw)
        lm._active_station_name = ""
        self.assertEqual(
            lm._resolve_title("http://leaked", "x-sonosapi-stream:s1"),
            "",
        )

    def test_apply_notify_uses_preset_name_when_title_is_url(self):
        """End-to-end check for the NOTIFY path: a radio stream's
        first notify often carries the stream URL in streamContent
        before ICY metadata arrives. Title output must show the
        preset name, not the URL."""
        fw = StubFramework()
        lm = self.player.LogicModule(fw)
        lm.debug = fw.create_debug_section()
        lm._host = "10.0.0.5"
        # User just triggered the BBC Radio 1 preset.
        lm._active_station_name = "BBC Radio 1"
        # First notify after connect: streamContent contains the URL.
        lm._apply_notify_parsed({
            "state":         "PLAYING",
            "title":         "",
            "artist":        "",
            "album":         "",
            "streamContent": "x-rincon-mp3radio://stream.bbc.co.uk/r1.mp3",
            "trackUri":      "x-sonosapi-stream:s12345",
        })
        # Title is the preset name, not the URL.
        self.assertEqual(fw.outputs["Title"], b"BBC Radio 1")

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

    def test_is_group_join_uri_distinguishes_from_other_schemes(self):
        """Only bare x-rincon:RINCON_xxx is the group-join scheme. The
        sibling x-rincon-* hyphen-prefixed URIs all mean different
        things and must NOT match."""
        c = self.player._is_group_join_uri
        self.assertTrue(c("x-rincon:RINCON_AABBCC112233"))
        self.assertTrue(c("x-rincon:RINCON_ANY"))
        # Hyphen-prefixed variants are different schemes and must miss.
        self.assertFalse(c("x-rincon-stream:RINCON_AABB"))
        self.assertFalse(c("x-rincon-mp3radio://stream"))
        self.assertFalse(c("x-rincon-cpcontainer:1006206cspotify"))
        self.assertFalse(c("x-rincon-queue:RINCON_xx#0"))
        self.assertFalse(c("x-rincon-playlist:RINCON_xx#A:PL/foo"))
        # And every other scheme misses too.
        self.assertFalse(c("x-sonosapi-stream:s12345"))
        self.assertFalse(c("http://stream.example.com/r.mp3"))
        self.assertFalse(c(""))
        self.assertFalse(c(None))

    def test_action_start_radio_join_preset_skips_play(self):
        """A preset whose URI is x-rincon:RINCON_<master> must dispatch
        ONE SetAVTransportURI (the player becomes a slave) and NO Play
        — slaves auto-inherit the master's transport state, so calling
        Play would just error or duplicate. Verifies the join branch in
        _action_start_radio."""
        with self.admin._registry_lock:
            self.admin._stations.clear()
            self.admin._stations["j"] = {
                "id": "j", "name": "Join Kitchen", "type": "join",
                "uri": "x-rincon:RINCON_KITCHENAABB",
                "metadata": "",
            }
        fw = StubFramework()
        lm = self.player.LogicModule(fw)
        lm.debug = fw.create_debug_section()
        lm._host = "10.0.0.5"
        calls = []
        lm._soap = lambda s, a, e: (calls.append((a, e)) or (True, "", ""))
        lm._action_start_radio(1)
        actions = [a for (a, _e) in calls]
        # Exactly one SOAP, exactly SetAVTransportURI — no Play.
        self.assertEqual(actions, ["SetAVTransportURI"])
        self.assertIn("x-rincon:RINCON_KITCHENAABB", calls[0][1])
        # ActiveStation + ActiveStationName captured for the visualisation.
        self.assertEqual(fw.outputs["ActiveStation"], 1.0)
        self.assertEqual(fw.outputs["ActiveStationName"], b"Join Kitchen")

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

    def test_play_via_queue_detaches_slave_first(self):
        """When the player is currently a slave (CurrentTrackURI =
        x-rincon:MASTER), queue operations get silently rejected
        because the slave's transport follows the master. The fix:
        BecomeCoordinatorOfStandaloneGroup must run BEFORE the queue
        dance so the player owns its own queue."""
        fw = StubFramework()
        lm = self.player.LogicModule(fw)
        lm.debug = fw.create_debug_section()
        lm._host = "10.0.0.1"
        lm._uuid = "RINCON_THIS"
        lm._last_group_master = "RINCON_MASTER"  # currently a slave
        calls = []
        lm._soap = lambda s, a, e: (calls.append(a) or (True, "", ""))
        lm._play_via_queue(
            "x-rincon-cpcontainer:1006206cspotify:playlist:abc",
            "<DIDL-Lite/>",
            5, "My Playlist",
        )
        # BecomeCoordinatorOfStandaloneGroup MUST come first, before
        # any queue manipulation.
        self.assertEqual(calls[0], "BecomeCoordinatorOfStandaloneGroup")
        self.assertEqual(calls[1:], [
            "RemoveAllTracksFromQueue",
            "AddURIToQueue",
            "SetAVTransportURI",
            "Play",
        ])
        # And the optimistic slave-state clear happened — a chained
        # call this cycle wouldn't try to detach a second time.
        self.assertEqual(lm._last_group_master, "")

    def test_play_via_queue_skips_detach_when_already_standalone(self):
        """No wasted SOAP on a player that's already coordinator /
        standalone. _last_group_master = "" → skip the detach step."""
        fw = StubFramework()
        lm = self.player.LogicModule(fw)
        lm.debug = fw.create_debug_section()
        lm._host = "10.0.0.1"
        lm._uuid = "RINCON_THIS"
        lm._last_group_master = ""
        calls = []
        lm._soap = lambda s, a, e: (calls.append(a) or (True, "", ""))
        lm._play_via_queue(
            "x-rincon-cpcontainer:foo",
            "",
            1, "Test",
        )
        self.assertNotIn("BecomeCoordinatorOfStandaloneGroup", calls)
        self.assertEqual(calls, [
            "RemoveAllTracksFromQueue",
            "AddURIToQueue",
            "SetAVTransportURI",
            "Play",
        ])

    def test_join_preset_sets_optimistic_slave_state(self):
        """A Join preset (x-rincon:UUID) dispatches SetAVTransportURI
        and the NOTIFY confirming the slave state can lag the action
        by ~1 s. Optimistically update _last_group_master so an
        immediately-following playlist trigger sees the slave state
        and detaches before queue ops."""
        with self.admin._registry_lock:
            self.admin._stations.clear()
            self.admin._stations["j"] = {
                "id": "j", "name": "Join Kitchen", "type": "join",
                "uri": "x-rincon:RINCON_KITCHEN", "metadata": "",
            }
        fw = StubFramework()
        lm = self.player.LogicModule(fw)
        lm.debug = fw.create_debug_section()
        lm._host = "10.0.0.1"
        lm._last_group_master = ""  # we believe we're standalone
        lm._soap = lambda s, a, e: (True, "", "")
        lm._action_start_radio(1)
        # _last_group_master now reflects the new slave state
        # without waiting for the NOTIFY round-trip.
        self.assertEqual(lm._last_group_master, "RINCON_KITCHEN")

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

    def test_player_uses_admin_global_defaults_when_no_override(self):
        """Tunables come entirely from the Admin now. With no
        per-player override on the player record, _reload_config
        reads the project-wide defaults via get_player_tunables."""
        for mod_name, mod in list(sys.modules.items()):
            if mod is None:
                continue
            if "sonos_admin" in mod_name and hasattr(mod, "_player_defaults"):
                with mod._registry_lock:
                    mod._player_defaults.update({
                        "pollInterval": 25, "subTimeout": 900,
                        "httpTimeout":  7,  "callbackBase": "http://hs:8082",
                    })
                    mod._players.clear()  # no per-player record at all
        fw = StubFramework()
        lm = self.player.LogicModule(fw)
        lm.debug = fw.create_debug_section()
        lm._reload_config(make_player_inputs(host="10.0.0.1"))
        self.assertEqual(lm._poll_interval_s, 25)
        self.assertEqual(lm._sub_timeout_s, 900)
        self.assertEqual(lm._http_timeout_s, 7)
        self.assertEqual(lm._callback_base, "http://hs:8082")

    def test_player_per_player_override_beats_global_default(self):
        """When the player record carries pollInterval / subTimeout /
        httpTimeout / callbackBase fields, those win over the
        project-wide defaults — same speaker tunes itself."""
        for mod_name, mod in list(sys.modules.items()):
            if mod is None:
                continue
            if "sonos_admin" in mod_name and hasattr(mod, "_player_defaults"):
                with mod._registry_lock:
                    mod._player_defaults.update({
                        "pollInterval": 60, "subTimeout": 1800,
                        "httpTimeout":  5,  "callbackBase": "http://global:8081",
                    })
                    mod._players.clear()
                    # The Host input matches this player's IP (10.0.0.1).
                    mod._players["lr"] = {
                        "id": "lr", "name": "Living Room",
                        "zoneName": "Living Room",
                        "ip": "10.0.0.1", "mac": "", "uuid": "RINCON_LR",
                        "model": "", "source": "ssdp",
                        "pollInterval": 30,
                        "subTimeout":   600,
                        "httpTimeout":  4,
                        "callbackBase": "http://override:9000",
                    }
        fw = StubFramework()
        lm = self.player.LogicModule(fw)
        lm.debug = fw.create_debug_section()
        lm._reload_config(make_player_inputs(host="10.0.0.1"))
        self.assertEqual(lm._poll_interval_s, 30)
        self.assertEqual(lm._sub_timeout_s, 600)
        self.assertEqual(lm._http_timeout_s, 4)
        self.assertEqual(lm._callback_base, "http://override:9000")

    def test_admin_tunables_include_marquee_max_length(self):
        """Project default + per-player override layering covers the
        marqueeMaxLength field same as PollInterval / SubTimeout /
        HttpTimeout / CallbackBase."""
        fw = StubFramework()
        lm = self.admin.LogicModule(fw)
        lm.debug = fw.create_debug_section()
        # Project-wide default — must clamp to a minimum readable window.
        lm.api_set_player_defaults({"marqueeMaxLength": 20})
        with self.admin._registry_lock:
            self.admin._players.clear()
            self.admin._players["lr"] = {
                "id": "lr", "name": "lr", "zoneName": "Living Room",
                "ip": "10.0.0.50", "mac": "", "uuid": "RINCON_LR",
                "model": "", "source": "manual",
            }
        # No per-player override yet → fall through to project default.
        t = self.admin.get_player_tunables("RINCON_LR")
        self.assertEqual(t["marqueeMaxLength"], 20)
        # Per-player override beats the project default.
        lm.api_update_player("lr", {"marqueeMaxLength": 30})
        t = self.admin.get_player_tunables("RINCON_LR")
        self.assertEqual(t["marqueeMaxLength"], 30)
        # Clear the override (0) → back to project default.
        lm.api_update_player("lr", {"marqueeMaxLength": 0})
        t = self.admin.get_player_tunables("RINCON_LR")
        self.assertEqual(t["marqueeMaxLength"], 20)
        # Minimum-window clamp at the API: a value below 4 is bumped
        # up so the marquee window is always readable.
        lm.api_set_player_defaults({"marqueeMaxLength": 2})
        self.assertGreaterEqual(self.admin.get_player_defaults()["marqueeMaxLength"], 4)

    def test_admin_api_update_player_stores_overrides(self):
        """api_update_player accepts the four override fields and
        stores them on the player record. Sending 0 / empty clears
        the override (back to project default)."""
        fw = StubFramework()
        lm = self.admin.LogicModule(fw)
        lm.debug = fw.create_debug_section()
        with self.admin._registry_lock:
            self.admin._players["lr"] = {
                "id": "lr", "name": "lr", "zoneName": "Living Room",
                "ip": "10.0.0.50", "mac": "", "uuid": "RINCON_LR",
                "model": "", "source": "manual",
            }
        lm.api_update_player("lr", {
            "pollInterval": 30, "subTimeout": 600,
            "httpTimeout":  4,  "callbackBase": "http://override:9000",
        })
        with self.admin._registry_lock:
            rec = self.admin._players["lr"]
            self.assertEqual(rec["pollInterval"], 30)
            self.assertEqual(rec["subTimeout"],   600)
            self.assertEqual(rec["httpTimeout"],  4)
            self.assertEqual(rec["callbackBase"], "http://override:9000")
        # get_player_tunables layers them on top of the project defaults.
        t = self.admin.get_player_tunables("RINCON_LR")
        self.assertEqual(t["pollInterval"], 30)
        self.assertEqual(t["callbackBase"], "http://override:9000")
        # Clear by sending 0 / empty
        lm.api_update_player("lr", {
            "pollInterval": 0, "subTimeout": 0,
            "httpTimeout":  0, "callbackBase": "",
        })
        with self.admin._registry_lock:
            rec = self.admin._players["lr"]
            self.assertNotIn("pollInterval", rec)
            self.assertNotIn("callbackBase", rec)

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


# ---------------------------------------------------------------------------
# LBS 22002 — Sonos Sound Enhancement (optional companion to LBS 22000).
# ---------------------------------------------------------------------------


def make_sound_inputs(host="10.0.0.1", **overrides):
    base = {
        "Host":           StubSlot(host),
        "SetBass":        StubSlot(0),
        "SetTreble":      StubSlot(0),
        "SetLoudness":    StubSlot(0),
        "SetNightMode":   StubSlot(0),
        "SetDialogMode":  StubSlot(0),
        "SetCrossfade":   StubSlot(0),
        "SetSleepTimer":      StubSlot(0),
        "SetTVMode":          StubSlot(0),
        "SetLED":             StubSlot(0),
        "SetGroupVolume":     StubSlot(0),
        "SetSurroundEnable":  StubSlot(0),
        "SetSurroundLevel":   StubSlot(0),
        "SetSubEnable":       StubSlot(0),
        "SetSubGain":         StubSlot(0),
        "SetTrueplay":        StubSlot(0),
    }
    base.update({k: (v if isinstance(v, StubSlot) else StubSlot(v))
                 for k, v in overrides.items()})
    return StubSlots(base)


class TestSonosSoundHelpers(unittest.TestCase):
    """Pure helpers — no framework needed."""

    @classmethod
    def setUpClass(cls):
        sys.modules.setdefault("requests", _make_requests_stub())
        cls.mod = load_module(SOUND_PY, "sonos_sound_22002_helpers")

    def test_minutes_to_duration_formats_hh_mm_ss(self):
        m = self.mod.minutes_to_duration
        self.assertEqual(m(0),   "")     # 0 cancels the timer
        self.assertEqual(m(-5),  "")     # negative also cancels
        self.assertEqual(m(1),   "0:01:00")
        self.assertEqual(m(59),  "0:59:00")
        self.assertEqual(m(60),  "1:00:00")
        self.assertEqual(m(125), "2:05:00")
        # Garbage in → empty out, doesn't raise
        self.assertEqual(m("abc"), "")
        self.assertEqual(m(None),  "")

    def test_duration_to_seconds_parses_sonos_format(self):
        d = self.mod.duration_to_seconds
        self.assertEqual(d(""),         0)
        self.assertEqual(d(None),       0)
        self.assertEqual(d("0:00:00"),  0)
        self.assertEqual(d("0:01:30"),  90)
        self.assertEqual(d("2:00:00"),  7200)
        self.assertEqual(d("garbage"),  0)
        self.assertEqual(d("1:2:3:4"),  0)

    def test_clamp_constrains_range(self):
        c = self.mod.clamp
        self.assertEqual(c(0,  -10, 10), 0)
        self.assertEqual(c(15, -10, 10), 10)
        self.assertEqual(c(-15,-10, 10), -10)
        self.assertEqual(c("abc", -10, 10), 0)


class TestSonosSoundLogicModule(unittest.TestCase):
    """LogicModule IO contract + action dispatch with a stubbed SOAP layer."""

    @classmethod
    def setUpClass(cls):
        sys.modules.setdefault("requests", _make_requests_stub())
        cls.mod = load_module(SOUND_PY, "sonos_sound_22002_lm")

    def _make(self):
        fw = StubFramework()
        lm = self.mod.LogicModule(fw)
        lm.debug = fw.create_debug_section()
        lm._host = "10.0.0.5"
        lm._http_timeout_s = 5
        # Run actions inline so each on_calc test is deterministic.
        lm._run_threaded = lambda fn: fn()
        return fw, lm

    def test_set_bass_clamps_and_dispatches(self):
        fw, lm = self._make()
        calls = []
        lm._soap = lambda s, a, e: (calls.append((s, a, e)) or (True, "", ""))
        ins = make_sound_inputs(host="10.0.0.5", SetBass=15)
        ins["SetBass"] = StubSlot(15, changed=True)
        lm.on_calc(ins)
        self.assertEqual(calls[0][1], "SetBass")
        self.assertIn("<DesiredBass>10</DesiredBass>", calls[0][2])
        self.assertEqual(fw.outputs["Bass"], 10.0)

    def test_set_treble_clamps_negative(self):
        fw, lm = self._make()
        calls = []
        lm._soap = lambda s, a, e: (calls.append((s, a, e)) or (True, "", ""))
        ins = make_sound_inputs(host="10.0.0.5")
        ins["SetTreble"] = StubSlot(-99, changed=True)
        lm.on_calc(ins)
        self.assertEqual(calls[0][1], "SetTreble")
        self.assertIn("<DesiredTreble>-10</DesiredTreble>", calls[0][2])
        self.assertEqual(fw.outputs["Treble"], -10.0)

    def test_set_loudness_dispatches(self):
        fw, lm = self._make()
        calls = []
        lm._soap = lambda s, a, e: (calls.append((s, a, e)) or (True, "", ""))
        ins = make_sound_inputs(host="10.0.0.5")
        ins["SetLoudness"] = StubSlot(1, changed=True)
        lm.on_calc(ins)
        self.assertEqual(calls[0][1], "SetLoudness")
        self.assertIn("<DesiredLoudness>1</DesiredLoudness>", calls[0][2])
        self.assertEqual(fw.outputs["Loudness"], 1)

    def test_set_nightmode_unsupported_writes_tagged_error(self):
        """Non-soundbar players reject SetEQ NightMode with a SOAP
        fault. The module must surface that as NIGHTMODE_UNSUPPORTED
        — clearer than a generic 'EQ failed' — so the integrator can
        diagnose the wiring."""
        fw, lm = self._make()
        # Stub SOAP to fail.
        lm._soap = lambda s, a, e: (False, "<errorCode>800</errorCode>", "800")
        ins = make_sound_inputs(host="10.0.0.5")
        ins["SetNightMode"] = StubSlot(1, changed=True)
        lm.on_calc(ins)
        self.assertEqual(fw.outputs["LastError"], b"NIGHTMODE_UNSUPPORTED")
        # NightMode output stays at init (no _mark_night call).
        self.assertNotIn("NightMode", fw.outputs)

    def test_set_dialogmode_success_marks_output(self):
        fw, lm = self._make()
        lm._soap = lambda s, a, e: (True, "", "")
        ins = make_sound_inputs(host="10.0.0.5")
        ins["SetDialogMode"] = StubSlot(1, changed=True)
        lm.on_calc(ins)
        self.assertEqual(fw.outputs["DialogMode"], 1)

    def test_set_crossfade_dispatches(self):
        fw, lm = self._make()
        calls = []
        lm._soap = lambda s, a, e: (calls.append(a) or (True, "", ""))
        ins = make_sound_inputs(host="10.0.0.5")
        ins["SetCrossfade"] = StubSlot(1, changed=True)
        lm.on_calc(ins)
        self.assertEqual(calls, ["SetCrossfadeMode"])
        self.assertEqual(fw.outputs["Crossfade"], 1)

    def test_set_sleep_timer_sends_hh_mm_ss(self):
        """SetSleepTimer minute value gets formatted as HH:MM:SS per
        Sonos's ConfigureSleepTimer contract. 0 cancels — sent as the
        empty NewSleepTimerDuration string."""
        fw, lm = self._make()
        calls = []
        lm._soap = lambda s, a, e: (calls.append((a, e)) or (True, "", ""))
        ins = make_sound_inputs(host="10.0.0.5")
        ins["SetSleepTimer"] = StubSlot(90, changed=True)
        lm.on_calc(ins)
        self.assertEqual(calls[0][0], "ConfigureSleepTimer")
        self.assertIn("<NewSleepTimerDuration>1:30:00</NewSleepTimerDuration>",
                      calls[0][1])
        # Cancellation
        calls.clear()
        ins = make_sound_inputs(host="10.0.0.5")
        ins["SetSleepTimer"] = StubSlot(0, changed=True)
        lm.on_calc(ins)
        self.assertEqual(calls[0][0], "ConfigureSleepTimer")
        self.assertIn("<NewSleepTimerDuration></NewSleepTimerDuration>",
                      calls[0][1])

    def test_tick_polls_all_seven_and_sets_online(self):
        """A single Tick should poll every parameter and flip Online
        when at least one SOAP returned 200."""
        fw, lm = self._make()
        polls = []
        def fake_soap(service, action, envelope):
            polls.append(action)
            # Return believable payloads for each GET so the parsers
            # produce the expected outputs.
            if action == "GetBass":     return (True, "<CurrentBass>3</CurrentBass>", "")
            if action == "GetTreble":   return (True, "<CurrentTreble>-2</CurrentTreble>", "")
            if action == "GetLoudness": return (True, "<CurrentLoudness>1</CurrentLoudness>", "")
            if action == "GetEQ":
                # NightMode + DialogLevel — same response shape.
                return (True, "<CurrentValue>1</CurrentValue>", "")
            if action == "GetCrossfadeMode":
                return (True, "<CrossfadeMode>0</CrossfadeMode>", "")
            if action == "GetRemainingSleepTimerDuration":
                return (True, "<RemainingSleepTimerDuration>0:15:00</RemainingSleepTimerDuration>", "")
            return (True, "", "")
        lm._soap = fake_soap
        lm._tick_work()
        # All 7 GETs issued
        for needed in ("GetBass", "GetTreble", "GetLoudness", "GetEQ",
                       "GetCrossfadeMode", "GetRemainingSleepTimerDuration"):
            self.assertIn(needed, polls, needed)
        # Outputs reflect the parsed values
        self.assertEqual(fw.outputs["Bass"],         3.0)
        self.assertEqual(fw.outputs["Treble"],      -2.0)
        self.assertEqual(fw.outputs["Loudness"],     1)
        self.assertEqual(fw.outputs["NightMode"],    1)
        self.assertEqual(fw.outputs["DialogMode"],   1)
        self.assertEqual(fw.outputs["Crossfade"],    0)
        self.assertEqual(fw.outputs["SleepTimerRemaining"], 900.0)
        self.assertEqual(fw.outputs["Online"],       1)

    def test_set_led_dispatches_on_off_strings(self):
        fw, lm = self._make()
        calls = []
        lm._soap = lambda s, a, e: (calls.append((s, a, e)) or (True, "", ""))
        ins = make_sound_inputs(host="10.0.0.5")
        ins["SetLED"] = StubSlot(1, changed=True)
        lm.on_calc(ins)
        self.assertEqual(calls[0][0], "DeviceProperties")
        self.assertEqual(calls[0][1], "SetLEDState")
        self.assertIn("<DesiredLEDState>On</DesiredLEDState>", calls[0][2])
        self.assertEqual(fw.outputs["LED"], 1)
        # 0 → Off
        calls.clear()
        ins = make_sound_inputs(host="10.0.0.5")
        ins["SetLED"] = StubSlot(0, changed=True)
        lm.on_calc(ins)
        self.assertIn("<DesiredLEDState>Off</DesiredLEDState>", calls[0][2])

    def test_set_group_volume_clamps_and_dispatches(self):
        fw, lm = self._make()
        calls = []
        lm._soap = lambda s, a, e: (calls.append((s, a, e)) or (True, "", ""))
        ins = make_sound_inputs(host="10.0.0.5")
        ins["SetGroupVolume"] = StubSlot(150, changed=True)  # over-range
        lm.on_calc(ins)
        self.assertEqual(calls[0][0], "GroupRenderingControl")
        self.assertEqual(calls[0][1], "SetGroupVolume")
        self.assertIn("<DesiredVolume>100</DesiredVolume>", calls[0][2])
        self.assertEqual(fw.outputs["GroupVolume"], 100.0)

    def test_set_group_volume_non_coordinator_writes_tagged_error(self):
        """Sonos returns UPnP error 701 when SetGroupVolume is called
        on a non-coordinator. The module must surface this as
        GROUP_NOT_COORDINATOR so the integrator can wire the input
        to the right Player block."""
        fw, lm = self._make()
        lm._soap = lambda s, a, e: (False, "<errorCode>701</errorCode>", "701")
        ins = make_sound_inputs(host="10.0.0.5")
        ins["SetGroupVolume"] = StubSlot(40, changed=True)
        lm.on_calc(ins)
        self.assertEqual(fw.outputs["LastError"], b"GROUP_NOT_COORDINATOR")

    def test_set_surround_and_sub_eq_dispatches(self):
        fw, lm = self._make()
        calls = []
        lm._soap = lambda s, a, e: (calls.append(e) or (True, "", ""))
        ins = make_sound_inputs(host="10.0.0.5")
        ins["SetSurroundEnable"] = StubSlot(1, changed=True)
        lm.on_calc(ins)
        self.assertIn("<EQType>SurroundEnable</EQType>", calls[0])
        self.assertEqual(fw.outputs["SurroundEnable"], 1)
        # Level clamps to -15..15
        calls.clear()
        ins = make_sound_inputs(host="10.0.0.5")
        ins["SetSurroundLevel"] = StubSlot(99, changed=True)
        lm.on_calc(ins)
        self.assertIn("<EQType>SurroundLevel</EQType>", calls[0])
        self.assertIn("<DesiredValue>15</DesiredValue>", calls[0])
        self.assertEqual(fw.outputs["SurroundLevel"], 15.0)
        # SubGain clamps negative
        calls.clear()
        ins = make_sound_inputs(host="10.0.0.5")
        ins["SetSubGain"] = StubSlot(-99, changed=True)
        lm.on_calc(ins)
        self.assertIn("<EQType>SubGain</EQType>", calls[0])
        self.assertIn("<DesiredValue>-15</DesiredValue>", calls[0])
        self.assertEqual(fw.outputs["SubGain"], -15.0)

    def test_set_surround_unsupported_writes_tagged_error(self):
        fw, lm = self._make()
        lm._soap = lambda s, a, e: (False, "<errorCode>800</errorCode>", "800")
        ins = make_sound_inputs(host="10.0.0.5")
        ins["SetSurroundEnable"] = StubSlot(1, changed=True)
        lm.on_calc(ins)
        self.assertEqual(fw.outputs["LastError"], b"SURROUND_UNSUPPORTED")

    def test_set_trueplay_dispatches_and_marks(self):
        fw, lm = self._make()
        calls = []
        lm._soap = lambda s, a, e: (calls.append((s, a, e)) or (True, "", ""))
        ins = make_sound_inputs(host="10.0.0.5")
        ins["SetTrueplay"] = StubSlot(1, changed=True)
        lm.on_calc(ins)
        self.assertEqual(calls[0][1], "SetRoomCalibrationStatus")
        self.assertIn("<RoomCalibrationEnabled>1</RoomCalibrationEnabled>", calls[0][2])
        self.assertEqual(fw.outputs["Trueplay"], 1)

    def test_switch_tv_mode_uses_player_uuid(self):
        """TV mode URI carries the soundbar's RINCON UUID. The action
        must look this up via the Admin player registry; without a UUID
        we surface TV_NO_UUID rather than emitting an invalid URI."""
        # Seed an Admin player with a known UUID across every loaded
        # admin module (multiple are kept around in sys.modules from
        # earlier test classes).
        for mod_name, mod in list(sys.modules.items()):
            if mod is None:
                continue
            if "sonos_admin" in mod_name and hasattr(mod, "_players"):
                with mod._registry_lock:
                    mod._players.clear()
                    mod._players["beam"] = {
                        "id": "beam", "name": "Beam", "zoneName": "Living Room",
                        "ip": "10.0.0.5", "mac": "", "uuid": "RINCON_BEAMUUID",
                        "model": "Beam", "source": "ssdp",
                    }
        fw, lm = self._make()
        lm._host_spec = "RINCON_BEAMUUID"
        calls = []
        lm._soap = lambda s, a, e: (calls.append((s, a, e)) or (True, "", ""))
        ins = make_sound_inputs(host="RINCON_BEAMUUID")
        # Re-create with the UUID host since make_sound_inputs takes a
        # different signature than the default fixture.
        ins["SetTVMode"] = StubSlot(1, changed=True)
        lm.on_calc(ins)
        actions = [a for (_s, a, _e) in calls]
        # Expect SetAVTransportURI + Play.
        self.assertIn("SetAVTransportURI", actions)
        self.assertIn("Play", actions)
        # The URI must be the soundbar's UUID stream.
        seturi = [e for (_s, a, e) in calls if a == "SetAVTransportURI"][0]
        self.assertIn("x-sonos-htastream:RINCON_BEAMUUID:spdif", seturi)
        self.assertEqual(fw.outputs["TVMode"], 1)

    def test_switch_tv_mode_without_uuid_writes_error(self):
        # No Admin player records → UUID lookup returns nothing.
        for mod_name, mod in list(sys.modules.items()):
            if mod is None:
                continue
            if "sonos_admin" in mod_name and hasattr(mod, "_players"):
                with mod._registry_lock:
                    mod._players.clear()
        fw, lm = self._make()
        lm._host_spec = "10.0.0.5"
        lm._uuid = ""
        calls = []
        lm._soap = lambda *a, **kw: (calls.append(a) or (True, "", ""))
        ins = make_sound_inputs(host="10.0.0.5")
        ins["SetTVMode"] = StubSlot(1, changed=True)
        lm.on_calc(ins)
        self.assertEqual(calls, [])
        self.assertEqual(fw.outputs["LastError"], b"TV_NO_UUID")

    def test_fetch_battery_status_parses_xml(self):
        """The /status/batterystatus endpoint returns XML with <Data>
        rows. Helper must extract Level (percent) and PowerSource
        (charging boolean) and return None when the endpoint 404s
        (non-portable speaker)."""
        fw, lm = self._make()
        class _R:
            def __init__(self, status, body):
                self.status_code = status
                self.text = body
        # Stub requests.get to return a Move-style response.
        orig = self.mod.requests.get
        try:
            self.mod.requests.get = lambda *a, **kw: _R(200,
                '<ZPSupportInfo><LocalBatteryStatus>'
                '<Data name="Health">GREEN</Data>'
                '<Data name="Level">73</Data>'
                '<Data name="PowerSource">CHARGING</Data>'
                '</LocalBatteryStatus></ZPSupportInfo>')
            pct, charging = lm._fetch_battery_status()
            self.assertEqual(pct, 73)
            self.assertTrue(charging)
            # PowerSource=BATTERY → not charging
            self.mod.requests.get = lambda *a, **kw: _R(200,
                '<ZPSupportInfo><LocalBatteryStatus>'
                '<Data name="Level">42</Data>'
                '<Data name="PowerSource">BATTERY</Data>'
                '</LocalBatteryStatus></ZPSupportInfo>')
            pct, charging = lm._fetch_battery_status()
            self.assertEqual(pct, 42)
            self.assertFalse(charging)
            # 404 → (None, None) — non-portable hardware
            self.mod.requests.get = lambda *a, **kw: _R(404, "")
            pct, charging = lm._fetch_battery_status()
            self.assertIsNone(pct)
            self.assertIsNone(charging)
        finally:
            self.mod.requests.get = orig

    def test_no_host_writes_error_without_soap(self):
        fw, lm = self._make()
        lm._host = ""
        calls = []
        lm._soap = lambda *a, **kw: (calls.append(a) or (True, "", ""))
        ins = make_sound_inputs(host="")
        ins["SetBass"] = StubSlot(3, changed=True)
        lm.on_calc(ins)
        # No SOAP because no host
        self.assertEqual(calls, [])
        self.assertEqual(fw.outputs["LastError"], b"NO_HOST_CONFIGURED")


if __name__ == "__main__":
    unittest.main(verbosity=2)
