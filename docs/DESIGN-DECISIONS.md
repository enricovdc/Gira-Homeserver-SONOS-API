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

## 7. State in module-level globals, not stores

The bridge persisted state in `config.json` on disk. The HSL3 module
keeps subscription SIDs, last-known values, and edge-detection history
in **instance attributes** (`self._sid_av`, `self._last_state`, …).
These survive between block runs (the LogicModule is instantiated once
and reused), but reset when the Experte downloads the project.

Stores (`self.fw.set_store`) are reserved for values that must
survive a download (e.g. a permanent counter). Subscription SIDs
explicitly do not — they are renewed within one Tick of any restart.

A single placeholder `Reserved` store entry exists per module because
the HSL3 generator crashes if `stores` is empty AND because
`stores[].type` is a required field; if a future maintainer adds a
real store they'll have a working pattern to copy.

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

## 9. Direct stream URIs, not Sonos favorites

Reading the player's "Favorites" via `ContentDirectory#Browse` is
brittle:

- The response is heterogeneous DIDL-Lite mixing SMAPI items (Spotify,
  Apple Music) and direct streams.
- SMAPI-bound items require an account token and a service ID that
  changes across firmware.
- The list order is owned by the user via the Sonos app; the LBS
  can't predict the meaning of "favorite #3".

Eight configurable stream URIs let the integrator pick once, from the
Sonos app's "Information" panel, and bind them to KNX. Stable, no
account dependencies, immune to SMAPI changes.

## 10. No Sonos zone-group management

Each LBS instance controls one player by IP. Stereo pairs / surround
setups / coordinated groups are best controlled by targeting the
*group coordinator's* IP and letting Sonos propagate. Modeling group
membership in HSL would have required maintaining a separate
ZoneGroupTopology subscription and a join/leave control surface that
KNX doesn't naturally express.

If grouped control becomes necessary, the right shape is an
additional LBS that subscribes to ZoneGroupTopology and exposes
group-add / group-remove / group-coordinator outputs.

## 11. No external dependencies beyond `requests`

The GiraHSL skill documents `requests`, `websockets`, `beautifulsoup4`,
`pytz`, `python-dateutil`, and `pymodbus` as bundled on the HomeServer.
This integration only needs `requests`. Avoiding the others keeps the
dependency footprint minimal and the module portable across firmware
revisions.
