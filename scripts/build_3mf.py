#!/usr/bin/env python3
"""Build and verify a lens-cap 3MF release from one job file.

This is the public, one-command bridge between the repository CLI and the
optional desktop adapters.  It deliberately keeps the deterministic artwork
and model stages in ``lens_cap_pipeline`` and delegates only the external
OpenSCAD/Bambu operations to ``tools/3mf_adapter``.  A native (unsliced) 3MF
is required by default; a printer-profiled Bambu 3MF can be requested as a
second, explicitly recorded output.

The command is intentionally strict about the requested deliverable.  If a
host has no OpenSCAD, it reports ``unverifiable`` and exits non-zero instead
of presenting a SCAD or handoff JSON as if it were a 3MF.  Users who already
have an externally generated STL can still use the adapter's dependency-free
``standard`` route directly (documented in tools/3mf_adapter/README.md).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
ADAPTER = ROOT / "tools" / "3mf_adapter" / "three_mf_adapter.py"
PROJECTION = ROOT / "scripts" / "audit_stl_projection.py"
# Keep the repository launcher usable immediately after a clone, before an
# editable install has been performed.  The regular ``bin/lens-cap`` launcher
# follows the same rule; relying only on site-packages would make the new
# one-command 3MF path fail in a clean checkout.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class ReleaseError(RuntimeError):
    """Raised when the requested 3MF release cannot be verified."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _run(command: Sequence[str], *, timeout: int = 1800) -> dict[str, Any]:
    argv = [str(item) for item in command]
    try:
        result = subprocess.run(
            argv,
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ReleaseError(f"cannot run {argv[0]!r}: {type(exc).__name__}: {exc}") from exc
    return {
        "argv": argv,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def _last_json(text: str) -> dict[str, Any]:
    """Parse the final JSON object from a noisy external-tool response."""

    decoder = json.JSONDecoder()
    stripped = text.strip()
    if stripped:
        try:
            value = json.loads(stripped)
            if isinstance(value, dict):
                return value
        except json.JSONDecodeError:
            pass
    for start in range(len(text) - 1, -1, -1):
        if text[start] != "{":
            continue
        try:
            value, _ = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise ReleaseError(f"tool did not emit a JSON object: {text[-800:]!r}")


def _configured_tool_hint(
    cli_value: str | None,
    config_value: str | None,
    config_dir: Path,
) -> str | None:
    """Choose a CLI-over-config tool hint and make config paths portable.

    A path-like value in ``[print]`` is interpreted relative to the job file,
    matching the rest of the pipeline's path rules.  A bare executable name
    (for example ``openscad`` or ``BambuStudio``) is kept unchanged so the
    normal PATH lookup can find a host installation.  CLI paths retain normal
    shell semantics (relative to the caller's working directory) and are
    returned unchanged.  Keeping this distinction also makes an explicit bad
    CLI/config path authoritative instead of silently falling back to another
    desktop installation.
    """

    if cli_value is not None:
        return cli_value
    if not config_value:
        return None
    raw = str(config_value).strip()
    if not raw:
        return None
    candidate = Path(raw).expanduser()
    # Keep a bare command name for ``shutil.which``.  Anything that carries
    # path intent (a separator, an explicit ``.``/``~`` prefix, an extension,
    # or an absolute path) is resolved beside the job TOML.  This lets a job
    # pin ``tools/OpenSCAD`` while still allowing portable ``openscad`` or
    # ``BambuStudio`` values on hosts that expose those commands on PATH.
    path_like = (
        candidate.is_absolute()
        or raw.startswith((".", "~"))
        or "/" in raw
        or "\\" in raw
        or bool(candidate.suffix)
    )
    if path_like and not candidate.is_absolute():
        candidate = (config_dir / candidate).resolve()
    return str(candidate if path_like else raw)


def _resolve_executable(explicit: str | None, candidates: Sequence[str]) -> str | None:
    # An explicitly supplied executable is authoritative.  Silently falling
    # back to a different app bundle would make a typo (or a deliberately
    # isolated test) appear to use the requested tool and would undermine the
    # provenance recorded in the release report.
    if explicit:
        path = Path(explicit).expanduser()
        if path.is_file() and os.access(path, os.X_OK):
            return str(path.resolve())
        found = shutil.which(explicit)
        return str(Path(found).resolve()) if found else None
    for value in candidates:
        if not value:
            continue
        path = Path(value).expanduser()
        if path.is_file() and os.access(path, os.X_OK):
            return str(path.resolve())
        found = shutil.which(value)
        if found:
            return str(Path(found).resolve())
    return None


def _portable(path: str | Path, anchor: Path) -> str:
    """Keep release reports clone-portable without hiding useful names."""

    raw = Path(path)
    resolved = raw.expanduser().resolve()
    try:
        return resolved.relative_to(anchor.resolve()).as_posix()
    except ValueError:
        return f"<external>/{resolved.name}"


def _tool_label(path: str | None) -> str | None:
    if not path:
        return None
    text = str(path)
    for bundle in ("OpenSCAD.app", "BambuStudio.app"):
        marker = f"/{bundle}/"
        if marker in text:
            return f"<{bundle}>/{text.split(marker, 1)[1]}"
    return Path(text).name


def _build_job(
    config: Path,
    *,
    force: bool,
    openscad: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    command = [
        sys.executable,
        "-m",
        "lens_cap_pipeline.cli",
        "build",
        str(config),
        "--external",
        "--export-openscad",
        "--bambu-handoff",
        "--json",
    ]
    if force:
        command.insert(command.index("--external"), "--force")
    if openscad:
        command.extend(("--openscad", str(openscad)))
    run = _run(command)
    if run["returncode"] != 0:
        raise ReleaseError(
            f"lens-cap build failed (rc={run['returncode']})\n"
            f"stdout:\n{run['stdout'][-1800:]}\nstderr:\n{run['stderr'][-1800:]}"
        )
    payload = _last_json(run["stdout"])
    if payload.get("status") not in {"passed", "unverifiable"}:
        raise ReleaseError(f"unexpected build status: {payload.get('status')!r}")
    return payload, {"argv": command, "returncode": run["returncode"], "stderr_tail": run["stderr"][-600:]}


def _adapter(command: Sequence[str], *, timeout: int = 1800, require_slice: bool = False) -> dict[str, Any]:
    run = _run(command, timeout=timeout)
    if run["returncode"] != 0:
        raise ReleaseError(
            f"3MF adapter failed (rc={run['returncode']})\n"
            f"stdout:\n{run['stdout'][-1600:]}\nstderr:\n{run['stderr'][-1600:]}"
        )
    payload = _last_json(run["stdout"])
    output = payload.get("output")
    if not isinstance(output, str) or not Path(output).is_file():
        raise ReleaseError("3MF adapter reported success but its output file is missing")
    verify_command = [sys.executable, str(ADAPTER), "verify", output]
    if require_slice:
        verify_command.append("--require-slice")
    verify_run = _run(verify_command, timeout=180)
    if verify_run["returncode"] != 0:
        raise ReleaseError(
            f"generated 3MF failed verification: {output}\n{verify_run['stderr'][-1400:]}"
        )
    verified = _last_json(verify_run["stdout"])
    return {
        "output": output,
        "manifest": f"{output}.manifest.json",
        "adapter": payload,
        "verification": verified,
        "command": list(command),
    }


def _projection_audit(config: Any, *, timeout: int = 1200) -> dict[str, Any]:
    """Verify every relief STL still occupies its approved canvas footprint.

    The normal CLI deliberately keeps the renderer-agnostic projection audit
    as a separate command.  The public 3MF endpoint must not silently skip
    that gate, so this bridge invokes it after the requested OpenSCAD STL
    export and records the resulting report alongside the native package.
    """

    model_dir = (config.output_dir / "model").resolve()
    mask_dir = (config.output_dir / "masks").resolve()
    reliefs = [palette for palette in config.relief if palette.height_mm > 0]
    if not reliefs:
        return {"status": "not_required", "colors": {}}
    # ``export-openscad`` intentionally omits a relief selector whose mask has
    # zero pixels.  Read the passed process report so an optional empty color
    # does not turn a valid job into a false projection failure, while any
    # non-empty missing selector still fails closed.
    mask_stats: dict[str, Any] = {}
    process_report = config.output_dir / "process-report.json"
    if process_report.is_file():
        try:
            parsed = json.loads(process_report.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ReleaseError(f"cannot read process report for projection audit: {process_report}") from exc
        if isinstance(parsed, dict) and isinstance(parsed.get("mask_stats"), dict):
            mask_stats = parsed["mask_stats"]
    command: list[str] = [sys.executable, str(PROJECTION)]
    names: list[str] = []
    for palette in reliefs:
        name = str(palette.name)
        mesh = model_dir / "mesh" / f"{config.job_slug}-{name}_relief.stl"
        mask = mask_dir / f"{name}.png"
        if not mesh.is_file() or not mask.is_file():
            stats = mask_stats.get(name)
            if isinstance(stats, dict) and int(stats.get("pixels", 0)) == 0 and mask.is_file():
                continue
            raise ReleaseError(
                f"projection audit inputs are missing for relief {name!r}; "
                "OpenSCAD relief export did not produce a complete current set"
            )
        names.append(name)
        command.extend(("--mesh", f"{name}={mesh}", "--expected-mask", f"{name}={mask}"))
    if not names:
        return {"status": "not_required", "colors": {}, "reason": "all relief masks are empty"}
    report_path = model_dir / "projection-report.json"
    diff_dir = model_dir / "projection-diff"
    command.extend(
        (
            "--canvas-size-mm",
            str(float(config.face_diameter_mm)),
            "--tolerance-pixels",
            "1",
            "--output-report",
            str(report_path),
            "--output-dir",
            str(diff_dir),
        )
    )
    run = _run(command, timeout=timeout)
    if run["returncode"] != 0:
        detail = run["stdout"][-1200:] or run["stderr"][-1200:]
        raise ReleaseError(f"same-canvas relief projection audit failed:\n{detail}")
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseError(f"projection audit did not write a readable report: {report_path}") from exc
    if not isinstance(report, dict) or report.get("status") != "passed":
        raise ReleaseError(f"projection audit report is not passed: {report_path}")
    return {
        "status": "passed",
        "report": _portable(report_path, config.config_path.parent),
        "diff_dir": _portable(diff_dir, config.config_path.parent),
        "colors": {
            name: {
                "raw_iou": report["colors"][name]["raw_iou"],
                "missing_outside_tolerance": report["colors"][name][
                    "expected_pixels_outside_tolerance"
                ],
                "extra_outside_tolerance": report["colors"][name][
                    "projected_pixels_outside_tolerance"
                ],
            }
            for name in sorted(names)
        },
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build, verify, and optionally slice a lens-cap 3MF from a job TOML/JSON."
    )
    parser.add_argument("config", type=Path, help="job TOML/JSON")
    parser.add_argument("--force", action="store_true", help="replace generated job derivatives and 3MF outputs")
    parser.add_argument("--openscad", help="explicit OpenSCAD executable")
    parser.add_argument(
        "--bambu",
        choices=("never", "export", "slice"),
        default="never",
        help="optional Bambu Studio stage; slice requires three profile files",
    )
    parser.add_argument("--bambu-path", help="explicit Bambu Studio executable")
    parser.add_argument("--machine-profile", type=Path)
    parser.add_argument("--process-profile", type=Path)
    parser.add_argument("--filament-profile", type=Path)
    parser.add_argument("--native-output", type=Path, help="native 3MF path (default: job out/model/<slug>-native.3mf)")
    parser.add_argument("--slice-output", type=Path, help="sliced 3MF path (default: alongside native output)")
    parser.add_argument("--report", type=Path, help="release JSON path (default: alongside native output)")
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--json", action="store_true")
    return parser


def _human(report: dict[str, Any]) -> str:
    lines = [
        f"status: {report['status']}",
        f"native 3MF: {report.get('native_3mf', {}).get('output', 'UNVERIFIABLE')}",
    ]
    sliced = report.get("bambu_3mf")
    if sliced:
        lines.append(f"Bambu {sliced.get('mode', 'output')}: {sliced.get('output', 'UNVERIFIABLE')}")
    if report.get("reason"):
        lines.append(f"reason: {report['reason']}")
    lines.append("fit: UNVERIFIABLE until a physical coupon is printed and measured")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config = args.config.expanduser().resolve()
    if args.timeout <= 0:
        print("lens-cap build-3mf: --timeout must be positive", file=sys.stderr)
        return 2
    try:
        if not config.is_file():
            raise ReleaseError(f"job config not found: {config}")
        # Import only after argument validation so ``--help`` stays usable on
        # a machine that has not installed NumPy/Pillow yet.
        from lens_cap_pipeline.config import load_config

        loaded = load_config(config)
        model_dir = (loaded.output_dir / "model").resolve()
        native = (
            args.native_output.expanduser().resolve()
            if args.native_output
            else model_dir / f"{loaded.job_slug}-native.3mf"
        )
        slice_output = (
            args.slice_output.expanduser().resolve()
            if args.slice_output
            else model_dir / f"{loaded.job_slug}-bambu-{args.bambu}.3mf"
        )
        report_path = (
            args.report.expanduser().resolve()
            if args.report
            else model_dir / "3mf-release.json"
        )
        if report_path in {native, slice_output}:
            raise ReleaseError("--report must be different from every 3MF output path")
        for path in (native, slice_output if args.bambu != "never" else None, report_path):
            if path is not None:
                if path.exists() and not args.force:
                    raise ReleaseError(f"refusing to overwrite {path}; pass --force")

        openscad_hint = _configured_tool_hint(
            args.openscad,
            loaded.print.openscad_executable,
            config.parent,
        )
        openscad = _resolve_executable(
            openscad_hint,
            ("openscad", "openscad.com", "/Applications/OpenSCAD.app/Contents/MacOS/OpenSCAD"),
        )
        if not openscad:
            raise ReleaseError(
                "OpenSCAD is required for a native one-piece 3MF; install it, set "
                "[print].openscad_executable, or pass --openscad. "
                "A SCAD/handoff result is not reported as a 3MF."
            )
        # Do not create a job output tree until the required external tool has
        # been resolved.  A failed/unverifiable request should be side-effect
        # free, so callers cannot mistake an empty ``build/`` directory for a
        # partially successful 3MF release.
        for path in (native, slice_output if args.bambu != "never" else None, report_path):
            if path is not None:
                path.parent.mkdir(parents=True, exist_ok=True)
        build_payload, build_command = _build_job(config, force=args.force, openscad=openscad)
        scad = model_dir / f"{loaded.job_slug}.scad"
        if not scad.is_file():
            raise ReleaseError(f"build did not produce the expected SCAD: {scad}")
        projection = _projection_audit(loaded, timeout=args.timeout)
        native_command = [
            sys.executable,
            str(ADAPTER),
            "openscad",
            str(scad),
            str(native),
            "--openscad",
            openscad,
            "--timeout",
            str(args.timeout),
        ]
        native_report = _adapter(native_command, timeout=args.timeout + 120)
        result: dict[str, Any] = {
            "schema_version": 1,
            "status": "passed",
            "runner": "scripts/build_3mf.py",
            "job": _portable(config, config.parent),
            "job_sha256": _sha256(config),
            "build": {
                "status": build_payload.get("status"),
                "command": [_portable(item, ROOT) if Path(item).is_absolute() else item for item in build_command["argv"]],
                "returncode": build_command["returncode"],
                "stderr_tail": build_command["stderr_tail"],
            },
            "openscad": _tool_label(openscad),
            "native_3mf": {
                "output": _portable(native, config.parent),
                "manifest": _portable(Path(f"{native}.manifest.json"), config.parent),
                "sha256": native_report["verification"].get("sha256"),
                "bytes": native_report["verification"].get("bytes"),
                "model": native_report["verification"].get("model"),
                "command": [_portable(item, config.parent) if Path(item).is_absolute() else item for item in native_command],
            },
            "projection": projection,
            "bambu_3mf": None,
            "fit_status": "unverifiable_until_coupon_measurement",
            "notes": [
                "Artwork, mask, and relief projection gates are executed by the public build path.",
                "Native 3MF is unsliced; a printer-profiled 3MF requires the explicit Bambu stage.",
                "A valid package does not prove physical fit; print and measure a same-material coupon.",
            ],
        }
        if args.bambu != "never":
            bambu_hint = _configured_tool_hint(
                args.bambu_path,
                loaded.print.bambu_executable,
                config.parent,
            )
            bambu = _resolve_executable(
                bambu_hint,
                ("BambuStudio", "bambu-studio", "/Applications/BambuStudio.app/Contents/MacOS/BambuStudio"),
            )
            if not bambu:
                raise ReleaseError("Bambu Studio was requested but could not be found")
            bambu_command = [
                sys.executable,
                str(ADAPTER),
                "bambu",
                str(scad),
                str(slice_output),
                "--mode",
                args.bambu,
                "--bambu",
                bambu,
                "--openscad",
                openscad,
                "--timeout",
                str(args.timeout),
            ]
            if args.bambu == "slice":
                profiles = (args.machine_profile, args.process_profile, args.filament_profile)
                if not all(profiles) or not all(path.expanduser().is_file() for path in profiles if path):
                    raise ReleaseError(
                        "Bambu slice requires --machine-profile, --process-profile, and --filament-profile"
                    )
                bambu_command.extend(
                    [
                        "--machine-profile",
                        str(args.machine_profile.expanduser().resolve()),
                        "--process-profile",
                        str(args.process_profile.expanduser().resolve()),
                        "--filament-profile",
                        str(args.filament_profile.expanduser().resolve()),
                    ]
                )
            sliced_report = _adapter(
                bambu_command,
                timeout=args.timeout + 180,
                require_slice=args.bambu == "slice",
            )
            result["bambu_3mf"] = {
                "mode": args.bambu,
                "output": _portable(slice_output, config.parent),
                "manifest": _portable(Path(f"{slice_output}.manifest.json"), config.parent),
                "sha256": sliced_report["verification"].get("sha256"),
                "bytes": sliced_report["verification"].get("bytes"),
                "gcode_bytes": sliced_report["verification"].get("gcode_bytes"),
                "bambu": _tool_label(bambu),
            }
        report_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        output = result if args.json else _human(result)
        print(json.dumps(output, ensure_ascii=False, indent=2) if isinstance(output, dict) else output)
        return 0
    except (OSError, ValueError, KeyError, json.JSONDecodeError, ReleaseError) as exc:
        # Emit a small machine-readable failure when requested.  No partial
        # release report is written, so a failed run cannot look publishable.
        failure = {"status": "unverifiable", "runner": "scripts/build_3mf.py", "reason": str(exc)}
        if args.json:
            print(json.dumps(failure, ensure_ascii=False, indent=2))
        else:
            print(f"lens-cap build-3mf: {str(exc)}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
