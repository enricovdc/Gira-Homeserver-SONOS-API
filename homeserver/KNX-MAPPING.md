# KNX group address mapping (recommended)

Group addresses are **not hard-coded** in the bridge or in this project —
they are wired in the HS Experte. The mapping below is a recommended
template; substitute your own free range.

Assume **main group 5 = Sonos**, with one middle group per player.

```
5/0/x   Livingroom   (player "livingroom")
5/1/x   Kitchen      (player "kitchen")
5/2/x   Bathroom     (player "bathroom")
```

## Per-player layout (replace `5/0` with your middle group)

### Inputs (KNX → bridge)

| GA | DPT | Direction | Action |
| --- | --- | --- | --- |
| 5/0/1 | 1.001 | in | Play (on rising edge) |
| 5/0/2 | 1.001 | in | Pause (on rising edge) |
| 5/0/3 | 1.001 | in | Stop |
| 5/0/4 | 1.001 | in | Next track |
| 5/0/5 | 1.001 | in | Previous track |
| 5/0/6 | 1.008 | in | Volume up/down (Up/Down decoded) |
| 5/0/7 | 5.001 | in | Volume set (0–100 % → 0–100) |
| 5/0/8 | 1.001 | in | Mute set |
| 5/0/9 | 1.001 | in | Mute toggle |
| 5/0/10 | 5.010 | in | Radio station index (writes start station) |
| 5/0/11 | 16.000 | in | Radio station name (writes start station) |
| 5/0/20 | 1.001 | in | Direct trigger: station 1 |
| 5/0/21 | 1.001 | in | Direct trigger: station 2 |
| 5/0/22 | 1.001 | in | Direct trigger: station 3 |
| 5/0/23 | 1.001 | in | Direct trigger: station 4 |

### Outputs (bridge → KNX, via status poll)

| GA | DPT | Direction | Value |
| --- | --- | --- | --- |
| 5/0/100 | 1.002 | out | Player online (true/false) |
| 5/0/101 | 16.000 | out | Playback state (`PLAYING` / `PAUSED_PLAYBACK` / `STOPPED` / `TRANSITIONING`) |
| 5/0/102 | 5.001 | out | Volume (0–100) |
| 5/0/103 | 1.001 | out | Mute |
| 5/0/104 | 16.000 | out | Current track title |
| 5/0/105 | 16.000 | out | Current track artist |
| 5/0/106 | 16.000 | out | Active station name |
| 5/0/107 | 5.010 | out | Active station index |
| 5/0/108 | 16.000 | out | Last error code (e.g. `PLAYER_UNREACHABLE`) |

## DPT notes

- **DPT 5.001** "Scaling" expresses 0–100 % as 0–255 internally; the
  HomeServer scales automatically. The bridge expects 0–100.
- **DPT 16.000** "Character string ISO-8859-1" is fine for ASCII track
  titles. For Unicode titles use DPT 16.001.
- **DPT 5.010** "Counter pulses 1 byte" is the most common 0–255 mapping
  used for "station index".
- For volume up/down via **DPT 1.008** (Up/Down), translate Up → `POST
  /volume/up`, Down → `POST /volume/down` in the logic block.

## One-button-per-station layout

If the wall plate has discrete physical buttons for stations:

```
5/0/20  Station 1   (DPT 1.001)
5/0/21  Station 2
5/0/22  Station 3
5/0/23  Station 4
...
```

Each is wired to its own HS logic block that issues
`POST /players/livingroom/radio/start` with the hard-coded body
`{"index": N}`.

## One-value-for-station layout

Alternative: a single value GA holds the desired station index.

```
5/0/10  Station index (DPT 5.010)
```

A single HS logic block listens for value changes and sends `{"index":
<value>}`. This pattern is preferred when stations are picked from a Gira
visualization element with a numeric value rather than a button row.

## Multi-room scenes

Scenes that should control several players (e.g. "morning music in kitchen
+ bathroom") are best implemented in HomeServer logic by triggering the
relevant per-player HTTP requests in parallel, rather than trying to model
Sonos zone groups inside KNX.
