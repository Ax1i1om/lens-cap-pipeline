"""Optional, explicitly-audited adapters for OpenSCAD and Bambu Studio.

The deterministic core stops at a parameterised SCAD file and SVG masks.  A
renderer or slicer is an external stateful program, so this module treats it
as an adapter: commands are passed as argument lists, versions and hashes are
recorded, and a missing executable is reported as ``unverifiable`` rather
than being mistaken for success.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import struct
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .config import PipelineConfig
from .model import ModelResult

# OpenSCAD's CGAL backend can silently omit imported PolySet reliefs when an
# assembly combines the cup (a Nef polyhedron) with separate SVG extrusions.
# The Manifold backend preserves those disconnected, colour-layer solids and
# is therefore the required backend for the integrated assembly export.
OPENSCAD_BACKEND = "Manifold"


class ExternalToolError(RuntimeError):
    """Raised for an explicitly requested external export that failed."""


@dataclass(frozen=True)
class ExternalResult:
    report: dict[str, Any]
    report_path: Path


def _portable_path(path: Path, root: Path) -> str:
    """Return a clone-relative artifact path when it is inside ``root``."""
    resolved = path.expanduser().resolve()
    try:
        return resolved.relative_to(root.expanduser().resolve()).as_posix()
    except ValueError:
        return str(resolved)


def _stable_tool_text(text: str, *, root: Path, model: Path, temporary: Path | None = None) -> str:
    """Scrub checkout/temp paths and volatile OpenSCAD timing/cache lines."""
    if not text:
        return ""
    replacements = {
        str(root.expanduser().resolve()): "<job>",
        str(model.expanduser().resolve()): "<model>",
    }
    if temporary is not None:
        replacements[str(temporary.expanduser().resolve())] = "<temporary>"
    normalized = text
    for original, replacement in replacements.items():
        normalized = normalized.replace(original, replacement)
    lines = []
    for line in normalized.splitlines():
        lower = line.lower()
        if "cache" in lower or "total rendering time" in lower:
            continue
        lines.append(line)
    return "\n".join(lines[-40:])


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(payload.encode("utf-8"))
    try:
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _atomic_bytes(path: Path, payload: bytes) -> None:
    """Replace a binary artifact without exposing a partial write."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
        handle.write(payload)
    try:
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _resolve_tool(
    configured: str | None,
    names: Iterable[str],
    *,
    base_dir: Path | None = None,
) -> str | None:
    if configured:
        raw = str(configured).strip()
        if raw:
            candidate = Path(raw).expanduser()
            path_like = (
                candidate.is_absolute()
                or raw.startswith((".", "~"))
                or "/" in raw
                or "\\" in raw
                or bool(candidate.suffix)
            )
            if path_like:
                if not candidate.is_absolute() and base_dir is not None:
                    # Tool paths in a portable job file are interpreted
                    # relative to that file, just like source_art/output_dir.
                    # This keeps a CLI invocation independent of cwd.
                    candidate = (base_dir / candidate).resolve()
                if candidate.is_file() and candidate.stat().st_mode & 0o111:
                    return str(candidate.resolve())
                # On Windows an executable may not expose the POSIX execute
                # bit.  A path-like value is still authoritative when it is a
                # regular file.
                if candidate.is_file():
                    return str(candidate.resolve())
                # An explicit path typo must not silently select another
                # desktop installation.
                return None
            # A bare command name is intentionally resolved through PATH.  It
            # remains authoritative too: if it is absent, do not fall back to
            # a different candidate or app bundle.
            found = shutil.which(raw)
            return found if found else None
    # Desktop bundles are not normally on PATH on macOS.  Keep these as
    # explicit fallback candidates so a clean clone can discover the same
    # optional tools that the standalone 3MF adapter supports, while still
    # allowing a job's configured executable to take precedence.
    # Materialise once: callers normally pass tuples, but accepting a generic
    # iterable should not consume a generator while deciding which desktop
    # bundle fallback to append.
    fallback_names = list(names)
    lowered_names = [str(name).lower() for name in fallback_names]
    if any("bambu" in name for name in lowered_names):
        fallback_names.append("/Applications/BambuStudio.app/Contents/MacOS/BambuStudio")
    if any("openscad" in name for name in lowered_names):
        fallback_names.append("/Applications/OpenSCAD.app/Contents/MacOS/OpenSCAD")
    for name in fallback_names:
        found = shutil.which(name)
        if found:
            return found
    return None


