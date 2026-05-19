'use strict';

// In-memory state cache. Updated by:
//   1. Successful control actions (optimistic local update)
//   2. UPnP event NOTIFY callbacks (authoritative push from the player)
//   3. On-demand status polls (fallback for never-seen players)
//
// Anyone holding a subscription (Server-Sent Events, webhook push) is notified
// via the EventEmitter so KNX status outputs can update without polling.

const { EventEmitter } = require('events');

class StateStore extends EventEmitter {
  constructor() {
    super();
    this.byPlayer = new Map();
    this.activeStation = new Map();
    this.lastEventAt = new Map();
  }

  get(playerName) {
    return this.byPlayer.get(playerName) || null;
  }

  set(playerName, patch) {
    const prev = this.byPlayer.get(playerName) || {};
    const next = { ...prev, ...patch, updatedAt: new Date().toISOString() };
    this.byPlayer.set(playerName, next);
    this.emit('change', { player: playerName, state: next });
    return next;
  }

  markEventReceived(playerName) {
    this.lastEventAt.set(playerName, Date.now());
  }

  hasRecentEvent(playerName, maxAgeMs = 60000) {
    const ts = this.lastEventAt.get(playerName);
    if (!ts) return false;
    return (Date.now() - ts) < maxAgeMs;
  }

  setActiveStation(playerName, station) {
    if (station) this.activeStation.set(playerName, station);
    else this.activeStation.delete(playerName);
    this.set(playerName, {
      activeStation: station ? { index: station.index, name: station.name } : null
    });
  }

  getActiveStation(playerName) {
    return this.activeStation.get(playerName) || null;
  }

  all() {
    const out = {};
    for (const [name, state] of this.byPlayer.entries()) {
      out[name] = state;
    }
    return out;
  }
}

module.exports = { StateStore };
