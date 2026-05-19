# Manual verification checklist

Run through this list against a real Sonos player when commissioning
the HSL3 modules, after a Sonos firmware update, or after network
changes. The unit tests under `build/test_logic_modules.py` cover the
pure logic against a mocked framework; this checklist is for
hardware-in-the-loop validation.

## Pre-flight

- [ ] HomeServer firmware ≥ 4.13 (HSL3 / Python 3.9 supported).
- [ ] Both `.hslz` archives imported into Experte via
      *Logikbausteine → Importieren*.
- [ ] One Sonos Player block per player on the logic canvas.
- [ ] Each block's `Host` input is set to the player's LAN IP
      (verify via the Sonos app: *Settings → System → About*).
- [ ] Each block has its `Tick` virtual input wired to a 60 s timer
      (or `PollInterval` configured appropriately).
- [ ] Project downloaded to the HomeServer.

## Basic connectivity

- [ ] Open the player block's debug page in Experte. `Listener port`
      should show a numeric value (default 8081). If it shows
      `disabled`, the listener could not bind — eventing is off,
      polling-only mode is active.
- [ ] Within ~60 s of download, the `Online` output is `1`.
- [ ] The `State` output reports the player's current state
      (`PLAYING`, `PAUSED_PLAYBACK`, or `STOPPED`).

## Playback control

For each player, trigger the corresponding KNX address (or simulate
the input in Experte) and verify on the physical player:

- [ ] `Play` → playback resumes / starts.
- [ ] `Pause` → playback pauses.
- [ ] `Stop` → playback stops.
- [ ] `Next` → next track (when current source supports it).
- [ ] `Prev` → previous track.

## Volume and mute

- [ ] Write `15` to `SetVolume` → player at 15.
- [ ] Trigger `VolUp` (with `VolStep` = 5) → player at 20.
- [ ] Trigger `VolDown` → player at 15.
- [ ] Write `150` to `SetVolume` → clamped to 100 (verified by the
      `Volume` output reading 100).
- [ ] Write `1` to `SetMute` → player muted (front-panel LED if
      equipped).
- [ ] Trigger `MuteToggle` twice → mute toggles correctly.

## Radio stations

- [ ] Set `Station1Uri` to a known good stream URL (e.g. the URL the
      Sonos app shows for a station under *Information*).
- [ ] Write `1` to `StartRadio` → station starts within ~3 s, `State`
      becomes `PLAYING`, `Title` populates with the stream content,
      `ActiveStation` reads `1`.
- [ ] Write `9` to `StartRadio` → `LastError` reads
      `STATION_9_NOT_CONFIGURED`. No crash; no playback change.

## Status (NOTIFY-driven)

- [ ] With playback running, change the volume on the player itself
      (or via the Sonos app on a phone). Within ~1 s the `Volume`
      output should reflect the new value.
- [ ] Pause from the Sonos app. Within ~1 s `State` →
      `PAUSED_PLAYBACK`.
- [ ] Change tracks externally; `Title` and `Artist` update.

## Status (poll fallback)

- [ ] Block the listener port temporarily (firewall the
      HomeServer:8081). `Subscribed` should drop to `0`. The `State`
      / `Volume` / `Mute` outputs should still update every
      `PollInterval` seconds because of the Tick poll. This proves
      the graceful-degradation path.

## Resilience

- [ ] Power off the Sonos player. Within ~5–10 s the `Online` output
      drops to `0` and `LastError` reads `UNREACHABLE`. No crash.
- [ ] Power the player back on. Within ~60 s `Online` returns to `1`,
      `Subscribed` returns to `1`.
- [ ] Reboot the HomeServer. After firmware comes up, the player's
      LBS instance re-bootstraps; new SUBSCRIBE issued; outputs
      populate within one Tick.

## Firmware compatibility

If your player is on a newer Sonos firmware:

- [ ] Inspect `LastError` after a successful radio start: it should
      be empty. If `714` / `716` / `800` appears briefly and is then
      cleared, the metadata-fallback ladder fired and recovered —
      log this for the project notes.
- [ ] Confirm a NOTIFY arrives at least once (the debug page's
      `Notifies received` counter increments above zero).

## Multi-player

If running multiple LBS 22000 instances:

- [ ] All instances reach `Online = 1`.
- [ ] Only one listener thread is started (the first instance's
      `Listener port` shows the bound port; subsequent instances'
      debug pages show the same port).
- [ ] State changes on player A do not influence player B's outputs.
