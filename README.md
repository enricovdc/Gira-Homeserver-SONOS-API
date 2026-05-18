# Gira HomeServer Sonos API Bridge

A small HTTP bridge that lets a **Gira HomeServer** logic module control
**Sonos** players reliably over the local network. The HomeServer (or any KNX
logic that can issue an HTTP request action) calls simple REST endpoints on
this bridge; the bridge talks to the Sonos players via the local UPnP/SOAP
control API on port 1400.

This pattern avoids the Sonos Cloud Control API entirely (no OAuth, no
developer registration, no rate limits, no cloud dependency) while remaining
compatible with current and **2026** Sonos firmware generations.

---

## Why a bridge?

Gira HomeServer logic modules cannot easily speak UPnP/SOAP directly. The
HomeServer is comfortable issuing plain HTTP requests and parsing simple
responses. This project provides:

1. A **bridge HTTP service** (Node.js, zero runtime dependencies) that runs on
   any small Linux box, NAS, Raspberry Pi, or the HomeServer's companion
   server.
2. A set of **HomeServer logic module assets** — HTTP request templates,
   receive parser definitions, KNX group address mapping guidance — that the
   integrator imports into the HS Experte.

---

## Features

- Local-only operation (no cloud, no OAuth)
- Player and group selection via configuration
- Core playback control: play, pause, stop, next, previous
- Volume: set / up / down / mute / unmute / mute toggle
- Radio station playback by index or by name, with a fully configurable
  station list
- Aggregated status: online state, playback state, current track metadata,
  volume, mute, active radio station, current URI
- SSDP auto-discovery endpoint to find Sonos players on the LAN
- Graceful handling of offline players, malformed input, and Sonos SOAP faults
- Structured JSON logging with secret redaction
- Optional bearer-token auth for the bridge HTTP API
- Designed to work with **Sonos firmware 2026** (see *Firmware
  compatibility* below)
- Zero runtime npm dependencies; tests use the built-in `node --test` runner

---

## Quick start

### 1. Install Node.js 18+ on the host that will run the bridge

```sh
node --version   # must be >= 18
```

### 2. Clone & configure

```sh
git clone <this-repo>
cd Gira-Homeserver-SONOS-API
cp config.example.json config.json
$EDITOR config.json    # adjust players + radio stations
```

### 3. Discover your players (optional)

If you don't know the IP addresses of your Sonos players, run the bridge once
without players configured and call the discover endpoint:

```sh
SONOS_BRIDGE_CONFIG=config.json node src/server.js &
curl http://localhost:8080/players/discover
```

Copy the resulting `host` / `uuid` values into `config.json` under
`players[]`.

### 4. Run

```sh
npm start
```

### 5. Test from a terminal

```sh
curl -X POST http://localhost:8080/players/livingroom/play
curl -X POST http://localhost:8080/players/livingroom/volume -d '{"level":30}' -H 'Content-Type: application/json'
curl -X POST http://localhost:8080/players/livingroom/radio/start -d '{"index":1}' -H 'Content-Type: application/json'
curl       http://localhost:8080/players/livingroom/status
```

### 6. Wire up the Gira HomeServer

See [homeserver/SETUP.md](homeserver/SETUP.md) for HS Experte configuration,
[homeserver/REQUESTS.md](homeserver/REQUESTS.md) for HTTP request templates,
and [homeserver/KNX-MAPPING.md](homeserver/KNX-MAPPING.md) for KNX group
address mapping.

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
  server.js             HTTP server, routing, request validation
  config.js             JSON config loader + validator
  players.js            Player registry (name → SonosClient)
  radio.js              Radio station store
  logger.js             Structured JSON logger
  sonos/
    soap.js             SOAP envelope build/parse, HTTP transport
    client.js           High-level Sonos commands + metadata fallback
    discovery.js        SSDP discovery (multi-target)
test/
  *.test.js             node --test suites
  helpers/mock-sonos.js Mocked Sonos SOAP transport
homeserver/
  SETUP.md              HS Experte setup
  REQUESTS.md           HTTP request templates
  KNX-MAPPING.md        KNX group address mapping guidance
docs/
  TROUBLESHOOTING.md
  ARCHITECTURE.md
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
