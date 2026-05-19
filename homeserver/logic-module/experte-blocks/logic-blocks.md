# Logic-block wiring (KNX → action)

Each row below is one logic block ("Logikbaustein") in the HS Experte.
The block is triggered by an input (KNX GA, value change on a data
point, or timer) and runs one or more actions.

GAs use `5/0/x` as a placeholder — substitute your own free range. See
`homeserver/KNX-MAPPING.md` for the recommended layout.

## Per-player control bindings

| Logic block name | Trigger | Action |
| --- | --- | --- |
| `sonos.<name>.lb.play` | KNX `5/0/1` rising edge | `sonos.<name>.play` |
| `sonos.<name>.lb.pause` | KNX `5/0/2` rising edge | `sonos.<name>.pause` |
| `sonos.<name>.lb.stop` | KNX `5/0/3` rising edge | `sonos.<name>.stop` |
| `sonos.<name>.lb.next` | KNX `5/0/4` rising edge | `sonos.<name>.next` |
| `sonos.<name>.lb.previous` | KNX `5/0/5` rising edge | `sonos.<name>.previous` |
| `sonos.<name>.lb.volume` | KNX `5/0/7` value change | `sonos.<name>.setVolume` (substitute `{level}` with the KNX value) |
| `sonos.<name>.lb.mute` | KNX `5/0/8` value change | `sonos.<name>.setMute` (substitute `{mute}` with `1`/`0`) |
| `sonos.<name>.lb.muteToggle` | KNX `5/0/9` rising edge | Read `sonos.<name>.mute`, compute `!mute`, then `sonos.<name>.setMute` |
| `sonos.<name>.lb.volumeUp` | KNX `5/0/6` rising edge | See volume up/down note below |
| `sonos.<name>.lb.volumeDown` | KNX `5/0/6` falling edge | Same with `step = -2` |

### Volume up/down sequence

The logic block reads the current `sonos.<name>.volume` data point,
applies the step, clamps, then triggers `sonos.<name>.setVolume`:

```
new := clamp(sonos.<name>.volume + step, 0, 100)
sonos.<name>.setVolume(level = new)
```

If your Experte version doesn't support arithmetic in a logic block,
chain: KNX trigger → `sonos.<name>.getVolume` (fills data point) →
formula step → `sonos.<name>.setVolume`. The chained version adds ~150 ms
of latency but works on every HS version.

## Radio station bindings

### One-button-per-station layout

| Logic block name | Trigger | Action |
| --- | --- | --- |
| `sonos.<name>.lb.station1` | KNX `5/0/20` rising edge | Sequence: set `tmp.uri = sonos.station.1.uri`; `sonos.<name>.startRadio`; `sonos.<name>.play` |
| `sonos.<name>.lb.station2` | KNX `5/0/21` rising edge | Same with station 2 |
| `sonos.<name>.lb.station3` | KNX `5/0/22` rising edge | Same with station 3 |
| `sonos.<name>.lb.station4` | KNX `5/0/23` rising edge | Same with station 4 |

In each block, also write the station index into
`sonos.<name>.activeStation` so the visualisation reflects it.

### One-value-for-station layout

| Logic block name | Trigger | Action |
| --- | --- | --- |
| `sonos.<name>.lb.stationByIndex` | KNX `5/0/10` value change | Read the new index, look up `sonos.station.<i>.uri`, run `sonos.<name>.startRadio`, then `sonos.<name>.play`, write `sonos.<name>.activeStation = <i>` |

Implement the lookup with a switch in the Experte's formula step, or
with one branch per station index if the Experte's logic editor doesn't
support indirect data-point reads.

## Status output bindings (KNX outputs)

These are not separate logic blocks — bind each data point directly to
the matching KNX group address with the appropriate DPT. Whenever the
data point changes (driven by a NOTIFY or a poll), KNX gets the value.

| Data point | DPT | KNX GA |
| --- | --- | --- |
| `sonos.<name>.online` | 1.002 | 5/0/100 |
| `sonos.<name>.state`  | 16.000 | 5/0/101 |
| `sonos.<name>.volume` | 5.001 | 5/0/102 |
| `sonos.<name>.mute`   | 1.001 | 5/0/103 |
| `sonos.<name>.title`  | 16.000 | 5/0/104 |
| `sonos.<name>.artist` | 16.000 | 5/0/105 |
| `sonos.<name>.activeStation` | 5.010 | 5/0/107 |
| `sonos.<name>.lastError` | 16.000 | 5/0/108 |

See `homeserver/KNX-MAPPING.md` for the full layout.
