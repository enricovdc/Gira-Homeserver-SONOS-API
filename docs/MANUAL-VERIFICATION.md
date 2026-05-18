# Manual verification checklist

Run through this list against a real Sonos player when commissioning the
bridge for the first time, after a firmware update, or after changes to
the network configuration.

> The automated test suite (`npm test`) covers all of this against a mocked
> Sonos transport. This checklist is for hardware verification.

## Setup

- [ ] Node 18+ installed on the bridge host.
- [ ] `config.json` created from `config.example.json`.
- [ ] At least one player configured with the correct LAN IP.
- [ ] At least two radio stations configured.
- [ ] Bridge starts cleanly: `npm start` shows `Bridge listening` in logs.

## Connectivity

- [ ] `curl http://<bridge>/health` returns `{"ok":true,...}`.
- [ ] `curl http://<bridge>/players` lists the configured player(s).
- [ ] `curl http://<bridge>/players/discover` finds at least one player
      (skip if the network blocks SSDP multicast).
- [ ] `curl http://<player-ip>:1400/xml/device_description.xml` from the
      bridge host returns XML (proves LAN reachability).

## Playback control

For each configured player:

- [ ] `POST /players/<name>/play` → player resumes / starts.
- [ ] `POST /players/<name>/pause` → player pauses.
- [ ] `POST /players/<name>/stop` → player stops.
- [ ] `POST /players/<name>/next` → next track (when content allows).
- [ ] `POST /players/<name>/previous` → previous track.

## Volume

- [ ] `POST /players/<name>/volume` with `{"level":15}` → player at 15.
- [ ] `POST /players/<name>/volume/up` with `{"step":5}` → player at 20.
- [ ] `POST /players/<name>/volume/down` → player at 18.
- [ ] `POST /players/<name>/volume` with `{"level":150}` → clamped to 100.
- [ ] `POST /players/<name>/mute` → player muted (LED if applicable).
- [ ] `POST /players/<name>/mute/toggle` (twice) → mute toggles.

## Radio

- [ ] `GET /stations` lists configured stations sorted by index.
- [ ] `POST /players/<name>/radio/start` with `{"index":1}` → station 1
      starts playing within ~3 s.
- [ ] `POST /players/<name>/radio/start` with `{"name":"<station>"}` →
      named station starts.
- [ ] `POST /players/<name>/radio/start` with `{"index":999}` → 404
      `STATION_NOT_FOUND`.

## Status

- [ ] `GET /players/<name>/status` → returns `online:true`,
      `state:"PLAYING"`, current `volume`, and a `track.title`.
- [ ] Change volume on the player physically → next status poll shows the
      new volume.
- [ ] Pause from the Sonos app → next status poll shows
      `state:"PAUSED_PLAYBACK"`.
- [ ] After starting a station via the bridge, status shows
      `activeStation.name`.

## Resilience

- [ ] Unplug the player. `POST /players/<name>/play` → 503
      `PLAYER_UNREACHABLE`, no crash.
- [ ] `GET /players/<name>/status` while offline → 200 with
      `online:false`.
- [ ] Plug the player back in, wait 30 s. Bridge recovers automatically;
      next call succeeds.
- [ ] Bridge process survives an invalid station URL: the radio call fails
      with a SOAP_FAULT but the process keeps running.

## Firmware compatibility check

If the player is on a newer firmware:

- [ ] Confirm `radio/start` works for at least one station. The bridge's
      metadata-fallback ladder should handle metadata rejections silently.
- [ ] Inspect logs for `strategy: "direct+empty-meta"` or
      `strategy: "raw+empty-meta"` — these indicate fallback paths firing
      and tell you which metadata form the firmware accepts.

## HomeServer integration

- [ ] HS Experte HTTP request action successfully calls each control
      endpoint.
- [ ] Status poll (HS timer + receive parser) updates the configured KNX
      group addresses.
- [ ] Pressing a wall plate button starts the expected radio station.
- [ ] Volume slider on a Gira visualization element sets player volume.
