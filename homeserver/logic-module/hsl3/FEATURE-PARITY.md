# Feature parity audit

This document records every user-facing function the integration ever
shipped, and where it lives in the HSL3 modules. Nothing was dropped
on the way to an HSL3-only delivery.

## Sonos control surface

| Function | HSL3 location | Notes |
| --- | --- | --- |
| Play | LBS 22000 input `Play` (E2) | Rising edge → `_action_play` |
| Pause | LBS 22000 input `Pause` (E3) | Rising edge → `_action_pause` |
| Stop | LBS 22000 input `Stop` (E4) | Rising edge → `_action_stop` |
| Next track | LBS 22000 input `Next` (E5) | Rising edge → `_action_next` |
| Previous track | LBS 22000 input `Prev` (E6) | Rising edge → `_action_previous` |
| Set absolute volume | LBS 22000 input `SetVolume` (E7) | Numeric value 0–100 |
| Volume up by step | LBS 22000 input `VolUp` (E8) | Reads current then adjusts; step from `VolStep` |
| Volume down by step | LBS 22000 input `VolDown` (E9) | Reads current then adjusts; step from `VolStep` |
| Set mute (on / off) | LBS 22000 input `SetMute` (E10) | Numeric 0/1 |
| Toggle mute | LBS 22000 input `MuteToggle` (E11) | Rising edge → read + invert + write |
| Start a configured radio station | LBS 22000 input `StartRadio` (E12) | Numeric index 1–8 |
| Force UPnP re-subscribe | LBS 22000 input `Resubscribe` (E13) | Rising edge clears SIDs + re-subscribes |

## Status / observability

| Function | HSL3 location | Notes |
| --- | --- | --- |
| Player online state | LBS 22000 output `Online` (A1) | 1 = recently responded |
| Playback state | LBS 22000 output `State` (A2) | PLAYING / PAUSED_PLAYBACK / STOPPED / TRANSITIONING |
| Current volume | LBS 22000 output `Volume` (A3) | 0–100 |
| Current mute | LBS 22000 output `Mute` (A4) | 0/1 |
| Current track title | LBS 22000 output `Title` (A5) | Falls back to `streamContent` for radio |
| Current artist | LBS 22000 output `Artist` (A6) | |
| Active radio station index | LBS 22000 output `ActiveStation` (A7) | 0 = none |
| Last error code | LBS 22000 output `LastError` (A8) | UPnP code or `UNREACHABLE` / `HTTP_<n>` / `STATION_<n>_NOT_CONFIGURED` |
| UPnP subscription health | LBS 22000 output `Subscribed` (A9) | 1 = both AVTransport + RenderingControl alive |

The bridge also produced a "live event stream" via Server-Sent Events
and an HTTP `/status` endpoint. In HSL3 the same information is
emitted to KNX outputs, which is the consumer the HomeServer is built
for — no separate HTTP layer is needed.

## Radio stations

| Function | HSL3 location | Notes |
| --- | --- | --- |
| Configure up to 8 stream URIs | LBS 22000 inputs `Station1Uri` … `Station8Uri` (E19–E26) | Each is a string input. Wire to constants or to a runtime data point. |
| Direct trigger per station (one button per station) | KNX → small Experte logic block → write index to `StartRadio` | See `homeserver/KNX-MAPPING.md` "One button per station" |
| Single value picks station (visualisation slider) | KNX/visualisation → `StartRadio` directly | See `homeserver/KNX-MAPPING.md` "One value for the station" |
| Firmware-2026 metadata fallback | `_action_start_radio` in `hsl3_22000_sonos_player.py` | x-rincon-mp3radio:// rewrite + retry on UPnP error codes 714/716/800 |
| List configured stations | n/a in HSL3 — Experte shows the inputs of the block | The bridge's `/stations` endpoint mapped to the same source of truth |

## Discovery

