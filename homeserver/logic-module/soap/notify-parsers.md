# NOTIFY body format reference

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
