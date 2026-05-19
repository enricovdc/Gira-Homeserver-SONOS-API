# NOTIFY receive-parser patterns

Sonos sends NOTIFY events to the HomeServer's inbound URLs. The body is
XML where the actual values are nested inside two layers of XML escaping
(a `<LastChange>` element that contains an XML-encoded `<Event>`
sub-document).

The regexes below match values **inside the encoded inner document**, so
they handle the escaping correctly without needing a real XML parser.

Configure each as an **Empfangsfilter** ("receive parser") on the
corresponding inbound URL, set the captured group as the data point to
write.

## AVTransport NOTIFY (`/endpoints/sonos-notify-<name>-av`)

| Pattern | Captures | Write to |
| --- | --- | --- |
| `&lt;TransportState\s+val=&quot;([^&]+)&quot;` | playback state (`PLAYING`, `PAUSED_PLAYBACK`, `STOPPED`, `TRANSITIONING`) | `sonos.<name>.state` |
| `&lt;CurrentTrackURI\s+val=&quot;([^&]*?)&quot;` | track URI (decode `&amp;` → `&` after capture) | `sonos.<name>.trackUri` |
| `&lt;CurrentTrackMetaData\s+val=&quot;[^&]*?&amp;lt;dc:title&amp;gt;([^&]*?)&amp;lt;/dc:title&amp;gt;` | current track title | `sonos.<name>.title` |
| `&lt;CurrentTrackMetaData\s+val=&quot;[^&]*?&amp;lt;dc:creator&amp;gt;([^&]*?)&amp;lt;/dc:creator&amp;gt;` | artist | `sonos.<name>.artist` |
| `&lt;CurrentTrackMetaData\s+val=&quot;[^&]*?&amp;lt;r:streamContent&amp;gt;([^&]*?)&amp;lt;/r:streamContent&amp;gt;` | live stream title metadata | `sonos.<name>.streamContent` |

> Some HS Experte versions decode XML entities before applying the
> receive parser. If that's the case for your installation, use the
> non-escaped form: `<TransportState val="([^"]+)"` etc. Test once
> against a captured NOTIFY body and pick whichever form matches.

## RenderingControl NOTIFY (`/endpoints/sonos-notify-<name>-rc`)

| Pattern | Captures | Write to |
| --- | --- | --- |
| `&lt;Volume\s+channel=&quot;Master&quot;\s+val=&quot;([0-9]+)&quot;` | volume 0–100 | `sonos.<name>.volume` |
| `&lt;Mute\s+channel=&quot;Master&quot;\s+val=&quot;([01])&quot;` | mute 0/1 | `sonos.<name>.mute` |

## Status-poll response parsers (used in Step 6 status-polling fallback)

`GetTransportInfo` response:

| Pattern | Captures | Write to |
| --- | --- | --- |
| `<CurrentTransportState>([A-Z_]+)</CurrentTransportState>` | playback state | `sonos.<name>.state` |

`GetVolume` response:

| Pattern | Captures | Write to |
| --- | --- | --- |
| `<CurrentVolume>([0-9]+)</CurrentVolume>` | volume | `sonos.<name>.volume` |

`GetMute` response:

| Pattern | Captures | Write to |
| --- | --- | --- |
| `<CurrentMute>([01])</CurrentMute>` | mute 0/1 | `sonos.<name>.mute` |

`GetPositionInfo` response (rich, optional):

| Pattern | Captures | Write to |
| --- | --- | --- |
| `<dc:title>([^<]*)</dc:title>` | title | `sonos.<name>.title` |
| `<dc:creator>([^<]*)</dc:creator>` | artist | `sonos.<name>.artist` |
| `<r:streamContent>([^<]*)</r:streamContent>` | live stream metadata | `sonos.<name>.streamContent` |
| `<TrackURI>([^<]*)</TrackURI>` | current track URI | `sonos.<name>.trackUri` |

## SOAP fault detection (on any response)

| Pattern | Captures | Write to |
| --- | --- | --- |
| `<errorCode>([0-9]+)</errorCode>` | UPnP error code | `sonos.<name>.lastError` |
| `<errorDescription>([^<]+)</errorDescription>` | human description | `sonos.<name>.lastErrorMsg` |

If `lastError` is set after an action, treat the action as failed and
optionally fire a "Sonos error" KNX flag.

## Heartbeat / online tracking

There is no dedicated NOTIFY for "I'm online". Use one of:

- Set `sonos.<name>.online = true` whenever **any** response or NOTIFY
  arrives, and set it back to `false` if no response is received within
  a 30 s window (a watchdog timer logic block).
- Or, every 60 s, fire `get-transport`; success → `online = true`,
  timeout → `online = false`.

## Decoding `&` in captured strings

XML decoding inside the HS Experte's data point write step is typically
automatic. If a captured title contains `&amp;` literally, add a
post-processing replace step: `&amp;` → `&`, `&lt;` → `<`, `&gt;` → `>`,
`&quot;` → `"`, `&apos;` → `'`.