def _version(
    executable: str,
    *,
    root: Path | None = None,
    model: Path | None = None,
) -> dict[str, Any]:
    try:
        result = subprocess.run(
            [executable, "--version"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        # Keep diagnostics useful without embedding a configured checkout
        # path (for example ``/opt/tools/BambuStudio``) in a portable report.
        return {
            "status": "unverifiable",
            "executable": Path(executable).name,
            "error": f"{Path(executable).name}: {type(exc).__name__}",
        }
    raw_stdout = result.stdout or ""
    raw_stderr = result.stderr or ""
    # Detect a vendor banner before truncating diagnostic text.  Bambu prints
    # its banner near the beginning and can emit a long help page afterwards.
    banner = re.search(
        r"(?:BambuStudio|bambu-studio)[-_]?\d[\w.:-]*",
        raw_stdout + "\n" + raw_stderr,
        re.IGNORECASE,
    )
    stdout = raw_stdout[-1000:]
    stderr = raw_stderr[-1000:]
    if root is not None and model is not None:
        stdout = _stable_tool_text(stdout, root=root, model=model)
        stderr = _stable_tool_text(stderr, root=root, model=model)
    # Bambu Studio releases commonly reject ``--version`` with return code
    # 254 while still printing a precise ``BambuStudio-02.xx`` banner.  A
    # recognizable banner is sufficient tool identity; otherwise a non-zero
    # probe remains UNVERIFIABLE.  This prevents a usable slicer from being
    # misclassified merely because its CLI has no dedicated version flag.
    version_ok = result.returncode == 0 or banner is not None
    return {
        "status": "passed" if version_ok else "unverifiable",
        "returncode": result.returncode,
        "stdout": stdout,
        "stderr": stderr,
    }


def _stl_is_nonempty_manifold_candidate(path: Path) -> tuple[bool, dict[str, Any]]:
    """Perform cheap, format-level checks without pretending to prove CAD.

    OpenSCAD is the authority for geometry validity; this check catches a
    truncated/empty export and records the binary triangle count where
    possible.  Full manifold and projection audits remain separate stages.
    """

    try:
        size = path.stat().st_size
        if size < 84:
            return False, {"size_bytes": size, "reason": "STL is shorter than a binary header"}
        with path.open("rb") as handle:
            header = handle.read(80)
            raw_count = handle.read(4)
        count = int.from_bytes(raw_count, "little", signed=False)
        expected = 84 + count * 50
        exact = expected == size
        return bool(size > 84 and count > 0 and exact), {
            "size_bytes": size,
            "binary_triangle_count": count,
            "expected_size_bytes": expected,
            "size_exact": exact,
            "header": header[:32].decode("ascii", errors="replace"),
        }
    except OSError as exc:
        return False, {"reason": str(exc)}


def _stl_z_bounds(path: Path) -> tuple[float, float] | None:
    """Return vertex Z bounds for a binary STL, or ``None`` if unreadable."""
    try:
        data = path.read_bytes()
        if len(data) < 84:
            return None
        count = int.from_bytes(data[80:84], "little", signed=False)
        if count <= 0 or 84 + count * 50 != len(data):
            return None
        minimum = float("inf")
        maximum = float("-inf")
        for offset in range(84, len(data), 50):
            # Each binary STL record is normal (12 bytes), three vertices
            # (36 bytes), and a two-byte attribute field.  Vertex Z values are
            # positions 5, 8, and 11 in the twelve-float tuple.
            values = struct.unpack_from("<12f", data, offset)
            minimum = min(minimum, values[5], values[8], values[11])
            maximum = max(maximum, values[5], values[8], values[11])
        return (minimum, maximum)
    except (OSError, struct.error, ValueError):
        return None


def _canonicalize_binary_stl(path: Path) -> tuple[bool, dict[str, Any]]:
    """Normalize binary STL triangle order for cross-clone reproducibility.

    OpenSCAD can emit the same triangle multiset in a different order
    when run from separate checkouts.  Sorting complete 50-byte triangle
    records preserves geometry, normals, and attribute bytes while producing
    a stable artifact hash for manifests and handoffs.  The STL header is
    retained verbatim; no CAD semantics are inferred by this adapter.
    """
    try:
        data = path.read_bytes()
        if len(data) < 84:
            return False, {"status": "failed", "reason": "STL is shorter than a binary header"}
        count = int.from_bytes(data[80:84], "little", signed=False)
        expected_size = 84 + count * 50
        if count <= 0 or expected_size != len(data):
            return False, {
                "status": "failed",
                "reason": "binary STL triangle count does not match file size",
                "binary_triangle_count": count,
                "size_bytes": len(data),
                "expected_size_bytes": expected_size,
            }
        records = [data[offset : offset + 50] for offset in range(84, len(data), 50)]
        canonical = data[:84] + b"".join(sorted(records))
        changed = canonical != data
        if changed:
            _atomic_bytes(path, canonical)
        return True, {
            "status": "passed",
            "binary_triangle_count": count,
            "triangle_order_canonicalized": changed,
        }
    except OSError as exc:
        return False, {"status": "failed", "reason": str(exc)}


def export_openscad(
    config: PipelineConfig,
    model: ModelResult,
    *,
    parts: Iterable[str] | None = None,
    force: bool = False,
    timeout_s: int = 300,
) -> ExternalResult:
    """Export selected SCAD selectors to STL using a safe subprocess list."""

    model_report = model.report if isinstance(model.report, dict) else {}

    executable = _resolve_tool(
        config.print.openscad_executable,
        ("openscad", "openscad.com"),
        base_dir=config.config_path.parent,
    )
    report_path = model.model_dir / "external-openscad-report.json"
    model_root = model.model_dir.resolve()
    model_path = model.scad_path.resolve()
    report: dict[str, Any] = {
        "schema_version": 1,
        "adapter": "openscad",
        "status": "unverifiable",
        "production_status": model_report.get(
            "production_status", model_report.get("status")
        ),
        "backend": OPENSCAD_BACKEND,
        # Keep the legacy ``model`` key, but make its value relative to the
        # model directory.  An absolute checkout path would make this audit
        # manifest differ after cloning the same job on another machine.
        "model": _portable_path(model_path, model_root),
        "model_sha256": _sha256(model_path),
        "parts": {},
    }
    if executable is None:
        report["reason"] = "OpenSCAD executable not found"
        report["tool_status"] = "unverifiable"
        _atomic_json(report_path, report)
        return ExternalResult(report, report_path)

    if parts is None:
        # Do not ask OpenSCAD to export an empty optional colour.  Empty
        # selectors are a valid palette declaration (for example a job with
        # no red pixels), not a geometry failure.
        nonempty: set[str] = set()
        process_report_path = config.output_dir / "process-report.json"
        if process_report_path.is_file():
            try:
                process_report = json.loads(process_report_path.read_text(encoding="utf-8"))
                if not isinstance(process_report, dict):
                    raise ValueError("process-report.json root must be an object")
                nonempty = {
                    str(name)
                    for name, stats in (process_report.get("mask_stats", {}) or {}).items()
                    if isinstance(stats, dict) and int(stats.get("pixels", 0)) > 0
                }
            except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
                raise ExternalToolError(
                    f"cannot read current process report {process_report_path}: {exc}"
                ) from exc
        else:
            nonempty = {p.name for p in config.relief}
        selectors = [
            "assembly",
            "base",
            *[f"{p.name}_relief" for p in config.relief if p.name in nonempty],
            "fit_ring",
        ]
    else:
        # The public API accepts any iterable.  Canonicalise it here so a
        # caller passing a set (or duplicate command-line values) cannot make
        # report key order or export side effects depend on hash iteration.
        selectors = sorted({str(item) for item in parts})
    mesh_dir = model.model_dir / "mesh"
    mesh_dir.mkdir(parents=True, exist_ok=True)
    report["executable"] = Path(executable).name
    version = _version(
        executable,
        root=config.config_path.parent.resolve(),
        model=model_path,
    )
    report["version"] = version
    # A renderer can still export successfully when its ``--version`` flag
    # is unsupported, so keep tool identity separate from per-part geometry.
    # This prevents a dubious version probe from becoming a false PASS while
    # preserving a usable STL when the export itself is verified.
    report["tool_status"] = "passed" if version.get("status") == "passed" else "unverifiable"
    allowed = set(model_report.get("render_part_selectors", ()))
    if not allowed:
        allowed = {"assembly", "base", "cap_body", "face_base", "fit_ring"}
        allowed.update(f"{p.name}_relief" for p in config.relief)
    all_passed = True
    for selector in selectors:
        selector = str(selector)
        safe_selector = "".join(ch if ch.isalnum() or ch in "_.-" else "_" for ch in selector)
        if selector not in allowed:
            all_passed = False
            report["parts"][selector] = {
                "status": "failed",
                "reason": "unknown render_part selector",
                "allowed_selectors": sorted(allowed),
            }
            continue
        output = mesh_dir / f"{config.job_slug}-{safe_selector}.stl"
        if output.exists() and not force:
            all_passed = False
            report["parts"][selector] = {
                "status": "failed",
                "reason": "output exists; pass --force",
                "path": _portable_path(output, model_root),
            }
            continue
        command = [
            executable,
            "--backend",
            OPENSCAD_BACKEND,
            "--export-format",
            "binstl",
            "-D",
            f'render_part="{selector}"',
            "-o",
            str(output),
            str(model.scad_path),
        ]
        report_command = [
            Path(executable).name,
            "--backend",
            OPENSCAD_BACKEND,
            "--export-format",
            "binstl",
            "-D",
            f'render_part="{selector}"',
            "-o",
            _portable_path(output, model_root),
            _portable_path(model_path, model_root),
        ]
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout_s,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            all_passed = False
            report["parts"][selector] = {
                "status": "failed",
                "command": report_command,
                "error": _stable_tool_text(str(exc), root=model_root, model=model_path, temporary=output.parent),
                "path": _portable_path(output, model_root),
            }
            continue
        if output.is_file():
            canonical_ok, canonical_details = _canonicalize_binary_stl(output)
            valid, details = _stl_is_nonempty_manifold_candidate(output)
            details["canonicalization"] = canonical_details
            valid = bool(valid and canonical_ok)
            # A CGAL-style export can report success while dropping the
            # imported relief PolySets from the integrated assembly.  The
            # Manifold backend is the normal remedy; keep this invariant as a
            # second line of defence if a different backend is introduced.
            if selector == "assembly" and nonempty:
                z_bounds = _stl_z_bounds(output)
                expected_face_top = float(
                    getattr(model, "report", {}).get("mechanical", {}).get("total_height_mm", 0.0)
                )
                relief_heights = [float(p.height_mm) for p in config.relief if p.name in nonempty]
                required_top = expected_face_top + (min(relief_heights) if relief_heights else 0.0)
                if z_bounds is None or z_bounds[1] < required_top - 1e-3:
                    valid = False
                    details["assembly_relief_check"] = {
                        "status": "failed",
                        "reason": "assembly STL does not contain the declared relief height",
                        "z_bounds": list(z_bounds) if z_bounds is not None else None,
                        "required_top_mm": required_top,
                    }
                else:
                    details["assembly_relief_check"] = {
                        "status": "passed",
                        "z_bounds": list(z_bounds),
                        "required_top_mm": required_top,
                    }
        else:
            valid, details = False, {"reason": "output missing"}
        status = "passed" if result.returncode == 0 and valid else "failed"
        if status != "passed":
            all_passed = False
        report["parts"][selector] = {
            "status": status,
            "returncode": result.returncode,
            "command": report_command,
            "stdout_tail": _stable_tool_text(result.stdout, root=model_root, model=model_path, temporary=output.parent),
            "stderr_tail": _stable_tool_text(result.stderr, root=model_root, model=model_path, temporary=output.parent),
            "path": _portable_path(output, model_root),
            "sha256": _sha256(output) if output.is_file() else None,
            "format_check": details,
        }
    report["status"] = "passed" if all_passed else "failed"
    _atomic_json(report_path, report)
    if report["status"] == "failed":
        raise ExternalToolError(f"OpenSCAD export failed; inspect {report_path}")
    return ExternalResult(report, report_path)


def write_bambu_handoff(config: PipelineConfig, model: ModelResult) -> ExternalResult:
    """Write a portable Bambu handoff manifest without guessing CLI syntax.

    Bambu Studio's project metadata and command-line flags vary by release.
    The handoff file is intentionally version-neutral: it lists the exact
    SCAD/STL inputs, palette slots, printer/nozzle hints, and required human
    preview checks.  A future adapter can consume this contract without
    changing the deterministic stages.
    """

    path = model.model_dir / "bambu-handoff.json"
    model_report = model.report if isinstance(model.report, dict) else {}
    production_status = model_report.get("production_status", model_report.get("status"))
    executable = _resolve_tool(
        config.print.bambu_executable,
        ("BambuStudio", "bambu-studio"),
        base_dir=config.config_path.parent,
    )
    mesh_dir = model.model_dir / "mesh"
    model_root = model.model_dir.resolve()
    # Treat only regular files as printable STL inputs.  A partially failed
    # export (or a user-created directory ending in ``.stl``) must not make
    # handoff generation crash while hashing it; non-files are retained in
    # the ignored diagnostics below.
    all_mesh_entries = sorted(mesh_dir.glob(f"{config.job_slug}-*.stl"), key=lambda item: item.name)
    all_stl_paths = [item for item in all_mesh_entries if item.is_file()]
    nonfile_stl_paths = [item for item in all_mesh_entries if not item.is_file()]

    def selector_for(stl: Path) -> str:
        prefix = f"{config.job_slug}-"
        stem = stl.stem
        return stem[len(prefix) :] if stem.startswith(prefix) else stem

    # A force rebuild can leave an older selector's STL in ``mesh``.  When a
    # current geometry report declares selectors, only those names belong in
    # this handoff; unknown leftovers are listed explicitly instead of being
    # silently presented as printable inputs.  Legacy/manual models without
    # selector metadata retain the historical include-all behaviour.
    declared_selectors = {
        str(item)
        for item in model_report.get("render_part_selectors", ())
        if isinstance(item, str) and item
    }
    if declared_selectors:
        candidate_stl_paths = [item for item in all_stl_paths if selector_for(item) in declared_selectors]
        ignored_stl_paths = [item for item in all_stl_paths if selector_for(item) not in declared_selectors]
    else:
        candidate_stl_paths = all_stl_paths
        ignored_stl_paths = []

    # A mesh filename alone does not prove that it was generated from the
    # current SCAD.  A forced model rebuild intentionally leaves old STL files
    # in place for recovery; do not hand those stale bytes to a slicer by
    # accident.  When an OpenSCAD report is present, accept only regular files
    # whose model hash, selector, path and output hash all agree.  If no report
    # exists, retain the legacy/manual-STL behaviour but mark its provenance as
    # unverified in the handoff.
    external_report_path = model.model_dir / "external-openscad-report.json"
    current_model_hash = _sha256(model.scad_path)
    external_report: dict[str, Any] | None = None
    external_report_error: str | None = None
    external_model_mismatch = False
    provenance_status = "unverifiable"
    provenance_reason = "no external-openscad-report; STL provenance was not verified"
    stl_paths = list(candidate_stl_paths)
    if external_report_path.is_file():
        try:
            parsed_external = json.loads(external_report_path.read_text(encoding="utf-8"))
            if not isinstance(parsed_external, dict):
                raise ValueError("external-openscad-report.json root must be an object")
            external_report = parsed_external
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            external_report_error = str(exc)
            stl_paths = []
            ignored_stl_paths.extend(candidate_stl_paths)
            provenance_status = "failed"
            provenance_reason = "external-openscad-report could not be read"
        else:
            reported_model_hash = external_report.get("model_sha256")
            parts_report = external_report.get("parts")
            reported_hash_matches = (
                isinstance(reported_model_hash, str)
                and reported_model_hash.lower() == current_model_hash.lower()
            )
            if not reported_hash_matches or not isinstance(parts_report, dict):
                stl_paths = []
                ignored_stl_paths.extend(candidate_stl_paths)
                external_model_mismatch = True
                provenance_status = "failed"
                provenance_reason = "external-openscad-report belongs to a different model or is incomplete"
            else:
                verified_paths: list[Path] = []
                stale_details: dict[Path, str] = {}
                for stl in candidate_stl_paths:
                    selector = selector_for(stl)
                    part = parts_report.get(selector)
                    if not isinstance(part, dict) or part.get("status") != "passed":
                        stale_details[stl] = "selector has no passed entry in external-openscad-report"
                        continue
                    declared_path = part.get("path")
                    declared_hash = part.get("sha256")
                    if not isinstance(declared_path, str) or not isinstance(declared_hash, str):
                        stale_details[stl] = "external-openscad-report entry lacks path or hash"
                        continue
                    candidate_declared_path = Path(declared_path).expanduser()
                    if not candidate_declared_path.is_absolute():
                        candidate_declared_path = model_root / candidate_declared_path
                    try:
                        path_matches = candidate_declared_path.resolve() == stl.resolve()
                    except OSError:
                        path_matches = False
                    actual_hash = _sha256(stl)
                    if not path_matches or actual_hash.lower() != declared_hash.lower():
                        stale_details[stl] = "STL path or hash differs from external-openscad-report"
                        continue
                    verified_paths.append(stl)
                stl_paths = verified_paths
                for stale_path, reason in stale_details.items():
                    ignored_stl_paths.append(stale_path)
                    # The detailed reason is attached below after the normal
                    # selector filtering list has been converted to entries.
                if not candidate_stl_paths:
                    provenance_status = "unverifiable"
                    provenance_reason = "external-openscad-report is present but no STL inputs were found"
                elif len(verified_paths) == len(candidate_stl_paths):
                    provenance_status = "passed"
                    provenance_reason = "all STL inputs match the current OpenSCAD report"
                else:
                    provenance_status = "unverifiable"
                    provenance_reason = "some STL inputs lack a matching passed OpenSCAD report entry"
    # Keep per-file reasons for the report; selector mismatches retain their
    # existing explanation while provenance failures get a more actionable one.
    stale_reason_by_path: dict[Path, str] = {}
    if external_report is not None and isinstance(external_report.get("parts"), dict):
        parts_report = external_report["parts"]
        for stl in ignored_stl_paths:
            selector = selector_for(stl)
            if selector not in declared_selectors and declared_selectors:
                continue
            if external_model_mismatch:
                stale_reason_by_path[stl] = provenance_reason
                continue
            part = parts_report.get(selector)
            if not isinstance(part, dict) or part.get("status") != "passed":
                stale_reason_by_path[stl] = "selector has no passed entry in external-openscad-report"
            else:
                stale_reason_by_path[stl] = "STL path or hash differs from external-openscad-report"
    if external_report_error:
        for stl in ignored_stl_paths:
            if declared_selectors and selector_for(stl) not in declared_selectors:
                continue
            stale_reason_by_path[stl] = "external-openscad-report could not be read"

    def stl_entry(stl: Path, kind: str) -> dict[str, Any]:
        return {
            # ``path`` is retained for compatibility, but is now relative to
            # the handoff/model directory so the manifest survives cloning.
            "path": _portable_path(stl, model_root),
            "selector": selector_for(stl),
            "kind": kind,
            "sha256": _sha256(stl),
        }

    coupon_paths = [
        stl
        for stl in stl_paths
        if selector_for(stl).lower() in {"fit_ring", "fit-ring", "coupon"}
        or "coupon" in selector_for(stl).lower()
    ]
    assembly_paths = [stl for stl in stl_paths if stl not in coupon_paths and selector_for(stl).lower() == "assembly"]
    component_paths = [stl for stl in stl_paths if stl not in coupon_paths and stl not in assembly_paths]
    stl_inputs = [
        *[stl_entry(stl, "assembly") for stl in assembly_paths],
        *[stl_entry(stl, "component") for stl in component_paths],
        *[stl_entry(stl, "coupon") for stl in coupon_paths],
    ]
    ignored_stl_inputs = []
    for stl in ignored_stl_paths:
        selector = selector_for(stl)
        if declared_selectors and selector not in declared_selectors:
            reason = "selector is not declared by the current geometry report"
        else:
            reason = stale_reason_by_path.get(stl, "STL failed current-model provenance checks")
        ignored_stl_inputs.append(
            {
                "path": _portable_path(stl, model_root),
                "selector": selector,
                "reason": reason,
            }
        )
    ignored_stl_inputs.extend(
        {
            "path": _portable_path(entry, model_root),
            "selector": selector_for(entry),
            "reason": "STL input is not a regular file",
        }
        for entry in nonfile_stl_paths
    )
    bambu_version = (
        _version(
            executable,
            root=config.config_path.parent.resolve(),
            model=model.scad_path,
        )
        if executable
        else None
    )
    tool_status = "passed" if executable and bambu_version and bambu_version.get("status") == "passed" else "unverifiable"
    # A manual STL directory is useful as a handoff input, but file presence
    # alone cannot prove that the mesh came from the current SCAD.  Keep the
    # top-level status pending until the external export report has verified
    # every selector; this prevents a slicer-installed machine from turning
    # an untracked mesh into an apparent production PASS.
    handoff_status = (
        "available"
        if stl_paths and provenance_status == "passed"
        else "unverifiable"
    )
    payload = {
        "schema_version": 1,
        "adapter": "bambu-handoff",
        # STL availability is independent from whether Bambu Studio is
        # installed.  A portable handoff is useful on a second machine even
        # when the originating host has no desktop slicer.
        "status": handoff_status,
        "production_status": production_status,
        "slice_allowed": True,
        "tool_status": tool_status,
        "bambu_executable": Path(executable).name if executable else None,
        "bambu_version": bambu_version,
        "printer": config.print.printer,
        "nozzle_mm": config.print.nozzle_mm,
        "layer_height_mm": config.print.layer_height_mm,
        "assembly_mode": "integrated_part",
        "scad": _portable_path(model.scad_path, model_root),
        "stl_inputs": stl_inputs,
        "ignored_stl_inputs": ignored_stl_inputs,
        "mesh_provenance": {
            "status": provenance_status,
            "mode": "external_report" if external_report_path.is_file() else "manual",
            "reason": provenance_reason,
            "model_sha256": current_model_hash,
            "external_report": (
                _portable_path(external_report_path, model_root)
                if external_report_path.is_file()
                else None
            ),
            "external_report_sha256": (
                _sha256(external_report_path) if external_report_path.is_file() else None
            ),
        },
        "declared_selectors": sorted(declared_selectors) if declared_selectors else None,
        "assembly_stls": [stl_entry(stl, "assembly") for stl in assembly_paths],
        "component_stls": [stl_entry(stl, "component") for stl in component_paths],
        "coupon_stls": [stl_entry(stl, "coupon") for stl in coupon_paths],
        # Keep mutually exclusive import sets explicit.  ``stl_inputs`` is a
        # compatibility flat list, but importing the integrated assembly and
        # its component STLs together would duplicate overlapping geometry.
        "print_sets": {
            "integrated_monochrome": {
                "purpose": "single-material one-piece print",
                "inputs": [stl_entry(stl, "assembly") for stl in assembly_paths],
            },
            "multicolor_components": {
                "purpose": "assign filaments to the shared-canvas base/relief components",
                "inputs": [stl_entry(stl, "component") for stl in component_paths],
            },
            "fit_coupon": {
                "purpose": "short ring for physical fit measurement",
                "inputs": [stl_entry(stl, "coupon") for stl in coupon_paths],
            },
        },
        "recommended_print_set": (
            "multicolor_components" if component_paths else "integrated_monochrome"
        ),
        "do_not_import_print_sets_together": True,
        "fit_ring_coupon": bool(coupon_paths),
        "filament_slots": list(config.print.filament_slots),
        "required_preview_checks": [
            "choose exactly one cap print set (integrated_monochrome or multicolor_components)",
            "orientation and build-volume fit",
            "material/filament slot mapping",
            "one-piece overlap and layer-change preview",
            "purge tower and first-layer preview",
            "save the resulting 3MF and slicer report with this job",
        ],
        "note": (
            "No 3MF or slice success is claimed by this handoff alone."
            if stl_paths and provenance_status == "passed" and not executable
            else "STL inputs are present but their current-model provenance is unverified; "
            "run export-openscad or provide a matching external-openscad-report before slicing."
            if stl_paths and provenance_status != "passed"
            else "STL inputs are ready; open Bambu Studio to place and slice them."
            if stl_paths
            else "No STL inputs were found; export the assembly and fit_ring selectors first."
        ),
    }
    _atomic_json(path, payload)
    return ExternalResult(payload, path)


def doctor(config: PipelineConfig | None = None) -> dict[str, Any]:
    """Report optional tool availability for support requests and CI."""

    openscad_config = config.print.openscad_executable if config else None
    bambu_config = config.print.bambu_executable if config else None
    base_dir = config.config_path.parent if config else None
    openscad = _resolve_tool(openscad_config, ("openscad", "openscad.com"), base_dir=base_dir)
    bambu = _resolve_tool(bambu_config, ("BambuStudio", "bambu-studio"), base_dir=base_dir)
    return {
        "openscad": {"path": openscad, "version": _version(openscad) if openscad else None},
        "bambu_studio": {"path": bambu, "version": _version(bambu) if bambu else None},
    }