| Function | HSL3 location | Notes |
| --- | --- | --- |
| SSDP M-SEARCH | LBS 22001 (Sonos Discover) | Trigger input fires a multicast probe |
| Multi-target SSDP (2024+ firmware safe) | `SEARCH_TARGETS` in `hsl3_22001_sonos_discover.py` | ZonePlayer + SpeakerGroup + ssdp:all, filtered by `SERVER`/`USN` |
| Result list | LBS 22001 output `Result` | Newline-separated `ip;uuid;model` |
| Count | LBS 22001 output `Count` | |
| Error | LBS 22001 output `Error` | |

## UPnP event push (no polling)

| Function | HSL3 location | Notes |
| --- | --- | --- |
| Subscribe at startup | `_maintain_subscriptions` called from `_tick_work` | First Tick after `on_init` issues initial SUBSCRIBE |
| Auto-renew before timeout | Same | Renews when `(exp - now) < renew_threshold_s` |
| Re-bootstrap on HTTP 412 (SID expired after player reboot) | Same | Discards SID, falls through to initial SUBSCRIBE |
| Inbound NOTIFY listener | Shared class-level HTTP server, default port 8081 | `_ensure_listener_started`; falls back to next free port up to 8083 |
| NOTIFY routing by player | `_instances_by_host` registry keyed on host | Listener dispatches each NOTIFY to the right LogicModule instance |
| Parse LastChange envelope | `parse_notify` | Doubly-XML-decoded; extracts state, volume, mute, title, artist, streamContent, trackUri |
| Status poll fallback | `_tick_work` runs every `PollInterval` seconds | Bridges over missed NOTIFYs and detects offline players |

## Error handling

| Function | HSL3 location | Notes |
| --- | --- | --- |
| `PLAYER_UNREACHABLE` mapping | `_soap` catches `requests.exceptions.RequestException` → `UNREACHABLE` | Written to `LastError` |
| `SOAP_FAULT` parsing | `extract_soap_fault_code` pulls `<errorCode>` | UPnP code written as `LastError` |
| Metadata rejection fallback | `_action_start_radio` retries on codes 714/716/402/501/800 | |
| Worker-thread exceptions | `_run_safely` wraps every action, logs, writes `LastError = EXCEPTION:...` | |
| Listener thread crashes | `_NotifyHandler.do_NOTIFY` catches and returns 500 | Listener never dies on a malformed callback |

## Configuration

| Function | HSL3 location | Notes |
| --- | --- | --- |
| Per-player host | LBS 22000 input `Host` (E1) | Required |
| Volume step | LBS 22000 input `VolStep` (E14) | Default 2 |
| Status poll interval | LBS 22000 input `PollInterval` (E15) | Default 60 s |
| UPnP subscription timeout | LBS 22000 input `SubTimeout` (E16) | Default 1800 s |
| HTTP request timeout | LBS 22000 input `HttpTimeout` (E17) | Default 5 s |
| Callback base URL | LBS 22000 input `CallbackBase` (E18) | Leave empty for auto-detected `http://<lan-ip>:<listener-port>` |
| Runtime config edits | Wire any of the inputs above to HS data points that the visualisation can write | Equivalent to the bridge's runtime PUT /api/config |

## What is intentionally NOT in HSL3 (and why)

These were bridge-internal features whose purpose disappears when the
integration runs inside the HomeServer's own logic engine:

- **Standalone web admin UI** — not needed. The Experte logic editor +
  HomeServer visualisation **is** the admin UI in HSL3. Inputs and
  outputs are wired and edited natively.
- **HTTP REST API for external clients** — not needed. KNX, HS logic,
  visualisation, and Quad-Client are the consumers. None of them
  speak REST to a sidecar service.
- **Outbound webhook to a HS inbound URL** — not needed. The HSL3
  module **is** the HomeServer; it writes outputs directly. The
  bridge needed the webhook precisely because it was external.
- **Server-Sent Events stream** — not needed for the same reason.
- **Bearer-token auth** — not needed. There is no external HTTP
  surface to protect; KNX security and HS authentication already
  govern access to the logic and visualisation.

If any of these functions becomes useful again (e.g. integrating with
a non-HomeServer external system), they can be added as additional
LBS modules without changing LBS 22000 / 22001.
