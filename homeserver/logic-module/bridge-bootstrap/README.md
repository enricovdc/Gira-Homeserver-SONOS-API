# Auto-install the Node bridge from the Experte import

This directory contains everything needed to deploy and start the
Node.js bridge **as part of the HS Experte project download**, with no
SSH access to the HomeServer. The end-user experience:

1. Open the Experte project.
2. *Download to HomeServer*.
3. Within ~30 s the bridge is running and `http://<homeserver-ip>:8080/`
   serves the configuration UI.

No terminal, no installer, no manual file copy.

## How it works

The Experte project ships **one file**:
`dist/sonos-bridge.bundle.js` — a self-contained Node.js script (~80 KB,
zero npm dependencies). One of three deployment paths transfers that
file to the HomeServer and starts it.

The bridge writes its own `config.json` to a known path on first run if
none exists, and reads/writes it from then on. Editing is done via the
bridge's built-in web UI at the root URL.

## Deployment paths

The right path depends on what the HomeServer Experte version supports.
Try them in order; pick whichever works.

### Path A — Programmpaket (HS 4.10+, recommended)

HS Experte versions from 4.10 onward support attaching an *external
program package* to the project. The Experte uploads the package to the
HomeServer on download, registers it, and (optionally) auto-starts it.

1. In the Experte: *Datei → Programmpaket hinzufügen* (or similar; the
   exact menu name varies by version).
2. Choose a name like `sonos-bridge`. Working directory: `/data/programs/sonos-bridge`.
3. Add the file `dist/sonos-bridge.bundle.js` (from this repository).
4. Add the file `start-bridge.sh` (in this directory).
5. Set the start command to `sh start-bridge.sh`.
6. Set the package to **auto-start on HomeServer boot**.
7. Download the project. The HomeServer runs the start script.

The start script:

- Creates `/data/programs/sonos-bridge/` if missing.
- Writes a default `config.json` if missing.
- Launches `node sonos-bridge.bundle.js config.json` as a background
  process.
- Writes the PID to `bridge.pid` for clean restarts.

### Path B — File deployment + Systemkommando

For HS firmware that lacks the Programmpaket feature but does support:

- **Datei in Projekt importieren** (project file deployment)
- **Aktion: Systemkommando ausführen** (run shell command from a logic
  block)

The Experte deploys files to `/data/projects/<project>/files/` (or
similar — check your firmware's documentation), and a startup logic
block runs the start script.

1. In the Experte: *Datei in Projekt importieren* — add
   `dist/sonos-bridge.bundle.js` and `start-bridge.sh`.
2. Import the logic block `experte-blocks/sonos-startup-bootstrap.txt`
   (in this directory, see file for the action list).
3. Wire it to trigger on `Start des HomeServers`.

The logic block runs:

```
sh /data/projects/<project>/files/start-bridge.sh
```

which copies the bundle to `/var/lib/sonos-bridge/` (writable), writes
`config.json` if missing, and starts `node` in the background.

### Path C — Embed bundle in a string data point

For HS firmware that supports **none** of: Programmpaket, file
deployment, or Systemkommando, but **does** support:

- Writing a string data point to a file from a logic block
  (*Aktion: Datei schreiben*)
- Some form of process spawning

The bundle's source is embedded as the value of a string data point
named `sonos.bridge.bundleSrc`. A startup logic block:

1. Reads the data point.
2. Writes it to `/var/lib/sonos-bridge/sonos-bridge.bundle.js`.
3. Runs `node /var/lib/sonos-bridge/sonos-bridge.bundle.js`.

To populate the data point: open `dist/sonos-bridge.bundle.js`, copy the
entire file contents, paste into the data-point's initial value in the
Experte. The bundle is ~80 KB; some Experte versions cap data-point
strings at lower values. If yours does, fall back to Path A or B.

### Path D — Manual upload (fallback, no SSH but uses Experte file manager)

Some HS Experte versions expose a file manager that uploads files to
the HomeServer directly:

1. Use the Experte file manager to upload
   `dist/sonos-bridge.bundle.js` to `/var/lib/sonos-bridge/`.
2. Upload `start-bridge.sh` to the same directory.
3. Import the startup logic block and wire to project startup.

## Configuration after install

Once the bridge is running:

- Open `http://<homeserver-ip>:8080/` in a browser.
- Add players (or click *Discover* for SSDP auto-discovery — works
  because the bridge runs on the HomeServer with full network access).
- Add radio stations.
- Configure the webhook URL pointing back at the HomeServer's own
  inbound URL so KNX outputs update on UPnP state changes.

The bridge's `config.json` lives next to the bundle (or wherever the
start script puts it). It's edited automatically by the web UI — no
need to touch it manually.

## Prerequisites checklist for the HomeServer

The HomeServer must have:

- [ ] Node.js >= 18 in `PATH` (`/usr/bin/node` typically). Check via
      Experte's Systemkommando: `/usr/bin/node --version`. If missing,
      see "Installing Node on the HomeServer" below.
- [ ] A writable directory the start script can use (`/var/lib/sonos-bridge/`
      is the default; the script falls back to `/tmp/sonos-bridge/` if
      that's not writable, which works but loses state across reboots).
- [ ] Outbound HTTP to the Sonos players on port 1400 (no special
      firewall rules needed on a flat LAN).
- [ ] Inbound listening on the chosen HTTP port (default 8080). The
      HomeServer's main HTTP server uses port 80/443; the bridge picks
      8080 to avoid conflicts.

### Installing Node on the HomeServer

If Node is not present, the Experte project can ship a static Node 18
LTS tarball for the HomeServer's CPU architecture (most current
HomeServers are ARMv7 or AArch64). The `start-bridge.sh` script
auto-detects whether Node is in PATH; if not, it extracts the bundled
tarball into `/var/lib/sonos-bridge/node/` and uses
`/var/lib/sonos-bridge/node/bin/node`. The tarballs are not included in
this repository (they are ~30 MB each); download them from
`https://nodejs.org/dist/` for your target architecture and add to the
Experte project alongside the bundle.

## Verifying the install

After download:

1. Wait 30 s.
2. From your laptop on the LAN: `curl http://<homeserver-ip>:8080/health`.
   Expected: `{"ok":true,...}`.
3. From the HomeServer Experte's Systemkommando (if available):
   `cat /var/lib/sonos-bridge/bridge.log | tail -20`.

## Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| `curl: connection refused` | Bridge didn't start | check `bridge.log`; ensure Node is in PATH |
| `node: command not found` in log | Node not installed | bundle the Node tarball (see above) |
| `Error: listen EADDRINUSE` | Port 8080 in use | change `server.port` in the auto-written `config.json` |
| Bundle file not deployed | Wrong path in Experte | check the actual file location and adjust `start-bridge.sh` |
| Bridge starts then exits | Config validation error | look at `bridge.log` for `Configuration error` details |
| Cannot reach Sonos players | LAN/VLAN issue | see `docs/TROUBLESHOOTING.md` |

## When not to use this

The HomeServer-Experte-deploys-Node-bridge approach is a great fit for
*most* installations. Skip it and use the pure logic-module mode
(parent directory) if:

- Your HomeServer firmware lacks all four deployment paths.
- You cannot install Node on the HomeServer for policy reasons.
- You want zero processes other than the HS daemon.

The pure logic-module mode has the same features for control + radio +
status + UPnP events; only SSDP discovery and the standalone admin UI
are bridge-exclusive.
