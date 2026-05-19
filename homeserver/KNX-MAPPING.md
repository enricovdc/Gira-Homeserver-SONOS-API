# KNX group address mapping (recommended)

Group addresses are **not hard-coded** anywhere in the LBS modules — they
are wired in the HS Experte by connecting KNX group addresses to the
inputs and outputs of each *Sonos Player* (LBS 22000) instance. The
table below is a recommended template; substitute your own free range.

Assume **main group 5 = Sonos**, with one middle group per player:

```
5/0/x   Livingroom   (one Sonos Player instance with Host=192.168.1.50)
5/1/x   Kitchen      (one Sonos Player instance with Host=192.168.1.51)
5/2/x   Bathroom     (one Sonos Player instance with Host=192.168.1.52)
```

## Per-player layout (replace `5/0` with your middle group)

### Inputs — KNX → LBS 22000 input ports

Wire each KNX group address to the matching input on the Sonos Player
block in the Experte. The DPT is what KNX uses on the wire; the value
arrives at the LBS as a `number` or `string` per the input declaration
in `config.json`.

| GA | DPT | LBS input | Description |
| --- | --- | --- | --- |
| 5/0/1 | 1.001 | `Play` | Rising edge starts / resumes playback |
| 5/0/2 | 1.001 | `Pause` | Rising edge pauses |
| 5/0/3 | 1.001 | `Stop` | Rising edge stops |
| 5/0/4 | 1.001 | `Next` | Next track |
| 5/0/5 | 1.001 | `Prev` | Previous track |
| 5/0/6 | 1.008 | `VolUp` / `VolDown` | Up/Down on DPT 1.008: route Up to `VolUp`, Down to `VolDown` (two logic-block branches) |
| 5/0/7 | 5.001 | `SetVolume` | Absolute volume 0–100 % |
| 5/0/8 | 1.001 | `SetMute` | Mute on/off |
| 5/0/9 | 1.001 | `MuteToggle` | Rising edge toggles mute |
| 5/0/10 | 5.010 | `StartRadio` | Station index 1–8 starts that station |
| 5/0/11 | 1.001 | `Resubscribe` | Rising edge forces UPnP re-subscribe (diagnostics) |

### Outputs — LBS 22000 output ports → KNX

Wire each LBS output directly to the matching KNX group address.
Whenever the output changes (driven by NOTIFY or by `Tick` poll), the
LBS sends to KNX with the configured DPT.

| GA | DPT | LBS output | Value |
| --- | --- | --- | --- |
| 5/0/100 | 1.002 | `Online` | 1 = player responded recently, 0 = offline |
| 5/0/101 | 16.000 | `State` | `PLAYING` / `PAUSED_PLAYBACK` / `STOPPED` / `TRANSITIONING` |
| 5/0/102 | 5.001 | `Volume` | 0–100 |
| 5/0/103 | 1.001 | `Mute` | 1 = muted |
| 5/0/104 | 16.000 | `Title` | Current track title or `streamContent` for radio |
| 5/0/105 | 16.000 | `Artist` | Current artist |
| 5/0/106 | 5.010 | `ActiveStation` | Index of last started station (1–8, 0 = none) |
| 5/0/107 | 16.000 | `LastError` | Last error code (`UNREACHABLE`, UPnP fault code, …) |
| 5/0/108 | 1.002 | `Subscribed` | 1 = UPnP subscriptions healthy for both AVTransport + RenderingControl |

## DPT notes

- **DPT 5.001** "Scaling" represents 0–100 % as 0–255 on the wire. The
  HS Experte handles the conversion in both directions; the LBS sees
  the value as 0–100.
- **DPT 16.000** "Character string (ISO-8859-1)" is appropriate for
  ASCII track titles. For accented characters use **DPT 16.001**
  (ISO-10646 / UTF-8) on KNX with a supporting display. The LBS
  outputs the string as iso-8859-15 bytes per the HSL3 SDK rule, which
  Experte converts to the chosen DPT format.
- **DPT 5.010** "Counter pulses (1 byte)" is the standard 0–255 mapping
  for "station index". Eight stations (1–8) easily fit.
- **DPT 1.008** "Up/Down": split into two branches in the Experte —
  Up → `VolUp` input, Down → `VolDown` input.

## Radio: button per station vs single value

### One button per station

If wall plates have discrete physical buttons:

```
5/0/20  Station 1   (DPT 1.001)
5/0/21  Station 2
5/0/22  Station 3
5/0/23  Station 4
...
```

In the Experte, each button triggers a small logic block that writes
the station index (1, 2, 3, …) to the `StartRadio` input of the Sonos
Player block. The LBS handles the rest (`SetAVTransportURI` + `Play`
with the firmware-2026-resilient metadata-fallback ladder).

### One value for the station

Alternative when the trigger is a Gira visualisation numeric value:

```
5/0/10  Station index (DPT 5.010)
```

Wire `5/0/10` directly to the `StartRadio` input of the Sonos Player
block. Writing 0 is ignored; 1–8 starts the configured station; 9+
emits `LastError = STATION_9_NOT_CONFIGURED`.

## Multi-room scenes

Scenes that should control several players (e.g. "morning music in
kitchen + bathroom") are best implemented in HomeServer logic by
writing to the relevant per-player KNX group addresses in parallel.
The LBS does **not** try to model Sonos zone groups; each LBS instance
controls one player. If you want grouped output, control the *group
coordinator* via its Host and let the Sonos group itself propagate.

## Configuration inputs (set once, not via KNX)

These are wired to *constant values* or *internal data points* in the
Experte rather than to KNX group addresses — they're configuration,
not runtime control:

- `Station1Uri … Station8Uri` — radio station stream URIs
- `VolStep` — step size for `VolUp` / `VolDown` (default 2)
- `PollInterval` — Tick interval in seconds (default 60)
- `SubTimeout` — UPnP subscription timeout requested from the player
  (default 1800)
- `HttpTimeout` — HTTP request timeout in seconds (default 5)
- `CallbackBase` — leave empty for auto-detected `http://<hs-ip>:8081`

A common pattern is to bind these to HS Experte string/numeric data
points that the integrator edits in the visualisation, so end users
can tune intervals or change station URIs at runtime without opening
the Experte.

## Verifying the wiring

After downloading the project to the HomeServer:

1. Open the *Sonos Player* node's debug page in Experte (F2 in some
   versions). You should see counters for "Notifies received", "Status
   polls", "Subscribe attempts", and the listener port.
2. Press a play button on KNX — within ~1 s the `State` output should
   flip to `PLAYING`.
3. Change the volume on the Sonos app on a phone — within ~1 s the
   `Volume` KNX output should reflect the new value (driven by a NOTIFY,
   not a poll).
4. If NOTIFYs aren't arriving (e.g. firewall blocks the listener port),
   the `Tick` poll fallback still updates outputs every `PollInterval`
   seconds — slower but functional.
