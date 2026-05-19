# Inbound URLs (Empfangs-URL) — UPnP NOTIFY sink

The HomeServer Experte's *Aktionen → Empfangs-URL* feature lets you
declare an inbound URL on the HomeServer's HTTP server that triggers a
logic block when called. UPnP NOTIFY callbacks from Sonos players land
on these URLs.

## URL conventions

| Inbound URL path | Triggered by | Receive parser |
| --- | --- | --- |
| `/endpoints/sonos-notify-<name>-av` | Sonos `AVTransport` NOTIFY | AVTransport patterns in `soap/notify-parsers.md` |
| `/endpoints/sonos-notify-<name>-rc` | Sonos `RenderingControl` NOTIFY | RenderingControl patterns |

> The exact prefix (`/endpoints/`, `/quad/`, `/hslf/`, …) depends on
> your HS firmware version. Whatever it is, write the **full URL** that
> resolves on the HomeServer's HTTP server into the `CALLBACK` header of
> the SUBSCRIBE request (see `soap/subscribe.txt`).

Some HS Experte versions require inbound URLs to accept `POST` only.
UPnP NOTIFY uses the `NOTIFY` HTTP method, which some HS versions accept
on inbound URLs and some do not. If your HS rejects NOTIFY:

- Workaround A: configure the inbound URL to accept any method (HS 4.10+
  exposes this in the *Erweitert* tab of the inbound URL definition).
- Workaround B: fall back to status polling (Step 6 in INSTALL.md). You
  lose push-update latency but gain method portability.

## Receive parsers

Each inbound URL needs the regex patterns from `soap/notify-parsers.md`
attached as Empfangsfilter. Each pattern captures one value and writes
it to the corresponding data point. The Experte's parser engine
typically allows multiple parser rules per inbound URL; add one per
captured field.

The order of parsers usually doesn't matter — they all run against the
same body. If your Experte version short-circuits after the first
matching parser, give each parser a *distinct* pattern (the patterns in
`notify-parsers.md` are already distinct enough that this isn't a
problem).

## Inbound webhook for external triggers (optional)

If you also want external triggers (e.g. a phone-based automation that
hits a webhook), declare additional inbound URLs:

| Inbound URL path | Action |
| --- | --- |
| `/endpoints/sonos/<player>/play`     | Triggers `sonos.<player>.play` |
| `/endpoints/sonos/<player>/radio/N`  | Triggers `sonos.<player>.startRadio` with `{uri} = sonos.station.N.uri`, then `sonos.<player>.play` |

These let HomeKit / IFTTT / Node-RED trigger Sonos actions via plain
HTTP without going through KNX.

## Securing inbound URLs

The HS Experte usually allows requiring a token query parameter on
inbound URLs. If your HomeServer is reachable from the internet (or
even just from an untrusted Wi-Fi segment), enable this on every
`/endpoints/sonos*` URL. Document the token in the Experte project
notes — do not hard-code it in any of the files in this directory.

NOTIFY URLs do not benefit from token protection since the Sonos player
won't include arbitrary query parameters; instead, protect them by
keeping the path obscure and binding the HomeServer's HTTP server to the
LAN only.
