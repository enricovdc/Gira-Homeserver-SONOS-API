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
| 5/0/6  | 1.001 | `MuteToggle`   | Rising edge inverts mute |

### Transport (value toggles for one-bit GAs)

These dispatch on every value change. Pair each with a single 1-bit
KNX group address whose flipping drives the action.

| GA | DPT | Input | Description |
| --- | --- | --- | --- |
| 5/0/10 | 1.001 | `PlayPause`      | 1 = Play, 0 = Pause |
| 5/0/11 | 1.001 | `NextPrev`       | 1 = Next track, 0 = Previous track |
| 5/0/12 | 1.001 | `PresetNextPrev` | 1 = Next preset, 0 = Previous preset (cycles Admin library, wraps) |

### Volume / Mute

| GA | DPT | Input | Description |
| --- | --- | --- | --- |
| 5/0/20 | 5.001 | `SetVolume`  | Absolute volume 0–100 % |
| 5/0/21 | 1.001 | `VolUp`      | Rising edge adds `VolStep` |
| 5/0/22 | 1.001 | `VolDown`    | Rising edge subtracts `VolStep` |
| 5/0/23 | 1.001 | `SetMute`    | 1 = mute, 0 = unmute |

For a DPT 1.008 "Up/Down" rocker, route Up to `VolUp` and Down to
`VolDown` with two small logic-block branches.

### Play mode (shuffle / repeat)

| GA | DPT | Input | Description |
| --- | --- | --- | --- |
| 5/0/30 | 1.001 | `SetShuffle` | 1 = shuffle on, 0 = off. Combined with `SetRepeat` into the Sonos PlayMode. |
| 5/0/31 | 1.001 | `SetRepeat`  | 1 = repeat-all on, 0 = off |

### Presets and sounds

| GA | DPT | Input | Description |
| --- | --- | --- | --- |
| 5/0/40 | 5.010 | `StartRadio`     | Alphabetical index 1..N of a preset in the Admin library |
| 5/0/41 | 16.001 | `StartRadioName` | Preset name (case-insensitive). Useful for a Gira visualisation dropdown. |
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
| 5/0/150 | 5.010  | `ActiveStation`     | Alphabetical index of the last preset started (0 = none). Regardless of whether the preset was selected by index, name, or `PresetNextPrev`. |
| 5/0/151 | 16.001 | `ActiveStationName` | Display name of the active preset |

### Group membership

| GA | DPT | Output | Value |
| --- | --- | --- | --- |
| 5/0/160 | 1.002  | `IsCoordinator` | 1 = standalone or this player is the group coordinator. 0 = this player is a slave in someone else's group. |
| 5/0/161 | 16.001 | `GroupInfo`     | When slave: master's Zone Name (resolved via Admin) or RINCON UUID. Empty when coordinator/standalone. |

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
index to `StartRadio`. The Admin's preset library is the single source
of truth; preset numbering is alphabetical and shown in the `#` column
of the Admin web UI.

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

## Configuration inputs (constant or Admin-default)

These are wired to *constant values* or to *internal data points* in
Experte, not to KNX group addresses. Setting them to 0/empty makes the
Player block read the Admin web UI's *Player Defaults* values.

| Input | Default | Notes |
| --- | --- | --- |
| `Host`         | (required) | UUID preferred (`RINCON_xxx`). Falls back to MAC, name, or IPv4 — resolved via the Admin registry. |
| `VolStep`      | 2          | Step for `VolUp` / `VolDown` |
| `PollInterval` | 60 s       | 0 → use Admin default |
| `SubTimeout`   | 1800 s     | 0 → use Admin default |
| `HttpTimeout`  | 5 s        | 0 → use Admin default |
| `CallbackBase` | ""         | Empty → use Admin default → auto-detected `http://<lan-ip>:<listener-port>` |

A common pattern is to bind the tunables to HS Experte data points
the integrator edits in the visualisation, so tuning happens at
runtime without re-opening Experte.

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
