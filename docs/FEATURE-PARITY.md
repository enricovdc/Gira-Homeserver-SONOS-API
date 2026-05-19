# Feature parity audit

Every user-facing function the integration exposes, and where it lives
in the HSL3 modules. Refreshed against the v1.0.0 module shipped in
this repo: **LBS 22000 Sonos Player** with 27 inputs / 28 outputs and
**LBS 22001 Sonos Admin** with 7 inputs / 8 outputs + 5 retentive
stores.

## Sonos control surface (LBS 22000 inputs)

| Function | Input | Notes |
| --- | --- | --- |
| Play | `Play` (E2) | Rising edge → `_action_play` |
| Pause | `Pause` (E3) | Rising edge → `_action_pause` |
| Stop | `Stop` (E4) | Rising edge → `_action_stop` |
| Next track | `Next` (E5) | Rising edge → `_action_next` |
| Previous track | `Prev` (E6) | Rising edge → `_action_previous` |
| Set absolute volume | `SetVolume` (E7) | Numeric 0–100 |
| Volume up by step | `VolUp` (E8) | Reads current, adds `VolStep` |
| Volume down by step | `VolDown` (E9) | Reads current, subtracts `VolStep` |
| Set mute on / off | `SetMute` (E10) | Numeric 0/1 |
| Toggle mute | `MuteToggle` (E11) | Rising edge → read + invert + write |
| Set shuffle on / off | `SetShuffle` (E12) | Composed with `SetRepeat` into Sonos `PlayMode`; changing one input preserves the other |
| Set repeat-all on / off | `SetRepeat` (E13) | REPEAT_ONE surfaces on `RepeatState` but is not exposed as a separate input |
| Start preset by index | `StartRadio` (E14) | Alphabetical 1..N into the Admin preset library |
| Start preset by name | `StartRadioName` (E15) | Case-insensitive lookup in the same library |
| Form group preset by index | `GroupPreset` (E16) | Alphabetical 1..N into the Admin group-preset library; master + members come from the Admin definition |
| Form group preset by name | `GroupPresetName` (E17) | Case-insensitive |
| Break out of current group | `Ungroup` (E18) | Rising edge → `BecomeCoordinatorOfStandaloneGroup` |
| Force UPnP re-subscribe | `Resubscribe` (E19) | Rising edge clears SIDs + re-subscribes |
| Play / Pause toggle (single 1-bit GA) | `PlayPause` (E20) | Value-driven. Writing 1 plays, writing 0 pauses. Pairs with one KNX toggle GA. |
| Next / Previous track toggle | `NextPrev` (E21) | Value-driven. Writing 1 = next, writing 0 = previous. |
| Next / Previous preset toggle | `PresetNextPrev` (E22) | Steps through the Admin preset library alphabetically. Wraps at both ends. NO_PRESETS on LastError if the library is empty. |

## Status / observability (LBS 22000 outputs)

