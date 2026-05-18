# Troubleshooting

## Bridge won't start

- **`No config file found`** — Make sure `config.json` exists in the working
  directory, or set `SONOS_BRIDGE_CONFIG=/abs/path/to/config.json`.
- **`Invalid configuration`** — The error includes a `details.errors[]` list
  with the specific validation problems (duplicate player name, missing
  `streamUri`, …). Fix and restart.
- **`EADDRINUSE`** — Port already in use. Change `server.port` or stop the
  conflicting process (`ss -tlnp | grep <port>`).

## `PLAYER_UNREACHABLE` errors

- Confirm the player IP from the Sonos app (Settings → System → About).
- IPs change if you use DHCP without reservations. Use the discovery
  endpoint to refresh: `curl http://<bridge>/players/discover`.
- The Sonos player listens on TCP port 1400. From the bridge host:
  `curl http://<player-ip>:1400/xml/device_description.xml`. If this fails,
  it's a network problem (VLAN / firewall / mDNS isolation), not a bridge
  problem.

## `SOAP_FAULT` errors

- `faultCode` 701 — "Transition not available". The player is in a state
  where the action makes no sense (e.g. Next while Stopped, or starting a
  stream while in a stereo-pair member role). Try a `stop` first.
- `faultCode` 714 / 716 — "Illegal MIME / no such resource". The metadata
  was rejected. The bridge automatically retries with empty metadata; if
  this still fails, the stream URL itself is unreachable from the Sonos
  player. Test the URL on a desktop player first.
- `faultCode` 800 — "Invalid play mode". Indicates a queue/transport-mode
  mismatch. Usually only seen on Sonos zone members, not coordinators.

## Radio station won't start

1. Confirm the stream URL is reachable from the Sonos player's network
   (not just from your laptop). `x-rincon-mp3radio://` is just `http://`
   under the hood from the player's perspective.
2. Many internet radio stations require redirects (`302`); Sonos follows
   these but won't follow more than a few hops.
3. Some HLS streams (`.m3u8`) need to be referenced by their inner stream
   URL, not the playlist URL. If a stream doesn't play, check what the
   official Sonos app uses (Sonos app → station → Information → URL) and
   put that exact URL in `config.json`.
4. Sonos requires Icecast/Shoutcast-style metadata for `streamContent` to
   show up in the title field; that's a property of the stream itself,
   not the bridge.

## Status never updates in KNX

- Confirm the status timer is firing in the HomeServer (Experte → Diagnose).
- Confirm the receive parser regex matches: enable verbose logging on the
  HomeServer's HTTP request action and inspect the response body.
- The bridge polls Sonos on demand — there is no push. Status that changes
  externally (someone uses the Sonos app) only appears in KNX on the next
  poll cycle. Reduce `pollInterval` in the HS timer if you want faster
  updates, but be reasonable (every 2–5 s is plenty).

## New Sonos firmware broke something

The bridge is designed to be resilient to firmware changes (see *Firmware
compatibility* in the README). If a firmware update breaks playback:

1. Check `/health` still responds — confirms the bridge process is alive.
2. Check `/players/<name>/status` returns 200 — confirms local SOAP API
   still works.
3. If `SetAVTransportURI` fails for radio, check the bridge logs for the
   UPnP error code. The bridge will already have tried the empty-metadata
   fallback; if it still fails, capture the `faultCode` and open an issue.
4. If discovery stops finding players, point the bridge at known IPs via
   `players[].host`. SSDP advertising can change with firmware updates;
   the bridge does not require discovery to function.

## Authentication issues

- `401 UNAUTHORIZED` — Token mismatch. The bridge expects either
  `Authorization: Bearer <token>` or `?token=<token>` in the query string.
- Don't put the token in URL query strings if you log requests anywhere —
  use the header form instead.
