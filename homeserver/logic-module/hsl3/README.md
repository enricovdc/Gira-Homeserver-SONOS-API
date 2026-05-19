# HSL3 logic modules for the Gira HomeServer

Two LBS modules implementing the full Sonos integration as native HSL3
(Python 3.9) logic blocks for the Gira HomeServer / FacilityServer
firmware 4.13+:

| LBS ID | Name | Role |
| --- | --- | --- |
| **22000** | Sonos Player | Per-player control, status, eight radio stations, UPnP event push. One instance per Sonos player. |
| **22001** | Sonos Admin | Singleton companion. Web UI at `http://<hs-ip>:8080/` for players (by **IP and/or MAC**), radio-station library, and Sonos Cloud OAuth. Also runs periodic + KNX-triggerable SSDP discovery and exposes the result on `DiscoveredPlayers` / `LastDiscoveryCount` outputs for direct KNX wiring. Optional; LBS 22000 still works without it. |

LBS IDs are in the third-party range (20000–99999). Register them on
hs-help.net before publishing if you intend to distribute. For internal
use no registration is needed.

> **Note**: an earlier version shipped LBS 22001 as a separate "Sonos
> Discover" node and LBS 22002 as "Sonos Admin". They've been merged
> into the single LBS 22001 Sonos Admin shipped today — Admin's
> discovery covers everything Discover did, and the integrator only
> has one companion to wire up.

## How they relate

- **Standalone**: drop LBS 22000 (Sonos Player) on the canvas, set its
  `Host` to the player's IP, wire KNX. Works as documented in
  `FEATURE-PARITY.md`. No admin module needed.
- **With Admin**: add LBS 22001 (Sonos Admin). Its web UI lets end
  users add/edit players (manually or via SSDP discovery), edit a
  shared station library, and configure Sonos Cloud OAuth. LBS 22000
  then accepts a **player name** or a **MAC address** in its `Host`
  input and resolves it through Admin's registry. DHCP renumbering
  no longer breaks wiring. The Admin block also publishes the latest
  SSDP scan result on its `DiscoveredPlayers` output for KNX-side
  consumers that don't want to use the web UI.

## Directory layout

```
hsl3/
├── README.md                         this file (architecture + build + import)
├── FEATURE-PARITY.md                 every function and where it lives
├── DESIGN-DECISIONS.md               trade-offs + rationale behind the code
├── MANUAL-VERIFICATION.md            hardware-in-the-loop commissioning checklist
├── src_22000_sonos_player/
│   ├── hsl3_22000_sonos_player.py    LogicModule source
│   └── config.json                   inputs / outputs / store / timer / scripts
├── src_22001_sonos_admin/
│   ├── hsl3_22001_sonos_admin.py     web UI + REST + OAuth + SSDP discovery
│   └── config.json
├── help/
│   ├── style.css                     SDK stylesheet (from the GiraHSL skill)
│   ├── en/log22000.html              English help, F1 in Experte
│   ├── en/log22001.html
│   ├── de/log22000.html              German help
│   └── de/log22001.html
└── build/
    ├── build_hslz.py                 packager → dist/*.hslz
    ├── test_logic_modules.py         15 unit tests against a framework stub
    └── dist/                         output artifacts (gitignored)
```

## What's compliant with the SDK

The implementation follows every rule in `gira-hsl/SKILL.md` of your
GiraHSL skill:

- **Filename convention**: `hsl3_<ID>_<name>.py` per LBS.
- **Mandatory `LogicModule` class**: constructor takes the `hsl3`
  framework, stored as `self.fw`.
- **String outputs as bytes**: every `set_output` for a string output
  encodes via `.encode("iso-8859-15", "replace")` (helper `to_iso_bytes`).
- **Numeric outputs as float / int**: never str / None.
- **Set output only in node context**: HTTP work runs in worker threads
  which marshal back via `self.fw.run_in_context(callback, params)`.
- **Timer via `set_timer("Tick", seconds)`** plus `on_timer(timer)` with
  `timer["Tick"].changed`.
- **`stores[].type` field present** on every store entry in config.json
  (the generator crashes without it).
- **No `|` or `"` in any label, name, category, or translation string**
  — the record-5000 invariant. Verified by the test suite and by the
  build script.
- **Available libraries**: only `requests` from the optional list, plus
  Python 3.9 stdlib (`re`, `socket`, `threading`, `time`,
  `http.server`).
- **HSLZ layout**: flat archive root containing the `.hsl`,
  `EN-log<ID>.html`, `DE-log<ID>.html`, and `style.css`. Stylesheet
  href in the HTML is rewritten from `../style.css` to `style.css`
  on packaging.

## Building the deployable `.hsl` and `.hslz`

The Gira HSL3 generator is closed-source Python 3.9 bytecode shipped
with the HS Experte SDK. The packager in `build/build_hslz.py` runs it
if it can find it; otherwise it copies the Python source and config
JSON into `dist/` and bundles only the help and stylesheet into the
`.hslz`. The integrator then runs the generator on a machine that has
the SDK installed and the resulting `.hsl` slots into the same archive.