| Function | Output | Notes |
| --- | --- | --- |
| Sonos Zone Name | `ZoneName` (A1) | Refreshed each Tick from `<roomName>`; picks up renames from the Sonos app within one poll interval |
| Player online state | `Online` (A2) | 1 = recently responded |
| Friendly playback state | `State` (A3) | `Playing` / `Paused` / `Stopped` / `Transitioning` / `Buffering` / `Connecting` / `No media` / `Playing TV` / `Playing line-in`. Sonos `ZPSTR_` leaks are normalised. |
| Is playing | `IsPlaying` (A4) | 1 when `State` is `Playing`. Mutually exclusive with other `Is*` flags. |
| Is paused | `IsPaused` (A5) | |
| Is stopped | `IsStopped` (A6) | |
| Is transitioning | `IsTransitioning` (A7) | Brief; Sonos passes through this when starting / changing tracks |
| Play allowed right now | `PlayAllowed` (A8) | 1 when the player will accept a Play. From Sonos's `CurrentTransportActions`. 0 when offline. |
| Pause allowed | `PauseAllowed` (A9) | Typically 0 for live radio streams (cannot be paused) |
| Stop allowed | `StopAllowed` (A10) | |
| Next allowed | `NextAllowed` (A11) | Typically 1 for queue / playlist, 0 for radio |
| Previous allowed | `PrevAllowed` (A12) | |
| Shuffle allowed | `ShuffleAllowed` (A13) | Heuristic: same as `NextAllowed` — there must be a queue to navigate |
| Repeat-all allowed | `RepeatAllowed` (A14) | Same heuristic as `ShuffleAllowed` |
| Current volume | `Volume` (A15) | 0–100 |
| Current mute | `Mute` (A16) | 0/1 |
| Current track title | `Title` (A17) | Falls back to `streamContent` for radio; `ZPSTR_` leaks normalised |
| Current artist | `Artist` (A18) | |
| Current album | `Album` (A19) | Empty for radio streams |
| Album-art URL | `AlbumArtURI` (A20) | Relative `/getaa?...` paths converted to absolute `http://<player-ip>:1400/getaa?...` so a Gira visualisation tile can use them directly |
| Shuffle state | `ShuffleState` (A21) | 1 when the player is in any SHUFFLE mode; reflects changes made from the Sonos app |
| Repeat state | `RepeatState` (A22) | 1 when REPEAT_ALL or REPEAT_ONE |
| Group master | `GroupInfo` (A23) | Empty when coordinator / standalone; master's Zone Name (resolved via Admin) or RINCON UUID when a slave |
| Is coordinator | `IsCoordinator` (A24) | 1 when this player is the group coordinator or standalone, 0 when slave. Derived from `CurrentTrackURI` starting with `x-rincon:` |
| Active preset index | `ActiveStation` (A25) | 0 = none. Alphabetical 1..N index of the last preset started, regardless of whether it was selected by index, name, or `PresetNextPrev` |
| Active preset name | `ActiveStationName` (A26) | Preset's display name from the Admin library |
| Last error code | `LastError` (A27) | UPnP code, `UNREACHABLE`, `HTTP_<n>`, `PRESET_NOT_FOUND`, `NO_PRESETS`, `GROUP_NOT_FOUND`, `GROUP_PARTIAL`, `SET_PLAY_MODE_FAILED`, `EXCEPTION: …` |
| UPnP subscription health | `Subscribed` (A28) | 1 = both AVTransport + RenderingControl subscriptions alive |

## Player configuration (LBS 22000 tunable inputs)

| Function | Input | Default | Notes |
| --- | --- | --- | --- |
| Player host | `Host` (E1) | "" (required) | UUID (preferred) / MAC / name / IPv4. Admin resolves the first three to a current IP. |
| Volume step | `VolStep` (E23) | 2 | Applied by `VolUp` / `VolDown` |
| Status poll interval | `PollInterval` (E24) | 60 s | Leave at 0 to use the Admin's Player Defaults value |
| UPnP subscription timeout | `SubTimeout` (E25) | 1800 s | Leave at 0 to use the Admin's Player Defaults value |
| HTTP request timeout | `HttpTimeout` (E26) | 5 s | Leave at 0 to use the Admin's Player Defaults value |
| Callback base URL | `CallbackBase` (E27) | "" | Empty → Admin default → auto-detected `http://<lan-ip>:<listener-port>` |

## Presets (Admin library, no per-player slots)

| Function | Location | Notes |
| --- | --- | --- |
| Maintain the preset library | LBS 22001 Admin web UI (Presets section) | One source of truth across every player. Captures URI + DIDL-Lite metadata + type tag (radio / playlist / source / track). |
| Import a player's Favorites | Admin UI "Favorites" button on a player card | Browses FV:2 (Sonos Favorites), AI: (audio inputs — Line-In on Connect:Amp / Bluetooth on Era 100 / TV on Beam …), and SQ: (saved Sonos playlists). |
| Start preset by index | LBS 22000 input `StartRadio` | Alphabetical 1..N into the library |
| Start preset by name | LBS 22000 input `StartRadioName` | Case-insensitive |
| Direct-stream presets | `_action_start_radio` direct-play path | `x-rincon-mp3radio://` rewrite + metadata-rejection fallback ladder (UPnP error codes 714/716/402/501/800) |
| Cloud-service presets (TuneIn / Spotify / Apple) | `_action_start_radio` cloud path | Preserves the music-service binding from the captured `<r:resMD>` metadata — without it Sonos can't resolve the URI |
| Container playback (playlists, saved queues, albums) | `_play_via_queue` | `RemoveAllTracksFromQueue` → `AddURIToQueue` → `SetAVTransportURI(x-rincon-queue:<uuid>#0)` → `Play`. Routed by `_is_container_uri` |
| Cross-LBS station lookup | `get_station` / `get_station_uri` module-level helpers in the Admin | LBS 22000 calls via `sys.modules` lookup; returns the full record including metadata |
| Group-join preset | Preset record with `uri="x-rincon:RINCON_<master>"` and `type="join"` | Admin UI offers a dedicated "Add join preset" form with a master dropdown so the integrator never types the URI by hand. The Player's `_is_group_join_uri` detects the bare `x-rincon:` scheme and dispatches `SetAVTransportURI` without a subsequent `Play` — slaves auto-inherit the master's transport state. Triggered like any other preset via `StartRadio` / `StartRadioName` / `PresetNextPrev`. |

