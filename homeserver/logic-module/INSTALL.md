# Logic-module-only install — step-by-step

This guide walks an integrator through setting up the Sonos integration
**entirely inside the HS Experte**, without SSH access to the HomeServer.

Time required: ~30 min for the first player; ~5 min for each additional
player or station.

## Prerequisites

- HS Experte connected to the HomeServer.
- One or more Sonos players, all on the same LAN as the HomeServer.
- The **IP address** of each Sonos player. To find it, open the Sonos app
  on a phone, go to *Settings → System → About your system*, scroll to
  the player, note the **IP Address**. Write these down — manual entry is
  the one trade-off versus the bridge mode.

## Step 1 — Create the data points

In the HS Experte: *Kommunikationsobjekte → neuen Datenpunkt anlegen*.

Create the following data points. Use whatever main/middle/sub groups
suit your project; the names below are suggestions.

### Per-player data points (repeat for each player)

| Name                          | Type     | Notes |
| ----------------------------- | -------- | ----- |
| `sonos.<name>.host`           | string   | Sonos LAN IP, e.g. `192.168.1.50` |
| `sonos.<name>.state`          | string   | Latest playback state (filled by NOTIFY) |
| `sonos.<name>.volume`         | integer  | Current volume 0–100 |
| `sonos.<name>.mute`           | boolean  | Current mute state |
| `sonos.<name>.title`          | string   | Current track title / `streamContent` |
| `sonos.<name>.activeStation`  | integer  | Index of last started station (0 = none) |
| `sonos.<name>.online`         | boolean  | Heartbeat from last successful request |
| `sonos.<name>.sid_av`         | string   | UPnP subscription SID for AVTransport |
| `sonos.<name>.sid_rc`         | string   | UPnP subscription SID for RenderingControl |
| `sonos.<name>.lastError`      | string   | Last error code returned by the player |

### Per-station data points (repeat for each station, index 1..N)

| Name                          | Type     | Notes |
| ----------------------------- | -------- | ----- |
| `sonos.station.<n>.name`      | string   | Display name |
| `sonos.station.<n>.uri`       | string   | Stream URL — see "Radio station URLs" below |

### Global data points

| Name                          | Type     | Notes |
| ----------------------------- | -------- | ----- |
| `sonos.callbackBase`          | string   | `http://<homeserver-ip>` — used as UPnP callback target |
| `sonos.diag.lastNotifyAt`     | string   | ISO timestamp of last received NOTIFY |

> **Tip**: bind the player-state data points to KNX group addresses (see
> `homeserver/KNX-MAPPING.md`) so they auto-update KNX outputs whenever
> the data point changes.

## Step 2 — Create the outbound HTTP actions

For each Sonos action you want to control, create an **HTTP-Request
senden** action under *Aktionen*. Use the SOAP envelopes in `soap/` as
the body.

Example for `play`:

| Field             | Value |
| ----------------- | ----- |
| Host              | `{sonos.<name>.host}` (data point reference) |
| Port              | `1400` |
| Method            | `POST` |
| Path              | `/MediaRenderer/AVTransport/Control` |
| Header `Content-Type` | `text/xml; charset="utf-8"` |
| Header `SOAPACTION` | `"urn:schemas-upnp-org:service:AVTransport:1#Play"` |
| Body              | contents of `soap/play.xml` |

Repeat for each of the SOAP envelopes in `soap/` — see
`experte-blocks/http-actions.md` for the full list with exact
SOAPACTION values and paths.

The complete list of actions is:

- `play`, `pause`, `stop`, `next`, `previous`
- `set-volume` (template: `{level}` is substituted from the trigger value)
- `set-mute` (template: `{mute}` = `0` or `1`)
- `set-av-transport-uri` (template: `{uri}` from station data point) plus
  immediately-follow `play` action
- `get-transport`, `get-volume`, `get-mute`, `get-position` for status
  polling
- `subscribe-av`, `subscribe-rc` for UPnP eventing
- `unsubscribe` for cleanup
- `renew-av`, `renew-rc` for subscription renewal

## Step 3 — Bind logic blocks to KNX

In the HS Experte, create a logic block per action. Connect:

- **Input**: a KNX group address (e.g. `5/0/1` = "Play livingroom").
- **Action**: the HTTP-Request senden action you created in Step 2.

See `experte-blocks/logic-blocks.md` for the full set of suggested
bindings and `homeserver/KNX-MAPPING.md` for the recommended GA layout.

## Step 4 — Set up the UPnP event sink

The HomeServer has a built-in HTTP server that exposes inbound URLs at
paths like `/endpoints/<your-path>` (exact path depends on HS firmware
version — check the *Empfangs-URL* section in the Experte). These URLs
trigger a logic block when called.

1. Under *Aktionen → Empfangs-URL*, create an inbound URL named
   `sonos-notify-{player}-{service}` (use placeholder substitution if
   the Experte supports it; otherwise one URL per player+service combo,
   e.g. `sonos-notify-livingroom-av`).
