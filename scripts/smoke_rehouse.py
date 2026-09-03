#!/usr/bin/env python3
"""Run a clean-room, end-to-end lens-cap fixture smoke test.

The normal ``scripts/smoke.py`` intentionally stays tiny and portable.  This
runner is the heavier release rehearsal: it copies only an approved named-lens
fixture into a temporary checkout, invokes the public CLI as a new user would,
audits relief projections, exports native OpenSCAD 3MF packages, and (when the
local installation is available) asks Bambu Studio for one sliced snapshot.
No generated output is written back to the source fixture unless the caller
explicitly supplies ``--artifact-dir``.

The test is deliberately an integration rehearsal rather than a claim that a
file check proves a real lens fit.  Mechanical fit remains pending until a
same-material coupon is printed and measured.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from pathlib import Path
from typing import Any, Iterable, Mapping

# Keep direct execution usable before an editable install exists.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lens_cap_pipeline.config import PipelineConfig, load_config  # noqa: E402

ADAPTER = ROOT / "tools" / "3mf_adapter" / "three_mf_adapter.py"
PROJECTION = ROOT / "scripts" / "audit_stl_projection.py"
DEFAULT_FIXTURE = ROOT / "examples" / "fixtures" / "helios-44-2-rehouse"
DEFAULT_SIZES = (95, 82, 77)  # fallback for the original Helios fixture


class SmokeError(RuntimeError):
    """Raised when a deterministic smoke gate fails."""


_FOCAL_DISPLAY_RE = re.compile(
    r"^(?P<start>[0-9]+(?:\.[0-9]+)?)(?:mm)?"
    r"(?:-(?P<end>[0-9]+(?:\.[0-9]+)?)(?:mm)?)?$"
)


def _canonical_focal_display(value: Any, *, label: str = "focal_length_display") -> str:
    """Return a strict, unit-free focal display token.

    ``focal_length_mm`` remains the numeric machine anchor.  This optional
    companion accepts a prime token (``50mm``) or a zoom range (``28–70mm``)
    for the human-facing first display token, while rejecting arbitrary text.
    Unicode dashes, optional ``mm`` units, and the words ``to``/``至`` are
    normalized so briefs and natural-language transcripts can use ordinary
    typography without weakening the closed text contract.
    """

    if not isinstance(value, str) or not value.strip():
        raise SmokeError(f"{label} must be non-empty text")
    text = unicodedata.normalize("NFKC", value).casefold().strip()
    text = (
        text.replace("−", "-")
        .replace("–", "-")
        .replace("—", "-")
        .replace("‑", "-")
        .replace("‒", "-")
        .replace("－", "-")
        .replace("~", "-")
        .replace("毫米", "mm")
    )
    text = re.sub(r"\bto\b|至", "-", text)
    text = re.sub(r"\s+", "", text)
    match = _FOCAL_DISPLAY_RE.fullmatch(text)
    if match is None:
        raise SmokeError(
            f"{label} must be a focal token such as '50mm' or a zoom range such as '28–70mm'"
        )
    start = match.group("start")
    end = match.group("end")
    try:
        start_value = float(start)
        end_value = float(end) if end is not None else None
    except (TypeError, ValueError, OverflowError) as exc:  # pragma: no cover - regex already limits input
        raise SmokeError(f"{label} contains an invalid focal number") from exc
    if not math.isfinite(start_value) or start_value <= 0:
        raise SmokeError(f"{label} must use a positive focal length")
    if end_value is not None and (not math.isfinite(end_value) or end_value <= start_value):
        raise SmokeError(f"{label} zoom range must end above its starting focal length")
    return f"{start}-{end}" if end is not None else start


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json_from_output(text: str) -> dict[str, Any]:
    """Parse the last JSON object from a tool that may print diagnostics."""

    stripped = text.strip()
    if stripped:
        try:
            value = json.loads(stripped)
            if isinstance(value, dict):
                return value
        except json.JSONDecodeError:
            pass
    decoder = json.JSONDecoder()
    # Walking from the end avoids accidentally selecting a diagnostic object
    # printed before the command's final report.
    for index in range(len(text) - 1, -1, -1):
        if text[index] != "{":
            continue
        try:
            value, end = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if end == len(text[index:].rstrip()) and isinstance(value, dict):
            return value
        if isinstance(value, dict):
            return value
    raise SmokeError(f"tool did not emit a JSON object: {text[-600:]!r}")


def _run(
    command: Iterable[str],
    *,
    cwd: Path = ROOT,
    timeout: int = 900,
) -> dict[str, Any]:
    argv = [str(item) for item in command]
    try:
        result = subprocess.run(
            argv,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise SmokeError(f"command timed out after {timeout}s: {argv!r}\n{exc}") from exc
    return {
        "argv": argv,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def _find_executable(*candidates: str) -> str | None:
    for candidate in candidates:
        found = shutil.which(candidate)
        if found:
            return str(Path(found).resolve())
        path = Path(candidate).expanduser()
        if path.is_file() and os.access(path, os.X_OK):
            return str(path.resolve())
    return None


def _find_bambu_profiles() -> dict[str, Path] | None:
    """Find a conservative A1 mini/0.2 mm profile trio on common installs."""

    explicit = {
        "machine": os.environ.get("LENS_CAP_MACHINE_PROFILE"),
        "process": os.environ.get("LENS_CAP_PROCESS_PROFILE"),
        "filament": os.environ.get("LENS_CAP_FILAMENT_PROFILE"),
    }
    if all(explicit.values()):
        paths = {key: Path(value).expanduser() for key, value in explicit.items() if value}
        if all(path.is_file() for path in paths.values()):
            return paths

    roots = [
        Path("/Applications/BambuStudio.app/Contents/Resources/profiles/BBL"),
        Path("/Applications/BambuStudio.app/Contents/Resources/profiles"),
    ]
    for root in roots:
        machine_dir = root / "machine"
        if not machine_dir.is_dir():
            continue
        def preferred(pattern: str) -> Path | None:
            matches = sorted(root.glob(pattern))
            # Bambu ships similarly named A1/A1M profiles.  Prefer the A1M
            # variant because the machine profile below is the A1 mini; a
            # lexicographically first profile can otherwise be incompatible.
            matches.sort(key=lambda path: ("A1M" not in path.name, path.name))
            return matches[0] if matches else None

        machine = preferred("machine/*A1 mini*0.2 nozzle*.json")
        process = preferred("process/*0.10mm Standard*0.2 nozzle*.json")
        filament = preferred("filament/*Bambu PLA Basic*0.2 nozzle*.json")
        if machine and process and filament:
            return {"machine": machine, "process": process, "filament": filament}
    return None


def _copy_fixture(source: Path, destination: Path) -> list[Path]:
    """Copy only source inputs, never old generated outputs or artifacts."""

    required = (source / "design-brief.json", source / "prompt.txt", source / "art" / "master.png")
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise SmokeError("fixture is missing required source inputs: " + ", ".join(missing))
    (destination / "art").mkdir(parents=True, exist_ok=True)
    shutil.copy2(source / "design-brief.json", destination / "design-brief.json")
    shutil.copy2(source / "prompt.txt", destination / "prompt.txt")
    shutil.copy2(source / "art" / "master.png", destination / "art" / "master.png")
    source_jobs = sorted(source.glob("jobs/*/job.toml"))
    if not source_jobs:
        # Preserve a useful error for a malformed fixture instead of silently
        # producing a report with no mechanical scenarios.
        expected = ", ".join(f"jobs/{size}mm/job.toml" for size in DEFAULT_SIZES)
        raise SmokeError(f"fixture has no jobs/*/job.toml files (expected e.g. {expected})")
    copied_jobs: list[Path] = []
    for source_job in source_jobs:
        relative_job = source_job.relative_to(source)
        target_job = destination / relative_job
        target_job.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_job, target_job)
        copied_jobs.append(relative_job)
    return copied_jobs


def _validate_brief(fixture: Path) -> dict[str, Any]:
    fixture = fixture.expanduser().resolve()
    brief_path = fixture / "design-brief.json"
    brief = json.loads(brief_path.read_text(encoding="utf-8"))
    if not isinstance(brief, dict):
        raise SmokeError("design-brief.json must contain an object")
    identity = brief.get("lens_identity")
    if not isinstance(identity, dict):
        raise SmokeError("fixture must declare a lens_identity object")
    focal = identity.get("focal_length_mm")
    if isinstance(focal, bool):
        raise SmokeError("fixture focal_length_mm must be a positive number")
    try:
        focal_value = float(focal)
    except (TypeError, ValueError, OverflowError) as exc:
        raise SmokeError("fixture focal_length_mm must be a positive number") from exc
    if not math.isfinite(focal_value) or focal_value <= 0:
        raise SmokeError("fixture focal_length_mm must be a positive number")
    aperture = str(identity.get("maximum_aperture", "")).strip()
    if not aperture:
        raise SmokeError("fixture must declare maximum_aperture")
    hierarchy = brief.get("visual_direction", {}).get("dominant_hierarchy")
    if hierarchy != ["focal_length", "maximum_aperture"]:
        raise SmokeError("fixture artwork hierarchy is not focal length then maximum aperture")
    display = brief.get("display_text")
    allowed = brief.get("allowed_text")
    if (
        not isinstance(display, list)
        or len(display) < 2
        or not all(isinstance(token, str) and token.strip() for token in display)
        or not isinstance(allowed, list)
        or set(display) != set(allowed)
    ):
        raise SmokeError("display_text and allowed_text must be the same closed set")
    generation = brief.get("generation")
    if not isinstance(generation, Mapping):
        raise SmokeError("fixture must declare a generation handoff object")
    if generation.get("approved") is not True:
        raise SmokeError("approved artwork handoff must set generation.approved=true")
    anchors = brief.get("anchors")
    if not isinstance(anchors, list) or not anchors:
        raise SmokeError("fixture must declare at least one sourced design anchor")
    sourced_anchors = []
    culture_anchors = []
    for index, raw_anchor in enumerate(anchors):
        if not isinstance(raw_anchor, Mapping):
            raise SmokeError(f"anchors[{index}] must be an object")
        source = raw_anchor.get("source")
        evidence = raw_anchor.get("evidence_state")
        render_role = raw_anchor.get("render_role")
        if not all(isinstance(value, str) and value.strip() for value in (source, evidence, render_role)):
            raise SmokeError(f"anchors[{index}] must include source, evidence_state, and render_role")
        sourced_anchors.append(str(source).strip())
        claim_kind = str(raw_anchor.get("claim_kind", "")).casefold()
        if any(term in claim_kind for term in ("culture", "manufacturer", "rehouse", "cinema", "history", "craft", "system")):
            culture_anchors.append(str(source).strip())
    if not culture_anchors:
        raise SmokeError("fixture needs a source-backed manufacturer/culture or qualified rehouse anchor")
    provenance = brief.get("provenance")
    if not isinstance(provenance, Mapping):
        raise SmokeError("fixture must declare provenance/licence notes")
    for field in ("artwork_license", "film_or_history_permissions"):
        if not isinstance(provenance.get(field), str) or not provenance[field].strip():
            raise SmokeError(f"provenance.{field} must be recorded")

    focal_display_value = identity.get("focal_length_display")
    if focal_display_value is None:
        focal_display = format(focal_value, "g")
    else:
        # Keep the reviewed spelling (including an en dash or ``mm``) for the
        # provenance report; compare canonical forms only for validation.
        _canonical_focal_display(focal_display_value)
        focal_display = focal_display_value.strip()
    focal_display_canonical = _canonical_focal_display(focal_display, label="focal display")
    focal_display_start = float(focal_display_canonical.split("-", 1)[0])
    if not math.isclose(focal_display_start, focal_value, abs_tol=1e-9):
        raise SmokeError("focal_length_display must start at focal_length_mm")
    displayed_focal = _canonical_focal_display(
        display[0], label="display_text[0]"
    )
    if displayed_focal != focal_display_canonical:
        raise SmokeError("the first display_text token must be the focal length")
    aperture_token = "".join(aperture.upper().split()).replace("/", "")
    if not aperture_token.startswith("F"):
        aperture_token = "F" + aperture_token
    displayed_aperture = "".join(display[1].strip().upper().split()).replace("/", "")
    if displayed_aperture != aperture_token:
        raise SmokeError("the second display_text token must be the maximum aperture")
    prompt_value = generation.get("prompt")
    if not isinstance(prompt_value, str) or not prompt_value.strip():
        raise SmokeError("generation.prompt must point to the committed prompt record")
    prompt_path = (fixture / prompt_value).resolve()
    if not prompt_path.is_file() or fixture.resolve() not in prompt_path.parents:
        raise SmokeError("generation.prompt must stay inside the fixture")
    prompt_hash = generation.get("prompt_sha256")
    if not isinstance(prompt_hash, str) or prompt_hash.lower() != _sha256(prompt_path).lower():
        raise SmokeError("generation.prompt_sha256 does not match prompt.txt")
    prompt = prompt_path.read_text(encoding="utf-8")
    required_tokens = generation.get("required_prompt_tokens", display[:2])
    if not isinstance(required_tokens, list) or not all(isinstance(token, str) for token in required_tokens):
        raise SmokeError("generation.required_prompt_tokens must be a list of strings")
    for token in required_tokens:
        if token not in prompt:
            raise SmokeError(f"prompt record is missing exact token {token!r}")
    candidate_value = generation.get("candidate_path")
    if not isinstance(candidate_value, str) or not candidate_value.strip():
        raise SmokeError("generation.candidate_path must name the approved raster")
    art_path = (fixture / candidate_value).resolve()
    if fixture.resolve() not in art_path.parents or not art_path.is_file():
        raise SmokeError(f"approved artwork is missing: {art_path}")
    art_hash = generation.get("candidate_sha256")
    if not isinstance(art_hash, str) or _sha256(art_path).lower() != art_hash.lower():
        raise SmokeError("approved artwork hash does not match design brief")
    return {
        "status": "passed",
        "identity": identity,
        "focal_length_mm": focal_value,
        "focal_length_display": focal_display,
        "maximum_aperture": aperture,
        "display_text": display,
        "prompt": Path(os.path.relpath(prompt_path, fixture)).as_posix(),
        "prompt_sha256": _sha256(prompt_path),
        "artwork": Path(os.path.relpath(art_path, fixture)).as_posix(),
        "artwork_sha256": _sha256(art_path),
        "anchor_count": len(sourced_anchors),
        "culture_anchor_count": len(culture_anchors),
    }


def _build_job(
    job_path: Path,
    *,
    require_external: bool,
    export_openscad: bool,
) -> tuple[PipelineConfig, dict[str, Any]]:
    config = load_config(job_path)
    command = [
        sys.executable,
        "-m",
        "lens_cap_pipeline.cli",
        "build",
        str(job_path),
        "--force",
        "--bambu-handoff",
        "--external",
        "--json",
    ]
    if export_openscad:
        command.insert(command.index("--bambu-handoff"), "--export-openscad")
    if require_external:
        command.append("--strict-external")
    run = _run(command, timeout=1200)
    if run["returncode"] not in {0}:
        raise SmokeError(
            f"CLI build failed for {job_path} (rc={run['returncode']})\n"
            f"stdout:\n{run['stdout'][-1200:]}\nstderr:\n{run['stderr'][-1200:]}"
        )
    payload = _json_from_output(run["stdout"])
    if payload.get("status") not in {"passed", "unverifiable"}:
        raise SmokeError(f"unexpected build status for {job_path}: {payload.get('status')!r}")
    process_report = config.output_dir / "process-report.json"
    geometry_report = config.output_dir / "model" / "geometry-report.json"
    if not process_report.is_file() or not geometry_report.is_file():
        raise SmokeError(f"build did not write process/model reports for {job_path}")
    process_data = json.loads(process_report.read_text(encoding="utf-8"))
    geometry_data = json.loads(geometry_report.read_text(encoding="utf-8"))
    if process_data.get("status") != "passed" or geometry_data.get("status") != "passed":
        raise SmokeError(f"deterministic report failed for {job_path}")
    mechanical = geometry_data.get("mechanical")
    expected = float(config.measured_diameter_mm or config.face_diameter_mm)
    if not isinstance(mechanical, dict) or float(mechanical.get("measured_diameter_mm", -1)) != expected:
        raise SmokeError(f"measured diameter did not survive build for {job_path}")
    # A fixture may describe a step-up/adapter envelope as a nominal ring plus
    # a radial wall.  Check the arithmetic explicitly so a clean-room run
    # exercises the same measurement rule users need for real hardware.
    nominal = config.metadata.get("adapter_nominal_ring_mm") if isinstance(config.metadata, dict) else None
    radial_wall = config.metadata.get("adapter_radial_wall_mm") if isinstance(config.metadata, dict) else None
    measurement_note: dict[str, float] | None = None
    if nominal is not None or radial_wall is not None:
        if nominal is None or radial_wall is None:
            raise SmokeError(f"adapter envelope on {job_path} must declare both nominal ring and radial wall")
        try:
            nominal_value = float(nominal)
            radial_wall_value = float(radial_wall)
        except (TypeError, ValueError, OverflowError) as exc:
            raise SmokeError(f"adapter envelope on {job_path} contains non-numeric dimensions") from exc
        if not math.isfinite(nominal_value) or not math.isfinite(radial_wall_value) or nominal_value <= 0 or radial_wall_value < 0:
            raise SmokeError(f"adapter envelope on {job_path} contains invalid dimensions")
        derived = nominal_value + 2.0 * radial_wall_value
        if abs(derived - expected) > 1e-6:
            raise SmokeError(
                f"measured diameter {expected:g} does not equal nominal ring + 2*radial wall "
                f"({nominal_value:g} + 2*{radial_wall_value:g} = {derived:g}) for {job_path}"
            )
        measurement_note = {
            "nominal_ring_mm": nominal_value,
            "radial_adapter_wall_mm": radial_wall_value,
            "derived_mating_diameter_mm": derived,
        }
    if mechanical.get("friction_ribs_enabled") is not True:
        raise SmokeError(f"fixture default ribs were not enabled for {job_path}")
    if mechanical.get("friction_ribs_explicit") is not False:
        raise SmokeError(f"fixture default ribs were incorrectly marked explicit for {job_path}")
    scad = config.output_dir / "model" / f"{config.job_slug}.scad"
    assembly = config.output_dir / "model" / "mesh" / f"{config.job_slug}-assembly.stl"
    if not scad.is_file():
        raise SmokeError(f"build did not write a usable SCAD for {job_path}")
    if export_openscad and (not assembly.is_file() or assembly.stat().st_size <= 84):
        raise SmokeError(f"OpenSCAD was requested but no usable assembly STL was written for {job_path}")
    return config, {
        "status": "passed",
        "build_status": payload.get("status"),
        "job": str(job_path),
        "measured_diameter_mm": expected,
        "adapter_envelope": measurement_note,
        "friction_ribs": {
            "enabled": mechanical["friction_ribs_enabled"],
            "explicit": mechanical["friction_ribs_explicit"],
            "profile": mechanical.get("friction_rib_profile"),
        },
        "process_report": str(process_report),
        "geometry_report": str(geometry_report),
        "scad": str(scad),
        "assembly_stl": str(assembly) if assembly.is_file() else None,
        "cli": {"returncode": run["returncode"], "stderr_tail": run["stderr"][-600:]},
    }


def _projection_audit(config: PipelineConfig) -> dict[str, Any]:
    mesh_dir = config.output_dir / "model" / "mesh"
    mask_dir = config.output_dir / "masks"
    names: list[str] = []
    command = [sys.executable, str(PROJECTION)]
    for palette in config.relief:
        mesh = mesh_dir / f"{config.job_slug}-{palette.name}_relief.stl"
        mask = mask_dir / f"{palette.name}.png"
        if not mesh.is_file() or not mask.is_file():
            continue
        names.append(palette.name)
        command.extend(("--mesh", f"{palette.name}={mesh}", "--expected-mask", f"{palette.name}={mask}"))
    if not names:
        return {
            "status": "unverifiable",
            "reason": "relief STL exports are unavailable (install OpenSCAD for this optional gate)",
        }
    report_path = config.output_dir / "model" / "projection-report.json"
    diff_dir = config.output_dir / "model" / "projection-diff"
    command.extend(
        (
            "--canvas-size-mm",
            str(config.face_diameter_mm),
            "--tolerance-pixels",
            "1",
            "--output-report",
            str(report_path),
            "--output-dir",
            str(diff_dir),
        )
    )
    run = _run(command, timeout=1200)
    if run["returncode"] != 0:
        raise SmokeError(
            f"projection audit failed for {config.job_slug}\n{run['stdout'][-1200:]}\n{run['stderr'][-1200:]}"
        )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("status") != "passed":
        raise SmokeError(f"projection report is not passed for {config.job_slug}")
    return {
        "status": "passed",
        "report": str(report_path),
        "colors": {
            name: {
                "raw_iou": report["colors"][name]["raw_iou"],
                "missing_outside_tolerance": report["colors"][name]["expected_pixels_outside_tolerance"],
                "extra_outside_tolerance": report["colors"][name]["projected_pixels_outside_tolerance"],
            }
            for name in sorted(names)
        },
    }


def _adapter_verify(path: Path, *, require_slice: bool = False) -> dict[str, Any]:
    command = [sys.executable, str(ADAPTER), "verify", str(path)]
    if require_slice:
        command.append("--require-slice")
    run = _run(command, timeout=120)
    if run["returncode"] != 0:
        raise SmokeError(f"3MF verification failed for {path}: {run['stderr'][-1000:]}")
    return _json_from_output(run["stdout"])


def _native_3mf(config: PipelineConfig, output_dir: Path, openscad: str | None) -> dict[str, Any]:
    if not openscad:
        return {"status": "unverifiable", "reason": "OpenSCAD executable not found"}
    scad = config.output_dir / "model" / f"{config.job_slug}.scad"
    output = output_dir / f"{config.job_slug}-native.3mf"
    command = [
        sys.executable,
        str(ADAPTER),
        "openscad",
        str(scad),
        str(output),
        "--openscad",
        openscad,
        "--timeout",
        "1200",
    ]
    run = _run(command, timeout=1500)
    if run["returncode"] != 0 or not output.is_file():
        raise SmokeError(f"native OpenSCAD 3MF export failed for {config.job_slug}: {run['stderr'][-1600:]}")
    verified = _adapter_verify(output)
    manifest = output.with_suffix(output.suffix + ".manifest.json")
    return {
        "status": "passed",
        "mode": "openscad-native",
        "path": str(output),
        "manifest": str(manifest),
        "sha256": verified.get("sha256"),
        "bytes": verified.get("bytes"),
        "has_embedded_gcode": verified.get("has_embedded_gcode"),
    }


def _bambu_run(
    configs: list[PipelineConfig],
    output_dir: Path,
    *,
    bambu_mode: str,
    bambu: str | None,
    openscad: str | None,
    profiles: dict[str, Path] | None,
    require_external: bool,
) -> dict[str, Any]:
    if bambu_mode == "never":
        return {"status": "not_requested", "mode": "never"}
    if not bambu:
        result = {"status": "unverifiable", "mode": bambu_mode, "reason": "Bambu Studio executable not found"}
        if require_external:
            raise SmokeError(result["reason"])
        return result
    if bambu_mode == "slice" or (bambu_mode == "auto" and profiles):
        if not profiles:
            result = {"status": "unverifiable", "mode": "slice", "reason": "A1 mini profiles not found"}
            if require_external:
                raise SmokeError(result["reason"])
            return result
        # Slice the largest scenario once.  This keeps the rehearsal bounded
        # while retaining the most demanding common front diameter, regardless
        # of how a fixture names or orders its job directories.
        selected = max(
            configs,
            key=lambda config: float(config.measured_diameter_mm or config.face_diameter_mm),
        )
        mode = "slice"
    else:
        selected = max(
            configs,
            key=lambda config: float(config.measured_diameter_mm or config.face_diameter_mm),
        )
        mode = "export"
    assembly = selected.output_dir / "model" / "mesh" / f"{selected.job_slug}-assembly.stl"
    if not assembly.is_file():
        result = {
            "status": "unverifiable",
            "mode": mode,
            "reason": "assembly STL is unavailable; run OpenSCAD export first",
        }
        if require_external:
            raise SmokeError(result["reason"])
        return result
    output = output_dir / f"{selected.job_slug}-bambu-{mode}.3mf"
    command = [
        sys.executable,
        str(ADAPTER),
        "bambu",
        str(assembly),
        str(output),
        "--mode",
        mode,
        "--bambu",
        bambu,
        "--timeout",
        "1200",
    ]
    if openscad:
        command.extend(("--openscad", openscad))
    if mode == "slice":
        command.extend(
            (
                "--machine-profile",
                str(profiles["machine"]),
                "--process-profile",
                str(profiles["process"]),
                "--filament-profile",
                str(profiles["filament"]),
            )
        )
    run = _run(command, timeout=1800)
    if run["returncode"] != 0 or not output.is_file():
        raise SmokeError(f"Bambu {mode} failed: {run['stderr'][-1600:]}")
    verified = _adapter_verify(output, require_slice=mode == "slice")
    manifest = output.with_suffix(output.suffix + ".manifest.json")
    return {
        "status": "passed",
        "mode": mode,
        "job": selected.job_slug,
        "path": str(output),
        "manifest": str(manifest),
        "sha256": verified.get("sha256"),
        "bytes": verified.get("bytes"),
        "has_embedded_gcode": verified.get("has_embedded_gcode"),
        "profiles": {key: str(value) for key, value in (profiles or {}).items()} if mode == "slice" else None,
        "stderr_tail": run["stderr"][-600:],
    }


def _copy_artifacts(source_dir: Path, destination: Path, *, force: bool = False) -> list[str]:
    destination.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for path in sorted(source_dir.glob("*.3mf")):
        target = destination / path.name
        if target.exists() and not force:
            raise SmokeError(f"refusing to overwrite artifact; pass --force-artifacts: {target}")
        shutil.copy2(path, target)
        manifest = path.with_suffix(path.suffix + ".manifest.json")
        if manifest.is_file():
            manifest_target = destination / manifest.name
            if manifest_target.exists() and not force:
                raise SmokeError(f"refusing to overwrite artifact manifest: {manifest_target}")
            shutil.copy2(manifest, manifest_target)
        copied.append(str(target))
    if not copied:
        raise SmokeError(f"no 3MF outputs were produced under {source_dir}")
    return copied


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE, help="source fixture directory")
    parser.add_argument(
        "--bambu",
        choices=("auto", "never", "export", "slice"),
        default="auto",
        help="optional Bambu stage (auto slices once when profiles are found)",
    )
    parser.add_argument("--require-external", action="store_true", help="fail if OpenSCAD/Bambu stages are unavailable")
    parser.add_argument("--keep-workdir", action="store_true", help="retain the isolated generated checkout")
    parser.add_argument("--workdir", type=Path, help="explicit isolated output directory (implies --keep-workdir)")
    parser.add_argument("--artifact-dir", type=Path, help="copy generated 3MFs and sidecars here")
    parser.add_argument("--force-artifacts", action="store_true", help="allow replacing files in --artifact-dir")
    parser.add_argument("--json", action="store_true", help="print the complete report as JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    fixture = args.fixture.expanduser().resolve()
    if not fixture.is_dir():
        print(f"lens-cap smoke-rehouse: fixture directory not found: {fixture}", file=sys.stderr)
        return 2
    retained = args.workdir is not None or args.keep_workdir
    temp_context: tempfile.TemporaryDirectory[str] | None = None
    if args.workdir:
        work_root = args.workdir.expanduser().resolve()
        if work_root.exists() and any(work_root.iterdir()):
            print(f"lens-cap smoke-rehouse: workdir is not empty: {work_root}", file=sys.stderr)
            return 2
        work_root.mkdir(parents=True, exist_ok=True)
    else:
        if retained:
            # ``TemporaryDirectory`` always cleans itself at interpreter
            # shutdown, even when the object is kept alive.  Use mkdtemp for
            # an explicitly retained rehearsal and remove it ourselves only
            # in the ordinary ephemeral mode.
            work_root = Path(tempfile.mkdtemp(prefix="lens-cap-rehouse-smoke-"))
        else:
            temp_context = tempfile.TemporaryDirectory(prefix="lens-cap-rehouse-smoke-")
            work_root = Path(temp_context.name)

    try:
        clean_fixture = work_root / "fixture"
        generated_artifacts = work_root / "artifacts"
        job_relatives = _copy_fixture(fixture, clean_fixture)
        brief_report = _validate_brief(clean_fixture)
        openscad = _find_executable("openscad", "/Applications/OpenSCAD.app/Contents/MacOS/OpenSCAD")
        bambu = _find_executable(
            "bambu-studio",
            "BambuStudio",
            "/Applications/BambuStudio.app/Contents/MacOS/BambuStudio",
        )
        profiles = _find_bambu_profiles()
        if args.require_external and not openscad:
            raise SmokeError("OpenSCAD executable not found; --require-external needs native 3MF export")
        jobs: list[PipelineConfig] = []
        job_reports: list[dict[str, Any]] = []
        for relative_job in job_relatives:
            job_path = clean_fixture / relative_job
            config, report = _build_job(
                job_path,
                require_external=args.require_external,
                export_openscad=bool(openscad),
            )
            report["projection"] = _projection_audit(config)
            report["native_3mf"] = _native_3mf(config, generated_artifacts, openscad)
            if args.require_external and report["native_3mf"]["status"] != "passed":
                raise SmokeError(
                    f"native 3MF export is unavailable for {config.measured_diameter_mm or config.face_diameter_mm:g} mm"
                )
            jobs.append(config)
            job_reports.append(report)
        bambu_report = _bambu_run(
            jobs,
            generated_artifacts,
            bambu_mode=args.bambu,
            bambu=bambu,
            openscad=openscad,
            profiles=profiles,
            require_external=args.require_external,
        )
        copied = _copy_artifacts(generated_artifacts, args.artifact_dir.expanduser().resolve(), force=args.force_artifacts) if args.artifact_dir else []
        report = {
            "schema_version": 1,
            "status": "passed",
            "runner": "scripts/smoke_rehouse.py",
            "fixture": str(fixture),
            "clean_fixture": str(clean_fixture),
            "workdir_retained": retained,
            "tools": {
                "openscad": Path(openscad).name if openscad else None,
                "bambu": Path(bambu).name if bambu else None,
                "bambu_profiles_found": bool(profiles),
            },
            "brief": brief_report,
            "jobs": job_reports,
            "bambu": bambu_report,
            "copied_artifacts": copied,
            "fit_status": "unverifiable_until_coupon_measurement",
            "notes": [
                "Style-equivalence and same-canvas projection are audited; pixel-perfect generative reproduction is not claimed.",
                "A valid 3MF and slicer result do not prove physical fit; print and measure a coupon.",
            ],
        }
        print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else _human_summary(report))
        return 0
    except (OSError, ValueError, KeyError, json.JSONDecodeError, SmokeError) as exc:
        print(f"lens-cap smoke-rehouse: error: {exc}", file=sys.stderr)
        return 1
    finally:
        if temp_context is not None and not retained:
            temp_context.cleanup()


def _human_summary(report: dict[str, Any]) -> str:
    lines = [
        "status: passed",
        f"clean fixture: {report['clean_fixture']}",
        "jobs: " + ", ".join(f"{item['measured_diameter_mm']:.0f} mm" for item in report["jobs"]),
        "native 3MF: " + ", ".join(item["native_3mf"]["status"] for item in report["jobs"]),
        f"Bambu: {report['bambu']['status']} ({report['bambu'].get('mode')})",
        "fit: UNVERIFIABLE until a physical coupon is measured",
    ]
    if report.get("copied_artifacts"):
        lines.append("copied artifacts: " + ", ".join(report["copied_artifacts"]))
    if report.get("workdir_retained"):
        lines.append("workdir retained: inspect the generated reports and 3MF files above")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
