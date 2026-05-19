# Gira HomeServer Sonos API Bridge

A Sonos integration for the **Gira HomeServer**. The bridge runs **on the
HomeServer itself** as a systemd service — no separate machine, no extra
hardware. It exposes:

- a simple HTTP API the HomeServer logic calls for play/pause/volume/radio
  control,
- a built-in **web configuration page** at `http://<homeserver>:8080/` where
  players, radio stations, and the optional cloud/webhook integration are
  managed at runtime,
- a **UPnP event subscription** that pushes Sonos state changes (volume
  knob turned, app paused playback, etc.) into the HomeServer without
  polling — either via Server-Sent Events or an outbound webhook.

No OAuth. No Sonos developer registration. No cloud dependency. Compatible
with current and **2026** Sonos firmware generations.

---

## Why a bridge?

Gira HomeServer logic modules cannot speak UPnP/SOAP, run SSDP, or accept
inbound UPnP NOTIFY callbacks. The HomeServer is comfortable issuing plain
HTTP requests, accepting inbound POSTs on its own endpoints, and parsing
simple responses. This project provides:

1. A **bridge HTTP service** (Node.js 18+, zero npm dependencies) that runs
   directly on the HomeServer's Linux OS as a systemd service. See
   [homeserver/install/install.sh](homeserver/install/install.sh).
2. A built-in **web configuration page** served at the bridge root URL so
   the entire integration is managed from the browser — no editing JSON
   files by hand. The page can be linked from the HomeServer visualisation
   or embedded in an iframe.
3. A set of **HomeServer logic module assets** — HTTP request templates,
   receive parser definitions, KNX group address mapping guidance — that
   the integrator imports into the HS Experte.

---

## Features

- **Runs on the HomeServer itself** (systemd service, single device)
- **Web configuration UI** at `http://<homeserver>:8080/` — players, radio
  stations, webhook, cloud, all editable from the browser, no file edits
- **UPnP event push** (subscribes to AVTransport + RenderingControl, handles
  NOTIFY callbacks, renews subscriptions automatically)
- **Outbound webhook** to push state changes to a HomeServer inbound URL,
  enabling event-driven KNX updates without polling
- **Server-Sent Events** stream at `/events` for live state in the admin UI
  or any other consumer
- `/info` endpoint that returns the bridge URL — useful for surfacing the
  config-page URL on the HomeServer homepage / debug page
- Local-only operation (no cloud, no OAuth); optional Sonos Cloud Control
  API fallback is wired into the config UI
- Player selection via configuration; SSDP auto-discovery on the LAN
- Core playback: play, pause, stop, next, previous
- Volume: set / up / down / mute / unmute / mute toggle
- Radio station playback by index or by name, fully configurable
- Aggregated status: online state, playback state, current track metadata,
  volume, mute, active radio station, current URI
- Graceful handling of offline players, malformed input, and Sonos SOAP faults
- Structured JSON logging with secret redaction
- Optional bearer-token auth for the bridge HTTP API
- Designed to work with **Sonos firmware 2026** (see *Firmware
  compatibility* below)
- Zero runtime npm dependencies; tests use the built-in `node --test` runner

---

## Install modes

| Mode | What you import | Effort | Features |
| --- | --- | --- | --- |
| **A. HSL3 LBS modules** (recommended) | `22000_sonos_player.hslz` + `22001_sonos_discover.hslz` | Import in Experte. No SSH, no companion machine. | Full integration: control, radio, status, UPnP event push, SSDP discovery |
| **B. Node.js bridge on the HomeServer** (legacy) | This repo + `install.sh` over SSH | Run installer once on the HS Linux | Same features as A, plus a standalone web admin UI |

Mode A is the canonical path. Two native HSL3 / Python 3.9 logic modules
that run inside the HomeServer's own logic engine on firmware 4.13+. See
[homeserver/logic-module/hsl3/README.md](homeserver/logic-module/hsl3/README.md).

Mode B remains for HomeServer firmware <4.13 or for users who specifically
want the bridge's web admin UI. See `homeserver/install/install.sh`.

## Mode C — SSH systemd install

SSH into the HomeServer as root and run:

```sh
git clone <this-repo> /tmp/sonos-bridge
sh /tmp/sonos-bridge/homeserver/install/install.sh
```

The installer:

- creates the system user `sonos-bridge`,
- copies the source to `/opt/sonos-bridge`,
- writes `/etc/sonos-bridge/config.json` from the example,
- installs and starts the systemd service.

Logs: `journalctl -u sonos-bridge -f`.

### Open the configuration page

In a browser, open `http://<homeserver-ip>:8080/`. From there you can:

- add / remove Sonos players (or click **Discover** to auto-find them via SSDP),
- add / remove radio stations,
- configure the webhook URL the bridge POSTs state changes to,
- enable the optional Sonos Cloud Control API integration,
- watch the live event stream coming in from UPnP subscriptions.

### Surface the URL on the HomeServer