## Group presets

| Function | Location | Notes |
| --- | --- | --- |
| Define group presets | Admin web UI "Group presets" section | Master + members; master auto-stripped from members list |
| Form a group | LBS 22000 input `GroupPreset` / `GroupPresetName` | Resolves the master to a RINCON UUID, then issues `SetAVTransportURI(x-rincon:<master-uuid>)` on each member |
| Break out of a group | LBS 22000 input `Ungroup` | `BecomeCoordinatorOfStandaloneGroup` on this player only; other members keep playing on the master |
| Partial-failure reporting | `_action_group_form` | `GROUP_PARTIAL: <n> members not joined` on `LastError` when some members were unresolvable or rejected the SOAP |
| Cross-LBS group lookup | `get_group` module-level helper in the Admin | Returns `{id, name, master, members}` by index or name |

## Discovery (LBS 22001)

| Function | Location | Notes |
| --- | --- | --- |
| Periodic SSDP scan | `_run_discovery` via `on_timer` | Tunable via `AutoDiscoverInterval` input (default 300 s, floor 60 s) |
| On-demand SSDP scan | `TriggerDiscovery` input or web UI "Scan now" button | Rising edge / POST `/api/players/discover` |
| Multi-target SSDP (2024+ firmware safe) | `_ssdp_scan` in `hsl3_22001_sonos_admin.py` | ZonePlayer + SpeakerGroup + `ssdp:all`, filtered by `SERVER` / `USN` |
| ARP-based MAC ↔ IP resolution | `_resolve_mac_for_ip`, `_resolve_ip_for_mac` | Reads `/proc/net/arp`. Used so a manually-added MAC keeps tracking the current DHCP IP. |
| Result list | `DiscoveredPlayers` output | Newline-separated `ip;uuid;model` |
| Result count | `LastDiscoveryCount` output | |
| Registry merge | `scan_and_merge` | Discovery only adds / refreshes records; never overwrites manual entries or user-edited zone names |
| Persistence across HS restart | Retentive stores (see below) | The first discovery after a restart merges on top of the restored registry instead of starting blank |

## UPnP event push (no polling)

| Function | Location | Notes |
| --- | --- | --- |
| Initial SUBSCRIBE | `_subscribe_or_renew` called from `_maintain_subscriptions` | First Tick after `on_init` issues subscriptions for AVTransport + RenderingControl |
| Auto-renew before timeout | Same | Renews when `(exp - now) < renew_threshold_s` (= `SubTimeout / 6`, min 30 s) |
| Re-bootstrap on HTTP 412 (SID expired after player reboot) | Same | Discards SID, falls through to initial SUBSCRIBE |
| NOTIFY listener | Shared class-level HTTP server, default port 8081 | `_ensure_listener_started`; falls back to next free port up to 8083, then disables event push and continues polling-only |
| NOTIFY routing by player | `_instances_by_host` registry keyed on the raw Host spec (typically a UUID) | DHCP renumbering doesn't break routing — the spec stays stable |
| Parse `LastChange` envelope | `parse_notify` | Doubly-XML-decoded; extracts state, play mode, volume, mute, title, artist, album, album-art, stream content, track URI |
| Group-membership derivation | `extract_group_master_uuid` | Reads `CurrentTrackURI`; `x-rincon:RINCON_xxx` = slave (UUID = master) |
| Status poll fallback | `_tick_work` runs every `PollInterval` seconds | Bridges over missed NOTIFYs and detects offline players. Also runs `GetTransportSettings` to refresh `ShuffleState` / `RepeatState`. |

## Persistence (LBS 22001 retentive stores)

| Store | Holds | Survives HS restart |
| --- | --- | --- |
| `PersistedPlayers` | Discovered + manually-added players | yes |
| `PersistedStations` | Preset library entries (incl. DIDL-Lite metadata + type tag) | yes |
| `PersistedGroups` | Group presets (master + members) | yes |
| `PersistedCloud` | Sonos Cloud OAuth credentials + tokens | yes |
| `PersistedPlayerDefaults` | Project-wide Player tunable defaults | yes |