### With the generator available

```sh
# Point the script at the generator and a Python 3.9 interpreter.
export GIRA_HSL3_GEN=/path/to/generator3.cpython-39.pyc
export PYTHON39=/path/to/python3.9
python3 homeserver/logic-module/hsl3/build/build_hslz.py
```

Output: `homeserver/logic-module/hsl3/build/dist/22000_sonos_player.hslz`
and `22001_sonos_admin.hslz`, each containing the deployable `.hsl`
plus EN+DE help plus style.css. Import the `.hslz` in Experte via
**Logikbausteine → Importieren**.

### Without the generator (fallback)

```sh
python3 homeserver/logic-module/hsl3/build/build_hslz.py
```

Produces the same `.hslz` archives but without the `.hsl` deployable.
Hand the Python source and config.json from `build/dist/` to whoever
has the SDK; they run the generator and drop the resulting `.hsl` into
the archive.

The build script also verifies the record-5000 field count when a
real `.hsl` is produced (per the SDK invariant
`4 + n_inputs + 1 + n_outputs + 1`).

## Running the tests

```sh
python3 homeserver/logic-module/hsl3/build/test_logic_modules.py
```

15 tests cover:

- Pure helpers (`normalize_radio_uri`, `parse_notify`, `extract_*`,
  XML escaping, iso-8859-15 encoding round-trips).
- `LogicModule` IO contract (string outputs are bytes, numeric
  outputs are float/int, error path encodes correctly, status-poll
  offline transition).
- Sonos Admin (MAC / IP normalization, registry CRUD, host resolution
  by IP / MAC / name, station library lookup by name / index, OAuth
  parameter generation, cloud-secret-never-leaked invariant, KNX
  discovery outputs `DiscoveredPlayers` + `LastDiscoveryCount`).

The tests use a `StubFramework` that mirrors the real `Hsl3Framework`
surface (`set_output`, `set_timer`, `set_store`, `get_logger`,
`create_debug_section`, `run_in_context`) so the same code runs in
CI without a HomeServer.

## What the Sonos Player module does on the HomeServer

On `on_init`:

1. Reads all inputs (host, station URIs, intervals).
2. Registers itself in a class-level `_instances_by_host` map keyed
   by the player's host string so the shared NOTIFY listener can
   route UPnP callbacks back to the right instance.
3. Starts the shared NOTIFY HTTP listener on port 8081 (or next free
   port up to 8083). If binding fails the module silently falls back
   to status polling.
4. Arms the `Tick` timer.

On every `on_calc`:

- Detects which input changed and runs the corresponding action in
  a worker thread. The thread issues SOAP via `requests.post` to
  `http://<host>:1400/MediaRenderer/.../Control`, then marshals
  output writes back to node context via `run_in_context`.

On `on_timer` (Tick):

- Re-arms the timer immediately so a slow tick never causes drift.
- Spawns a worker that polls `GetTransportInfo`, `GetVolume`,
  `GetMute`, `GetPositionInfo` and maintains UPnP subscriptions
  (initial SUBSCRIBE, renewal before timeout, automatic
  re-subscribe on HTTP 412 SID-expired).

On NOTIFY arrival (in listener thread):

- Parses the `LastChange` envelope, extracts `TransportState`,
  `Volume`, `Mute`, `CurrentTrackURI`, and `dc:title` / `dc:creator`
  / `r:streamContent` from the encoded inner DIDL-Lite.
- Calls `run_in_context` to update outputs in node context.

This gives ~1 second latency between a state change on the Sonos
player (e.g. someone turning the volume knob, the app pausing
playback, AirPlay handing off) and the corresponding HomeServer
output changing.

## Importing in Experte

1. Open the project in HS Experte 4.13 or newer.
2. **Logikbausteine → Importieren** → select
   `dist/22000_sonos_player.hslz` and `dist/22001_sonos_admin.hslz`.
3. The two new blocks appear under **Multimedia → Sonos**.
4. **(Recommended)** Drag one Sonos Admin block onto the canvas, then
   open `http://<homeserver-ip>:8080/` in a browser to discover players
   or add them by IP / MAC.
5. For each Sonos player:
   - Drag a Sonos Player block onto the logic canvas.
   - Set the `Host` input to the player's IP, MAC, or name from the
     Admin web UI (the Admin block resolves names/MACs to the current
     IP and survives DHCP renumbering).
   - Wire control inputs to KNX group addresses.
   - Wire status outputs to KNX group addresses with the appropriate
     DPTs (see `homeserver/KNX-MAPPING.md` in the parent directory).
6. Download to the HomeServer.

## Web admin UI (LBS 22001)

Drop a *Sonos Admin* block onto the canvas (no inputs needed for the
default config) and download the project. The HSL3 module:

