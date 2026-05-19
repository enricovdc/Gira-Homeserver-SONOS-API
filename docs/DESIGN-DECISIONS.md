# Design decisions

The trade-offs and rationale behind the HSL3 modules. Useful when a
future maintainer is tempted to "simplify" something and the load-bearing
reasoning isn't obvious from the code alone.

## 1. Local UPnP/SOAP, not the Sonos Cloud Control API

The Sonos Cloud Control API requires:

- An OAuth 2.0 flow with a developer-registered application.
- Token storage + refresh logic.
- Cloud reachability from the HomeServer.
- Compliance with Sonos's developer terms.

The local SOAP API on port 1400 is the same surface every successful
third-party integration uses (Home Assistant, node-sonos, SoCo …). No
rate limits, no cloud dependency, no developer registration. It is
the right primitive for a HomeServer install.

## 2. One LogicModule instance per player, not one global manager

HSL3 instantiates one `LogicModule` per node placed on the canvas.
Building a single-node manager that internally tracks every player
would have meant:

- Re-implementing instance dispatch in our own code (which player did
  this trigger fire on?).
- Losing the Experte's "click a node, see its state" debug
  affordance.
- Confusing wiring: one set of inputs would have to demultiplex by
  player name.

One node per player makes the Experte wiring obvious and gives each
player its own debug section.

## 3. Shared NOTIFY HTTP listener via class-level state

All player instances share **one** HTTP listener thread bound on port
8081. The first instance to call `_ensure_listener_started` binds the
port; subsequent instances reuse the same thread. Each instance
registers itself in the `_instances_by_host` class-level registry so
the listener can route incoming NOTIFY POSTs to the right player.

Alternative considered: one listener per player. Rejected because
every Sonos player accepts only one CALLBACK URL per subscription, but
we need two services (AVTransport + RenderingControl). Multiplying
ports for N players × 2 services becomes a firewall nightmare quickly.
A single port that demultiplexes by URL path is simpler and matches
how `node-sonos-http-api` does it.

## 4. Metadata-free DIDL-Lite, with a fallback ladder

The `SetAVTransportURI` envelope this module sends has an **empty**
`<CurrentURIMetaData>` element. Older Sonos firmware accepted a rich
DIDL-Lite item with a SMAPI cloud-service binding (the
`SA_RINCON65031_` TuneIn handoff used by some third-party
integrations). Sonos firmware from 2024 onwards has tightened SMAPI
gating; the rich form is increasingly unreliable.

If even the empty form is rejected (UPnP error codes 714 / 716 / 800),
`_action_start_radio` retries with the raw URI (no `x-rincon-mp3radio`
rewrite). This three-layer ladder has been observed to recover on
every Sonos firmware tested so far including the 2024 wave.

## 5. `x-rincon-mp3radio://` URI scheme rewriting

Plain `http://` URIs are rewritten to `x-rincon-mp3radio://` before
being sent in `SetAVTransportURI`. This tells the Sonos player to treat
the stream as a continuous radio broadcast (no end-of-file, no auto-next,
correct `streamContent` ICY metadata handling).

It is a property of the player, not a Sonos cloud service, so it
remains stable across firmware generations. The fallback ladder
includes the raw URI to cover any future case where the rewritten
form is rejected.

## 6. Threading + `run_in_context`

`set_output` / `set_timer` / `set_store` must only be called in node
context (SDK rule). On_calc and on_timer run **in** node context, but
HTTP work would block all HSL3 nodes that share the context if done
inline. So:

- Worker threads handle every HTTP request (`requests.post`,
  `requests.request` for custom UPnP methods).
- Output writes are marshalled back via
  `self.fw.run_in_context(callback, params)` — the SDK guarantees the
  callback runs in node context.
- `_run_safely` wraps every worker function in a try/except that logs
  the exception and writes `LastError`.

This is the pattern shown in the GiraHSL skill's `examples.md` for
HTTP-poller modules. It is the only safe way to do network I/O in
HSL3.

## 7. Volatile state in instance attributes; retentive stores only for what truly persists

The two modules split state differently:

- **Player (LBS 22000)** keeps subscription SIDs, last-known
  outputs, edge-detection history, and the snapshot taken before a
  sound clip plays in **instance attributes** (`self._sid_av`,
  `self._last_state`, `self._active_station`, …). These survive
  between block runs (the LogicModule is instantiated once and
  reused) but reset when the Experte downloads the project. The
  Player has no retentive stores. Anything worth persisting lives in
  the Admin.

