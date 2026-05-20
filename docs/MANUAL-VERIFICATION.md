# Manual verification checklist

Run through this list against real Sonos hardware when commissioning
the HSL3 modules, after a Sonos firmware update, or after network
changes. The 121 unit tests under `tests/test_logic_modules.py` cover
the pure logic against a stubbed framework; this checklist is for
hardware-in-the-loop validation.

## Pre-flight

- [ ] HomeServer firmware ≥ 4.13 (HSL3 / Python 3.9 supported).
- [ ] Both `.hslz` archives imported into Experte via
      *Logikbausteine → Importieren*.
- [ ] One *Sonos Admin* block (LBS 22001) on the logic canvas.
- [ ] One *Sonos Player* block per Sonos speaker (LBS 22000).
- [ ] Each Player block's `Host` input set to the player's UUID
      (`RINCON_xxx`, preferred), MAC, name, or IPv4. Copy the UUID
      from the Admin web UI by clicking the UUID on the player card.
- [ ] Project downloaded to the HomeServer.

## Admin web UI

- [ ] Open `http://<homeserver-ip>:8080/` (or the port reported on
      the Admin block's `ListenPort` output).
- [ ] **Players** section lists every discovered Sonos. Names match
      the Sonos app. UUIDs visible.
- [ ] *Scan now* triggers an SSDP scan; previously-missing players
      appear within 5–10 s.
- [ ] Manually add a player by IP or MAC; verify it shows in the
      list with `source: manual`.

## Basic connectivity (per Player block)

- [ ] Open the Player block's debug page in Experte. `Listener port`
      shows a numeric value (default 8081). `disabled` means the
      listener couldn't bind — polling-only mode is active.
- [ ] Within ~60 s of download, `Online` is 1 and `ZoneName` matches
      the Sonos app.
- [ ] `State` reports the player's current state (`Playing`, `Paused`,
      `Stopped`).

## Playback control — rising-edge inputs

Trigger the corresponding KNX address (or simulate the input in Experte):

- [ ] `Play` → playback starts. `IsPlaying` → 1.
- [ ] `Pause` → playback pauses. `IsPaused` → 1.
- [ ] `Stop` → playback stops. `IsStopped` → 1.
- [ ] `Next` / `Prev` → track advance (when current source supports
      it; check `NextAllowed` / `PrevAllowed` first).
- [ ] `MuteToggle` twice → mute inverts then restores.

## Playback control — value toggles

- [ ] Write 1 to `PlayPause` → playback starts. Write 0 → pause.
- [ ] Write 1 to `NextPrev` → next track. Write 0 → previous track.
- [ ] Write 1 to `PresetNextPrev` (with at least 2 presets in the
      Admin library) → next preset starts. Write 0 → previous preset.
      At the last preset, another 1 wraps to the first.

## Volume / Mute

- [ ] Write `15` to `SetVolume` → player at 15 %. `Volume` output reads 15.
- [ ] Trigger `VolUp` (with `VolStep = 5`) → player at 20.
- [ ] Trigger `VolDown` → player at 15.
- [ ] Write 1 to `VolUpDown` → volume rises by `VolStep`. Write 0 →
      volume falls by `VolStep`. This is the DPT 1.008 rocker path —
      a single KNX 1-bit GA drives both directions without helper logic.
- [ ] Write `150` to `SetVolume` → clamped to 100 (output reads 100).
- [ ] Write 1 to `SetMute` → muted, `Mute` output reads 1.

## Play mode (shuffle / repeat)

- [ ] Write 1 to `SetShuffle` → `ShuffleState` output → 1, Sonos app
      shows shuffle on.
- [ ] Write 1 to `SetRepeat` → `RepeatState` output → 1.
- [ ] Toggle shuffle/repeat from the Sonos app on a phone — the
      respective `*State` outputs update within ~1 s (NOTIFY-driven).

## Presets (Admin library)

- [ ] Add at least 3 presets in the Admin web UI: a direct radio
      stream (paste URL), a Sonos Favorite via the *Favorites* button
      on a player card, and a saved playlist via *Playlists*.
- [ ] Write `1` to `StartRadio` → first preset starts within ~3 s.
      `State` → `Playing`, `Title` populates, `ActiveStation` reads 1,
      `ActiveStationName` matches the preset's display name.
- [ ] Write a non-existent index (e.g. `99`) → `LastError` reads
      `PRESET_NOT_FOUND: 99`. No crash, no playback change.
