# Sonos wire-format reference

Reference for the raw UPnP/SOAP messages LBS 22000 exchanges with the
player. The Python source is the implementation; this doc captures the
on-wire shape so the integrator can verify behaviour with tcpdump or
port the logic elsewhere.

## NOTIFY body format

Sonos UPnP NOTIFY events arrive as XML where the actual values are
nested inside two layers of XML escaping: a `<LastChange>` element
that contains an XML-encoded `<Event>` sub-document. This document
describes the wire format so the integrator can debug or extend the
parsing logic in `hsl3_22000_sonos_player.py` (see `parse_notify`).

The LBS 22000 Sonos Player module already parses every field listed
below. This file is reference documentation, not a configuration
artefact — there is nothing to wire up in Experte.

## AVTransport NOTIFY

Arrives on the listener path `/upnp/<host>/av`. Relevant fields inside
the doubly-encoded `<Event>`:

| Element | Value | Maps to LBS output |
| --- | --- | --- |
| `<TransportState val="…">` | `PLAYING` / `PAUSED_PLAYBACK` / `STOPPED` / `TRANSITIONING` | `State` |
| `<CurrentTrackURI val="…">` | Current track URI (`x-rincon-mp3radio://…`) | (internal) |
| `<CurrentTrackMetaData val="…">` containing `<dc:title>` | Track title | `Title` (overridden by `streamContent` for radio) |
| ... containing `<dc:creator>` | Artist | `Artist` |
| ... containing `<r:streamContent>` | Live stream title metadata | `Title` (preferred for radio) |

## RenderingControl NOTIFY

Arrives on the listener path `/upnp/<host>/rc`.

| Element | Value | Maps to LBS output |
| --- | --- | --- |
| `<Volume channel="Master" val="N">` | 0–100 | `Volume` |
| `<Mute channel="Master" val="0\|1">` | mute state | `Mute` |

## Parsing implementation

The LBS uses these regexes against the **already-unescaped inner
document** (so the patterns are simple, not doubly-escaped):

```python
re.search(r'<TransportState\s+val="([^"]+)"', inner)
re.search(r'<Volume\s+channel="Master"\s+val="(\d+)"', inner)
re.search(r'<Mute\s+channel="Master"\s+val="([01])"', inner)
re.search(r'<CurrentTrackURI\s+val="([^"]*)"', inner)
re.search(r'<CurrentTrackMetaData\s+val="([^"]*)"', inner)
# CurrentTrackMetaData is itself encoded DIDL-Lite, decoded again:
re.search(r'<dc:title>([^<]*)</dc:title>',     meta)
re.search(r'<dc:creator>([^<]*)</dc:creator>', meta)
re.search(r'<r:streamContent>([^<]*)</r:streamContent>', meta)
```

The `unescape_xml` helper applies the standard entity replacements
(`&amp;` → `&`, `&lt;` → `<`, `&gt;` → `>`, `&quot;` → `"`,
`&apos;` → `'`) once per layer.

## SOAP fault detection

When a control SOAP call fails, the LBS extracts the UPnP error code
from the response body:

```
<errorCode>([0-9]+)</errorCode>
```

The error code is written to the `LastError` output as a string
(e.g. `714`, `701`, `HTTP_500`). Codes 714 / 716 / 800 indicate the
player rejected our metadata format and trigger the metadata-fallback
ladder in `_action_start_radio`.

## Status-poll responses

When NOTIFYs aren't available (port binding failed, network isolation),
the `Tick` timer drives status polls. The response bodies use the same
field names as a normal SOAP response — these are simple, single-layer
XML:

| Service / action | Response field | Maps to LBS output |
| --- | --- | --- |
| AVTransport#GetTransportInfo | `<CurrentTransportState>` | `State` |
| RenderingControl#GetVolume   | `<CurrentVolume>` | `Volume` |
| RenderingControl#GetMute     | `<CurrentMute>` | `Mute` |
| AVTransport#GetPositionInfo  | `<TrackMetaData>` containing `<dc:title>`, `<dc:creator>`, `<r:streamContent>` | `Title`, `Artist` |
## UPnP eventing — raw SUBSCRIBE / RENEW / UNSUBSCRIBE

These are NOT SOAP envelopes — they are HTTP requests with a custom method
(`SUBSCRIBE` or `UNSUBSCRIBE`) and no body. Documented here so raw
traffic captured with tcpdump can be matched against
`_subscribe_or_renew` in `projects/sonos_player_hsl3/hsl3_22000_sonos_player.py`.

----------------------------------------------------------------
1) Initial SUBSCRIBE — AVTransport
----------------------------------------------------------------

Host:    {sonos.<name>.host}
Port:    1400
Method:  SUBSCRIBE
Path:    /MediaRenderer/AVTransport/Event
Headers:
  CALLBACK: <{sonos.callbackBase}/endpoints/sonos-notify-<name>-av>
  NT:       upnp:event
  TIMEOUT:  Second-1800

Body: (empty)

Response will contain:
  SID:     uuid:RINCON_XXX...
  TIMEOUT: Second-1800

Parse `SID:\s*(\S+)` from the response header into
`sonos.<name>.sid_av`.

----------------------------------------------------------------
2) Initial SUBSCRIBE — RenderingControl
----------------------------------------------------------------

Same as above, but:
  Path: /MediaRenderer/RenderingControl/Event
  CALLBACK: <{sonos.callbackBase}/endpoints/sonos-notify-<name>-rc>

Store SID into `sonos.<name>.sid_rc`.

----------------------------------------------------------------
3) RENEW (every 25 minutes from a timer logic block)
----------------------------------------------------------------

Same Host/Port/Path as the initial SUBSCRIBE for the matching service,
but instead of CALLBACK + NT, use:

  SID:     {sonos.<name>.sid_av}    (or sid_rc)
  TIMEOUT: Second-1800

Body: (empty)

If renewal returns HTTP 412 "Precondition Failed", the SID has expired.
The renewal logic block should detect this and re-issue the initial
SUBSCRIBE (step 1 or 2) instead.

----------------------------------------------------------------
4) UNSUBSCRIBE (at shutdown / when removing a player)
----------------------------------------------------------------

Same Host/Port/Path, method UNSUBSCRIBE, only header:

  SID: {sonos.<name>.sid_av}    (or sid_rc)

Body: (empty)

----------------------------------------------------------------
Notes
----------------------------------------------------------------

- The CALLBACK URL must be reachable from the Sonos player's network.
  If the HomeServer is on a different VLAN or has firewall restrictions,
  fall back to status polling.
- Sonos players accept multiple subscriptions per service; this is fine.
- TIMEOUT can be anywhere between Second-1 and Second-86400. 1800 (30 min)
  is the conventional value.
- After a player reboot, all SIDs are invalid. The "subscribe at startup"
  logic block should also fire on a periodic timer (e.g. every hour) to
  re-subscribe to players that came back online, or you can wire it to
  fire on "online" transitions detected by the status poll.
