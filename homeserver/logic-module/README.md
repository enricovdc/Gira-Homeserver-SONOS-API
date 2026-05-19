# Gira HomeServer logic modules

The canonical deliverable for this project: **native HSL3 logic modules**
that run inside the Gira HomeServer's own logic engine. No external
service, no SSH, no companion machine. Import the `.hslz` archive in
the HS Experte and you have working Sonos control.

## What's here

```
homeserver/logic-module/
├── hsl3/        Native HSL3 / Python 3.9 LogicModule sources.
│                Two LBS modules: 22000 Sonos Player, 22001 Sonos Discover.
│                Run `build/build_hslz.py` to package as .hslz for Experte import.
│                See hsl3/README.md for details.
│
└── soap/        Raw SOAP envelope templates used inside the HSL3 Python.
                 Kept here as a documentation cross-reference so the
                 integrator can verify what wire-format the modules send.
                 Not used directly in Experte — the .py file embeds them.
```

The `.hsl` files are the deliverable; the `.py` files in
`hsl3/src_*` are the source that the Gira HSL3 generator compiles into
those `.hsl` files. See `hsl3/README.md` for the build and import flow.

## Quick start

```sh
# 1. Build the HSLZ archives
python3 homeserver/logic-module/hsl3/build/build_hslz.py

# 2. In Experte: Logikbausteine → Importieren →
#    select hsl3/build/dist/22000_sonos_player.hslz and 22001_sonos_discover.hslz

# 3. Drag a Sonos Player block onto the logic canvas, set Host to the
#    player's IP, wire inputs/outputs to KNX. Done.
```

## What the HSL3 modules do

- Control Sonos players (play/pause/stop/next/prev, volume, mute, mute toggle).
- Eight configurable radio stations per player; firmware-2026-resilient
  metadata-free direct-broadcast playback with automatic fallback.
- Status outputs (Online, State, Volume, Mute, Title, Artist,
  ActiveStation, LastError, Subscribed) updated in ~1 second via UPnP
  event push (NOTIFY callback on TCP 8081 inside the HomeServer), with
  status-poll fallback on a `Tick` timer for resilience.
- Optional SSDP discovery to find players on the LAN at commissioning.

See `hsl3/README.md` for the full input/output specification and
KNX-mapping guidance.