- [ ] Test a playlist preset → the queue-and-play path runs
      (`RemoveAllTracksFromQueue` + `AddURIToQueue` + transport switch
      + `Play`). Playback should start the playlist.
- [ ] Trigger a slow preset (a group-join preset, or a playlist whose
      queue-and-play takes a couple of seconds). The
      `ActiveStationName` output should immediately show
      `Loading: <name>` and then drop the prefix once the player
      switches to the new source.

## Track-metadata refresh

- [ ] Start a Spotify track on a player (Artist + Album metadata are
      populated). Then switch to a radio stream (e.g. via a different
      preset). Within one Tick, the `Artist` and `Album` outputs
      should clear — radio streams have no `<dc:creator>`/`<upnp:album>`
      tags so the previous Spotify metadata must not leak.
- [ ] The `Title` output for the radio stream becomes the
      `streamContent` value (the now-playing label from the stream).

## Group presets

- [ ] In the Admin web UI's *Group presets* section, define a group:
      pick a master + at least one member.
- [ ] On any Player block (master or member), write the group's index
      to `GroupPreset` → audio plays on all group members within ~3 s
      with the master as coordinator. `IsCoordinator` is 1 on the
      master and 0 on the slaves; `GroupInfo` on the slaves shows
      the master's Zone Name.
- [ ] Write 1 to `Ungroup` on a slave → slave drops out of the group.
      The master keeps playing.
- [ ] Form a group with an unknown player id (edit the Admin record
      out-of-band, then trigger the preset) → `LastError = GROUP_PARTIAL: <n>`.

## Player tunables (Admin-managed)

- [ ] In the Admin web UI's *Player Defaults* section, change
      `Status poll interval` to e.g. 30 s. Within ~60 s every Player
      block honours the new poll cadence (visible on the debug page's
      "Status polls" counter, which now increments roughly every
      30 s).
- [ ] Expand *Advanced overrides* on a single player card. Set
      `Status poll (s)` = 10. That speaker's Player block now polls
      every 10 s while the rest stay at the project default.
- [ ] Clear the override (empty input). The player reverts to the
      project default within one on_calc cycle.

## Sound Enhancement companion (LBS 22002)

Optional per-player block. Skip this section if you didn't drop an
LBS 22002 onto the canvas.

- [ ] On a Player block's matching LBS 22002, `Online` is 1 within
      ~60 s of download. Same Host value as the LBS 22000 — UUID
      preferred.
- [ ] Write -5 to `SetBass`. Bass output reads -5 within one Tick.
      The Sonos app's EQ panel shows -5 too.
- [ ] Write 11 to `SetBass`. The block clamps to 10 and dispatches
      that. `Bass` output reads 10.
- [ ] Write 1 to `SetTreble`, 1 to `SetLoudness`, 1 to `SetCrossfade`.
      Each output mirrors. Sonos app reflects the changes.
- [ ] On a non-soundbar speaker (Move / One / Five / Connect:Amp):
      write 1 to `SetNightMode`. `LastError` reads
      `NIGHTMODE_UNSUPPORTED`. `NightMode` output stays at 0.
- [ ] On a Beam / Arc / Playbar: write 1 to `SetNightMode` /
      `SetDialogMode`. Outputs mirror.
- [ ] Write 5 to `SetSleepTimer` (5 min). `SleepTimerRemaining`
      output starts ~300 and counts down on each Tick.
- [ ] Write 0 to `SetSleepTimer`. `SleepTimerRemaining` drops to 0.
- [ ] Write 1 to `SetLED`. The status LED on the speaker lights up.
      `LED` output reads 1. Write 0 → LED off, output reads 0.
- [ ] On a Move/Roam: `BatteryPercent` reads the current level (0-100)
      within ~60 s, `BatteryCharging` flips to 1 when plugged in.
      On a mains-only speaker: both stay at 0.
- [ ] On a soundbar (Beam/Arc/Playbar): write 1 to `SetTVMode`.
      The TV input engages; `TVMode` output → 1 within one Tick.
      Trigger a music preset on LBS 22000 to return to music.
- [ ] On a non-soundbar: write 1 to `SetTVMode` → `LastError` reads
      `NO_HT_STREAM`. No URI change.
- [ ] On a soundbar with surround pair: write 1 to `SetSurroundEnable`,
      then a value to `SetSurroundLevel` (-15..15). Both outputs mirror.
- [ ] On a player with paired Sub: write 1 to `SetSubEnable` and
      a value to `SetSubGain`. Outputs mirror.