See [homeserver/HOMEPAGE.md](homeserver/HOMEPAGE.md) for how to add the
configuration page URL to the HomeServer visualisation as a tile or
iframe, and how to use `/info` to dynamically display the URL on a wall
panel or debug page.

### Wire up KNX

See [homeserver/SETUP.md](homeserver/SETUP.md) for HS Experte configuration,
[homeserver/REQUESTS.md](homeserver/REQUESTS.md) for HTTP request templates,
and [homeserver/KNX-MAPPING.md](homeserver/KNX-MAPPING.md) for KNX group
address mapping.

### Or run elsewhere (developer mode)

```sh
git clone <this-repo>
cd Gira-Homeserver-SONOS-API
cp config.example.json config.json
npm start
```

Then open `http://localhost:8080/`.

---

## Configuration

All configuration lives in a single JSON file (default `config.json`). See
`config.example.json` for a complete example.

| Key | Type | Notes |
| --- | --- | --- |
| `server.host` | string | Bind address. Use `0.0.0.0` for LAN access. |
| `server.port` | integer | HTTP port (default `8080`). |
| `server.authToken` | string | Optional bearer token; empty disables auth. |
| `discovery.enabled` | boolean | Reserved for periodic SSDP refresh. |
| `discovery.timeoutMs` | integer | Discovery wait time (default `4000`). |
| `players[]` | array | Configured Sonos players (see below). |
| `players[].name` | string | Logical name used in URLs / KNX bindings. |
| `players[].host` | string | LAN IP of the Sonos player. |
| `players[].uuid` | string | Optional; only used for diagnostics. |
| `defaultPlayer` | string | Name used when a request omits `?player=`. |
| `radioStations[]` | array | Configured stations (see below). |
| `radioStations[].index` | integer | Stable index used by KNX triggers. |
| `radioStations[].name` | string | Display name, case-insensitive lookup. |
| `radioStations[].streamUri` | string | Direct stream URL (`http://…`) or `x-rincon-mp3radio://…`. |
| `radioStations[].metadata.title` | string | Optional title sent to the player. |
| `status.pollIntervalMs` | integer | Reserved for future event push. |
| `logging.level` | string | `error` / `warn` / `info` / `debug`. |

### Credentials & secrets

- The bridge talks to Sonos over the LAN — **no Sonos credentials are
  required**.
- The bridge's own `server.authToken` is the only secret. Keep `config.json`
  out of version control (the included `.gitignore` already does this).
- Never commit `config.json`, only `config.example.json`.

---

## HTTP API

| Method | Path | Body | Description |
| --- | --- | --- | --- |
| GET | `/` | – | Web configuration UI (HTML) |
| GET | `/info` | – | Bridge metadata incl. URL — used by HS homepage |
| GET | `/events` | – | Server-Sent Events stream of state changes |
| NOTIFY | `/upnp/event/:player/:service` | UPnP XML | Sonos pushes events here |
| GET | `/api/config` | – | Current config (secrets redacted) |
| PUT | `/api/config` | partial cfg | Patch + persist config |
| GET | `/api/players/full` | – | Players + live state from cache |
| POST | `/api/players` | `{name,host,...}` | Add a player and persist |
| DELETE | `/api/players/:name` | – | Remove a player and persist |
| POST | `/api/stations` | `{index,name,streamUri}` | Add a radio station |
| DELETE | `/api/stations/:index` | – | Remove a radio station |
| POST | `/api/webhook/test` | – | Send a test event to the webhook URL |
| POST | `/api/events/resubscribe` | – | Force UPnP re-subscribe to all players |
| GET | `/health` | – | Bridge liveness + player count |
| GET | `/players` | – | List configured players |
| GET | `/players/discover` | – | SSDP discovery (returns Sonos units on LAN) |
| GET | `/players/:name/status` | – | Aggregated status for one player |
| POST | `/players/:name/play` | – | Resume / start playback |
| POST | `/players/:name/pause` | – | Pause playback |
| POST | `/players/:name/stop` | – | Stop playback |
| POST | `/players/:name/next` | – | Skip to next track |
| POST | `/players/:name/previous` | – | Skip to previous track |
| POST | `/players/:name/volume` | `{level:0-100}` | Set absolute volume |
| POST | `/players/:name/volume/up` | `{step?:int}` | Increment (default step 2) |
| POST | `/players/:name/volume/down` | `{step?:int}` | Decrement |
| POST | `/players/:name/mute` | `{mute?:bool}` | Set mute (defaults to true) |
| POST | `/players/:name/unmute` | – | Unmute |
| POST | `/players/:name/mute/toggle` | – | Toggle mute |
| GET | `/stations` | – | List configured radio stations |
| POST | `/players/:name/radio/start` | `{index?:int, name?:str}` | Start a station |
| POST | `/players/:name/radio/index` | `{index:int}` | Start by index |
| POST | `/players/:name/radio/name` | `{name:str}` | Start by name |
| GET | `/status?player=:name` | – | Same as `/players/:name/status` |

All responses are JSON with `ok: true|false`. Errors include `error`
(machine code) and `message` (human description).

### Error codes

