'use strict';

class RadioError extends Error {
  constructor(message, code) {
    super(message);
    this.name = 'RadioError';
    this.code = code || 'RADIO_ERROR';
  }
}

class RadioStationStore {
  constructor(stations = []) {
    this.stations = new Map();
    this.byIndex = new Map();
    for (const s of stations) this.add(s);
  }

  add(station) {
    if (!station || !station.name || !station.streamUri || !Number.isInteger(station.index)) {
      throw new RadioError(`Invalid station: ${JSON.stringify(station)}`, 'INVALID_STATION');
    }
    const key = station.name.toLowerCase();
    this.stations.set(key, station);
    this.byIndex.set(station.index, station);
  }

  list() {
    return Array.from(this.byIndex.values()).sort((a, b) => a.index - b.index);
  }

  byName(name) {
    if (!name) throw new RadioError('Station name required', 'INVALID_ARG');
    const s = this.stations.get(String(name).toLowerCase());
    if (!s) throw new RadioError(`Unknown station: ${name}`, 'STATION_NOT_FOUND');
    return s;
  }

  remove(index) {
    const i = Number(index);
    const s = this.byIndex.get(i);
    if (!s) return false;
    this.byIndex.delete(i);
    this.stations.delete(s.name.toLowerCase());
    return true;
  }

  byIndexLookup(index) {
    const i = Number(index);
    if (!Number.isInteger(i)) throw new RadioError('Station index must be integer', 'INVALID_ARG');
    const s = this.byIndex.get(i);
    if (!s) throw new RadioError(`Unknown station index: ${i}`, 'STATION_NOT_FOUND');
    return s;
  }
}

module.exports = { RadioStationStore, RadioError };