- [ ] On the **group coordinator** speaker: write 50 to
      `SetGroupVolume`. The whole group's volume scales to ~50 %
      proportionally. `GroupVolume` output reads 50.
- [ ] On a **slave** speaker in a group: write 50 to `SetGroupVolume`
      → `LastError` reads `GROUP_NOT_COORDINATOR`. No volume change.
- [ ] On a player with a Trueplay calibration: write 0 to `SetTrueplay`
      → calibration is no longer applied, `Trueplay` output reads 0
      and `TrueplayAvailable` stays at 1 (the profile still exists).
      Write 1 → calibration re-applied, `Trueplay` reads 1.

## Sounds / announcements

- [ ] Upload a short audio clip (e.g. doorbell.wav, 1–3 s) via the
      Admin UI's *Sounds* section. The Sounds table shows it with
      index 1.
- [ ] Start playing some music on a player. Write 1 to `PlaySound`
      → on Sonos S2 hardware, the music ducks, the clip plays, the
      music resumes automatically. No interruption of the source.
- [ ] On S1 hardware (no AudioClip service): the snapshot/restore
      fallback fires — the music briefly stops, the clip plays, the
      music resumes from where it was paused. Slightly less smooth
      than S2.
- [ ] Write an out-of-range PlaySound index → `LastError =
      SOUND_NOT_FOUND`. No SOAP traffic.

## Status (NOTIFY-driven)

- [ ] With playback running, change the volume on the player itself
      (or via the Sonos app). Within ~1 s the `Volume` output reflects
      the new value.
- [ ] Pause from the Sonos app. Within ~1 s `IsPlaying` → 0 and
      `IsPaused` → 1.
- [ ] Change tracks externally; `Title` / `Artist` / `Album` /
      `AlbumArtURI` update.

## Transport-actions feedback

- [ ] During radio playback: `PlayAllowed` = 1, `StopAllowed` = 1,
      everything else = 0. Confirms Sonos doesn't support pause/seek
      on the stream.
- [ ] During queue / playlist playback: every `*Allowed` = 1.
- [ ] After power-off of the speaker: all `*Allowed` flags drop to 0
      within one Tick (`Online` also drops to 0).

## Status (poll fallback)

- [ ] Block the listener port temporarily (firewall the HomeServer:
      8081). `Subscribed` should drop to 0. The state outputs still
      update every `PollInterval` seconds via the Tick poll. This
      proves the graceful-degradation path.

## Resilience

- [ ] Power off the Sonos player. Within ~5–10 s `Online` drops to 0
      and `LastError` reads `UNREACHABLE`. No crash.
- [ ] Power the player back on. Within ~60 s `Online` returns to 1,
      `Subscribed` returns to 1.
- [ ] Reboot the HomeServer. After firmware comes up, the Admin
      restores the registries from retentive stores (players, presets,
      group presets, sounds, cloud creds, defaults). The Player block
      re-bootstraps and outputs populate within one Tick.
- [ ] DHCP renumber: change a player's IP (or wait for a lease
      renewal). `Host` set to a UUID/MAC keeps working — the registry
      re-resolves on every Tick.

## Firmware compatibility

If your player is on a newer Sonos firmware:

- [ ] After a successful preset start, `LastError` is empty. If
      `714` / `716` / `800` appeared briefly and was then cleared,
      the metadata-fallback ladder fired and recovered — log this
      for the project notes.
- [ ] Confirm a NOTIFY arrives at least once (the debug page's
      `Notifies received` counter increments above zero).
- [ ] If using `PlaySound`: confirm the native AudioClip path is in
      effect (S2). On S2 there's no audible gap when the clip plays;
      on S1 the music pauses briefly. Log which path your hardware
      takes.

## Multi-player

If running multiple LBS 22000 instances:

- [ ] All instances reach `Online = 1`.
- [ ] Only one NOTIFY listener thread is started (the first
      instance's `Listener port` shows the bound port; subsequent
      instances' debug pages show the same port).
- [ ] State changes on player A do not influence player B's outputs.

## Sonos Cloud (optional)

- [ ] In the Admin UI's *Sonos Cloud* section, paste a developer
      `client_id` + `client_secret` + your HomeServer's reachable
      redirect base URL.
- [ ] Click *Authorize with Sonos* → browser redirects to Sonos →
      successful login → callback completes → `CloudAuthorized`
      output goes to 1.
- [ ] Reboot the HomeServer; `CloudAuthorized` remains 1 (token
      survives via the retentive store).