Writes go through `_persist()` after every mutation, marshalled into
node context via `run_in_context` so the HTTP-handler thread doesn't
trigger `Hsl3ContextError`. Garbage in any store is tolerated —
`_load_persisted` swallows malformed JSON and falls back to empty
registries.

## Error handling

| Function | Location | Notes |
| --- | --- | --- |
| `UNREACHABLE` mapping | `_soap` catches `requests.exceptions.RequestException` | Written to `LastError` |
| SOAP fault parsing | `extract_soap_fault_code` pulls `<errorCode>` | UPnP code written as `LastError` |
| Metadata rejection fallback | `_action_start_radio` retries on codes 714 / 716 / 402 / 501 / 800 | Cloud-bound presets skip the fallback (the music-service binding would be lost) |
| Worker-thread exceptions | `_run_safely` wraps every action | Logs + writes `LastError = EXCEPTION: …` |
| Listener thread crashes | `_NotifyHandler.do_NOTIFY` catches and returns 500 | Listener never dies on a malformed callback |
| Persistence serialisation errors | `_persist` `try` / `except (TypeError, ValueError)` | Logs and skips the persist for that cycle; never blocks the web UI |

## Web admin UI (LBS 22001)

The Admin module brings a runtime web UI into HSL3 without an external
process. Python's `http.server` runs on a daemon thread inside the HSL3
process, using the bundled `requests` / `threading` / `socket`
primitives.

| Function | API route | Implementation |
| --- | --- | --- |
| Listening port report | output `ListenPort` | `_start_server` binds preferred → range → ephemeral |
| Project info | `GET /api/info` | `api_info` |
| List players | `GET /api/players` | `api_list_players` |
| Add player by IP and/or MAC | `POST /api/players` | `api_add_player` |
| Edit a player's name / IP / MAC | `PATCH /api/players/{id}` | `api_update_player` |
| Remove a player | `DELETE /api/players/{id}` | `api_remove_player` |
| Trigger SSDP scan | `POST /api/players/discover` | `api_discover_now` |
| Browse a player's Favorites + AI: + SQ: | `GET /api/players/{id}/favorites` | `api_player_favorites` |
| Browse a player's saved playlists | `GET /api/players/{id}/playlists` | `api_player_playlists` |
| List preset library | `GET /api/stations` | `api_list_stations` |
| Add / edit / remove a preset | `POST` / `PATCH` / `DELETE /api/stations[/{id}]` | `api_add_station` / `api_update_station` / `api_remove_station` |
| List group presets | `GET /api/groups` | `api_list_groups` |
| Add / edit / remove a group preset | `POST` / `PATCH` / `DELETE /api/groups[/{id}]` | `api_add_group` / `api_update_group` / `api_remove_group` |
| Get player defaults | `GET /api/player-defaults` | `api_get_player_defaults` |
| Set player defaults | `PUT /api/player-defaults` | `api_set_player_defaults`. Lower-bound clamping mirrors the Player module's. |
| Sonos Cloud credentials | `GET` / `PUT /api/cloud` | `api_get_cloud` / `api_set_cloud`. `clientSecret` never echoed back to UI. |
| OAuth authorize flow | `GET /oauth/start` | 302 to Sonos with the configured `client_id` + redirect URI |
| OAuth callback + token exchange | `GET /oauth/callback` | POSTs to the Sonos token endpoint, stores access + refresh tokens |
| HomeServer start-page tile | `GET /tile.html` | Inline SVG icon + a link to the Admin URL; see [docs/HOMESERVER-TILE.md](HOMESERVER-TILE.md) |
| Cross-LBS host resolution | n/a (module-level) | `resolve_host(spec)`; LBS 22000 calls it via `sys.modules` lookup. Accepts IP / MAC / UUID / name. |

## What is intentionally NOT in HSL3 (and why)

- **Bearer-token auth on the web UI** — the Admin runs LAN-only by
  default. If you need an external surface, put a reverse proxy with
  auth in front rather than re-implementing auth inside HSL3.
- **Server-Sent Events stream** — superseded by KNX outputs (the
  consumer the HomeServer is built for) and the Admin web UI's
  periodic polling.
- **Outbound webhook to a HS inbound URL** — not needed. The HSL3
  module *is* the HomeServer; it writes outputs directly.

If any of these becomes useful again, they can be added without
changing the LBS 22000 / 22001 pair.
