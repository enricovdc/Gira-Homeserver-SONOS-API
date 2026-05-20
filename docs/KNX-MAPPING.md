# KNX group address mapping (recommended)

Group addresses are **not hard-coded** anywhere in the LBS modules — you
wire them in HS Experte by connecting KNX group addresses to the
inputs and outputs of each *Sonos Player* (LBS 22000) instance. The
tables below are a recommended template; substitute any free range
that suits your project.

Assume **main group 5 = Sonos**, with one middle group per player:

```
5/0/x   Living Room   (Sonos Player block; Host = RINCON_xxx or IP)
5/1/x   Kitchen
5/2/x   Bathroom
```

## Inputs — KNX → LBS 22000

Wire each KNX group address to the matching input on the Sonos Player
block. The DPT is what KNX uses on the wire; the value arrives at the
LBS as a `number` or `string` per the input declaration in
`projects/sonos_player_hsl3/config.json`.

### Transport (rising-edge momentary)

| GA | DPT | Input | Description |
| --- | --- | --- | --- |
| 5/0/1  | 1.001 | `Play`         | Rising edge starts / resumes playback |
| 5/0/2  | 1.001 | `Pause`        | Rising edge pauses |
| 5/0/3  | 1.001 | `Stop`         | Rising edge stops |
| 5/0/4  | 1.001 | `Next`         | Next track |
| 5/0/5  | 1.001 | `Prev`         | Previous track |
| 5/0/6  | 1.001 | `MuteToggle`   | **Rising edge only** — every write of 1 reads the player's mute state and flips it. For a KNX push button that always sends 1 on press. Use `SetMute` instead for a regular switch GA. |

### Transport (value toggles for one-bit GAs)

These dispatch on every value change. Pair each with a single 1-bit
KNX group address whose flipping drives the action.

| GA | DPT | Input | Description |
| --- | --- | --- | --- |
| 5/0/10 | 1.001 | `PlayPause`      | 1 = Play, 0 = Pause |
| 5/0/11 | 1.001 | `NextPrev`       | 1 = Next track, 0 = Previous track |
| 5/0/12 | 1.001 | `PresetNextPrev` | 1 = Next preset, 0 = Previous preset. Cycles this player's configured slots (1..10) then every global preset (11+), wraps at both ends; empty slots are skipped. |

### Volume / Mute

| GA | DPT | Input | Description |
| --- | --- | --- | --- |
| 5/0/20 | 5.001 | `SetVolume`  | Absolute volume 0–100 % |
| 5/0/21 | 1.001 | `VolUp`      | Rising edge adds `VolStep` |
| 5/0/22 | 1.001 | `VolDown`    | Rising edge subtracts `VolStep` |
| 5/0/23 | 1.008 | `VolUpDown`  | DPT 1.008 "Up/Down" rocker — wire the single 1-bit GA straight in. Each write of 1 adds `VolStep`, each write of 0 subtracts. No helper logic blocks needed. |
| 5/0/24 | 1.001 | `SetMute`    | **Value-driven** — 1 = mute, 0 = unmute. Pair with a KNX switch GA that carries the desired mute state. The Mute output mirrors what the player actually does. |

### Play mode (shuffle / repeat)

| GA | DPT | Input | Description |
| --- | --- | --- | --- |
| 5/0/30 | 1.001 | `SetShuffle` | 1 = shuffle on, 0 = off. Combined with `SetRepeat` into the Sonos PlayMode. |
| 5/0/31 | 1.001 | `SetRepeat`  | 1 = repeat-all on, 0 = off |

### Presets and sounds

| GA | DPT | Input | Description |
| --- | --- | --- | --- |
| 5/0/40 | 5.010 | `StartRadio`     | 1..10 = per-player preset slot configured on this player's Admin card; 11..(10+N) = global preset (alphabetical). The same global index points at the same preset on every player. |
| 5/0/41 | 16.001 | `StartRadioName` | Preset name (case-insensitive). Per-player names win over a global of the same name. |
| 5/0/45 | 5.010 | `PlaySound`       | Alphabetical index of a notification clip in the Admin Sounds library. Triggers the Sonos native announcement (or snapshot/restore on S1). |

### Group presets

