# HTTP-Request senden actions (Experte)

Every Sonos control is one *Aktion → HTTP-Request senden* in the
Experte. The table below is exhaustive — create one action per row.

Use the data-point reference syntax of your Experte version (commonly
`{{datapoint.name}}` or `$datapoint.name$`) to substitute values into
the host, path, headers, and body.

All requests share:
- **Port**: `1400`
- **Header `Content-Type`**: `text/xml; charset="utf-8"` (for SOAP)
- **Method**: `POST` (for SOAP) — SUBSCRIBE/UNSUBSCRIBE are described in
  `soap/subscribe.txt`.
- **Host**: `{sonos.<name>.host}` — one set of actions per configured
  player, or use the Experte's parameterisation feature to share the
  action across players.

## Per-player control actions

| Action name | Path | SOAPACTION header | Body file |
| --- | --- | --- | --- |
| `sonos.<name>.play`     | `/MediaRenderer/AVTransport/Control` | `"urn:schemas-upnp-org:service:AVTransport:1#Play"` | `soap/play.xml` |
| `sonos.<name>.pause`    | `/MediaRenderer/AVTransport/Control` | `"urn:schemas-upnp-org:service:AVTransport:1#Pause"` | `soap/pause.xml` |
| `sonos.<name>.stop`     | `/MediaRenderer/AVTransport/Control` | `"urn:schemas-upnp-org:service:AVTransport:1#Stop"` | `soap/stop.xml` |
| `sonos.<name>.next`     | `/MediaRenderer/AVTransport/Control` | `"urn:schemas-upnp-org:service:AVTransport:1#Next"` | `soap/next.xml` |
| `sonos.<name>.previous` | `/MediaRenderer/AVTransport/Control` | `"urn:schemas-upnp-org:service:AVTransport:1#Previous"` | `soap/previous.xml` |
| `sonos.<name>.setVolume`| `/MediaRenderer/RenderingControl/Control` | `"urn:schemas-upnp-org:service:RenderingControl:1#SetVolume"` | `soap/set-volume.xml` — `{level}` replaced by the input value |
| `sonos.<name>.setMute`  | `/MediaRenderer/RenderingControl/Control` | `"urn:schemas-upnp-org:service:RenderingControl:1#SetMute"` | `soap/set-mute.xml` — `{mute}` replaced by `0` or `1` |
| `sonos.<name>.startRadio` | `/MediaRenderer/AVTransport/Control` | `"urn:schemas-upnp-org:service:AVTransport:1#SetAVTransportURI"` | `soap/set-av-transport-uri.xml` — `{uri}` replaced by `{sonos.station.<n>.uri}` |

After `startRadio`, immediately invoke `sonos.<name>.play`. The Experte's
sequence runner can chain these in a single logic block.

## Per-player status actions (used by polling and on-startup)

| Action name | Path | SOAPACTION header | Body file |
| --- | --- | --- | --- |
| `sonos.<name>.getTransport` | `/MediaRenderer/AVTransport/Control` | `"urn:schemas-upnp-org:service:AVTransport:1#GetTransportInfo"` | `soap/get-transport.xml` |
| `sonos.<name>.getVolume`    | `/MediaRenderer/RenderingControl/Control` | `"urn:schemas-upnp-org:service:RenderingControl:1#GetVolume"` | `soap/get-volume.xml` |
| `sonos.<name>.getMute`      | `/MediaRenderer/RenderingControl/Control` | `"urn:schemas-upnp-org:service:RenderingControl:1#GetMute"` | `soap/get-mute.xml` (variant of get-volume) |
| `sonos.<name>.getPosition`  | `/MediaRenderer/AVTransport/Control` | `"urn:schemas-upnp-org:service:AVTransport:1#GetPositionInfo"` | `soap/get-position.xml` |

Each must have an **Empfangsfilter** attached using the patterns in
`soap/notify-parsers.md`. The captured value writes to the corresponding
`sonos.<name>.*` data point.

## Per-player subscription actions

See `soap/subscribe.txt` for the raw HTTP requests:

- `sonos.<name>.subscribe-av`
- `sonos.<name>.subscribe-rc`
- `sonos.<name>.renew-av`
- `sonos.<name>.renew-rc`
- `sonos.<name>.unsubscribe-av`
- `sonos.<name>.unsubscribe-rc`

Each subscribe / renew response includes a `SID:` header. Attach a
receive parser matching `SID:\s*(\S+)` and write to `sonos.<name>.sid_av`
or `sonos.<name>.sid_rc` accordingly.

## Volume up / down

There is no native UPnP "volume up" — read the current volume, add or
subtract a step, clamp 0..100, write back:

1. Trigger logic block fires.
2. Action `sonos.<name>.getVolume` runs; its receive parser writes
   `sonos.<name>.volume`.
3. A small Expert formula computes the new value:
   `new = max(0, min(100, sonos.<name>.volume + step))`.
4. Action `sonos.<name>.setVolume` runs with `{level} = new`.

If the Experte version supports it, chain these in a single sequence
with the formula step between the two HTTP actions. Otherwise use two
back-to-back logic blocks with the volume data point as the intermediate
trigger.

## Parameterised actions (optional, advanced)

If your Experte version supports parameterised HTTP actions, you can
collapse the per-player duplication: one `sonos.play` action where the
`host` field uses a parameter `${player}` that the caller sets. The
visualisation config page (`visualisation/config-page.html`) reads the
list of configured player data points and renders them, so no Experte
changes are needed when a player is added at runtime.
