# Gira HomeServer Sonos integration

Native HSL3 logic modules for the Gira HomeServer / FacilityServer that
control Sonos players over the local UPnP/SOAP API. No bridge service,
no external machine, no SSH access required — the integration runs
entirely inside the HomeServer's own logic engine.

Three LBS modules:

| LBS | Name | Role |
| --- | --- | --- |
| **22000** | Sonos Player | One instance per Sonos player. Play / pause / stop / next / prev (both rising-edge and value-toggle inputs), volume, mute, shuffle, repeat, preset stepping through the Admin library, status outputs (discrete Is\* booleans, per-action \*Allowed flags so a Gira tile can grey out buttons the player would reject, Album, AlbumArtURI, active preset name, group info), UPnP event push. |
| **22001** | Sonos Admin | Singleton companion. Web UI at `http://<hs-ip>:8080/` for managing players (by **UUID / MAC / IP / name**), a global preset library (radio / playlists / line-in / Bluetooth), group presets, project-wide Player Defaults, and Sonos Cloud OAuth. Runs periodic + KNX-triggerable SSDP discovery with a structured `DiscoveredPlayers` output. All inside HSL3, no external process. Optional but recommended. |
| **22002** | Sonos Sound Enhancement | **Optional** per-player companion to LBS 22000. Bass, Treble, Loudness, soundbar Night Mode + Dialog Mode, Crossfade, Sleep Timer, soundbar TV input, status LED, battery (Move/Roam), group volume, surround + sub EQ, Trueplay status. Drop one in for any speaker that needs the extras; leave it off elsewhere. Soundbar / portable-only features no-op gracefully on other hardware. |

Compatible with Sonos firmware **2024+ and 2026** and Gira HomeServer
firmware **4.13+** (HSL3 / Python 3.9 logic-module SDK).

## Features

- **Pure HomeServer install.** Import the `.hslz` archives in Experte
  via *Logikbausteine → Importieren*. No bridge, no SSH, no Linux box.
- **Local control only.** Talks to Sonos players directly via SOAP on
  port 1400. No Sonos OAuth required for control, no developer
  registration, no rate limits, no cloud dependency.
- **UPnP event push.** Each player subscribes to AVTransport +
  RenderingControl events; status outputs update in ~1 s when state
  changes externally (Sonos app, AirPlay handoff, volume knob).
- **Firmware-2026 hardening.** Metadata-free SetAVTransportURI, the
  `x-rincon-mp3radio://` direct-broadcast scheme, and an automatic
  fallback ladder for players that reject the first attempt. Container
  URIs (Spotify / Apple / Sonos saved queues) take the queue-and-play
  path so playlists actually play.
- **Web admin UI** (LBS 22001, optional): runs at
  `http://<hs-ip>:8080/` *from inside HSL3* — no external process.
  Manage discovered + manually-added players, browse a player's
  Favorites (FV:2) + audio inputs (AI:) + saved playlists (SQ:), edit
  the global preset library — including join presets that make a
  player follow a master speaker — define group presets, set
  project-wide Player Defaults (PollInterval / SubTimeout /
  HttpTimeout / CallbackBase), authorize Sonos Cloud OAuth.
- **DHCP-resilient player references.** When Admin is present, LBS
  22000's `Host` input accepts UUID / MAC / name / IP. UUID is
  preferred — stable across firmware updates and DHCP renumbering.
- **Group presets.** Predefine master + members in the Admin UI;
  trigger `GroupPreset` on any Player block to form the group.
  `Ungroup` breaks the player out again.
- **KNX-friendly outputs.** ZoneName, Online, State, IsPlaying /
  IsPaused / IsStopped / IsTransitioning, PlayAllowed / PauseAllowed /
  StopAllowed / NextAllowed / PrevAllowed / ShuffleAllowed /
  RepeatAllowed, Volume, Mute, Title, Artist, Album, AlbumArtURI,
  ShuffleState, RepeatState, GroupInfo, IsCoordinator, ActiveStation,
  ActiveStationName, LastError, Subscribed — wire to group addresses with the recommended
  DPTs in [docs/KNX-MAPPING.md](docs/KNX-MAPPING.md).
- **Persistence.** Admin's registries (players, presets, group
  presets, cloud credentials, player defaults) survive HomeServer
  restarts via HSL3 retentive stores.
- **Graceful degradation.** If a listener can't bind, modules fall
  back to timer-based status polling and keep working.
- **Join presets.** Create a preset in the Admin UI that joins another
  speaker instead of playing a stream: name it, pick the master from
  the dropdown, and that preset (triggerable via `StartRadio` /
  `StartRadioName` / `PresetNextPrev`) makes this player follow the
  master's playback.
- **Sounds library.** Upload notification clips (doorbell, alarm, TTS
  recordings) via the Admin UI. The Player block's `PlaySound` input
  invokes Sonos's native `AudioClip.LoadAudioClip` service on S2
  firmware — same path Home Assistant's `announce: true` uses — so the
  player ducks the music, plays the clip, and resumes automatically.
  On S1 hardware (no AudioClip service) it falls back to a snapshot /
  play / restore cycle. Library starts empty; drop in any MP3 / WAV /
  AAC / OGG / FLAC up to 2 MB.
- **Optional sound-tuning companion (LBS 22002).** Per-player,
  additive — leave it off when you don't need the extras. 16 inputs
  / 20 outputs covering Bass, Treble, Loudness, soundbar Night Mode
  + Dialog Mode, Crossfade, Sleep Timer, soundbar TV input,
  status LED, battery + charging (Move/Roam), proportional group
  volume, surround channels + sub gain, Trueplay status. Each
  hardware-specific knob (soundbar EQ, portable battery, coordinator
  group volume) fails gracefully on the wrong hardware with a tagged
  LastError so the integrator can spot misconfigured wiring instantly.