| GA | DPT | Input | Description |
| --- | --- | --- | --- |
| 5/0/50 | 5.010 | `GroupPreset`     | Alphabetical index of a group preset (master + members defined in the Admin UI) |
| 5/0/51 | 16.001 | `GroupPresetName` | Group preset by name |
| 5/0/52 | 1.001 | `Ungroup`         | Rising edge breaks this player out of its zone group |

### Diagnostics

| GA | DPT | Input | Description |
| --- | --- | --- | --- |
| 5/0/60 | 1.001 | `Resubscribe` | Rising edge forces UPnP re-subscribe |

## Outputs — LBS 22000 → KNX

When an output changes (driven by NOTIFY or by `Tick` poll), the LBS
sends to the wired KNX group address with the configured DPT.

### Player identity / health

| GA | DPT | Output | Value |
| --- | --- | --- | --- |
| 5/0/100 | 16.001 | `ZoneName`  | The Sonos Zone Name from the Sonos app ("Living Room"). Updated on every Tick — picks up app-side renames within one poll interval. |
| 5/0/101 | 1.002  | `Online`    | 1 = player responded recently |
| 5/0/102 | 1.002  | `Subscribed`| 1 = both AVTransport + RenderingControl UPnP subscriptions alive |
| 5/0/103 | 16.001 | `LastError` | Last error code (`UNREACHABLE`, UPnP fault code, `PRESET_NOT_FOUND`, `NO_PRESETS`, `GROUP_PARTIAL`, `SOUND_NOT_FOUND`, `EXCEPTION: …`). Empty when healthy. |

### Playback state

| GA | DPT | Output | Value |
| --- | --- | --- | --- |
| 5/0/110 | 16.001 | `State`           | Friendly state: `Playing` / `Paused` / `Stopped` / `Transitioning` / `Buffering` / `Connecting` / `No media` / `Playing TV` / `Playing line-in` |
| 5/0/111 | 1.002  | `IsPlaying`       | 1 = state is `Playing`. Mutually exclusive with the other `Is*` flags. |
| 5/0/112 | 1.002  | `IsPaused`        | 1 = state is `Paused` |
| 5/0/113 | 1.002  | `IsStopped`       | 1 = state is `Stopped` |
| 5/0/114 | 1.002  | `IsTransitioning` | 1 = state is `Transitioning` (brief — Sonos passes through this when starting / changing tracks) |

### Transport "is allowed right now"

Mirrors Sonos's `CurrentTransportActions`. Useful for greying out
buttons in a visualisation that the player would reject.

| GA | DPT | Output | Value |
| --- | --- | --- | --- |
| 5/0/120 | 1.002 | `PlayAllowed`    | 1 = the player will accept Play |
| 5/0/121 | 1.002 | `PauseAllowed`   | 1 = Pause accepted (radio streams: 0) |
| 5/0/122 | 1.002 | `StopAllowed`    | 1 = Stop accepted |
| 5/0/123 | 1.002 | `NextAllowed`    | 1 = Next accepted (radio streams: 0) |
| 5/0/124 | 1.002 | `PrevAllowed`    | 1 = Previous accepted |
| 5/0/125 | 1.002 | `ShuffleAllowed` | 1 = there's a queue (shuffle makes sense) |
| 5/0/126 | 1.002 | `RepeatAllowed`  | 1 = there's a queue (repeat makes sense) |

### Volume / Mute / Play mode

| GA | DPT | Output | Value |
| --- | --- | --- | --- |
| 5/0/130 | 5.001 | `Volume`       | 0–100 |
| 5/0/131 | 1.002 | `Mute`         | 1 = muted |
| 5/0/132 | 1.002 | `ShuffleState` | 1 = any SHUFFLE play mode (reflects Sonos-app changes too) |
| 5/0/133 | 1.002 | `RepeatState`  | 1 = REPEAT_ALL or REPEAT_ONE |

### Track metadata

| GA | DPT | Output | Value |
| --- | --- | --- | --- |
| 5/0/140 | 16.001 | `Title`        | Current track title or live radio `streamContent` |
| 5/0/141 | 16.001 | `Artist`       | Current artist if available |
| 5/0/142 | 16.001 | `Album`        | Current album (empty for radio streams) |
| 5/0/143 | 16.001 | `AlbumArtURI`  | Absolute cover-art URL (relative `/getaa?…` paths are auto-prefixed with `http://<player-ip>:1400`). Wire to a Gira visualisation image element. |

