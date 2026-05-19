# Gira HomeServer Sonos integration

Native HSL3 logic modules for the Gira HomeServer / FacilityServer that
control Sonos players over the local UPnP/SOAP API. No bridge service,
no external machine, no SSH access required — the integration runs
entirely inside the HomeServer's own logic engine.

Two LBS modules:

| LBS | Name | Role |
| --- | --- | --- |
| **22000** | Sonos Player | One instance per Sonos player. Play / pause / stop / next / previous, volume, mute, eight configurable radio stations, status outputs, UPnP event push. |
| **22001** | Sonos Admin | Singleton companion. Web UI at `http://<hs-ip>:8080/` for managing players (by **IP and/or MAC**), a global radio-station library, and Sonos Cloud OAuth. Also runs periodic + KNX-triggerable SSDP discovery with a structured `DiscoveredPlayers` output. All inside HSL3, no external process. Optional but recommended. |

Compatible with Sonos firmware **2024+ and 2026** and Gira HomeServer
firmware **4.13+** (HSL3 / Python 3.9 logic-module SDK).

## Features

- **Pure HomeServer install.** Import the `.hslz` archives in Experte
  via *Logikbausteine → Importieren*. No bridge, no SSH, no Linux box.
- **Local control only.** Talks to Sonos players directly via SOAP on
  port 1400. No Sonos OAuth required, no developer registration, no
  rate limits, no cloud dependency.
- **UPnP event push.** Each player subscribes to AVTransport +
  RenderingControl events; status outputs update in ~1 s when state
  changes externally (Sonos app, AirPlay handoff, volume knob).
- **Firmware-2026 hardening.** Metadata-free SetAVTransportURI, the
  `x-rincon-mp3radio://` direct-broadcast scheme, and an automatic
  fallback ladder for players that reject the first attempt.
- **Per-player radio** (8 configurable stations per LBS 22000 instance)
  **or** a shared global station library managed via the Admin web UI.
- **Web admin UI** (LBS 22001, optional): runs at
  `http://<hs-ip>:8080/` *from inside HSL3* — no external process.
  Manage discovered + manually-added players (by **IP and/or MAC**;
  MAC is preferred under DHCP because the registry auto-refreshes
  IP-from-MAC via `/proc/net/arp` on every scan), edit the station
  library, and authorize Sonos Cloud OAuth.
- **DHCP-resilient player references.** When Admin is present, LBS
  22000's `Host` input accepts IP / MAC / name / UUID. DHCP renumbering
  no longer breaks wiring — the registry re-resolves on every Tick.
- **Cloud-ready.** Admin's OAuth handler captures Sonos Cloud Control
  API tokens (client_id + client_secret + redirect_uri configured in
  the UI). Local SOAP remains the primary path; the tokens are
  plumbing for a future cloud-fallback LBS.
- **KNX-friendly outputs.** Online, State, Volume, Mute, Title, Artist,
  ActiveStation, LastError, Subscribed — wire to group addresses with
  the recommended DPTs in [homeserver/KNX-MAPPING.md](homeserver/KNX-MAPPING.md).
- **Graceful degradation.** If a listener can't bind, modules fall
  back to timer-based status polling and keep working.
- **30 unit tests** with a stubbed `Hsl3Framework`, runnable in CI.

## Quick start

