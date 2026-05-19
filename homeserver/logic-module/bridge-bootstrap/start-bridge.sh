#!/bin/sh
# start-bridge.sh — auto-launch the Sonos bridge on the HomeServer.
#
# Deployed alongside `sonos-bridge.bundle.js` by the HS Experte project.
# Invoked either from a Programmpaket auto-start or from a startup logic
# block via the *Systemkommando ausführen* action.
#
# Behaviour:
#   1. Picks the right Node executable (system PATH, or a bundled tarball).
#   2. Picks a writable working directory (/var/lib/sonos-bridge by
#      preference, /tmp/sonos-bridge as a fallback).
#   3. Copies the bundle to the working directory if not already there.
#   4. Writes a default config.json if missing.
#   5. Kills any previous instance whose PID is recorded.
#   6. Starts the bridge in the background, redirects stdout/stderr to
#      bridge.log, and writes the new PID to bridge.pid.
#
# Designed for /bin/sh on POSIX-ish HomeServer Linux. No bash-isms.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BUNDLE_SOURCE="$SCRIPT_DIR/sonos-bridge.bundle.js"

# Pick a writable working directory.
choose_workdir() {
  for candidate in /var/lib/sonos-bridge /data/sonos-bridge "$HOME/.sonos-bridge" /tmp/sonos-bridge; do
    if mkdir -p "$candidate" 2>/dev/null && [ -w "$candidate" ]; then
      echo "$candidate"
      return 0
    fi
  done
  echo "ERROR: no writable working directory" >&2
  exit 1
}

WORKDIR="$(choose_workdir)"
BUNDLE="$WORKDIR/sonos-bridge.bundle.js"
CONFIG="$WORKDIR/config.json"
LOGFILE="$WORKDIR/bridge.log"
PIDFILE="$WORKDIR/bridge.pid"

# Copy the bundle into the writable working directory if needed (the
# Experte's deployment path is usually read-only).
if [ -f "$BUNDLE_SOURCE" ] && { [ ! -f "$BUNDLE" ] || [ "$BUNDLE_SOURCE" -nt "$BUNDLE" ]; }; then
  cp "$BUNDLE_SOURCE" "$BUNDLE"
fi
if [ ! -f "$BUNDLE" ]; then
  echo "ERROR: bundle not found at $BUNDLE (and not at $BUNDLE_SOURCE)" >&2
  exit 1
fi

# Pick a Node executable. Prefer one bundled alongside the script
# (useful when the HomeServer doesn't ship Node), then PATH.
pick_node() {
  for candidate in "$SCRIPT_DIR/node/bin/node" "$WORKDIR/node/bin/node" "$(command -v node 2>/dev/null)"; do
    if [ -n "$candidate" ] && [ -x "$candidate" ]; then
      # Check version >= 18.
      MAJOR=$("$candidate" -e 'console.log(process.versions.node.split(".")[0])' 2>/dev/null || echo "0")
      if [ "$MAJOR" -ge 18 ] 2>/dev/null; then
        echo "$candidate"
        return 0
      fi
    fi
  done
  echo "ERROR: Node.js >= 18 not found" >&2
  echo "  Install Node on the HomeServer or bundle a static tarball next to this script as node/bin/node" >&2
  exit 1
}

NODE="$(pick_node)"

# Default config: bind on all interfaces, port 8080, no players yet.
# The web UI at http://<hs-ip>:8080/ adds players and stations at runtime.
if [ ! -f "$CONFIG" ]; then
  cat > "$CONFIG" <<'JSON'
{
  "server": { "host": "0.0.0.0", "port": 8080, "authToken": "" },
  "discovery": { "enabled": true, "timeoutMs": 4000 },
  "players": [],
  "radioStations": [],
  "eventing": { "enabled": true, "callbackBaseUrl": "", "timeoutSec": 1800 },
  "webhook": { "url": "", "authHeader": "" },
  "cloud": { "enabled": false, "clientId": "", "clientSecret": "" },
  "admin": { "enabled": true },
  "logging": { "level": "info" }
}
JSON
  chmod 600 "$CONFIG"
fi

# Stop any previous instance.
if [ -f "$PIDFILE" ]; then
  OLD_PID="$(cat "$PIDFILE" 2>/dev/null || echo)"
  if [ -n "$OLD_PID" ] && kill -0 "$OLD_PID" 2>/dev/null; then
    echo "Stopping previous bridge instance (pid $OLD_PID)"
    kill "$OLD_PID" 2>/dev/null || true
    # Wait up to 5 s for clean shutdown.
    i=0
    while kill -0 "$OLD_PID" 2>/dev/null && [ "$i" -lt 5 ]; do
      sleep 1
      i=$((i + 1))
    done
    kill -9 "$OLD_PID" 2>/dev/null || true
  fi
  rm -f "$PIDFILE"
fi

# Truncate / rotate log if it's gotten huge (> 5 MB).
if [ -f "$LOGFILE" ]; then
  SIZE=$(wc -c < "$LOGFILE" 2>/dev/null || echo 0)
  if [ "$SIZE" -gt 5242880 ]; then
    mv "$LOGFILE" "$LOGFILE.old"
  fi
fi

cd "$WORKDIR"
echo "Starting bridge: $NODE $BUNDLE $CONFIG"
nohup "$NODE" "$BUNDLE" "$CONFIG" >> "$LOGFILE" 2>&1 &
BRIDGE_PID=$!
echo "$BRIDGE_PID" > "$PIDFILE"

# Give it a moment, then verify it didn't crash on startup.
sleep 2
if ! kill -0 "$BRIDGE_PID" 2>/dev/null; then
  echo "ERROR: bridge exited immediately. Last 20 log lines:" >&2
  tail -20 "$LOGFILE" >&2 || true
  rm -f "$PIDFILE"
  exit 1
fi

echo "Bridge running as pid $BRIDGE_PID; logs at $LOGFILE"
echo "Open http://$(hostname -I 2>/dev/null | awk '{print $1}'):8080/ to configure."