### Active preset

| GA | DPT | Output | Value |
| --- | --- | --- | --- |
| 5/0/150 | 5.010  | `ActiveStation`     | Index of the last preset started (0 = none). 1..10 = a per-player slot; 11+ = a global preset's offset index. Stable across name / index / `PresetNextPrev` triggers. |
| 5/0/151 | 16.001 | `ActiveStationName` | Display name of the active preset (per-player or global) |

### Group membership

| GA | DPT | Output | Value |
| --- | --- | --- | --- |
| 5/0/160 | 1.002  | `IsCoordinator` | 1 = standalone or this player is the group coordinator. 0 = this player is a slave in someone else's group. |
| 5/0/161 | 16.001 | `GroupInfo`     | When slave: master's Zone Name (resolved via Admin) or RINCON UUID. Empty when coordinator/standalone. |

## Sound Enhancement companion (LBS 22002) — recommended GAs

Optional per-player block. Skip if you didn't drop an LBS 22002 onto
the canvas. The Host input takes the same value as the matching LBS
22000.

### Inputs

| GA | DPT | Input | Description |
| --- | --- | --- | --- |
| 5/0/300 | 6.010 | `SetBass`       | -10..+10 (signed 1-byte) |
| 5/0/301 | 6.010 | `SetTreble`     | -10..+10 |
| 5/0/302 | 1.001 | `SetLoudness`   | 0/1 |
| 5/0/303 | 1.001 | `SetNightMode`  | 0/1 (soundbar only) |
| 5/0/304 | 1.001 | `SetDialogMode` | 0/1 (soundbar only) |
| 5/0/305 | 1.001 | `SetCrossfade`     | 0/1 |
| 5/0/306 | 7.007 | `SetSleepTimer`    | minutes (2-byte unsigned). 0 cancels. |
| 5/0/307 | 1.001 | `SetTVMode`        | Rising edge engages the soundbar's TV input |
| 5/0/308 | 1.001 | `SetLED`           | 0/1 — status LED |
| 5/0/320 | 5.001 | `SetGroupVolume`   | 0–100 % whole-group volume (coordinator only) |
| 5/0/321 | 1.001 | `SetSurroundEnable`| 0/1 — soundbar surround channels |
| 5/0/322 | 6.010 | `SetSurroundLevel` | -15..+15 |
| 5/0/323 | 1.001 | `SetSubEnable`     | 0/1 — paired Sub on/off |
| 5/0/324 | 6.010 | `SetSubGain`       | -15..+15 |
| 5/0/325 | 1.001 | `SetTrueplay`      | 0/1 — apply stored calibration |

### Outputs

| GA | DPT | Output | Description |
| --- | --- | --- | --- |
| 5/0/310 | 1.002 | `Online`              | 1 = SOAP reachable |
| 5/0/311 | 6.010 | `Bass`                | -10..+10 |
| 5/0/312 | 6.010 | `Treble`              | -10..+10 |
| 5/0/313 | 1.002 | `Loudness`            | 0/1 |
| 5/0/314 | 1.002 | `NightMode`           | 0/1 |
| 5/0/315 | 1.002 | `DialogMode`          | 0/1 |
| 5/0/316 | 1.002 | `Crossfade`           | 0/1 |
| 5/0/317 | 7.005 | `SleepTimerRemaining` | seconds remaining (2-byte) |
| 5/0/330 | 1.002 | `TVMode`              | 1 = soundbar TV input engaged |
| 5/0/331 | 1.002 | `LED`                 | 1 = status LED on |
| 5/0/332 | 5.001 | `BatteryPercent`      | 0–100 % (Move / Roam) |
| 5/0/333 | 1.002 | `BatteryCharging`     | 1 = charging |
| 5/0/334 | 5.001 | `GroupVolume`         | 0–100 % whole-group volume |
| 5/0/335 | 1.002 | `SurroundEnable`      | 0/1 |
| 5/0/336 | 6.010 | `SurroundLevel`       | -15..+15 |
| 5/0/337 | 1.002 | `SubEnable`           | 0/1 |
| 5/0/338 | 6.010 | `SubGain`             | -15..+15 |
| 5/0/339 | 1.002 | `Trueplay`            | 1 = calibration applied |
| 5/0/340 | 1.002 | `TrueplayAvailable`   | 1 = calibration profile exists on player |
| 5/0/341 | 16.001 | `LastError`          | Error tag (`NIGHTMODE_UNSUPPORTED`, `GROUP_NOT_COORDINATOR`, `TV_NO_UUID`, …) |

