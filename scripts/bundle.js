#!/usr/bin/env node
'use strict';

// Minimal CommonJS bundler. Produces dist/sonos-bridge.bundle.js — one
// self-contained file containing every module under src/, runnable on
// any Node.js 18+ install with no `npm install` step. The bundle is
// intended for deployment from the Gira HomeServer Experte project
// (see homeserver/logic-module/bridge-bootstrap/).
//
// Approach:
//   - Walk src/ to find every .js file.
//   - For each, wrap its source in a function so it has its own
//     `module`/`exports`/`require` like Node's real module loader does.
//   - Replace `require('./...')` and `require('../...')` calls with
//     synthetic IDs pointing at the bundled modules.
//   - `require(...)` for anything else falls through to Node's built-in
//     loader (so `http`, `fs`, `dgram`, `events`, `os`, `url`, `path`
//     keep working).
//
// Output preserves line numbers within each module so stack traces
// remain useful.

const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const SRC = path.join(ROOT, 'src');
const OUT_DIR = path.join(ROOT, 'dist');
const OUT_FILE = path.join(OUT_DIR, 'sonos-bridge.bundle.js');
const ENTRY = path.join(SRC, 'server.js');

function walk(dir, files = []) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) walk(full, files);
    else if (entry.name.endsWith('.js')) files.push(full);
  }
  return files;
}

function moduleId(absPath) {
  return path.relative(ROOT, absPath).replace(/\\/g, '/');
}

