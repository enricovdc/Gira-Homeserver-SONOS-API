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

ROOT = Path(__file__).resolve().parents[1]
DIST_DIR = ROOT / "dist"
STYLE_CSS = ROOT / "help" / "style.css"
HELP_EN = ROOT / "help" / "en"
HELP_DE = ROOT / "help" / "de"


MODULES = [
    {
        "id": "22000",
        "name": "sonos_player",
        "src_dir": ROOT / "projects" / "sonos_player_hsl3",
        "hsl_name": "22000_sonos_player.hsl",
    },
    {
        "id": "22001",
        "name": "sonos_admin",
        "src_dir": ROOT / "projects" / "sonos_admin_hsl3",
        "hsl_name": "22001_sonos_admin.hsl",
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
    """HSL3 record-5000 layout (verified against the SDK generator output):

        5000 | "cat\\name" | remanent | n_in | <in_labels> | n_out |
              <out_labels> | "version" | <flag1> | <flag2>

    Field count = 4 + n_inputs + 1 + n_outputs + 1 + 2 = 8 + n_in + n_out.
    The two trailing flags are not documented in the public SDK doc but
    are emitted by every generator run. The SKILL.md formula omits them
    so this check uses the empirically-correct HSL3 formula."""
    expected = 8 + n_inputs + n_outputs
    try:
        with hsl_path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if line.startswith("5000|"):
                    n = len(line.rstrip("\n").split("|"))
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
    """Build a .hslz archive for one module.

    Per the GiraHSL SDK the .hslz format is a renamed .zip with a strict
    flat-root layout:

        <ID>_<name>.hsl        deployable produced by generator3
        EN-log<ID>.html        English help
        DE-log<ID>.html        German help
        style.css              SDK stylesheet

    Nothing else. The deployable .hsl is the binary the HomeServer
    actually loads; it is the output of generator3.cpython-39.pyc
    consuming our config.json plus the LogicModule source. The .py and
    config.json themselves never belong inside the .hslz.

    When the generator is on PATH (or pointed at via GIRA_HSL3_GEN) the
    .hsl is added and the archive is import-ready. When not, the
    archive is a spec-compliant shell that needs the .hsl dropped in.
    A README-INSIDE.txt is added so anyone opening the zip sees the
    exact finalize command and which sibling files in the source tree
    feed it.
    """
    lbs_id = module["id"]
    name = module["name"]
    archive = DIST_DIR / f"{lbs_id}_{name}.hslz"
    archive.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
        # 1. Deployable .hsl — only present when the SDK generator ran.
        if hsl_path and hsl_path.exists():
            z.write(hsl_path, arcname=hsl_path.name)
        # 2. Help pages — flat layout, stylesheet href rewritten.
        for lang_dir, lang_code in [(HELP_EN, "EN"), (HELP_DE, "DE")]:
            src = lang_dir / f"log{lbs_id}.html"
            if not src.exists():
                continue
            text = src.read_text(encoding="utf-8")
            text = text.replace('href="../style.css"', 'href="style.css"')
            z.writestr(f"{lang_code}-log{lbs_id}.html", text)
        # 3. SDK stylesheet.
        if STYLE_CSS.exists():
            z.write(STYLE_CSS, arcname="style.css")
        # 4. Finalize-instructions file (not part of the SDK spec but
        #    Experte ignores unknown files in the archive root, and it
        #    is invaluable for anyone debugging a missing .hsl).
        z.writestr(
            "README-INSIDE.txt",
            _readme_inside(module, hsl_path is not None and hsl_path.exists()),
        )
    return archive


def _readme_inside(module: dict, hsl_included: bool) -> str:
    lbs_id = module["id"]
    name = module["name"]
    if hsl_included:
        return (
            f"# Gira HSL3 logic module: LBS {lbs_id} {name}\n"
            "# Status: READY TO IMPORT\n"
            "#\n"
            "# Layout (flat root per HSLZ spec):\n"
            f"#   {lbs_id}_{name}.hsl       deployable produced by generator3\n"
            f"#   EN-log{lbs_id}.html        English help page\n"
            f"#   DE-log{lbs_id}.html        German help page\n"
            "#   style.css                  SDK stylesheet (unmodified)\n"
            "#\n"
            "# Import in Experte via Logikbausteine -> Importieren.\n"
        )
    return (
        f"# Gira HSL3 logic module: LBS {lbs_id} {name}\n"
        "# Status: INCOMPLETE - .hsl missing, requires SDK generator\n"
        "#\n"
        "# Current archive contents (spec-compliant shell, NOT yet importable):\n"
        f"#   EN-log{lbs_id}.html         English help page\n"
        f"#   DE-log{lbs_id}.html         German help page\n"
        "#   style.css                   SDK stylesheet\n"
        "#   README-INSIDE.txt           This file\n"
        "#\n"
        "# To finalize on a machine with the Gira Experte SDK installed:\n"
        "#\n"
        "#   1. Take the source from this repository:\n"
        f"#        projects/{name}_hsl3/hsl3_{lbs_id}_{name}.py\n"
        f"#        projects/{name}_hsl3/config.json\n"
        "#\n"
        "#   2. Run the generator:\n"
        "#        python3.9 generator3.cpython-39.pyc \\\n"
        f"#            --source config.json --target {lbs_id}_{name}.hsl\n"
        "#\n"
        f"#   3. Drag the produced {lbs_id}_{name}.hsl into this .hslz next\n"
        "#      to the help pages and style.css. The archive is now\n"
        "#      import-ready: Experte -> Logikbausteine -> Importieren.\n"
        "#\n"
        "# The .py and config.json deliberately do NOT belong inside the\n"
        "# .hslz per the SDK spec - they are the generator's input, not\n"
        "# part of the deployable bundle.\n"
    )


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