DPT 6.010 "Counter pulses (signed)" carries the -10..+10 range
natively. DPT 7.007 "Time (16-bit unsigned)" gives plenty of room
for sleep timer minutes (max ~18 hours).

## DPT cheat sheet

- **DPT 1.001** "Switch" and **1.002** "Boolean": both 1-bit, semantically
  different in ETS but identical on the wire. Use 1.001 for commands
  the user triggers, 1.002 for status feedback (this is the convention
  used above).
- **DPT 1.008** "Up/Down": split into two branches in the Experte —
  Up → `VolUp` input, Down → `VolDown` input.
- **DPT 5.001** "Scaling" represents 0–100 % as 0–255 on the wire. Experte
  handles the conversion in both directions; the LBS sees 0–100.
- **DPT 5.010** "Counter pulses (1 byte)" — standard 0–255 mapping. Plenty
  for preset / sound / group indices.
- **DPT 16.001** "Character string (ISO 10646 / UTF-8)" handles
  accented characters in track titles, zone names, error codes. The
  LBS encodes string outputs as iso-8859-15 bytes per the HSL3 SDK
  rule; Experte converts to the chosen DPT format.

## Pattern: one-button-per-preset vs single-value

### One button per preset

Wall plates with discrete physical buttons:

```
5/0/200  Preset #1   (DPT 1.001)
5/0/201  Preset #2
5/0/202  Preset #3
...
```

Each button triggers a small logic block that writes the corresponding
index to `StartRadio`. The Admin holds two registries: each player's
**per-player presets** (slots 1..10, shown on the player card) and the
**global preset library** (indices 11+, alphabetical). Choose buttons
1..10 to drive that player's own slots, or 11+ to drive shared globals
— both can coexist on the same Player block.

### Single value picks the preset

Gira visualisation dropdown or slider:

```
5/0/40  Preset index   (DPT 5.010)
```

Wire `5/0/40` directly to `StartRadio`. Writing 0 is ignored. Writing
an out-of-range index emits `LastError = PRESET_NOT_FOUND`.

For string-based selection (visualisation text input or dropdown that
emits the preset name), wire to `StartRadioName` (DPT 16.001) instead.

## Multi-room: prefer Admin group presets

Define the group (master + members) once in the Admin web UI's *Group
presets* section. Trigger a group preset via `GroupPreset` or
`GroupPresetName` on any Player block — that block doesn't have to be
the master; the Admin definition decides. `Ungroup` on any member
puts it back into standalone mode.