function rewriteRequires(source, fromAbs, modules) {
  return source.replace(/require\(\s*(['"])([^'"]+)\1\s*\)/g, (m, q, target) => {
    if (!target.startsWith('.')) return m; // node built-in or 3rd party — leave alone
    const resolved = resolveRelative(target, fromAbs);
    if (!resolved || !modules.has(moduleId(resolved))) {
      throw new Error(`Cannot bundle require('${target}') from ${moduleId(fromAbs)}: not under src/`);
    }
    return `__bundleRequire(${JSON.stringify(moduleId(resolved))})`;
  });
}

function resolveRelative(target, fromAbs) {
  const fromDir = path.dirname(fromAbs);
  let p = path.resolve(fromDir, target);
  if (fs.existsSync(p) && fs.statSync(p).isFile()) return p;
  if (fs.existsSync(p + '.js')) return p + '.js';
  if (fs.existsSync(path.join(p, 'index.js'))) return path.join(p, 'index.js');
  return null;
}

function readPackageVersion() {
  try {
    const pkg = JSON.parse(fs.readFileSync(path.join(ROOT, 'package.json'), 'utf8'));
    return pkg.version;
  } catch { return '0.0.0'; }
}

function build() {
  const files = walk(SRC);
  const modules = new Map();
  for (const f of files) modules.set(moduleId(f), fs.readFileSync(f, 'utf8'));

  // package.json is read at runtime by server.js for VERSION — inline it.
  const version = readPackageVersion();

  // Also inline an entry that loads the entry module and starts the server
  // when run as the main file.
  const entryId = moduleId(ENTRY);

  const out = [];
  out.push('#!/usr/bin/env node');
  out.push('// Bundled by scripts/bundle.js — do not edit by hand.');
  out.push(`// Version: ${version}`);
  out.push("'use strict';");
  out.push('');
  out.push('const __realRequire   = require;');
  out.push('const __bundleModules = Object.create(null);');
  out.push('const __bundleCache   = Object.create(null);');
  out.push('');
  out.push('// require() inside a bundled module: if the id is one of our bundled');
  out.push('// modules, load it from the in-memory table; otherwise fall through to');
  out.push('// the real Node require (so http, fs, dgram, events, os, path, url work).');
  out.push('function __makeRequire() {');
  out.push('  return function (id) {');
  out.push('    if (id in __bundleModules) return __bundleRequire(id);');
  out.push('    return __realRequire(id);');
  out.push('  };');
  out.push('}');
  out.push('');
  out.push('function __bundleRequire(id) {');
  out.push('  if (__bundleCache[id]) return __bundleCache[id].exports;');
  out.push('  const mod = __bundleCache[id] = { exports: {} };');
  out.push('  const fn = __bundleModules[id];');
  out.push('  if (!fn) throw new Error("Unknown bundled module: " + id);');
  out.push('  fn(mod, mod.exports, __makeRequire());');
  out.push('  return mod.exports;');
  out.push('}');
  out.push('');
  // Inline package.json so `require("../package.json")` resolves at runtime.
  out.push(`__bundleModules['package.json'] = function (module) { module.exports = ${JSON.stringify({ name: 'gira-homeserver-sonos-api', version })}; };`);
  out.push('');

  for (const [id, source] of modules) {
    // Rewrite ../package.json before relative path rewriting (server.js does this).
    let rewritten = source.replace(/require\(\s*(['"])\.\.\/package\.json\1\s*\)/g, `__bundleRequire('package.json')`);
    rewritten = rewriteRequires(rewritten, path.join(ROOT, id), modules);
    out.push(`__bundleModules[${JSON.stringify(id)}] = function (module, exports, require) {`);
    out.push(rewritten);
    out.push('};');
    out.push('');
  }

  out.push('// Entry: load the bundled server module to register its exports, then start it.');
  out.push(`const __entry  = __bundleRequire(${JSON.stringify(entryId)});`);
  out.push(`const __config = __bundleRequire('src/config.js');`);
  out.push(`const __logger = __bundleRequire('src/logger.js');`);
  out.push('module.exports = __entry;');
  out.push('');
  out.push('if (require.main === module) {');
  out.push('  const fs = require("fs");');
  out.push('  const argPath = process.argv[2];');
  out.push('  const cfgPath = (argPath && fs.existsSync(argPath)) ? argPath');
  out.push('    : (process.env.SONOS_BRIDGE_CONFIG && fs.existsSync(process.env.SONOS_BRIDGE_CONFIG)) ? process.env.SONOS_BRIDGE_CONFIG');
  out.push('    : (fs.existsSync("./config.json") ? "./config.json" : null);');
  out.push('  if (!cfgPath) {');
  out.push('    console.error("No config found. Pass a path, set SONOS_BRIDGE_CONFIG, or create ./config.json.");');
  out.push('    process.exit(1);');
  out.push('  }');
  out.push('  try {');
  out.push('    const cfg = __config.load(cfgPath);');
  out.push('    __logger.info("Config loaded", { configPath: cfgPath });');
  out.push('    __entry.startServer(cfg, { configPath: cfgPath }).then(({ server, eventMgr }) => {');
  out.push('      const shutdown = async (sig) => {');
  out.push('        __logger.info("Shutting down", { signal: sig });');
  out.push('        if (eventMgr) await eventMgr.stop();');
  out.push('        server.close(() => process.exit(0));');
  out.push('        setTimeout(() => process.exit(0), 3000).unref();');
  out.push('      };');
  out.push('      process.on("SIGTERM", () => shutdown("SIGTERM"));');
  out.push('      process.on("SIGINT",  () => shutdown("SIGINT"));');
  out.push('    }).catch((err) => {');
  out.push('      __logger.error("Failed to start server", { error: err.message });');
  out.push('      process.exit(1);');
  out.push('    });');
  out.push('  } catch (err) {');
  out.push('    __logger.error("Configuration error", { error: err.message, details: err.details });');
  out.push('    process.exit(1);');
  out.push('  }');
  out.push('}');

  fs.mkdirSync(OUT_DIR, { recursive: true });
  fs.writeFileSync(OUT_FILE, out.join('\n'), { mode: 0o755 });
  return { outFile: OUT_FILE, version, count: modules.size, bytes: fs.statSync(OUT_FILE).size };
}

if (require.main === module) {
  const r = build();
  process.stdout.write(`bundled ${r.count} module(s) → ${path.relative(ROOT, r.outFile)} (${(r.bytes / 1024).toFixed(1)} KB, v${r.version})\n`);
}

module.exports = { build };
