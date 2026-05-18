# HTTP request templates for the Gira HomeServer

Replace `<bridge>` with `<host>:<port>` of the Sonos bridge (e.g.
`192.168.1.20:8080`). Replace `livingroom` with the player name from your
`config.json`.

If `server.authToken` is configured, add this header to every request:

```
Authorization: Bearer <token>
```

All POST bodies use `Content-Type: application/json`.

## Playback control

| Action | Method | URL | Body |
| --- | --- | --- | --- |
| Play | POST | `http://<bridge>/players/livingroom/play` | – |
| Pause | POST | `http://<bridge>/players/livingroom/pause` | – |
| Stop | POST | `http://<bridge>/players/livingroom/stop` | – |
| Next track | POST | `http://<bridge>/players/livingroom/next` | – |
| Previous track | POST | `http://<bridge>/players/livingroom/previous` | – |

## Volume

| Action | Method | URL | Body |
| --- | --- | --- | --- |
| Set volume (0–100) | POST | `http://<bridge>/players/livingroom/volume` | `{"level": 30}` |
| Volume up (default +2) | POST | `http://<bridge>/players/livingroom/volume/up` | – |
| Volume up by step | POST | `http://<bridge>/players/livingroom/volume/up` | `{"step": 5}` |
| Volume down | POST | `http://<bridge>/players/livingroom/volume/down` | `{"step": 5}` |
| Mute | POST | `http://<bridge>/players/livingroom/mute` | – |
| Unmute | POST | `http://<bridge>/players/livingroom/unmute` | – |
| Toggle mute | POST | `http://<bridge>/players/livingroom/mute/toggle` | – |

## Radio stations

| Action | Method | URL | Body |
| --- | --- | --- | --- |
| Start by index | POST | `http://<bridge>/players/livingroom/radio/start` | `{"index": 1}` |
| Start by name | POST | `http://<bridge>/players/livingroom/radio/start` | `{"name": "Radio 1"}` |
| Start by index (alt) | POST | `http://<bridge>/players/livingroom/radio/index` | `{"index": 1}` |
| Start by name (alt) | POST | `http://<bridge>/players/livingroom/radio/name` | `{"name": "Radio 1"}` |
| List stations | GET | `http://<bridge>/stations` | – |

### Direct station triggers

If you want one KNX button per station (no value passing), wire each button
to its own logic block with the body hard-coded:

- Button → `POST /players/livingroom/radio/start` with `{"index": 1}`
- Button → `POST /players/livingroom/radio/start` with `{"index": 2}`
- …

If you prefer a single KNX value object that picks the station (DPT 5.010
1-byte unsigned), use one logic block that fires on value change and
substitutes the value into the body.

## Status

| Action | Method | URL |
| --- | --- | --- |
| Aggregated status | GET | `http://<bridge>/players/livingroom/status` |
| Same, query form | GET | `http://<bridge>/status?player=livingroom` |
| List players | GET | `http://<bridge>/players` |
| Discovery (SSDP) | GET | `http://<bridge>/players/discover` |
| Bridge liveness | GET | `http://<bridge>/health` |

### Status response (example)

```json
{
  "ok": true,
  "player": "livingroom",
  "online": true,
  "state": "PLAYING",
  "volume": 30,
  "mute": false,
  "track": {
    "title": "Programme name",
    "artist": null,
    "album": null,
    "streamContent": "Now playing: Something",
    "uri": "x-rincon-mp3radio://stream.example.com/r1.mp3",
    "duration": "0:00:00",
    "position": "0:01:42"
  },
  "currentUri": "x-rincon-mp3radio://stream.example.com/r1.mp3",
  "activeStation": { "index": 1, "name": "Radio 1" }
}
```

### Receive parser patterns

For HomeServer "Empfangsfilter" / receive parsers extracting from the JSON:

| Target | Regex (single capture group) | DPT |
| --- | --- | --- |
| Online flag | `"online":(true\|false)` | 1.001 |
| State | `"state":"([A-Z_]+)"` | 16.000 |
| Volume | `"volume":([0-9]+)` | 5.001 |
| Mute | `"mute":(true\|false)` | 1.001 |
| Track title | `"title":"([^"]*)"` | 16.000 |
| Active station name | `"activeStation":\{"index":[0-9]+,"name":"([^"]*)"` | 16.000 |
| Active station index | `"activeStation":\{"index":([0-9]+)` | 5.010 |
| Last error code | `"error":"([A-Z_]+)"` | 16.000 |

Map the captured strings/numbers onto KNX group addresses via the
HomeServer's standard "value to KA" assignment.
