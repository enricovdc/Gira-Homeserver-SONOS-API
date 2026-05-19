'use strict';

const { SonosClient } = require('./sonos/client');

class PlayerError extends Error {
  constructor(message, code) {
    super(message);
    this.name = 'PlayerError';
    this.code = code || 'PLAYER_ERROR';
  }
}

class PlayerRegistry {
  constructor({ players = [], defaultPlayer = null, clientFactory } = {}) {
    this.byName = new Map();
    this.defaultName = defaultPlayer;
    this.clientFactory = clientFactory || ((opts) => new SonosClient(opts));
    for (const p of players) this.register(p);
  }

  register(playerCfg) {
    if (!playerCfg || !playerCfg.name || !playerCfg.host) {
      throw new PlayerError(`Invalid player config: ${JSON.stringify(playerCfg)}`, 'INVALID_CONFIG');
    }
    const client = this.clientFactory({
      host: playerCfg.host,
      port: playerCfg.port || 1400,
      name: playerCfg.name,
      uuid: playerCfg.uuid
    });
    this.byName.set(playerCfg.name.toLowerCase(), client);
    if (!this.defaultName) this.defaultName = playerCfg.name;
  }

  get(name) {
    const key = (name || this.defaultName || '').toLowerCase();
    if (!key) throw new PlayerError('No player specified and no default configured', 'NO_DEFAULT');
    const client = this.byName.get(key);
    if (!client) throw new PlayerError(`Unknown player: ${name}`, 'PLAYER_NOT_FOUND');
    return client;
  }

  list() {
    return Array.from(this.byName.values()).map((c) => ({
      name: c.name,
      host: c.host,
      port: c.port,
      uuid: c.uuid
    }));
  }

  has(name) {
    return this.byName.has(String(name || '').toLowerCase());
  }

  unregister(name) {
    const key = String(name || '').toLowerCase();
    const existed = this.byName.delete(key);
    if (this.defaultName && this.defaultName.toLowerCase() === key) {
      const next = this.byName.values().next().value;
      this.defaultName = next ? next.name : null;
    }
    return existed;
  }
}

module.exports = { PlayerRegistry, PlayerError };