2. Configure the receive parser ("Empfangsfilter") with the regex
   patterns in `soap/notify-parsers.md`. Each parser captures one value
   (state, volume, mute, title, …) and writes it into the corresponding
   `sonos.<name>.*` data point.
3. Determine the full inbound URL the HomeServer exposes for this
   endpoint — typically
   `http://<homeserver-ip>/endpoints/sonos-notify-livingroom-av`.
   Write this URL into the `sonos.callbackBase` data point's
   subscription path. The exact format the Experte exposes is shown in
   the inbound-URL details panel.

## Step 5 — Subscribe to UPnP events

Create a one-shot logic block named "Sonos subscribe at startup":

1. **Trigger**: HomeServer startup (`Start des HomeServers`).
2. **Actions** (one per player, one per service):
   - Execute the `subscribe-av` HTTP action for the player.
   - Parse the response headers and store `SID` into `sonos.<name>.sid_av`
     (use a receive parser matching `SID:\s*(\S+)` against the response
     header line).
   - Same for `subscribe-rc` and `sonos.<name>.sid_rc`.

Create a periodic logic block named "Sonos renew subscriptions":

1. **Trigger**: timer every 25 minutes.
2. **Actions**: for each player, issue `renew-av` and `renew-rc`
   (`SUBSCRIBE` request with the existing `SID` header — see
   `soap/subscribe.txt`).

Without renewal, the Sonos player will drop the subscription after ~30
minutes and stop sending NOTIFYs.

## Step 6 — Status polling fallback

For redundancy in case a NOTIFY is missed, create a logic block:

1. **Trigger**: timer every 10 seconds (tune as needed).
2. **For each player**: issue `get-transport`, `get-volume`, `get-mute`
   actions and parse responses into `sonos.<name>.*` data points using
   the regex patterns documented in `experte-blocks/http-actions.md`.

If you trust UPnP eventing, this can be every 60 s as a heartbeat only.

## Step 7 — Install the configuration page

Copy `visualisation/config-page.html` into a Gira visualisation page
(see `visualisation/README.md` for the embedding mechanism). The page
reads and writes the data points created in Step 1, so end users can
add/edit players and stations from the HS visualisation without
touching the Experte.

## Step 8 — Verify

1. Download the project to the HomeServer.
2. From a browser, hit a control endpoint directly — e.g. the Experte's
   *Aktion testen* feature on the `play` action.
3. Confirm the Sonos player responds.
4. Press a KNX button bound to a logic block. Confirm playback.
5. Change volume on the Sonos app on a phone. Within ~1 s the
   `sonos.<player>.volume` data point should update (NOTIFY arrived).
   If it doesn't, fall back to status polling and check the subscription
   logic.

## Radio station URLs

The bridge mode discovers favorites via Sonos's local content service.
In logic-module-only mode you set stream URLs manually:

1. In the Sonos app on a phone, open the station.
2. Tap *Information* → look for the actual stream URL (it is often
   `http://stream.example.com/something`).
3. Paste that URL into `sonos.station.<n>.uri`. The bridge SOAP
   templates automatically use the firmware-safe direct-broadcast scheme
   `x-rincon-mp3radio://` and the metadata-fallback ladder, so the same
   URL works on every firmware including 2026.

For TuneIn-bound stations that only exist as Sonos service references
(not direct HTTP streams), use a public Icecast/Shoutcast mirror of the
station instead. Direct streams are more resilient across firmware
generations anyway — see the firmware-compatibility section of the main
README.

## Troubleshooting

- **HTTP 500 / SOAP fault 714** when starting a station → the player
  rejected the metadata. The templates in `soap/set-av-transport-uri.xml`
  use empty metadata, which works on all firmware including 2026. If you
  modified the template to include rich metadata and it now fails, revert
  to the empty form.
- **No NOTIFY ever arrives** → check that:
  - the `CALLBACK` URL in `soap/subscribe.txt` resolves from the player's
    network (Sonos players can't reach VLAN-isolated HomeServers),
  - the HomeServer's inbound URL accepts the `NOTIFY` HTTP method (some
    Experte versions only allow `GET`/`POST` on inbound URLs — use a
    `POST` translation rule or fall back to status polling).
- **Subscription drops after 30 minutes** → the renewal timer is not
  firing or the `SID` is stale. Capture the response of `subscribe-av`
  and confirm the `sonos.<name>.sid_av` data point updates.
- **Player offline** → SOAP requests will time out. The `online` data
  point can be driven by the status-poll logic block: set it `false` on
  timeout, `true` on a successful response.

## Distribution

To hand this integration to another integrator:

1. In the HS Experte, *Datei → Projekt exportieren*. The resulting `.fwz`
   file contains all the data points, actions, inbound URLs, logic
   blocks, and visualisation pages you created.
2. Bundle that `.fwz` together with this `homeserver/logic-module/`
   directory.
3. The receiving integrator opens the `.fwz` in Experte, downloads to
   their HomeServer, then edits the per-player `host` data points and
   per-station `uri` data points via the visualisation page in Step 7.

No SSH, no Node.js, no external service.
