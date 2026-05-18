# Gira HomeServer setup

This document describes how to wire the **Gira HomeServer Experte** to the
Sonos API Bridge.

## Prerequisites

- HomeServer with Experte software installed.
- The bridge is running on the local network and reachable from the
  HomeServer (e.g. `http://192.168.1.20:8080`).
- One or more Sonos players configured in `config.json`.
- Optional: a bearer token (`server.authToken`) — see *Authentication* below.

## High-level pattern

The HomeServer integrates with the bridge using **HTTP request actions**
("Aktion: HTTP-Request senden"). For every Sonos command you want to trigger
from KNX, you create one HTTP request action that calls the appropriate
endpoint on the bridge. For status that you want to surface back into KNX,
you periodically poll `/players/:name/status` from a timer logic block and
parse the JSON response with a **receive parser** ("Empfangsfilter").

```
 KNX group address (e.g. play button)
        │
        ▼
 HomeServer logic block (trigger)
        │
        ▼
 HTTP-Request action  ─► Sonos Bridge ─► Sonos player
        │
        ▼
 Optional: response handler updates a KNX group address
```

## Step 1 — Create a "Sonos Bridge" base entry

1. Open the HS Experte.
2. Navigate to **Kommunikationsobjekte** → create a new HTTP host entry for
   the bridge, e.g.:
   - Name: `sonos-bridge`
   - Host: `192.168.1.20`
   - Port: `8080`
3. Save.

> If your HS Experte version does not support reusable HTTP host entries,
> set the host and port directly in each HTTP request action.

## Step 2 — Create one logic block per command

For each Sonos action (play, pause, volume up, ...), add a logic block:

1. Add an **input** bound to the KNX group address that should trigger the
   action (e.g. `0/0/1` — "Sonos play").
2. Inside the block, add an **HTTP request action** with:
   - Method: `POST`
   - URL: `http://<bridge-host>:<port>/players/livingroom/play`
   - Body: empty (or JSON for actions that require parameters)
   - Headers (optional): `Authorization: Bearer <token>` if you configured
     `server.authToken`.
3. Save and download the configuration.

See [REQUESTS.md](REQUESTS.md) for the full list of request templates.

## Step 3 — Status polling

To reflect Sonos state back into KNX:

1. Create a timer logic block firing every 5 seconds (or whatever feels
   right; the bridge handles polling rates fine).
2. Issue `GET http://<bridge-host>:<port>/players/livingroom/status`.
3. Configure an **Empfangsfilter** / receive parser that extracts JSON
   fields. The HomeServer's parser can match patterns like:
   - `"state":"([A-Z]+)"` → playback state
   - `"volume":([0-9]+)` → volume
   - `"mute":(true|false)` → mute
   - `"title":"([^"]*)"` → current track title
4. Map each parsed value to the appropriate KNX group address (DPT 5.001 for
   volume, DPT 1.001 for mute, DPT 16.000 for text).

See [KNX-MAPPING.md](KNX-MAPPING.md) for a recommended group-address layout.

## Step 4 — Radio station triggers

For "press a button to start radio station X" automations:

1. Add a logic block with input bound to the KNX button.
2. Issue `POST /players/livingroom/radio/start` with body `{"index": 1}` for
   station 1, `{"index": 2}` for station 2, etc.
3. The `radioStations[]` array in `config.json` defines which index maps to
   which stream URL — change the streams without redeploying the HomeServer
   configuration.

Alternatively, use a single KNX value object (DPT 5.010, 1-byte unsigned)
that holds the desired station index, and a logic block that fires on value
change and sends the index in the body.

## Authentication

If `server.authToken` is set in `config.json`, every HTTP request action
must include the header:

```
Authorization: Bearer <token>
```

Treat the token as a shared secret on your LAN. Do not put it in KNX object
descriptions or anywhere it might leak in a backup export.

## Validating the integration

1. `curl http://<bridge-host>:<port>/health` — should return
   `{"ok":true,...}`.
2. From the HS Experte, trigger one of the play actions manually
   ("Aktion testen").
3. Confirm the Sonos player responds.
4. Check the bridge logs (stdout) for structured JSON entries showing the
   request.

## Common pitfalls

- Wrong **host/port** in the HTTP request action. Always test with `curl`
  first.
- Forgetting the `Content-Type: application/json` header for actions with a
  JSON body — most HS Experte versions add it automatically, but some
  require it explicitly.
- Trying to control a player that is in a Sonos group as a *member*: the
  group coordinator must be controlled instead. Configure the coordinator's
  IP in `players[]`.
- Players that have been offline for an extended period may need a small
  retry — the bridge will return 503 `PLAYER_UNREACHABLE`; the HS logic
  block can react by setting an "offline" KNX flag.