- **Admin (LBS 22001)** has six retentive stores:
  `PersistedPlayers` (discovered + manual players),
  `PersistedStations` (preset library, including DIDL-Lite metadata
  for cloud-service favorites), `PersistedGroups` (group presets),
  `PersistedCloud` (OAuth credentials + tokens),
  `PersistedPlayerDefaults` (project-wide tunable defaults), and
  `PersistedSounds` (uploaded notification clips, audio bytes
  base64-encoded inside the JSON blob). On `on_init` they reload;
  every mutation writes back via `_persist` (marshalled into node
  context so HTTP-thread mutations don't trip `Hsl3ContextError`).

Subscription SIDs deliberately are NOT stored — they re-bootstrap
within one Tick of any restart, and a stale SID after a player
reboot would fail HTTP 412 anyway.

## 8. Status polling alongside event push

Even though UPnP events deliver state changes in ~1 s, the `Tick`
timer also issues a SOAP status poll. Reasons:

- Recovery if NOTIFYs were lost (network glitch, listener restart).
- Heartbeat: a player that doesn't respond to the poll is marked
  `Online = 0`, which the bridge could not detect from absence of
  NOTIFYs alone.
- Initial population: between block start and the first NOTIFY there
  may be a gap; the poll fills outputs immediately.

The cost is low (3 SOAP calls every `PollInterval` per player) and
the operational gain is substantial.

## 9. Admin-managed preset library, not per-player URI inputs

An earlier revision shipped eight `StationNUri` inputs per Player
block. It worked but had three sharp edges:

- **Eight is arbitrary**. Real installations have ~3 presets for the
  bathroom radio, ~15 for the living room, none in the bedroom. A
  fixed slot count is the wrong shape.
- **Editing presets meant editing the project**. A new radio URL
  required opening Experte, finding every player block that should
  expose it, and re-downloading.
- **Cloud-service URIs (TuneIn / Spotify / Apple Music) don't survive
  a bare URI**. They need DIDL-Lite metadata with the music-service
  binding (`<desc id="cdudn">SA_RINCON…</desc>`) — there's no way to
  paste that into an Experte string input.

The Admin module's preset library solves all three: one centralised
list across every Player block, edited via the web UI without
re-downloading the project, with the full DIDL-Lite metadata
captured when the integrator imports a Sonos Favorite. LBS 22000's
`StartRadio` / `StartRadioName` look it up via `sys.modules` —
zero coupling, optional dependency (the Player works standalone but
without preset capability).

For radio-stream presets the metadata-fallback ladder still applies
(`x-rincon-mp3radio://` rewrite + retry on UPnP error codes 714 /
716 / 800), so the preset library doesn't lose direct-stream
robustness.

## 10. Group presets — pre-define, don't ad-hoc

Sonos exposes zone-group joining via
`SetAVTransportURI(x-rincon:<master-uuid>)`. Modelling that as live
inputs on every Player block (master-uuid + join/leave) would have
required mirroring `ZoneGroupTopology` and a join/leave control
surface KNX doesn't naturally express.

Instead, the Admin holds named group presets (master + members,
typed and validated against the player registry). LBS 22000's
`GroupPreset` / `GroupPresetName` triggers the dispatch; `Ungroup`
breaks this player out via `BecomeCoordinatorOfStandaloneGroup`.
This puts the multi-room control surface in the same place as the
multi-room *definition*, and a "kitchen + bathroom" group becomes
a single KNX address — not a multi-block scene logic.

Member status surfaces back via the `IsCoordinator` + `GroupInfo`
outputs (derived from `CurrentTrackURI` starting with `x-rincon:`),
so visualisations can show "Living Room: following Kitchen" without
the LBS needing a separate ZoneGroupTopology subscription.

## 11. Notification announcements via Sonos's native AudioClip

For doorbells / alarms / TTS, the Sonos S2 firmware exposes
`AudioClip.LoadAudioClip` at `/AudioClip/Control` (service
`urn:schemas-sonos-com:service:AudioClip:1`). It plays a clip on top
of the current source — the player ducks the music, plays the clip,
and resumes automatically. Same path Home Assistant's `announce:
true` uses.

The implementation tries this first. Older S1 hardware (no AudioClip
service) returns a SOAP fault, at which point we fall back to the
snapshot/restore pattern: `GetMediaInfo` + `GetPositionInfo` +
`GetTransportInfo` + `GetVolume` + `GetMute` → play the clip → poll
for `STOPPED` → `SetAVTransportURI` back + `Seek` + restore Volume
+ Mute + (re-)`Play`. Strictly worse — there's an audible gap —
but functional on every Sonos generation.

The sound bytes are served by the Admin's HTTP listener at
`/sounds/<id>/<filename>`. No external file hosting needed.

## 11. No external dependencies beyond `requests`

The GiraHSL skill documents `requests`, `websockets`, `beautifulsoup4`,
`pytz`, `python-dateutil`, and `pymodbus` as bundled on the HomeServer.
This integration only needs `requests`. Avoiding the others keeps the
dependency footprint minimal and the module portable across firmware
revisions.
