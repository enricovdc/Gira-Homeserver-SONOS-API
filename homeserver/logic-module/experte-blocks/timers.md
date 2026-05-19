# Timer logic blocks

Two periodic timers are required for a healthy logic-module-only
installation. A third is recommended.

## 1. UPnP subscription renewal (required for event push)

| Property | Value |
| --- | --- |
| Logic block name | `sonos.timer.renewSubscriptions` |
| Trigger          | every 25 minutes (subscriptions are granted for 30 min) |
| Actions          | for each player, run `sonos.<name>.renew-av` and `sonos.<name>.renew-rc` |

The block must also handle a `HTTP 412 Precondition Failed` response,
which means the SID has expired (e.g. the Sonos player rebooted). On
412:

1. Discard the stored SID (`sonos.<name>.sid_*`).
2. Re-issue the initial SUBSCRIBE (`sonos.<name>.subscribe-av` / `subscribe-rc`).
3. The success response's receive parser will populate the new SID.

If your Experte version cannot branch on HTTP status, set the renewal
timer interval to 28 minutes and *always* re-issue the initial
SUBSCRIBE (idempotent — Sonos will hand out a new SID).

## 2. Status poll watchdog (recommended)

Even with event push, run a low-frequency status poll as a heartbeat
and to catch any missed events:

| Property | Value |
| --- | --- |
| Logic block name | `sonos.timer.statusWatchdog` |
| Trigger          | every 60 s |
| Actions          | for each player, run `sonos.<name>.getTransport` and `sonos.<name>.getVolume` |

The receive parsers attached to those actions overwrite the same
`sonos.<name>.*` data points the NOTIFY parsers write to. If both
sources are up-to-date, the value is unchanged and KNX outputs don't
fire — no harm done.

The watchdog also updates `sonos.<name>.online`:

- on a successful response, set `online = true`,
- on a timeout / connection refused, set `online = false`,
- transition `false → true` should trigger a re-subscribe to the player
  (chain into `sonos.<name>.subscribe-av/rc`).

## 3. Startup subscribe (required)

| Property | Value |
| --- | --- |
| Logic block name | `sonos.startup.subscribeAll` |
| Trigger          | `Start des HomeServers` |
| Actions          | for each player, run `subscribe-av` and `subscribe-rc` |

Also good practice: on startup, fire the status-watchdog block once to
prime the data points.

## 4. Optional: stale-event watchdog

A logic block that fires every 5 minutes and checks
`sonos.diag.lastNotifyAt` (updated by every NOTIFY): if older than 5
minutes, re-subscribe. This protects against silent subscription drops
that don't trigger an HTTP error during renewal.

## Failure modes and recovery

| Symptom | Likely cause | Recovery |
| --- | --- | --- |
| No NOTIFY ever arrives | CALLBACK URL unreachable from player | check VLAN, firewall, HomeServer URL; fall back to polling |
| NOTIFYs arrive at first, then stop | subscription expired without renewal | timer 1 misconfigured; check interval and SID storage |
| 412 on every renew | SID outdated due to player reboot | renewal block falls back to initial SUBSCRIBE on 412 |
| State data points stuck | inbound URL didn't trigger | check inbound URL HTTP method support; check receive parser regex |
| Player offline | network issue | watchdog flips `online` to `false`; KNX flag fires |