| Code | HTTP | Meaning |
| --- | --- | --- |
| `UNAUTHORIZED` | 401 | Missing or wrong bearer token |
| `NOT_FOUND` | 404 | Unknown route |
| `PLAYER_NOT_FOUND` | 404 | No configured player with that name |
| `STATION_NOT_FOUND` | 404 | Unknown station index/name |
| `INVALID_ARG` | 400 | Bad input (e.g. volume out of range) |
| `PLAYER_UNREACHABLE` | 503 | Player offline / network unreachable |
| `SOAP_FAULT` | 502 | Sonos rejected the SOAP call (UPnP error) |
| `INTERNAL` | 500 | Unexpected error (logged) |

---

## Firmware compatibility

This bridge is designed to keep working across Sonos firmware updates, in
particular the 2024-and-later wave that more strictly validates DIDL-Lite
metadata and gates SMAPI cloud-service handoffs.

Key compatibility decisions:

1. **No SMAPI binding in radio metadata.** The DIDL-Lite envelope sent with
   `SetAVTransportURI` does **not** contain a `<desc>` element pointing at a
   SMAPI service (e.g. the `SA_RINCON65031_` TuneIn handoff that many older
   integrations use). Direct streams play directly; the player does not need
   to consult a cloud service.
2. **Direct-broadcast URI scheme.** HTTP stream URLs are rewritten to
   `x-rincon-mp3radio://` so the player treats them as continuous broadcast
   audio.
3. **Metadata fallback.** If the player rejects rich metadata with a UPnP
   error in the 714 / 716 / 800 family (common after a firmware tightening),
   the bridge automatically retries with empty metadata and finally with the
   raw URI. See `src/sonos/client.js` → `playStreamUri`.
4. **Multi-target SSDP discovery.** Discovery probes
   `urn:schemas-upnp-org:device:ZonePlayer:1`,
   `urn:smartspeaker-audio:service:SpeakerGroup:1`, and `ssdp:all`, then
   filters by `SERVER`/`USN` containing `sonos`/`rincon`. This survives
   advertising-format changes.
5. **No reliance on legacy queue-management endpoints.** Core actions use
   only the long-stable `AVTransport` and `RenderingControl` services.

If a future firmware does remove local SOAP control entirely, the only path
forward will be the Sonos Cloud Control API (OAuth + developer registration).
The current architecture isolates Sonos-specific code in `src/sonos/` so a
cloud adapter can be added behind the same `SonosClient` interface without
touching the HTTP routes or the HomeServer integration.

---

## Testing

```sh
npm test          # 43+ tests, ~250ms
npm run test:watch
npm run lint
```

Tests cover:

- SOAP envelope build / parse, fault handling
- Volume clamping, mute toggling, status aggregation
- Radio station lookup (by index / name, case-insensitive)
- Configuration validation (missing fields, duplicates, bad defaults)
- HTTP routes (auth, success, offline player, unknown station)
- Metadata-fallback path for 2024+ firmware

Tests use a mocked SOAP transport — no Sonos hardware needed.

---

## Project layout

```
src/
  server.js              HTTP server, routing, NOTIFY handler, SSE, auth
  config.js              JSON config loader + validator
  persist.js             Atomic config write-back
  players.js             Player registry (name → SonosClient)
  radio.js               Radio station store
  state.js               In-memory state cache, event emitter
  webhook.js             Outbound webhook publisher
  admin-ui.js            Single-file vanilla JS configuration UI
  logger.js              Structured JSON logger
  sonos/
    soap.js              SOAP envelope build/parse, HTTP transport
    client.js            High-level Sonos commands + metadata fallback
    discovery.js         SSDP discovery (multi-target)
    events.js            UPnP SUBSCRIBE/RENEW/NOTIFY parsing
test/
  *.test.js              node --test suites (61 tests)
  helpers/mock-sonos.js  Mocked Sonos SOAP transport
homeserver/
  install/
    install.sh           Installer for HomeServer-hosted deployment
    sonos-bridge.service systemd unit
  SETUP.md               HS Experte setup
  REQUESTS.md            HTTP request templates
  KNX-MAPPING.md         KNX group address mapping guidance
  HOMEPAGE.md            How to surface the bridge URL on the HS homepage
docs/
  TROUBLESHOOTING.md
  ARCHITECTURE.md
  MANUAL-VERIFICATION.md
config.example.json
```

---

## Troubleshooting

See [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md).

## Known limitations

- The MVP does **not** subscribe to UPnP events; status is fetched on
  request. KNX status outputs that need to reflect external changes (e.g.
  user used the Sonos app) should poll `/status` on a HomeServer timer.
- Queue editing, Sonos favorites enumeration, and Sonos playlist start are
  not implemented (out of MVP scope).
- Group/zone management is not exposed; each configured player is controlled
  individually. Grouped players will reflect the group coordinator's state.
- Spotify / Apple Music / Amazon Music account-bound playback requires SMAPI
  service IDs and account tokens that are out of scope. Use Sonos favorites
  via the Sonos app and trigger them externally if needed.

## License

MIT
