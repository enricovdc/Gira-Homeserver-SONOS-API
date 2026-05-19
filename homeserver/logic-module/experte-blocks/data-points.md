# Data points to create in the Experte

Create these as *Kommunikationsobjekte → Datenpunkte* in the HS Experte.
All are HomeServer-internal (not necessarily KNX-bound), though many
will be bound to KNX group addresses for status output (see
`logic-blocks.md` → "Status output bindings").

## Per-player (repeat for each player)

Replace `<name>` with a short identifier like `livingroom`, `kitchen`.

| Name                          | Type     | Initial | Notes |
| ----------------------------- | -------- | ------- | ----- |
| `sonos.<name>.host`           | string   | (empty) | LAN IP — set via config page |
| `sonos.<name>.state`          | string   | `STOPPED` | Playback state |
| `sonos.<name>.volume`         | integer  | `0`     | 0–100 |
| `sonos.<name>.mute`           | boolean  | `false` | |
| `sonos.<name>.title`          | string   | (empty) | Current track title |
| `sonos.<name>.artist`         | string   | (empty) | |
| `sonos.<name>.streamContent`  | string   | (empty) | Live radio metadata |
| `sonos.<name>.trackUri`       | string   | (empty) | Current track URI |
| `sonos.<name>.activeStation`  | integer  | `0`     | Last started station index |
| `sonos.<name>.online`         | boolean  | `false` | Heartbeat |
| `sonos.<name>.sid_av`         | string   | (empty) | AVTransport subscription SID |
| `sonos.<name>.sid_rc`         | string   | (empty) | RenderingControl subscription SID |
| `sonos.<name>.lastError`      | string   | (empty) | UPnP error code |
| `sonos.<name>.lastErrorMsg`   | string   | (empty) | UPnP error description |

## Per-station (repeat 1..N for up to N stations)

| Name                          | Type     | Initial | Notes |
| ----------------------------- | -------- | ------- | ----- |
| `sonos.station.<n>.name`      | string   | (empty) | Display name |
| `sonos.station.<n>.uri`       | string   | (empty) | Stream URL. Preferably `x-rincon-mp3radio://host/path`; the SOAP template will accept either form. |

> **Tip**: pre-create slots for 8–16 stations even if you only fill in 2
> initially. Adding more data points later requires re-downloading the
> project from the Experte; pre-allocating avoids that.

## Global

| Name                          | Type     | Initial | Notes |
| ----------------------------- | -------- | ------- | ----- |
| `sonos.callbackBase`          | string   | (empty) | `http://<homeserver-ip>` — used in SUBSCRIBE `CALLBACK` header |
| `sonos.diag.lastNotifyAt`     | string   | (empty) | ISO timestamp of last received NOTIFY |
| `sonos.diag.subscribedCount`  | integer  | `0`     | How many subscriptions are currently healthy |
| `sonos.config.pollIntervalS`  | integer  | `60`    | Status-poll interval, for the watchdog timer |
| `sonos.config.renewIntervalS` | integer  | `1500`  | UPnP renew interval (≤ 1800) |

The visualisation config page (`visualisation/config-page.html`) reads
and writes most of these so end users don't have to touch the Experte.

## Persistence

Mark every data point above as **persistent** so values survive a
HomeServer reboot. The Experte's data-point editor has a *Persistent*
checkbox. Forgetting it means players and stations have to be
re-entered after every power cycle.

## Naming convention

The `sonos.<name>.*` and `sonos.station.<n>.*` namespaces are referenced
by every action, logic block, receive parser, and visualisation field in
this directory. If you choose a different convention, search and replace
across all of `homeserver/logic-module/` before importing — the
configuration page in particular hard-codes the prefix.