1. **Build the HSLZ archives** (requires a Python 3.9 with the Gira
   HSL3 generator; falls back to a help-only archive if the generator
   isn't on PATH — see [hsl3/README.md](homeserver/logic-module/hsl3/README.md)):

   ```sh
   python3 homeserver/logic-module/hsl3/build/build_hslz.py
   ```

   Output: `homeserver/logic-module/hsl3/build/dist/22000_sonos_player.hslz`
   and `22001_sonos_admin.hslz`.

2. **Import in Experte.** *Logikbausteine → Importieren* → pick the
   two `.hslz` files. The blocks appear under **Multimedia → Sonos**.

3. **(Recommended) Add the Admin block.** Drop a *Sonos Admin* block
   onto the canvas. Download. Browse to
   `http://<hs-ip>:8080/` — discover players, add players manually by
   IP and/or MAC, define the radio-station library, optionally
   authorize Sonos Cloud.

4. **Add a *Sonos Player* block per player.** Set `Host` to the
   player's IP — or, with Admin loaded, to the player's MAC, name,
   or UUID. Wire control inputs (Play, Pause, SetVolume, …) to KNX
   group addresses. Wire status outputs to group addresses using
   the DPTs in [homeserver/KNX-MAPPING.md](homeserver/KNX-MAPPING.md).

5. **Configure radio stations.** Either write the stream URIs to the
   player block's `Station1Uri … Station8Uri` inputs, or add them
   in the Admin web UI's central library. Trigger playback by
   writing the station index to `StartRadio`.

6. **Download to HomeServer.** Within one Tick interval (~60 s) the
   subscriptions register and status outputs populate.

## Repository layout

```
README.md                                  this file
homeserver/
├── KNX-MAPPING.md                         recommended group-address layout / DPTs
└── logic-module/
    ├── README.md                          logic-module overview
    ├── hsl3/                              the HSL3 deliverable
    │   ├── README.md                      detailed module reference
    │   ├── src_22000_sonos_player/        LogicModule source + config.json
    │   ├── src_22001_sonos_admin/         LogicModule source + config.json (web UI + discovery + OAuth)
    │   ├── help/                          EN + DE help pages, SDK style.css
    │   └── build/
    │       ├── build_hslz.py              packager (.hslz archives)
    │       └── test_logic_modules.py      30 unit tests with framework stub
    └── soap/                              SOAP envelope XML reference
                                           (the same wire format the .py uses)
```

## How it works

The Sonos Player module is a `LogicModule` class that runs in the
HomeServer's HSL3 environment. On `on_init` it registers itself in a
class-level `_instances_by_host` map and starts a shared NOTIFY HTTP
listener (default port 8081, next-free fallback up to 8083). On every
`on_calc` it detects which control input changed and dispatches the
SOAP request in a daemon thread; output writes are marshalled back to
node context via `self.fw.run_in_context(callback, params)` per the
SDK contract. The `Tick` timer drives status polling and UPnP
subscription renewal (re-subscribes automatically on HTTP 412 SID
expiry).

When a Sonos player sends a NOTIFY callback to the listener, the
parser pulls `TransportState`, `Volume`, `Mute`, and track metadata
from the doubly-XML-encoded `LastChange` envelope and updates the
matching instance's outputs. Result: external state changes (someone
uses the Sonos app, AirPlay takes over, volume knob turned) reach KNX
within ~1 second without polling.

Detail in [homeserver/logic-module/hsl3/README.md](homeserver/logic-module/hsl3/README.md).

## Testing

```sh
python3 homeserver/logic-module/hsl3/build/test_logic_modules.py
```

15 tests cover the pure helpers (SOAP fault extraction, NOTIFY
parsing, URI normalisation, iso-8859-15 encoding) and the
`LogicModule` IO contract (string outputs are bytes, numeric outputs
are float/int, offline path, error encoding). The tests use a
`StubFramework` that mirrors the real `Hsl3Framework` surface, so no
HomeServer is required.

## SDK compliance

The modules conform to every rule in the GiraHSL skill:

- Filename `hsl3_<ID>_<name>.py`, mandatory `LogicModule` class,
  constructor takes `hsl3` framework as `self.fw`.
- String outputs encoded as `bytes` with `iso-8859-15`; numeric outputs
  as `float`/`int`; never `str` or `None`.
- `set_output` / `set_timer` / `set_store` only ever called in node
  context. Worker threads use `self.fw.run_in_context(callback, params)`.
- `stores[].type` set on every store entry (generator crashes without
  it).
- No `|` or `"` in any label, name, category, or translation — verified
  by the build script and tests.
- HSLZ archives use the flat-root layout
  (`<ID>_<name>.hsl`, `EN-log<ID>.html`, `DE-log<ID>.html`, `style.css`)
  with the `href="../style.css"` → `href="style.css"` rewrite per SDK.
- Help files use the SDK's required `auto_index_anchor_N_` anchors and
  `table-in` / `table-out` / `table-logic-info` classes.

## Documentation

- [homeserver/logic-module/hsl3/README.md](homeserver/logic-module/hsl3/README.md)
  — full input / output / parameter reference for both modules,
  architecture, build flow, testing.
- [homeserver/logic-module/hsl3/FEATURE-PARITY.md](homeserver/logic-module/hsl3/FEATURE-PARITY.md)
  — exhaustive mapping of every integration feature to its HSL3 home.
- [homeserver/logic-module/hsl3/DESIGN-DECISIONS.md](homeserver/logic-module/hsl3/DESIGN-DECISIONS.md)
  — trade-offs and rationale behind the implementation.
- [homeserver/logic-module/hsl3/MANUAL-VERIFICATION.md](homeserver/logic-module/hsl3/MANUAL-VERIFICATION.md)
  — hardware commissioning checklist.
- [homeserver/KNX-MAPPING.md](homeserver/KNX-MAPPING.md)
  — recommended KNX group-address layout and DPTs.
- [homeserver/logic-module/soap/](homeserver/logic-module/soap/)
  — raw SOAP envelopes and NOTIFY format reference. The same
  envelopes are embedded as string constants in
  `hsl3_22000_sonos_player.py`.

## License

MIT