This is preferred over wiring per-player KNX addresses in parallel
because the dispatch happens through Sonos's native zone-group
mechanism (slaves auto-mirror the master's audio).

## Configuration inputs (constant)

| Input | Default | Notes |
| --- | --- | --- |
| `Host`    | (required) | UUID preferred (`RINCON_xxx`). Falls back to MAC, name, or IPv4 — resolved via the Admin registry. |
| `VolStep` | 2          | Step for `VolUp` / `VolDown` / `VolUpDown` |

**Status poll interval, UPnP subscription timeout, HTTP timeout, and
the callback base URL are not inputs on the Player block.** They live
in the Sonos Admin web UI:

- **Project-wide defaults** in the *Player Defaults* section
  (`http://<hs-ip>:8080/`). These apply to every Player + Sound
  Enhancement block unless overridden.
- **Per-player overrides** in each player card's *Advanced overrides*
  collapsible. Leave a field empty to fall back to the project
  default. Useful for e.g. shortening the poll interval on a single
  speaker that needs faster feedback.

## Single-GA loopback (Volume → SetVolume, Mute → SetMute, …)

A common Gira QuadClient pattern is to bind a single KNX group
address to both the **set** input and the **status** output of the
same control — one address serves both directions of a slider /
switch widget. Without protection this creates a feedback loop:
the LBS broadcasts a status update, KNX echoes it back to the
matching input, the LBS dispatches SOAP again, Sonos echoes
another status… forever.

Both LBS 22000 and LBS 22002 break this loop on both sides:

- **Output (send-by-change)**: `Volume`, `Mute`, `State`,
  `ShuffleState`, `RepeatState`, `Bass`, `Treble`, `Loudness`,
  `Crossfade`, `LED`, `GroupVolume`, `NightMode`, `DialogMode`,
  `SurroundEnable`, `SurroundLevel`, `SubEnable`, `SubGain`,
  `Trueplay` — each only writes to `set_output` when the value
  actually changed from the last published value.
- **Input (echo suppression)**: `SetVolume`, `SetMute`, `SetShuffle`,
  `SetRepeat` (LBS 22000) and every value-driven `Set*` input on
  LBS 22002 (`SetBass`, `SetTreble`, `SetLoudness`, `SetNightMode`,
  `SetDialogMode`, `SetCrossfade`, `SetLED`, `SetGroupVolume`,
  `SetSurroundEnable`, `SetSurroundLevel`, `SetSubEnable`,
  `SetSubGain`, `SetTrueplay`) skip the SOAP dispatch when the
  requested value already matches the last known player state.

The two guards belt-and-braces: either alone would terminate the
loop, but together they also avoid wasting one redundant SOAP per
cycle. You can wire the same GA to both directions and the system
will behave correctly.

Trade-offs to be aware of:

- The `SetMute` / `MuteToggle` distinction still applies — see the
  Volume / Mute table above.
- `MuteToggle`, `PlayPause`, `NextPrev`, `VolUpDown` and the rising-
  edge buttons (`Play`, `Pause`, `Stop`, `Next`, `Prev`, `VolUp`,
  `VolDown`, `Ungroup`, `Resubscribe`) are commands, not state
  mirrors, so they're not loop-prone and have no suppression.
- A status output that hasn't been written yet (first cycle after
  download) won't broadcast its init value to KNX — the bus stays
  unchanged until the player produces a real reading. That's the
  intended SBC behaviour.

## Long text outputs — marquee scroll

The `Title`, `Artist`, `Album`, `ActiveStationName`, `ZoneName`,
`GroupInfo`, and `LastError` outputs route through a single helper
that can optionally scroll long values marquee-style. KNX text-field
widgets in Gira QuadClient typically have a fixed visible width;
when a track title is longer than that width, the integrator
historically had to truncate or accept clipping.

Configure the maximum visible length once in the Admin web UI's
*Player Defaults* (project-wide) or per-player in *Advanced
overrides*. Field name: **Max text length**. Behaviour:

- **0 (default)** — marquee disabled. Text is emitted at its full
  length; the visualisation handles overflow.
- **`N > 0`** — any output whose text exceeds N characters scrolls
  one character per second through the full value, with a three-
  space separator between repetitions. Texts at or below N
  characters emit as-is, no scrolling.

The new text changes mid-scroll (e.g. a track change) reset the
scroll position to 0 so the visualisation always reads the new
title from the start. SBC at the view level means KNX only sees a
broadcast when the visible substring actually changes.

Minimum window the Admin will accept is 4 characters so the
scrolling content stays readable. Set to 0 to fully disable.

## Verifying the wiring

After downloading the project to the HomeServer:

1. Open the *Sonos Player* node's debug page in Experte (F2 in some
   versions). You should see counters for "Notifies received", "Status
   polls", "Subscribe attempts", and the listener port.
2. Press a Play button on KNX — within ~1 s the `IsPlaying` output
   should flip to 1.
3. Change the volume on the Sonos app on a phone — within ~1 s the
   `Volume` KNX output should reflect the new value (driven by a
   NOTIFY, not a poll).
4. If NOTIFYs aren't arriving (e.g. firewall blocks the listener port,
   `Subscribed` stays at 0), the `Tick` poll fallback still updates
   outputs every `PollInterval` seconds — slower but functional.
