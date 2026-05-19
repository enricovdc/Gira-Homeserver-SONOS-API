# Surfacing the bridge URL on the HomeServer homepage / debug page

The bridge runs **on the HomeServer itself** as a systemd service listening on
`http://<homeserver-ip>:8080`. The configuration page is served at the root
URL. This document explains how to make that URL one click away from
inside the HomeServer's own UI.

## Option 1 — Link from the HomeServer visualisation (recommended)

In the **HS Experte → Visualisierung**:

1. Add a new "URL element" / "Webseite einbetten" to a homepage tile.
2. Set the URL to `http://<homeserver-ip>:8080/` (or `http://127.0.0.1:8080/`
   if the visualisation runs on the HomeServer itself).
3. Label the tile "Sonos Konfiguration".

When users open the HomeServer visualisation they will see a tile that, when
clicked, opens the bridge's configuration UI directly. The UI supports:

- adding / removing players,
- adding / removing radio stations,
- viewing the live event stream,
- testing the webhook,
- enabling the optional Sonos Cloud integration.

## Option 2 — Iframe embed inside an existing page

If your visualisation supports a generic HTML widget:

```html
<iframe src="http://127.0.0.1:8080/"
        style="width: 100%; height: 100%; border: 0;"
        title="Sonos Bridge Configuration"></iframe>
```

## Option 3 — Debug page link

The HomeServer's **Debug** / **Diagnose** view typically allows custom
hyperlinks. Add a link named "Sonos Bridge" pointing to
`http://<homeserver-ip>:8080/`.

## Option 4 — Auto-discovery via `/info`

The bridge exposes a machine-readable info endpoint:

```
GET http://127.0.0.1:8080/info
```

Returns:

```json
{
  "ok": true,
  "name": "Gira HomeServer Sonos Bridge",
  "version": "0.2.0",
  "publicUrl": "http://192.168.1.10:8080",
  "adminUrl":  "http://192.168.1.10:8080/",
  "eventsUrl": "http://192.168.1.10:8080/events",
  "callbackBaseUrl": "http://192.168.1.10:8080",
  "hostname": "homeserver",
  "players": [{"name":"livingroom","host":"192.168.1.50"}],
  "stations": 4,
  "eventingEnabled": true,
  "webhookConfigured": false,
  "cloudEnabled": false
}
```

A HomeServer logic block can `GET` this and write the `adminUrl` into a
KNX text group address (DPT 16.000 / 16.001), so a wall display or
visualisation widget shows the live URL. This is the cleanest way to make
the URL "visible on the HomeServer" without hard-coding it in the
visualisation.

## Auto-loading the URL into a visualisation text element

In the HS Experte, create a logic block that:

1. Triggers on `Start des HomeServers` and every 5 minutes.
2. Issues `GET http://127.0.0.1:8080/info`.
3. Parses `"adminUrl":"([^"]+)"` from the response.
4. Writes the captured value into a KNX group address bound to a text
   display element in the visualisation.

This way, if you ever change the bridge port or host, the visualisation
updates itself.

## Receiving pushed state on the HomeServer (no polling)

The bridge can push state changes to the HomeServer instead of relying on
periodic polls.

1. In the HomeServer Experte, create a logic block that listens on a
   custom HTTP endpoint, e.g. `POST /quad/sonos-event`. The Experte calls
   these "HTTP-Empfangs-Aktionen" / inbound URLs.
2. In the bridge config (web UI → Cloud / Event push), set the Webhook URL
   to that endpoint, e.g. `http://127.0.0.1/quad/sonos-event`.
3. (Optional) Set an Authorization header value, and require it in the
   logic block.

The bridge will POST a JSON payload to the URL whenever a state change is
observed (either driven by an HS-issued control action or by a UPnP event
pushed from the Sonos player). Payload shape:

```json
{
  "event": "state",
  "payload": {
    "player": "livingroom",
    "state": {
      "state": "PLAYING",
      "volume": 30,
      "mute": false,
      "track": { "title": "..." },
      "online": true,
      "activeStation": { "index": 1, "name": "Radio 1" },
      "updatedAt": "2026-05-19T08:12:34.567Z"
    }
  },
  "ts": "2026-05-19T08:12:34.567Z"
}
```

A receive parser on the HomeServer logic block can extract the same
fields documented in `homeserver/REQUESTS.md` and update the
corresponding KNX group addresses (see `homeserver/KNX-MAPPING.md`).

This makes the integration **event-driven end-to-end**: a user changes
volume via the Sonos app → the Sonos player emits a UPnP NOTIFY → the
bridge updates state and POSTs to the HomeServer → the logic block
writes the new volume to KNX. No polling.
