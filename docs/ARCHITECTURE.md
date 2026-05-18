# Architecture

## Decisions and trade-offs

### 1. Local UPnP/SOAP, not the Sonos Cloud Control API

The Sonos Cloud Control API requires:

- An OAuth 2.0 flow with a registered developer application
- Token storage and refresh logic
- Cloud reachability from the host
- Compliance with Sonos's developer terms

For a HomeServer / KNX integration this is excessive. The local SOAP API on
port 1400 is the same API the Sonos app uses for many operations, has no
rate limits, no cloud dependency, and is the API behind every successful
third-party integration (Home Assistant, node-sonos, SoCo, …).

This bridge is structured so that a future cloud adapter could be slotted
behind the same `SonosClient` interface if local SOAP is ever fully removed.

### 2. Bridge process, not a HomeServer .so logic module

Gira HomeServer 4/5 supports compiled C/C++ logic modules, but:

- They are platform-specific (architecture, libc version).
- UPnP/SOAP from inside the HomeServer process is awkward (HTTP client +
  long-running socket management inside a logic block).
- They are hard to test in isolation.

Running a Node.js HTTP bridge as a small service on a neighbouring Linux
host (NAS, Raspberry Pi, or any always-on machine) is simpler, more
portable, and matches the established pattern from similar Sonos
integrations (`node-sonos-http-api` etc).

### 3. Zero runtime dependencies

The bridge uses only Node.js built-ins (`http`, `dgram`, `fs`). No
`node_modules` is installed in production. This keeps the deployment surface
tiny and removes the supply-chain risk of pulling third-party Sonos
libraries.

### 4. Stateless HTTP, status on demand

The bridge does not subscribe to UPnP events for the MVP. The HomeServer
polls `/status` from a timer. Trade-off: external state changes (someone
using the Sonos app) only reach KNX on the next poll. Benefit: dramatically
simpler operationally — no long-lived subscriptions, no callback URLs to
configure on the HomeServer's IP.

If event subscription is needed later, the natural extension point is
`src/sonos/events.js` issuing UPnP `SUBSCRIBE` requests and a long-poll
endpoint the HomeServer can call.

### 5. Radio = configured streams, not Sonos favorites enumeration

Reading "Sonos Favorites" requires `ContentDirectory#Browse` plus careful
DIDL-Lite parsing per item type, with separate paths for SMAPI-bound items
(Spotify, TuneIn) vs direct streams. That is fragile across firmware
generations.

Instead, the bridge takes a simple `radioStations[]` config array of
**direct stream URIs**. The KNX integrator picks the streams once, by hand,
from the Sonos app's "Information" panel for each station they want to
expose. The bridge then plays them directly — no cloud handoff, immune to
SMAPI gating changes, indexed by an integer that maps cleanly onto KNX
button rows.

## File layout

```
src/
  server.js        HTTP server + routing + auth + body parsing
  config.js        JSON config loader + validator
  players.js       Maps player names to SonosClient instances
  radio.js         RadioStationStore (lookup by index / name)
  logger.js        JSON logger with secret redaction
  sonos/
    soap.js        SOAP envelope build/parse, HTTP transport,
                   error classification (PLAYER_UNREACHABLE vs SOAP_FAULT)
    client.js      Sonos commands (play/volume/etc) + metadata fallback
                   ladder for 2024+ firmware
    discovery.js   SSDP (multi-target) discovery
test/
  helpers/mock-sonos.js  Mocked SOAP transport
  soap.test.js
  client.test.js
  radio.test.js
  config.test.js
  server.test.js   HTTP end-to-end with mocked Sonos
```

## Module boundaries

```
   HTTP routes (server.js)
        │
        ▼
   PlayerRegistry   ──►   SonosClient   ──►   soapCall   ──►   HTTP transport
        │
        ▼
   RadioStationStore  (pure, no IO)
```

- `server.js` is the only file that knows about HTTP request/response
  details (status codes, JSON, auth headers).
- `client.js` is the only file that knows Sonos SOAP action names.
- `soap.js` is the only file that knows how to format/parse SOAP envelopes
  and classify errors.
- `radio.js` is pure data — no IO, no Sonos knowledge.

This separation makes it cheap to swap the transport (mock vs real) for
tests and would make a cloud-API adapter straightforward to add.

## Error model

| Origin | Type | Maps to HTTP |
| --- | --- | --- |
| Bad input | `RadioError` / `PlayerError` / `SonosError(code=INVALID_ARG)` | 400 |
| Unknown resource | `*Error(code=*_NOT_FOUND)` | 404 |
| Auth | n/a | 401 |
| Player offline | `SonosError(code=PLAYER_UNREACHABLE)` | 503 |
| Sonos rejected the SOAP call | `SonosError(code=SOAP_FAULT, faultCode=*)` | 502 |
| Unknown | anything | 500 |

The HTTP response always includes `ok: false`, `error`, and `message` for
machine consumption.

## Threading / concurrency

Node.js single-threaded event loop. Concurrent HTTP requests issue
concurrent SOAP calls; the `Promise.all` in `getStatus` issues five SOAP
calls in parallel against a single player, which Sonos handles fine.

A single Sonos player generally tolerates ~10–20 concurrent SOAP calls.
The bridge does not rate-limit; if you have hundreds of HomeServer triggers
firing per second, the player will start rejecting calls and you should
debounce at the HomeServer side.
