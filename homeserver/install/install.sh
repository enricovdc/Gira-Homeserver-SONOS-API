#!/usr/bin/env sh
# Install the Sonos bridge directly on the Gira HomeServer.
#
# The HomeServer is a Linux device with a writable filesystem and (on
# HomeServer 4.x / 5.x) Node.js available. Running the bridge as a systemd
# service on the HomeServer itself removes the need for any separate
# bridge machine.
#
# Prerequisites
#   - SSH access to the HomeServer as root (or sudo).
#   - Node.js >= 18 installed at /usr/bin/node (check: `node -v`).
#     If missing, install via your HomeServer's package manager. On
#     HomeServer images without Node, install a static Node 18 LTS
#     tarball into /opt/node and symlink /usr/bin/node.
#
# Usage on the HomeServer
#   git clone <this-repo> /tmp/sonos-bridge
#   sh /tmp/sonos-bridge/homeserver/install/install.sh
#
# What this does
#   1. Creates the system user `sonos-bridge`.
#   2. Copies source into /opt/sonos-bridge.
#   3. Creates /etc/sonos-bridge/config.json from the example (if missing).
#   4. Installs the systemd unit and enables it.
#   5. Starts the service.

set -e

SOURCE_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
INSTALL_DIR=/opt/sonos-bridge
CONFIG_DIR=/etc/sonos-bridge
SERVICE_USER=sonos-bridge

require_root() {
  if [ "$(id -u)" -ne 0 ]; then
    echo "install.sh must be run as root (use sudo)." >&2
    exit 1
  fi
}

require_node() {
  if ! command -v node >/dev/null 2>&1; then
    echo "Node.js is not installed. Install Node 18+ before running this script." >&2
    exit 1
  fi
  NODE_MAJOR=$(node -e 'console.log(process.versions.node.split(".")[0])')
  if [ "$NODE_MAJOR" -lt 18 ]; then
    echo "Node.js >= 18 required (found $(node -v))." >&2
    exit 1
  fi
}

ensure_user() {
  if ! id "$SERVICE_USER" >/dev/null 2>&1; then
    useradd -r -d "$INSTALL_DIR" -s /usr/sbin/nologin "$SERVICE_USER"
    echo "Created user $SERVICE_USER"
  fi
}

install_files() {
  mkdir -p "$INSTALL_DIR"
  cp -R "$SOURCE_DIR/src" "$INSTALL_DIR/"
  cp "$SOURCE_DIR/package.json" "$INSTALL_DIR/"
  cp "$SOURCE_DIR/config.example.json" "$INSTALL_DIR/"
  chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"
}

install_config() {
  mkdir -p "$CONFIG_DIR"
  if [ ! -f "$CONFIG_DIR/config.json" ]; then
    cp "$SOURCE_DIR/config.example.json" "$CONFIG_DIR/config.json"
    chmod 600 "$CONFIG_DIR/config.json"
    echo "Created $CONFIG_DIR/config.json from example. Edit it before"
    echo "starting the service, or use the web UI after starting."
  fi
  chown -R "$SERVICE_USER:$SERVICE_USER" "$CONFIG_DIR"
}

install_service() {
  cp "$SOURCE_DIR/homeserver/install/sonos-bridge.service" /etc/systemd/system/
  systemctl daemon-reload
  systemctl enable sonos-bridge.service
  systemctl restart sonos-bridge.service
  sleep 1
  systemctl --no-pager status sonos-bridge.service | head -20
}

main() {
  require_root
  require_node
  ensure_user
  install_files
  install_config
  install_service
  echo
  echo "----"
  echo "Sonos bridge installed."
  echo "  Source dir : $INSTALL_DIR"
  echo "  Config     : $CONFIG_DIR/config.json"
  echo "  Logs       : journalctl -u sonos-bridge -f"
  echo "  Web UI     : http://<homeserver-ip>:8080/"
  echo "----"
}

main "$@"