- **No tunables on the Player blocks.** `PollInterval`, `SubTimeout`,
  `HttpTimeout`, and `CallbackBase` live entirely in the Admin web UI:
  project-wide *Player Defaults* plus optional per-player *Advanced
  overrides* on each player card. One place to tune, no input wires
  to maintain.
- **143 unit tests** with a stubbed `Hsl3Framework`, runnable in CI.

## Repository layout

```
.
├── README.md                    this file
├── requirements.txt             runtime deps (just `requests`, bundled by Gira)
├── docs/                        design / wire-format / commissioning reference
├── help/                        EN + DE help pages and SDK style.css
│   ├── en/log22000.html         English help, F1 in Experte
│   ├── en/log22001.html
│   ├── de/log22000.html
│   ├── de/log22001.html
│   └── style.css                SDK stylesheet
├── projects/
│   ├── sonos_player_hsl3/       LBS 22000 source: config.json + hsl3_22000_*.py
│   ├── sonos_admin_hsl3/        LBS 22001 source: config.json + hsl3_22001_*.py
│   └── sonos_sound_hsl3/        LBS 22002 source: config.json + hsl3_22002_*.py
├── scripts/
│   └── build_hslz.py            packager → dist/*.hslz
├── tests/
│   └── test_logic_modules.py    143 unit tests with a stubbed framework
└── dist/
    ├── 22000_sonos_player.hslz  deployable archive (committed)
    ├── 22001_sonos_admin.hslz
    └── 22002_sonos_sound.hslz
```

## Quick start

1. **Import the .hslz files in Experte.** *Logikbausteine →
   Importieren* → pick the two archives from `dist/`. The blocks
   appear under **Multimedia → Sonos**.

2. **(Recommended) Add the Admin block.** Drop a *Sonos Admin* block
   onto the canvas. Download. Browse to `http://<hs-ip>:8080/` —
   discover players, add players manually, browse Favorites, build
   the preset library, define group presets, set defaults.

3. **Add a *Sonos Player* block per player.** Set `Host` to the
   player's UUID (copy from the Admin UI by clicking the UUID on the
   card). Wire control inputs to KNX group addresses. Wire status
   outputs using the DPTs in [docs/KNX-MAPPING.md](docs/KNX-MAPPING.md).

4. **Download to HomeServer.** Within one Tick interval (default 60 s)
   the UPnP subscriptions register and status outputs populate.

## Build the .hslz from source

The committed archives in `dist/` are ready to import. To rebuild
after editing a `.py` or `config.json` you need the Gira HSL3
generator (closed-source Python 3.9 bytecode shipped with the Experte
SDK):

```sh
GIRA_HSL3_GEN=/path/to/generator3.cpython-39.pyc \
PYTHON39=/path/to/python3.9 \
python3 scripts/build_hslz.py
```

Without the generator the script still produces a spec-compliant
archive shell with help + style.css; drop the `.hsl` into the archive
on a machine that has the SDK.

## Run the tests

```sh
python3 tests/test_logic_modules.py
```

143 tests covering: pure helpers (SOAP fault extraction, NOTIFY parsing,
URI normalisation, play-mode composition, transport-actions parsing,
iso-8859-15 encoding), the
`LogicModule` IO contract (string outputs are bytes, numeric outputs
are float/int), Admin registry CRUD + persistence round-trip, group
preset dispatch, container playback (queue-and-play), and the
Admin-default fallback for Player tunables. No HomeServer needed —
the `StubFramework` mirrors the real `Hsl3Framework` surface.

## Documentation

- [docs/KNX-MAPPING.md](docs/KNX-MAPPING.md) — recommended group-address
  layout and DPTs.
- [docs/FEATURE-PARITY.md](docs/FEATURE-PARITY.md) — exhaustive mapping
  of every integration feature to where it lives in the code.
- [docs/DESIGN-DECISIONS.md](docs/DESIGN-DECISIONS.md) — trade-offs and
  rationale behind the implementation.
- [docs/MANUAL-VERIFICATION.md](docs/MANUAL-VERIFICATION.md) —
  hardware-in-the-loop commissioning checklist.
- [docs/HOMESERVER-TILE.md](docs/HOMESERVER-TILE.md) — how to surface
  the Admin URL on the HomeServer's start page as a tile.
- [docs/sonos-wire-format.md](docs/sonos-wire-format.md) — UPnP NOTIFY
  payload reference + raw SUBSCRIBE / RENEW / UNSUBSCRIBE format.

## SDK compliance

The modules conform to every rule in the GiraHSL skill:

- Filename `hsl3_<ID>_<name>.py`, mandatory `LogicModule` class,
  constructor takes `hsl3` framework as `self.fw`.
- String outputs encoded as `bytes` with `iso-8859-15`; numeric outputs
  as `float`/`int`; never `str` or `None`.
- `set_output` / `set_timer` / `set_store` only ever called in node
  context. Worker threads use `self.fw.run_in_context(callback, params)`.
- `stores[].type` set on every store entry.
- No `|` or `"` in any label, name, category, or translation —
  verified by the build script's record-5000 field-count check.
- HSLZ archives use the flat-root layout
  (`<ID>_<name>.hsl`, `EN-log<ID>.html`, `DE-log<ID>.html`,
  `style.css`) with the `href="../style.css"` → `href="style.css"`
  rewrite per SDK.

## License

MIT