1. Binds an HTTP server on port 8080 (next-free fallback up to 8083,
   then an OS-assigned ephemeral port). `ListenPort` output reports
   the actual port.
2. Starts a periodic SSDP scanner (default every 5 minutes).
3. Maintains a class-level registry of players and stations that
   other LBS modules can read.

Open `http://<homeserver-ip>:<ListenPort>/` in a browser:

### Players section

- Lists every player in the registry with **IP**, **MAC**, model,
  and a `source` tag (`ssdp` for auto-discovered, `manual` for
  added in the UI).
- **Add player** form takes Name + IP + MAC. At least one of IP / MAC
  is required. MAC is preferred when DHCP is in use because the
  module re-resolves IP-from-MAC on every periodic scan, so a DHCP
  renumber doesn't break the integration.
- **Scan now** button triggers an immediate SSDP M-SEARCH.
- Each row's Name / IP / MAC fields are editable inline — PATCH
  saves to the registry.

### Stations section

- A central radio-station library shared across all players.
- **Add station** with Name + Stream URI. The URI accepts plain
  `http://...` (auto-rewritten to `x-rincon-mp3radio://` at playback
  time) or the direct-broadcast form.
- LBS 22000 instances can read this library via the helper
  `get_station_uri(name_or_index)` exported by the Admin module,
  so multiple players can share one station list.

### Sonos Cloud (optional)

- Stores OAuth client credentials and the redirect-base URL.
- **Authorize with Sonos** button starts the OAuth flow:
  `/oauth/start` redirects to Sonos's authorize URL with the right
  parameters; Sonos redirects back to `/oauth/callback` with a
  code; the Admin exchanges the code for access + refresh tokens
  and stores them in the registry.
- `CloudAuthorized` output flips to `1` when a valid token is held.
- Actual cloud-API calls are out of scope for this module (it's the
  plumbing); a future LBS can read the tokens and fall back to the
  Sonos Cloud Control API when local SOAP is unavailable.

## KNX-triggered discovery (formerly LBS 22001 Discover)

The Admin block's `TriggerDiscovery` input (rising edge) launches an
on-demand SSDP scan. After every scan — periodic or triggered — these
outputs are updated for KNX consumers:

| Output | Description |
| --- | --- |
| `DiscoveredPlayers` | String, newline-separated `ip;uuid;model` rows. Wire to a DPT 16.x output if you want to display it on a panel; or split with a parser in another logic block. |
| `LastDiscoveryCount` | Number of players seen in the most recent scan. |
| `PlayerCount` | Total players currently in the registry (SSDP + manual). |

The `DiscoveryTimeout` input (default 4 s) controls how long each scan
waits for responses.

## Using a name or MAC as a player Host

When LBS 22001 (Sonos Admin) is present, LBS 22000's `Host` input
accepts more than a literal IPv4 address:

| Host value | Behaviour |
| --- | --- |
| `192.168.1.50` | Used as-is (backward compatible). |
| `00:0E:58:AB:CD:EF` | Looked up in the Admin registry. If a player has this MAC, its current IP is used. ARP table is consulted if no match. |
| `livingroom` | Case-insensitive name lookup in the registry. Useful when the same player must be referenced from multiple LBS instances. |
| `RINCON_XXX...` | Sonos UUID lookup. |

If resolution fails, the player block writes `LastError = ""` (it
treats the player as offline) and recovers automatically on the next
Tick once the registry updates.

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| `LastError = UNREACHABLE` | Player offline or wrong IP | Check the Sonos app for the player's current IP; trigger an SSDP scan from the Sonos Admin web UI (or wait for the periodic refresh). |
| `LastError = HTTP_<code>` | Sonos returned an unexpected HTTP status | Look at the player's debug page in Experte and any SOAP fault code; check that the player isn't a stereo-pair member (control the coordinator instead). |
| `Subscribed = 0` permanently | NOTIFY listener didn't bind (port 8081 unavailable, firewall) | The module falls back to polling automatically. Check the *Listener port* field on the debug page; if it shows `disabled`, free up the port or accept polling-only operation. |
| Outputs not updating in KNX | Wiring missing in Experte or KNX bus down | Confirm the LBS output is wired to a KNX group address; check `Online`/`State` change in the Experte debug view. |
| `LastError = STATION_N_NOT_CONFIGURED` | Station N's `StationNUri` input is empty | Set the URI (use the player's own stream URL from the Sonos app's "Information" panel). |
| Subscriptions drop after 30 min | `Tick` timer not firing or `SubTimeout` < 60 s | Ensure `PollInterval` >= 10 and `SubTimeout` >= 60. Default values are safe. |
| New firmware breaks playback | A future Sonos firmware update could change SOAP behaviour | The fallback ladder in `_action_start_radio` handles known metadata-rejection codes (714, 716, 800). Open an issue with the new `errorCode` from the player's response. |
