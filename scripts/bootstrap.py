#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Create a project-local environment and install lens-cap-pipeline.

This helper deliberately writes only to ``.venv`` below the repository root.
It never invokes a shell, installs into the system interpreter, or accepts an
arbitrary destination path.  It is therefore usable from macOS, Linux, and
Windows (with Python 3.11 or newer).
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENV_DIR = ROOT / ".venv"


def _python_in_venv() -> Path:
    """Return the platform-specific interpreter path in the local venv."""

    relative = Path("Scripts/python.exe") if sys.platform == "win32" else Path("bin/python")
    return VENV_DIR / relative


def _run(command: list[str]) -> None:
    """Run a command without shell interpolation and with repository cwd."""

    print("$", " ".join(command))
    subprocess.run(command, cwd=ROOT, check=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Create .venv inside this checkout and install local extras."
    )
    parser.add_argument(
        "--dev",
        action="store_true",
        help="install development extras (tests plus ruff) instead of test-only extras",
    )
    parser.add_argument(
        "--locked",
        action="store_true",
        help="use the committed uv.lock (requires uv) for byte-stable dependencies",
    )
    args = parser.parse_args(argv)

    if sys.version_info < (3, 11):
        print("lens-cap bootstrap requires Python 3.11 or newer", file=sys.stderr)
        return 2

    # ``ROOT`` is derived from this file, never from the caller's cwd, so a
    # shell invocation cannot redirect writes to an arbitrary directory.
    if not (ROOT / "pyproject.toml").is_file():
        print(f"cannot find pyproject.toml under {ROOT}", file=sys.stderr)
        return 2

    try:
        interpreter = _python_in_venv()
        if VENV_DIR.is_symlink():
            print(f"refusing to use symlinked virtual-environment path: {VENV_DIR}", file=sys.stderr)
            return 2
        if VENV_DIR.exists() and not VENV_DIR.is_dir():
            print(f"virtual-environment path is not a directory: {VENV_DIR}", file=sys.stderr)
            return 2
        if not interpreter.is_file():
            print(f"creating project-local virtual environment: {VENV_DIR}")
            venv.EnvBuilder(with_pip=True, clear=False).create(VENV_DIR)
        if not interpreter.is_file():
            print(f"virtual-environment interpreter was not created: {interpreter}", file=sys.stderr)
            return 2

        extra = "dev" if args.dev else "test"
        if args.locked:
            uv = shutil.which("uv")
            if uv is None:
                print("--locked requires the uv command; install uv or omit --locked", file=sys.stderr)
                return 2
            # uv owns the same project-local .venv and resolves the committed
            # lockfile without shell interpolation.  Keep the command list
            # explicit so paths and extras cannot be injected through a shell.
            _run([uv, "sync", "--locked", "--extra", extra])
        else:
            _run([str(interpreter), "-m", "pip", "install", "-e", f".[{extra}]"])
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"bootstrap failed: {exc}", file=sys.stderr)
        return 1

    print(f"ready: {interpreter}")
    if sys.platform == "win32":
        print(r"activate with (PowerShell): .venv\Scripts\Activate.ps1")
        print(r"activate with (cmd.exe): .venv\Scripts\activate.bat")
    else:
        print("activate with: . .venv/bin/activate")
    print("then run: lens-cap doctor --json")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
