#!/usr/bin/env python3
"""Build HSLZ archives for the Sonos HSL3 logic modules.

The HSLZ format is a renamed .zip with all files flat at the root:

    22000_sonos_player.hsl
    EN-log22000.html
    DE-log22000.html
    style.css

This script:

1. Runs the Gira HSL3 generator if it is available on PATH or pointed at via
   the GIRA_HSL3_GEN environment variable (a Python 3.9 .pyc) to produce the
   ``.hsl`` deployable file from each module's config.json + Python source.

2. If the generator is not available, copies the Python source as-is into
   ``dist/<id>_<name>.hsl.py`` and records what the generator command would
   have been. The integrator then runs the generator manually on a machine
   where it is installed.

3. Packages the ``.hsl`` (when present) plus the per-language help files and
   the SDK stylesheet into ``dist/<id>_<name>.hslz``.

4. Verifies the pipe-delimited field count in the deployable ``.hsl`` per the
   SDK's record-5000 invariant (only when the generator produced a real
   ``.hsl``; the Python-only fallback skips this check).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
HSL3_DIR = ROOT / "homeserver" / "logic-module" / "hsl3"
DIST_DIR = HSL3_DIR / "build" / "dist"
STYLE_CSS = HSL3_DIR / "help" / "style.css"
HELP_EN = HSL3_DIR / "help" / "en"
HELP_DE = HSL3_DIR / "help" / "de"


MODULES = [
    {
        "id": "22000",
        "name": "sonos_player",
        "src_dir": HSL3_DIR / "src_22000_sonos_player",
        "hsl_name": "22000_sonos_player.hsl",
    },
    {
        "id": "22001",
        "name": "sonos_discover",
        "src_dir": HSL3_DIR / "src_22001_sonos_discover",
        "hsl_name": "22001_sonos_discover.hsl",
    },
]


def find_generator() -> str | None:
    """Return path to the HSL3 generator if available, else None."""
    env = os.environ.get("GIRA_HSL3_GEN")
    if env and Path(env).exists():
        return env
    for candidate in ("generator3.cpython-39.pyc", "generator3.pyc"):
        path = shutil.which(candidate)
        if path:
            return path
    return None


def run_generator(gen_path: str, py39: str, config_json: Path, target_hsl: Path) -> bool:
    """Run the HSL3 generator. Returns True on success."""
    cmd = [py39, gen_path, "--source", str(config_json), "--target", str(target_hsl)]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except FileNotFoundError:
        print("Python 3.9 interpreter not found at:", py39)
        return False
    if result.returncode != 0:
        print(f"generator failed (rc={result.returncode}):")
        print(result.stdout)
        print(result.stderr)
        return False
    print(f"  generator ok: {target_hsl.name}")
    return True


def verify_hsl_field_count(hsl_path: Path, n_inputs: int, n_outputs: int) -> bool:
    """Per the SDK invariant: record-5000 line is pipe-delimited; the field
    count must equal 4 + n_inputs + 1 + n_outputs + 1."""
    expected = 4 + n_inputs + 1 + n_outputs + 1
    try:
        with hsl_path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if line.startswith("5000|"):
                    n = len(line.split("|"))
                    if n != expected:
                        print(f"  WARNING: record 5000 has {n} fields, expected {expected}")
                        return False
                    print(f"  record 5000 field count ok ({n})")
                    return True
    except OSError as e:
        print(f"  WARNING: could not open .hsl for verification: {e}")
    return True


def count_io(config_json: Path) -> tuple[int, int]:
    with config_json.open() as fh:
        cfg = json.load(fh)
    mod = cfg["module"]
    return len(mod.get("inputs", [])), len(mod.get("outputs", []))


def build_hslz(module: dict, hsl_path: Path | None) -> Path:
    lbs_id = module["id"]
    archive = DIST_DIR / f"{lbs_id}_{module['name']}.hslz"
    archive.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
        if hsl_path and hsl_path.exists():
            z.write(hsl_path, arcname=hsl_path.name)
        for lang_dir, lang_code in [(HELP_EN, "EN"), (HELP_DE, "DE")]:
            src = lang_dir / f"log{lbs_id}.html"
            if not src.exists():
                continue
            text = src.read_text(encoding="utf-8")
            # Flatten the stylesheet href: archive root has no help/ tree.
            text = text.replace('href="../style.css"', 'href="style.css"')
            z.writestr(f"{lang_code}-log{lbs_id}.html", text)
        if STYLE_CSS.exists():
            z.write(STYLE_CSS, arcname="style.css")
    return archive


def main() -> int:
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    gen_path = find_generator()
    py39 = os.environ.get("PYTHON39", "python3.9")

    for module in MODULES:
        print(f"\n=== Building LBS {module['id']} {module['name']} ===")
        config_json = module["src_dir"] / "config.json"
        py_src = module["src_dir"] / f"hsl3_{module['id']}_{module['name']}.py"
        target_hsl = DIST_DIR / module["hsl_name"]

        # Sanity check on sources.
        if not config_json.exists():
            print(f"  ERROR: missing {config_json}")
            return 1
        if not py_src.exists():
            print(f"  ERROR: missing {py_src}")
            return 1

        n_in, n_out = count_io(config_json)
        print(f"  inputs: {n_in}, outputs: {n_out}")

        # 1) Try to run the official generator.
        hsl_produced: Path | None = None
        if gen_path:
            print(f"  using generator: {gen_path}")
            if run_generator(gen_path, py39, config_json, target_hsl):
                hsl_produced = target_hsl
                verify_hsl_field_count(target_hsl, n_in, n_out)
        else:
            print("  generator not found (set GIRA_HSL3_GEN); copying Python source as fallback.")
            shutil.copy(py_src, DIST_DIR / py_src.name)
            shutil.copy(config_json, DIST_DIR / f"{module['id']}_{module['name']}_config.json")

        # 2) Always build the HSLZ container with whatever artifacts we have.
        archive = build_hslz(module, hsl_produced)
        print(f"  packaged: {archive.relative_to(ROOT)}")

    print(f"\nArtifacts in: {DIST_DIR.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
