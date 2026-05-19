# Logic-module-only installation (no SSH, no separate server)

This directory contains everything needed to install the Sonos integration
**entirely from inside the Gira HomeServer Experte**, with **no SSH access**
to the HomeServer and **no Node.js bridge** running anywhere.

The integrator imports the contents of `experte-blocks/` and
`visualisation/` in the HS Experte, sets a handful of data points, and
downloads the project to the HomeServer. That's it.

## How it works

The HomeServer Experte already provides every building block we need:

- **HTTP-Request senden** action — for outbound SOAP calls to the Sonos
  player on port 1400 (play, pause, volume, radio start, …).
- **Empfangs-URL** (inbound URL) — for receiving UPnP NOTIFY callbacks
  from Sonos players when state changes. The HomeServer's own HTTP server
  becomes the UPnP event sink.
- **Empfangsfilter** (receive parser) — to extract values from SOAP
  responses and from NOTIFY bodies using regex.
- **Datenpunkte** (data points) — to store player IPs, station stream
  URLs, current state, and subscription SIDs across reboots.
- **Visualisierung** — to render a configuration page in the HomeServer's
  own UI where the end user edits players and stations at runtime.
- **Timer Logikbausteine** — to renew UPnP subscriptions before they
  expire and to poll status for players that didn't acknowledge a
  subscription.

No external process. No port binding. No SSH.

## Architecture

```
┌────────────────────────────────────────────────────────┐
│  Gira HomeServer                                        │
│                                                         │
│   KNX (in/out) ──┐                                      │
│                  ▼                                      │
│   Logic blocks   →  HTTP-Request senden  ──┐            │
│                                            ▼            │
│                                       Sonos Player      │
│                                       :1400/MediaRen…   │
│                                            │            │
│                                            ▼            │
│   Inbound URL  ←  UPnP NOTIFY  ←  Sonos Player          │
│   (Empfangsfilter parses payload, writes data points)   │
│                                                         │
│   Visualisation  ↔  Data points (players, stations,     │
│                       state, SIDs)                      │
└────────────────────────────────────────────────────────┘
```

## Trade-offs vs the bridge

| Feature | Logic-module-only | Bridge service |
| --- | --- | --- |
| Play / pause / next / prev / stop | ✓ | ✓ |
| Volume / mute / mute-toggle | ✓ | ✓ |
| Radio stations | ✓ | ✓ |
| Status (poll) | ✓ | ✓ |
| UPnP event push (no polling) | ✓ | ✓ |
| SSDP auto-discovery | ✗ — enter IPs manually | ✓ |
| Self-served web admin UI | Via HS visualisation page | Standalone admin page |
| Installation effort | Import in Experte | One-time SSH + systemd |
| Works on 2026 firmware | ✓ | ✓ |

The bridge gives you SSDP discovery and a single self-contained admin UI;
this mode gives you a zero-SSH install at the cost of typing player IPs
in by hand once. For a residential Gira install with 1–6 Sonos players,
the trade-off is usually worth it.

## Files

```
soap/
  play.xml              SOAP envelope: AVTransport#Play
  pause.xml             SOAP envelope: AVTransport#Pause
  stop.xml
  next.xml
  previous.xml
  set-volume.xml        AVTransport-style SetVolume on RenderingControl
  set-mute.xml
  get-transport.xml     AVTransport#GetTransportInfo (status poll)
  get-volume.xml
  set-av-transport-uri.xml   Radio station start (with metadata fallback)
  subscribe.txt         Raw SUBSCRIBE request (curl-style headers)
  notify-parsers.md     Regex patterns to extract values from NOTIFY
experte-blocks/
  data-points.md        List of data points to create, with names and types
  http-actions.md       Each outbound HTTP-Request senden action defined
  receive-urls.md       Each inbound Empfangs-URL (NOTIFY sink, webhook in)
  logic-blocks.md       Wiring: which KNX GA triggers which HTTP action
  timers.md             Subscription renewal + status poll
visualisation/
  config-page.html      Embeddable HTML for HS visualisation config page
  README.md             How to host this page inside the HS visualisation
INSTALL.md              Step-by-step Experte setup walkthrough
```

## Start here

Read [INSTALL.md](INSTALL.md). It takes ~30 minutes to wire up the first
player and radio stations.
