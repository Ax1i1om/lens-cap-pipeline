"""Independent validation of process, model, and handoff artifacts."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from .config import PipelineConfig
from .external import OPENSCAD_BACKEND

VALIDATE_VERSION = "0.1.0"


def _object(value: Any) -> dict[str, Any]:
    """Return a JSON object or an empty mapping for malformed nested data.

    Validation is a trust boundary: a syntactically valid report may still
    contain ``source: []`` or ``mechanical: "oops"``.  Treat those shapes as
    failed evidence instead of allowing an incidental ``AttributeError`` to
    escape the validator.
    """
    return value if isinstance(value, dict) else {}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _portable_path(path: Path, root: Path) -> str:
    """Render an artifact path relative to a job root when possible.

    Validation reports are routinely archived and compared after cloning a
    job to another checkout. Absolute checkout prefixes make otherwise
    identical reports differ, so use a relative path (including parent
    segments) whenever the platform permits it. A path on another Windows
    drive is retained explicitly because silently reducing it to a basename
    would destroy provenance.
    """
    resolved = path.expanduser().resolve()
    try:
        return Path(os.path.relpath(resolved, root.expanduser().resolve())).as_posix()
    except (ValueError, OSError):
        return str(resolved)


def _stable_tool_text(text: str, *, root: Path, scad: Path, temporary: Path) -> str:
    """Remove machine-specific paths and timing noise from tool diagnostics."""
    if not text:
        return ""
    replacements = {
        str(root.expanduser().resolve()): "<job>",
        str(scad.expanduser().resolve()): "<job-scad>",
        str(temporary.expanduser().resolve()): "<temporary>",
    }
    normalized = text
    for original, replacement in replacements.items():
        normalized = normalized.replace(original, replacement)
    # OpenSCAD emits cache sizes and elapsed rendering time; these are useful
    # interactively but make a validation manifest non-reproducible.
    lines = []
    for line in normalized.splitlines():
        if "cache" in line.lower() or "total rendering time" in line.lower():
            continue
        lines.append(line)
    return "\n".join(lines[-40:])


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(text.encode("utf-8"))
    try:
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _tool(
    configured: str | None,
    names: tuple[str, ...],
    *,
    base_dir: Path | None = None,
) -> str | None:
    if configured:
        candidate = Path(configured).expanduser()
        if not candidate.is_absolute() and base_dir is not None:
            # Match config/source path semantics used by the export adapter:
            # a relative executable is relative to the job file, not cwd.
            candidate = (base_dir / candidate).resolve()
        if candidate.is_file():
            return str(candidate.resolve())
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return None


def _check_openscad(config: PipelineConfig, scad: Path) -> dict[str, Any]:
    executable = _tool(
        config.print.openscad_executable,
        ("openscad", "openscad.com"),
        base_dir=config.config_path.parent,
    )
    if executable is None:
        return {"status": "unverifiable", "reason": "OpenSCAD executable not found"}
    # Compile to a temporary output so validation never overwrites a user's
    # mesh.  OpenSCAD's return code is the only success signal we accept.
    job_root = config.output_dir.resolve()
    with tempfile.TemporaryDirectory(prefix="lenscap-openscad-") as temp:
        output = Path(temp) / "probe.stl"
        command = [
            executable,
            "--backend",
            OPENSCAD_BACKEND,
            "--export-format",
            "binstl",
            "-D",
            'render_part="assembly"',
            "-o",
            str(output),
            str(scad),
        ]
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=300, check=False)
        except (OSError, subprocess.SubprocessError) as exc:
            safe_error = str(exc).replace(executable, Path(executable).name)
            return {
                "status": "unverifiable",
                "executable": Path(executable).name,
                "error": safe_error,
                "command": [
                    Path(executable).name,
                    "--backend",
                    OPENSCAD_BACKEND,
                    "--export-format",
                    "binstl",
                    "-D",
                    'render_part="assembly"',
                    "-o",
                    "<temporary>/probe.stl",
                    _portable_path(scad, job_root),
                ],
            }
        report_command = [
            Path(executable).name,
            "--backend",
            OPENSCAD_BACKEND,
            "--export-format",
            "binstl",
            "-D",
            'render_part="assembly"',
            "-o",
            "<temporary>/probe.stl",
            _portable_path(scad, job_root),
        ]
        return {
            "status": "passed" if result.returncode == 0 and output.is_file() and output.stat().st_size > 84 else "failed",
            "executable": Path(executable).name,
            "returncode": result.returncode,
            "command": report_command,
            "stdout_tail": _stable_tool_text(result.stdout, root=job_root, scad=scad, temporary=output.parent),
            "stderr_tail": _stable_tool_text(result.stderr, root=job_root, scad=scad, temporary=output.parent),
            "probe_size_bytes": output.stat().st_size if output.is_file() else None,
        }


def _read_binary_mask(path: Path, shape: tuple[int, ...]) -> tuple[np.ndarray | None, dict[str, Any]]:
    """Read a semantic mask and report shape/value-domain diagnostics.

    Process masks are intentionally binary PNGs.  Treating every non-zero
    grayscale value as positive would let an antialiased or accidentally
    colourised mask pass a partition check, so retain a separate ``binary``
    gate while returning the convenient boolean view for set operations.
    """
    try:
        raw = np.asarray(Image.open(path).convert("L"), dtype=np.uint8)
    except (OSError, ValueError) as exc:
        return None, {"status": "failed", "reason": str(exc)}
    shape_ok = tuple(raw.shape) == tuple(shape)
    binary = bool(np.all((raw == 0) | (raw == 255)))
    mask = raw > 0
    ok = bool(shape_ok and binary)
    details = {
        "status": "passed" if ok else "failed",
        "dimensions_px": [int(raw.shape[1]), int(raw.shape[0])] if raw.ndim == 2 else None,
        "dimensions_match": shape_ok,
        "binary": binary,
        "pixels": int(np.count_nonzero(mask)),
        "sha256": _sha256(path),
    }
    if not shape_ok:
        details["reason"] = "mask dimensions differ from process master"
    elif not binary:
        details["reason"] = "mask contains grayscale values other than 0/255"
    # A shape-mismatched array cannot participate in the stack below.  Return
    # it as unreadable while retaining the diagnostic details for the report.
    return (mask if shape_ok else None), details


def _mask_check(
    config: PipelineConfig,
    output: Path,
    alpha: np.ndarray,
    process_report: dict[str, Any] | None = None,
    master_rgb: np.ndarray | None = None,
) -> dict[str, Any]:
    masks_dir = output / "masks"
    masks: list[np.ndarray] = []
    details: dict[str, Any] = {}
    failed = False
    report_stats = (process_report or {}).get("mask_stats", {})
    required_outputs: dict[str, Any] = {}
    for palette in config.palette:
        path = masks_dir / f"{palette.name}.png"
        if not path.is_file():
            details[palette.name] = {"status": "failed", "reason": "missing mask"}
            required_outputs[palette.name] = {
                "required": bool(palette.required),
                "mask_present": False,
                "svg_present": (output / "vector" / f"{palette.name}.svg").is_file(),
                "pixels": 0,
                "nonempty": False,
                "status": "failed" if palette.required else "failed",
            }
            failed = True
            continue
        mask, mask_details = _read_binary_mask(path, alpha.shape)
        if mask is None:
            details[palette.name] = mask_details
            required_outputs[palette.name] = {
                "required": bool(palette.required),
                "mask_present": True,
                "svg_present": (output / "vector" / f"{palette.name}.svg").is_file(),
                "pixels": 0,
                "nonempty": False,
                "status": "failed",
            }
            failed = True
            continue
        expected_stats = report_stats.get(palette.name) if isinstance(report_stats, dict) else None
        expected_pixels = expected_stats.get("pixels") if isinstance(expected_stats, dict) else None
        expected_mask_sha = expected_stats.get("mask_sha256") if isinstance(expected_stats, dict) else None
        try:
            report_pixels_match = (
                isinstance(expected_stats, dict)
                and isinstance(expected_pixels, int)
                and expected_pixels == mask_details["pixels"]
            )
        except (TypeError, ValueError, OverflowError):
            report_pixels_match = False
        report_hash_match = (
            isinstance(expected_mask_sha, str)
            and expected_mask_sha == mask_details["sha256"]
        )
        master_color_match = True
        if master_rgb is not None:
            try:
                expected_rgb = np.asarray(palette.rgb, dtype=np.uint8)
                master_color_match = bool(
                    master_rgb.shape[:2] == mask.shape
                    and np.all(master_rgb[mask] == expected_rgb)
                )
            except (TypeError, ValueError):
                master_color_match = False
        mask_details.update(
            {
                "required": bool(palette.required),
                "report_pixels_match": bool(report_pixels_match),
                "report_hash_matches": bool(report_hash_match),
                "master_color_matches": bool(master_color_match),
            }
        )
        if not report_pixels_match or not report_hash_match or not master_color_match:
            mask_details["status"] = "failed"
            if not report_pixels_match or not report_hash_match:
                mask_details["reason"] = "mask differs from process-report mask_stats"
            elif not master_color_match:
                mask_details["reason"] = "mask pixels do not carry the declared palette RGB"
        if mask_details["status"] != "passed":
            failed = True
        masks.append(mask)
        details[palette.name] = mask_details
        svg_path = output / "vector" / f"{palette.name}.svg"
        svg_present = svg_path.is_file() and svg_path.stat().st_size > 0
        nonempty = mask_details["pixels"] > 0
        required_ok = bool(
            (not palette.required)
            or (
                mask_details["status"] == "passed"
                and nonempty
                and svg_present
                and report_pixels_match
                and report_hash_match
                and master_color_match
            )
        )
        required_outputs[palette.name] = {
            "required": bool(palette.required),
            "mask_present": True,
            "svg_present": svg_present,
            "pixels": mask_details["pixels"],
            "nonempty": nonempty,
            "report_pixels_match": bool(report_pixels_match),
            "report_hash_matches": bool(report_hash_match),
            "master_color_matches": bool(master_color_match),
            "status": "passed" if required_ok else "failed" if palette.required else "not_required",
        }
        if not required_ok:
            failed = True
    if not masks:
        return {
            "status": "failed",
            "colors": details,
            "required_outputs": required_outputs,
            "required_palette_outputs": {"status": "failed", "colors": required_outputs},
        }
    stack = np.stack(masks, axis=0)
    overlap = np.max(np.sum(stack, axis=0))
    union = np.any(stack, axis=0)
    alpha_inside = alpha > 0
    partition = bool(overlap <= 1 and np.array_equal(union, alpha_inside))
    required_ok = all(
        item["status"] == "passed"
        for item in required_outputs.values()
        if item["required"]
    )
    return {
        "status": "passed" if partition and not failed and required_ok else "failed",
        "colors": details,
        "max_overlap": int(overlap),
        "union_matches_alpha": partition,
        "required_outputs": required_outputs,
        "required_palette_outputs": {
            "status": "passed" if required_ok else "failed",
            "colors": required_outputs,
        },
    }


def _check_vectors(
    config: PipelineConfig,
    output: Path,
    process_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    vectors = output / "vector"
    details: dict[str, Any] = {}
    failed = False
    expected = f'viewBox="0 0 {config.grid_size} {config.grid_size}"'
    for palette in config.palette:
        path = vectors / f"{palette.name}.svg"
        if not path.is_file():
            details[palette.name] = {"status": "failed", "reason": "missing SVG"}
            failed = True
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            details[palette.name] = {
                "status": "failed",
                "reason": str(exc).replace(str(path), _portable_path(path, output)),
            }
            failed = True
            continue
        dimensions = re.search(r'width="([0-9.eE+-]+)mm"\s+height="([0-9.eE+-]+)mm"', text)
        ok = expected in text and dimensions is not None
        if dimensions is not None:
            try:
                width_mm = float(dimensions.group(1))
                height_mm = float(dimensions.group(2))
            except (TypeError, ValueError, OverflowError):
                ok = False
            else:
                ok = ok and abs(width_mm - config.face_diameter_mm) < 1e-6
                ok = ok and abs(height_mm - config.face_diameter_mm) < 1e-6
        actual_hash = _sha256(path)
        report_stats = (process_report or {}).get("mask_stats", {})
        expected_stats = report_stats.get(palette.name) if isinstance(report_stats, dict) else None
        expected_svg_hash = expected_stats.get("svg_sha256") if isinstance(expected_stats, dict) else None
        report_hash_match = isinstance(expected_svg_hash, str) and expected_svg_hash == actual_hash
        ok = bool(ok and report_hash_match)
        details[palette.name] = {
            "status": "passed" if ok else "failed",
            "sha256": actual_hash,
            "report_hash_matches": bool(report_hash_match),
        }
        if not report_hash_match:
            details[palette.name]["reason"] = "SVG differs from process-report mask_stats"
        failed |= not ok
    return {"status": "failed" if failed else "passed", "colors": details}


def _role_mask_check(
    config: PipelineConfig,
    output: Path,
    alpha: np.ndarray,
) -> dict[str, Any]:
    """Validate semantic role masks against alpha and colour masks.

    ``outside`` is the no-geometry complement of the process-master alpha;
    ``base`` and ``relief`` must exactly equal the unions of palette masks
    carrying those roles.  The optional safe border is allowed to be empty,
    but every positive pixel must be inside the face and base-only.  These
    checks catch the common failure where a renderer imports a role mask with
    an inverted alpha or accidentally turns the transparent margin into
    positive relief.
    """
    shape = tuple(alpha.shape)
    role_files: dict[str, Any] = {}
    role_arrays: dict[str, np.ndarray] = {}
    for name in ("outside", "base", "relief", "safe-border"):
        path = output / f"{name}.png"
        if not path.is_file():
            role_files[name] = {
                "status": "failed",
                "path": _portable_path(path, output),
                "reason": "missing role mask",
            }
            continue
        mask, details = _read_binary_mask(path, shape)
        details["path"] = _portable_path(path, output)
        role_files[name] = details
        if mask is not None:
            role_arrays[name] = mask

    # Re-read palette masks here rather than trusting the summary generated by
    # _mask_check; role semantics must remain independently auditable when a
    # caller invokes this helper in isolation.
    palette_arrays: dict[str, np.ndarray] = {}
    palette_readable = True
    for palette in config.palette:
        path = output / "masks" / f"{palette.name}.png"
        if not path.is_file():
            palette_readable = False
            continue
        mask, _details = _read_binary_mask(path, shape)
        if mask is None:
            palette_readable = False
            continue
        palette_arrays[palette.name] = mask

    alpha_inside = alpha > 0
    outside = role_arrays.get("outside")
    base = role_arrays.get("base")
    relief = role_arrays.get("relief")
    safe_border = role_arrays.get("safe-border")
    expected_base = np.zeros(shape, dtype=bool)
    expected_relief = np.zeros(shape, dtype=bool)
    for palette in config.palette:
        mask = palette_arrays.get(palette.name)
        if mask is None:
            continue
        if palette.role == "base":
            expected_base |= mask
        elif palette.role == "relief":
            expected_relief |= mask

    semantics = {
        "outside_matches_alpha": bool(outside is not None and np.array_equal(outside, ~alpha_inside)),
        "base_matches_palette_union": bool(base is not None and np.array_equal(base, expected_base)),
        "relief_matches_palette_union": bool(relief is not None and np.array_equal(relief, expected_relief)),
        "role_partition": bool(
            outside is not None
            and base is not None
            and relief is not None
            and np.all(
                outside.astype(np.uint8)
                + base.astype(np.uint8)
                + relief.astype(np.uint8)
                == 1
            )
        ),
        "base_relief_disjoint": bool(
            base is not None and relief is not None and not np.any(base & relief)
        ),
        "safe_border_inside": bool(
            safe_border is not None and np.all(~safe_border | alpha_inside)
        ),
        "safe_border_base_only": bool(
            safe_border is not None and base is not None and np.all(~safe_border | base)
        ),
        "safe_border_disjoint_relief": bool(
            safe_border is not None and relief is not None and not np.any(safe_border & relief)
        ),
        "palette_masks_readable": palette_readable,
    }
    file_status = all(item.get("status") == "passed" for item in role_files.values())
    status = "passed" if file_status and all(semantics.values()) else "failed"
    # Keep the historical ``checks.role_masks.<name>`` layout while exposing
    # an explicit ``files`` grouping for new consumers.
    result: dict[str, Any] = {
        "status": status,
        "files": role_files,
        "semantics": semantics,
    }
    result.update(role_files)
    return result


def validate_job(
    config: PipelineConfig,
    *,
    run_external: bool = False,
    strict_external: bool = False,
) -> dict[str, Any]:
    """Validate all artifacts available for a job.

    Core checks are deterministic and required.  External tool checks are
    optional; ``strict_external`` turns an unavailable/failed adapter into a
    failing exit status for release CI.
    """

    output = config.output_dir.resolve()
    report: dict[str, Any] = {
        "schema_version": 1,
        "validate_version": VALIDATE_VERSION,
        "job_slug": config.job_slug,
        "status": "passed",
        "config_sha256": config.digest(),
        "checks": {},
    }
    checks = report["checks"]
    in_progress = output / ".process-in-progress"
    checks["process_transaction"] = {
        "status": "failed" if in_progress.exists() else "passed",
        "path": _portable_path(in_progress, output),
        "reason": "an incomplete process transaction is present" if in_progress.exists() else None,
    }
    if in_progress.exists():
        report["status"] = "failed"
    process_report_path = output / "process-report.json"
    master_path = output / "process-master.png"
    if not process_report_path.is_file() or not master_path.is_file():
        checks["process_artifacts"] = {"status": "failed", "reason": "process-report.json or process-master.png missing"}
        report["status"] = "failed"
        _atomic_json(output / "validation-report.json", report)
        return report
    try:
        parsed_process_report = json.loads(process_report_path.read_text(encoding="utf-8"))
        if not isinstance(parsed_process_report, dict):
            raise ValueError("process-report.json root must be an object")
        process_report = parsed_process_report
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        checks["process_report"] = {"status": "failed", "reason": str(exc)}
        report["status"] = "failed"
        _atomic_json(output / "validation-report.json", report)
        return report
    report_config_hash = process_report.get("config_sha256")
    report_config_hash_matches = isinstance(report_config_hash, str) and report_config_hash == config.digest()
    report_schema_ok = process_report.get("schema_version") == 1
    checks["process_report"] = {
        "status": "passed"
        if process_report.get("status") == "passed" and report_schema_ok and report_config_hash_matches
        else "failed",
        "sha256": _sha256(process_report_path),
        "schema_version_ok": report_schema_ok,
        "config_sha256_present": isinstance(report_config_hash, str),
        "config_sha256_matches": report_config_hash_matches,
    }
    if checks["process_report"]["status"] != "passed":
        report["status"] = "failed"

    try:
        image = Image.open(master_path).convert("RGBA")
        rgba = np.asarray(image, dtype=np.uint8)
        alpha = rgba[:, :, 3]
        dimensions_ok = image.width == config.grid_size and image.height == config.grid_size
        binary_alpha = bool(np.all((alpha == 0) | (alpha == 255)))
        master_hash = _sha256(master_path)
        output_manifest = process_report.get("outputs", {})
        master_entry = output_manifest.get("process_master", {}) if isinstance(output_manifest, dict) else {}
        declared_master_hash = master_entry.get("sha256") if isinstance(master_entry, dict) else None
        master_hash_matches = isinstance(declared_master_hash, str) and declared_master_hash == master_hash
        checks["process_master"] = {
            "status": "passed" if dimensions_ok and binary_alpha and master_hash_matches else "failed",
            "dimensions_px": [image.width, image.height],
            "dimensions_match_config": dimensions_ok,
            "binary_alpha": binary_alpha,
            "sha256": master_hash,
            "declared_sha256": declared_master_hash,
            "declared_sha256_present": isinstance(declared_master_hash, str),
            "hash_matches_report": bool(master_hash_matches),
        }
        if checks["process_master"]["status"] != "passed":
            report["status"] = "failed"
    except (OSError, ValueError) as exc:
        checks["process_master"] = {"status": "failed", "reason": str(exc)}
        report["status"] = "failed"
        _atomic_json(output / "validation-report.json", report)
        return report

    source_lock = output / "source-lock.json"
    source_actual: str | None = None
    source_dimensions: list[int] | None = None
    source_alpha_present: bool | None = None
    source_error: str | None = None
    try:
        source_actual = _sha256(config.source_path)
        with Image.open(config.source_path) as source_image:
            source_dimensions = [int(source_image.width), int(source_image.height)]
            # Match process.py's RGBA conversion so palette images with a
            # tRNS transparency table are audited correctly as well.
            source_rgba = source_image.convert("RGBA")
            source_alpha_present = bool(source_rgba.getchannel("A").getextrema() != (255, 255))
    except (OSError, ValueError) as exc:
        source_error = str(exc).replace(
            str(config.source_path),
            _portable_path(config.source_path, config.config_path.parent),
        )

    # The process report is authoritative for the run, while source-lock.json
    # is the explicit, human-auditable lock file.  Validate both so a copied
    # or hand-edited lock cannot make a substituted source look trusted.
    lock_data: dict[str, Any] = {}
    lock_error: str | None = None
    if source_lock.is_file():
        try:
            parsed_lock = json.loads(source_lock.read_text(encoding="utf-8"))
            if not isinstance(parsed_lock, dict):
                raise ValueError("source-lock.json root must be an object")
            lock_data = parsed_lock
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            lock_error = str(exc)

    lock_path_ok = False
    if isinstance(lock_data.get("path"), str):
        try:
            candidate = Path(lock_data["path"]).expanduser()
            if candidate.is_absolute():
                candidates = [candidate.resolve()]
            else:
                # Older manifests used either the output directory or the
                # config directory as the relative-path base.  Accept both
                # while still requiring an exact resolved match.
                candidates = [
                    (output / candidate).resolve(),
                    (config.config_path.parent / candidate).resolve(),
                ]
            lock_path_ok = any(item == config.source_path.resolve() for item in candidates)
        except (OSError, RuntimeError, ValueError):
            lock_path_ok = False
    lock_dimensions_ok = lock_data.get("dimensions_px") == source_dimensions
    lock_alpha_ok = lock_data.get("alpha_mask_present") == source_alpha_present
    lock_ok = bool(
        source_actual is not None
        and not lock_error
        and lock_data.get("sha256") == source_actual
        and lock_path_ok
        and lock_dimensions_ok
        and lock_alpha_ok
    )
    process_source = _object(process_report.get("source"))
    source_ok = bool(source_actual is not None and source_actual == process_source.get("sha256"))
    if config.source_sha256 is not None:
        source_ok = source_ok and source_actual == config.source_sha256
    source_ok = source_ok and lock_ok
    checks["source_lock"] = {
        "status": "passed" if source_ok else "failed",
        "actual_sha256": source_actual,
        "report_sha256": process_source.get("sha256"),
        "lock_file_present": source_lock.is_file(),
        "lock_sha256_matches": bool(lock_data.get("sha256") == source_actual and source_actual is not None),
        "lock_path_matches": lock_path_ok,
        "lock_dimensions_matches": lock_dimensions_ok,
        "lock_alpha_matches": lock_alpha_ok,
        "error": source_error or lock_error,
    }
    if not source_ok:
        report["status"] = "failed"

    manifest_path = output / "run-manifest.json"
    manifest_ok = False
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if not isinstance(manifest, dict):
                raise ValueError("run-manifest.json root must be an object")
            manifest_source = _object(manifest.get("source"))
            manifest_ok = (
                manifest.get("schema_version") == 1
                and manifest.get("status") == "passed"
                and manifest.get("job_slug") == config.job_slug
                and manifest.get("config_sha256") == config.digest()
                and manifest_source.get("sha256") == source_actual
                and manifest.get("process_report_sha256") == _sha256(process_report_path)
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            manifest_ok = False
    checks["run_manifest"] = {
        "status": "passed" if manifest_ok else "failed",
        "path": _portable_path(manifest_path, output),
    }
    if not manifest_ok:
        report["status"] = "failed"

    checks["masks"] = _mask_check(config, output, alpha, process_report, rgba[:, :, :3])
    checks["vectors"] = _check_vectors(config, output, process_report)
    if checks["masks"]["status"] != "passed" or checks["vectors"]["status"] != "passed":
        report["status"] = "failed"
    # Keep the compact required-palette gate addressable at the top level for
    # CI clients, while retaining the detailed per-colour diagnostics under
    # ``masks`` for older consumers.
    checks["required_palette_outputs"] = checks["masks"].get(
        "required_palette_outputs",
        {"status": "failed", "colors": {}},
    )
    # A required palette is only complete when both its raster mask and its
    # vector handoff validate.  Fold the vector result into the compact gate
    # while preserving the detailed diagnostics under ``masks`` and
    # ``vectors``.
    required_colors = checks["required_palette_outputs"].setdefault("colors", {})
    for palette in config.palette:
        item = required_colors.get(palette.name)
        vector_item = checks["vectors"].get("colors", {}).get(palette.name, {})
        if item is None:
            item = {
                "required": bool(palette.required),
                "mask_present": False,
                "svg_present": False,
                "pixels": 0,
                "nonempty": False,
                "status": "failed" if palette.required else "not_required",
            }
            required_colors[palette.name] = item
        item["svg_valid"] = vector_item.get("status") == "passed"
        if palette.required and (
            item.get("status") != "passed" or item.get("svg_valid") is not True
        ):
            item["status"] = "failed"
    required_gate_ok = all(
        item.get("status") == "passed"
        for item in required_colors.values()
        if item.get("required")
    )
    checks["required_palette_outputs"]["status"] = "passed" if required_gate_ok else "failed"
    if checks["required_palette_outputs"]["status"] != "passed":
        report["status"] = "failed"

    checks["role_masks"] = _role_mask_check(config, output, alpha)
    if checks["role_masks"]["status"] != "passed":
        report["status"] = "failed"

    geometry_path = output / "model" / "geometry-report.json"
    if geometry_path.is_file():
        try:
            geometry = json.loads(geometry_path.read_text(encoding="utf-8"))
            if not isinstance(geometry, dict):
                raise ValueError("geometry-report.json root must be an object")
            expected_cavity = None
            if config.measured_diameter_mm is not None:
                expected_cavity = config.cavity_diameter_mm
            geometry_mechanical = _object(geometry.get("mechanical"))
            actual_cavity = geometry_mechanical.get("cavity_diameter_mm")
            cavity_ok = expected_cavity is None or (
                actual_cavity is not None and abs(float(actual_cavity) - expected_cavity) < 1e-6
            )
            # The geometry report is also the audit record for retention
            # geometry.  Compare every current FitSpec value, not only the
            # cavity diameter, so a report cannot silently claim a smooth wall
            # while the SCAD still contains ribs (or vice versa).
            expected_mechanical: dict[str, Any] = {}
            if config.measured_diameter_mm is not None:
                expected_mechanical = {
                    "measured_diameter_mm": config.measured_diameter_mm,
                    "foam_liner_status": config.fit.foam_liner_status,
                    "liner_thickness_mm": config.fit.liner_thickness_mm,
                    "compression_fraction": config.fit.compression_fraction,
                    "bare_clearance_mm": config.fit.bare_clearance_mm,
                    "cavity_diameter_mm": config.cavity_diameter_mm,
                    "wall_thickness_mm": config.fit.wall_thickness_mm,
                    "bottom_thickness_mm": config.fit.bottom_thickness_mm,
                    "side_height_mm": config.fit.side_height_mm,
                    "friction_rib_profile": config.fit.friction_rib_profile,
                    "friction_ribs_enabled": config.fit.friction_ribs_enabled,
                    "friction_ribs_explicit": config.fit.friction_ribs_explicit,
                    "friction_rib_count": config.fit.friction_rib_count,
                    "friction_rib_protrusion_mm": config.fit.friction_rib_protrusion_mm,
                    "friction_rib_width_mm": config.fit.friction_rib_width_mm,
                    "friction_rib_height_mm": config.fit.friction_rib_height_mm,
                    "friction_rib_start_mm": config.fit.friction_rib_start_mm,
                    "friction_rib_wall_overlap_mm": min(
                        0.60, config.fit.wall_thickness_mm * 0.5
                    ),
                    "friction_rib_angle_deg": (
                        min(
                            8.0,
                            360.0
                            * config.fit.friction_rib_width_mm
                            / (math.pi * config.cavity_diameter_mm),
                            180.0 / config.fit.friction_rib_count,
                        )
                        if config.fit.friction_ribs_enabled
                        else 0.0
                    ),
                    "friction_rib_tip_angle_deg": (
                        min(
                            8.0,
                            360.0
                            * config.fit.friction_rib_width_mm
                            / (math.pi * config.cavity_diameter_mm),
                            180.0 / config.fit.friction_rib_count,
                        )
                        * 0.55
                        if config.fit.friction_ribs_enabled
                        else 0.0
                    ),
                    "friction_rib_tip_diameter_mm": (
                        config.cavity_diameter_mm
                        - 2.0 * config.fit.friction_rib_protrusion_mm
                        if config.fit.friction_ribs_enabled
                        else None
                    ),
                    "friction_rib_bare_interference_mm": (
                        config.measured_diameter_mm
                        - (
                            config.cavity_diameter_mm
                            - 2.0 * config.fit.friction_rib_protrusion_mm
                        )
                        if config.fit.friction_ribs_enabled
                        and config.fit.foam_liner_status == "none"
                        else None
                    ),
                    "foam_local_compression_fraction": (
                        config.fit.compression_fraction
                        + config.fit.friction_rib_protrusion_mm / config.fit.liner_thickness_mm
                        if config.fit.friction_ribs_enabled
                        and config.fit.foam_liner_status == "foam"
                        and config.fit.liner_thickness_mm
                        else config.fit.compression_fraction
                        if config.fit.foam_liner_status == "foam"
                        else None
                    ),
                }
            mechanical_matches: dict[str, bool] = {}
            for key, expected in expected_mechanical.items():
                actual = geometry_mechanical.get(key)
                if isinstance(expected, (int, float)) and not isinstance(expected, bool):
                    try:
                        mechanical_matches[key] = actual is not None and abs(float(actual) - expected) < 1e-6
                    except (TypeError, ValueError):
                        mechanical_matches[key] = False
                else:
                    mechanical_matches[key] = actual == expected
            mechanical_ok = all(mechanical_matches.values()) if mechanical_matches else True
            raw_scad_path = geometry.get("scad_path")
            if isinstance(raw_scad_path, str) and raw_scad_path:
                scad_path = Path(raw_scad_path).expanduser()
                if not scad_path.is_absolute():
                    scad_path = (geometry_path.parent / scad_path).resolve()
            else:
                scad_path = geometry_path.parent / f"{config.job_slug}.scad"
            actual_scad_hash = _sha256(scad_path) if scad_path.is_file() else None
            declared_scad_hash = geometry.get("scad_sha256")
            scad_hash_ok = bool(actual_scad_hash and declared_scad_hash == actual_scad_hash)
            process_hash = _sha256(process_report_path)
            process_link_ok = geometry.get("process_report_sha256") == process_hash
            declared_face = geometry.get("face_target_mm")
            face_ok = isinstance(declared_face, (int, float)) and abs(
                float(declared_face) - config.face_diameter_mm
            ) < 1e-6
            selectors = geometry.get("render_part_selectors")
            selectors_ok = isinstance(selectors, list) and {
                "assembly",
                "base",
                "cap_body",
                "face_base",
                "fit_ring",
            }.issubset(set(selectors))
            geometry_ok = bool(
                cavity_ok
                and mechanical_ok
                and geometry.get("status") == "passed"
                and scad_hash_ok
                and process_link_ok
                and face_ok
                and selectors_ok
            )
            checks["geometry"] = {
                "status": "passed" if geometry_ok else "failed",
                "cavity_diameter_mm": actual_cavity,
                "expected_cavity_diameter_mm": expected_cavity,
                "mechanical_parameters_match": mechanical_ok,
                "mechanical_parameter_checks": mechanical_matches,
                "scad_path": _portable_path(scad_path, output),
                "scad_sha256": actual_scad_hash,
                "declared_scad_sha256": declared_scad_hash,
                "scad_hash_matches": scad_hash_ok,
                "process_report_link_matches": process_link_ok,
                "face_target_matches": face_ok,
                "face_target_declared": isinstance(declared_face, (int, float)),
                "selectors_complete": selectors_ok,
            }
            if checks["geometry"]["status"] != "passed":
                report["status"] = "failed"
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            checks["geometry"] = {"status": "failed", "reason": str(exc)}
            report["status"] = "failed"
    else:
        checks["geometry"] = {"status": "unverifiable", "reason": "model has not been generated"}

    model_manifest_path = output / "model" / "model-manifest.json"
    model_manifest_ok = False
    if model_manifest_path.is_file() and geometry_path.is_file():
        try:
            model_manifest = json.loads(model_manifest_path.read_text(encoding="utf-8"))
            if not isinstance(model_manifest, dict):
                raise ValueError("model-manifest.json root must be an object")
            scad_path = output / "model" / f"{config.job_slug}.scad"
            model_manifest_ok = (
                model_manifest.get("schema_version") == 1
                and model_manifest.get("status") == "passed"
                and model_manifest.get("job_slug") == config.job_slug
                and model_manifest.get("config_sha256") == config.digest()
                and model_manifest.get("process_report_sha256") == _sha256(process_report_path)
                and model_manifest.get("geometry_report_sha256") == _sha256(geometry_path)
                and model_manifest.get("scad_sha256") == (_sha256(scad_path) if scad_path.is_file() else None)
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            model_manifest_ok = False
    checks["model_manifest"] = {
        "status": "passed" if model_manifest_ok else "unverifiable" if not geometry_path.is_file() else "failed",
        "path": _portable_path(model_manifest_path, output),
    }
    if geometry_path.is_file() and not model_manifest_ok:
        report["status"] = "failed"

    checks["physical_fit"] = {
        "status": "passed" if bool(config.metadata.get("fit_coupon_verified", False)) else "unverifiable",
        "note": "A printed coupon and the actual lens/liner are required to prove fit.",
    }

    if run_external:
        scad_path = output / "model" / f"{config.job_slug}.scad"
        if scad_path.is_file():
            checks["openscad"] = _check_openscad(config, scad_path)
        else:
            checks["openscad"] = {"status": "unverifiable", "reason": "SCAD missing"}
        if strict_external and checks["openscad"]["status"] != "passed":
            report["status"] = "failed"
    else:
        checks["openscad"] = {"status": "not_requested"}

    _atomic_json(output / "validation-report.json", report)
    return report


# Backwards-compatible short name for downstream scripts.
validate = validate_job
