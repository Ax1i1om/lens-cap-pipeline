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
import math
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile
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

from lens_cap_pipeline.brief import resolve_brief_path, validate_design_brief  # noqa: E402
from lens_cap_pipeline.status import process_production_status  # noqa: E402


class ReleaseError(RuntimeError):
    """Raised when the requested 3MF release cannot be verified."""


class ExternalDependencyUnavailable(ReleaseError):
    """Raised only when a required external executable/profile is unavailable.

    A malformed job, failed geometry invariant, bad material binding, or stale
    source hash is a deterministic ``failed`` result.  Reserving
    ``unverifiable`` for this narrower exception keeps the public JSON status
    aligned with the Skills' PASS/FAIL/UNVERIFIABLE contract.
    """


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _atomic_json_file(path: Path, payload: dict[str, Any]) -> None:
    """Atomically replace a JSON marker on the target filesystem."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, delete=False
    ) as handle:
        temporary = Path(handle.name)
        handle.write(
            (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
                "utf-8"
            )
        )
    try:
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _audit_build_source_binding(config: Any, brief_report: dict[str, Any]) -> dict[str, Any]:
    """Rebind the post-process raster to the exact approved candidate hash."""

    report_path = config.output_dir / "process-report.json"
    try:
        process_report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleaseError(f"cannot read current process report: {report_path}") from exc
    process_status = process_production_status(process_report)
    if process_status is None:
        raise ReleaseError("current process report failed an artwork-integrity gate")
    in_progress = config.output_dir / ".process-in-progress"
    if in_progress.exists():
        raise ReleaseError("current process transaction is incomplete")
    source_record = process_report.get("source")
    process_hash = source_record.get("sha256") if isinstance(source_record, dict) else None
    approved_hash = brief_report.get("candidate_sha256")
    if not isinstance(process_hash, str) or not isinstance(approved_hash, str):
        raise ReleaseError("process report or design brief lacks a source hash")
    current_hash = _sha256(config.source_path)
    if not (
        process_hash.lower() == approved_hash.lower() == current_hash.lower()
    ):
        raise ReleaseError(
            "approved candidate, process source lock, and current source artwork hashes disagree"
        )
    return {
        "status": "passed",
        "approved_candidate_sha256": approved_hash.lower(),
        "process_source_sha256": process_hash.lower(),
        "current_source_sha256": current_hash.lower(),
        "process_report": _portable(report_path, config.config_path.parent),
        "process_report_sha256": _sha256(report_path),
        "process_status": process_status,
        "scope": "post_build_and_prepublication_approved_source_binding",
    }


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
        raise ExternalDependencyUnavailable(
            f"cannot run {argv[0]!r}: {type(exc).__name__}: {exc}"
        ) from exc
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


def _validate_release_paths(
    config: Any,
    *,
    native: Path,
    bambu_output: Path | None,
    report: Path,
    brief: Path,
) -> list[Path]:
    """Reject collisions with inputs and between release transaction files."""

    if native.suffix.casefold() != ".3mf":
        raise ReleaseError("--native-output must end in .3mf")
    if bambu_output is not None and bambu_output.suffix.casefold() != ".3mf":
        raise ReleaseError("--slice-output must end in .3mf")
    if report.suffix.casefold() != ".json":
        raise ReleaseError("--report must end in .json")

    outputs = [native, Path(f"{native}.manifest.json"), report]
    if bambu_output is not None:
        outputs.extend((bambu_output, Path(f"{bambu_output}.manifest.json")))
    resolved_outputs = [path.expanduser().resolve() for path in outputs]
    if len(set(resolved_outputs)) != len(resolved_outputs):
        raise ReleaseError(
            "native, Bambu, report, and manifest output paths must all be distinct"
        )

    model_dir = (config.output_dir / "model").resolve()
    protected = {
        config.config_path.expanduser().resolve(),
        config.source_path.expanduser().resolve(),
        brief.expanduser().resolve(),
        (config.output_dir / "process-report.json").resolve(),
        (model_dir / "geometry-report.json").resolve(),
        (model_dir / "projection-report.json").resolve(),
        (model_dir / f"{config.job_slug}.scad").resolve(),
    }
    collisions = [path for path in resolved_outputs if path in protected]
    if collisions:
        raise ReleaseError(
            "release output path collides with a protected input/intermediate: "
            + ", ".join(str(path) for path in collisions)
        )
    for path in resolved_outputs:
        if path.exists() and path.is_dir():
            raise ReleaseError(f"release output path is a directory: {path}")
    return resolved_outputs


def _stage_path(final: Path, stage_dirs: list[Path]) -> Path:
    """Create a same-filesystem staging directory and preserve the basename."""

    directory = Path(
        tempfile.mkdtemp(prefix=f".{final.name}.stage-", dir=final.parent)
    )
    stage_dirs.append(directory)
    return directory / final.name


def _read_adapter_manifest(output: Path) -> dict[str, Any]:
    """Read the adapter sidecar that carries evidence omitted from CLI stdout."""

    manifest = Path(f"{output}.manifest.json")
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleaseError(f"cannot read staged adapter manifest: {manifest}") from exc
    if not isinstance(payload, dict):
        raise ReleaseError(f"staged adapter manifest is not an object: {manifest}")
    return payload


def _retarget_adapter_manifest(
    staged_output: Path,
    final_output: Path,
    *,
    release_context: dict[str, Any] | None = None,
) -> Path:
    """Rewrite the adapter sidecar so its path fields describe publication."""

    manifest = Path(f"{staged_output}.manifest.json")
    payload = _read_adapter_manifest(staged_output)
    portable_final = _portable(final_output, ROOT)
    payload["output"] = portable_final
    verification = payload.get("verification")
    if isinstance(verification, dict):
        verification["path"] = portable_final
    if release_context is not None:
        payload["release_context"] = release_context
    manifest.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


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


def _adapter(
    command: Sequence[str],
    *,
    timeout: int = 1800,
    require_slice: bool = False,
    require_closed: bool = False,
    require_single_volume: bool = False,
) -> dict[str, Any]:
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
    if require_closed:
        verify_command.append("--require-closed")
    if require_single_volume:
        verify_command.append("--require-single-volume")
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
        "adapter_manifest": _read_adapter_manifest(Path(output)),
        "verification": verified,
        "command": list(command),
    }


def _angular_distance_degrees(left: float, right: float) -> float:
    delta = abs((left - right) % 360.0)
    return min(delta, 360.0 - delta)


def _audit_integrated_ribs(path: Path, mechanical: dict[str, Any]) -> dict[str, Any]:
    """Prove that enabled inner-wall ribs survived into the native 3MF mesh.

    The geometry report proves what the SCAD requested; this mesh-level check
    proves that the final integrated package contains referenced tip edges at
    every expected angular position across the complete rib span.  Every
    accepted tip must belong to the mesh's largest connected component and be
    joined to the cavity wall at both axial ends.  Merely appending unused
    marker vertices therefore cannot spoof the gate.  This is a
    structural-presence check, not a claim that the friction fit has been
    physically validated.
    """

    enabled = mechanical.get("friction_ribs_enabled")
    if enabled is False:
        return {"status": "not_required", "reason": "friction ribs disabled"}
    if enabled is not True:
        raise ReleaseError("geometry report does not declare the friction-rib decision")
    try:
        count = int(mechanical["friction_rib_count"])
        cavity_radius = float(mechanical["cavity_diameter_mm"]) / 2.0
        protrusion = float(mechanical["friction_rib_protrusion_mm"])
        start = float(mechanical["friction_rib_start_mm"])
        end = start + float(mechanical["friction_rib_height_mm"])
        tip_angle = float(mechanical["friction_rib_tip_angle_deg"])
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise ReleaseError("geometry report lacks valid friction-rib audit fields") from exc
    if count < 3 or not all(
        math.isfinite(value)
        for value in (cavity_radius, protrusion, start, end, tip_angle)
    ):
        raise ReleaseError("geometry report has invalid friction-rib audit fields")
    tip_radius = cavity_radius - protrusion
    declared_tip_width = 2.0 * tip_radius * math.sin(math.radians(tip_angle / 2.0))
    if tip_radius <= 0 or end <= start:
        raise ReleaseError("geometry report has invalid friction-rib tip geometry")

    try:
        with zipfile.ZipFile(path) as archive:
            root = ET.fromstring(archive.read("3D/3dmodel.model"))
    except (OSError, KeyError, zipfile.BadZipFile, ET.ParseError) as exc:
        raise ReleaseError(f"cannot inspect native 3MF rib geometry: {exc}") from exc
    if str(root.attrib.get("unit", "")).strip().casefold() != "millimeter":
        raise ReleaseError("native 3MF rib audit requires an explicit millimeter model")
    for node in root.iter():
        local = node.tag.rsplit("}", 1)[-1]
        if local == "component":
            raise ReleaseError(
                "native 3MF rib audit requires direct integrated mesh geometry, not components"
            )
        if local == "item" and node.attrib.get("transform"):
            raise ReleaseError(
                "native 3MF rib audit does not accept an unexpanded build transform"
            )

    radial_tolerance = 0.03
    axial_tolerance = 0.03
    angular_tolerance = max(1.0, tip_angle)

    meshes: list[dict[str, Any]] = []
    for mesh_node in (
        node for node in root.iter() if node.tag.rsplit("}", 1)[-1] == "mesh"
    ):
        vertices_node = next(
            (node for node in mesh_node if node.tag.rsplit("}", 1)[-1] == "vertices"),
            None,
        )
        triangles_node = next(
            (node for node in mesh_node if node.tag.rsplit("}", 1)[-1] == "triangles"),
            None,
        )
        if vertices_node is None or triangles_node is None:
            raise ReleaseError("native 3MF rib audit found an incomplete mesh")
        vertices: list[tuple[float, float, float]] = []
        for node in vertices_node:
            if node.tag.rsplit("}", 1)[-1] != "vertex":
                continue
            try:
                vertex = tuple(float(node.attrib[axis]) for axis in "xyz")
            except (KeyError, TypeError, ValueError, OverflowError) as exc:
                raise ReleaseError("native 3MF contains an invalid mesh vertex") from exc
            if len(vertex) != 3 or not all(math.isfinite(value) for value in vertex):
                raise ReleaseError("native 3MF contains a non-finite mesh vertex")
            vertices.append(vertex)
        adjacency: list[set[int]] = [set() for _ in vertices]
        edges: set[tuple[int, int]] = set()
        referenced: set[int] = set()
        triangles: list[tuple[int, int, int]] = []
        for node in triangles_node:
            if node.tag.rsplit("}", 1)[-1] != "triangle":
                continue
            try:
                indices = tuple(int(node.attrib[name]) for name in ("v1", "v2", "v3"))
            except (KeyError, TypeError, ValueError, OverflowError) as exc:
                raise ReleaseError("native 3MF contains an invalid mesh triangle") from exc
            if len(set(indices)) != 3 or any(index < 0 or index >= len(vertices) for index in indices):
                raise ReleaseError("native 3MF contains a degenerate or out-of-range mesh triangle")
            triangles.append(indices)
            referenced.update(indices)
            for left, right in (
                (indices[0], indices[1]),
                (indices[1], indices[2]),
                (indices[2], indices[0]),
            ):
                edge = (left, right) if left < right else (right, left)
                edges.add(edge)
                adjacency[left].add(right)
                adjacency[right].add(left)
        if not referenced:
            continue

        # A mesh can contain a tiny exporter artefact shell.  Rib evidence
        # must nevertheless be part of the dominant cap shell, not a detached
        # triangle/tetrahedron planted at the expected coordinates.
        remaining = set(referenced)
        components: list[set[int]] = []
        while remaining:
            seed = remaining.pop()
            component = {seed}
            stack = [seed]
            while stack:
                current = stack.pop()
                for neighbor in adjacency[current]:
                    if neighbor in remaining:
                        remaining.remove(neighbor)
                        component.add(neighbor)
                        stack.append(neighbor)
            components.append(component)
        main_component = max(components, key=len)
        meshes.append(
            {
                "vertices": vertices,
                "adjacency": adjacency,
                "edges": edges,
                "triangles": triangles,
                "triangle_sets": {frozenset(triangle) for triangle in triangles},
                "referenced": referenced,
                "main_component": main_component,
                "component_count": len(components),
            }
        )
    if not meshes:
        raise ReleaseError("native 3MF rib audit found no triangle mesh")

    def candidates(
        mesh: dict[str, Any], target_z: float, expected_angle: float
    ) -> list[int]:
        result: list[int] = []
        main_component = mesh["main_component"]
        for index in main_component:
            x, y, z = mesh["vertices"][index]
            if abs(z - target_z) > axial_tolerance:
                continue
            if abs(math.hypot(x, y) - tip_radius) > radial_tolerance:
                continue
            angle = (math.degrees(math.atan2(y, x)) + 360.0) % 360.0
            if _angular_distance_degrees(angle, expected_angle) <= angular_tolerance:
                result.append(index)
        return result

    def wall_neighbors(mesh: dict[str, Any], index: int, target_z: float) -> list[int]:
        result: list[int] = []
        for neighbor in mesh["adjacency"][index]:
            x, y, z = mesh["vertices"][neighbor]
            if abs(z - target_z) <= axial_tolerance and abs(
                math.hypot(x, y) - cavity_radius
            ) <= radial_tolerance:
                result.append(neighbor)
        return result

    def maximum_span(
        mesh: dict[str, Any], indices: Sequence[int]
    ) -> float:
        points = [mesh["vertices"][index] for index in set(indices)]
        return max(
            (
                math.hypot(left[0] - right[0], left[1] - right[1])
                for offset, left in enumerate(points)
                for right in points[offset + 1 :]
            ),
            default=0.0,
        )

    def full_height_tip_face(
        mesh: dict[str, Any], expected_angle: float
    ) -> tuple[bool, float]:
        """Require the actual continuous contact face of an extruded rib.

        Point samples, even across the entire z span, can be spoofed by a comb
        of arbitrarily thin axial knives placed exactly at the public XY samples.
        The generated tapered rib instead has one two-triangle quadrilateral
        at its innermost contact chord.  Proving that full-width face exists
        makes gaps between comb teeth impossible without failing topology.
        """

        start_candidates = candidates(mesh, start, expected_angle)
        end_candidates = candidates(mesh, end, expected_angle)
        if len(start_candidates) < 2 or len(end_candidates) < 2:
            return False, 0.0
        tangent = (
            -math.sin(math.radians(expected_angle)),
            math.cos(math.radians(expected_angle)),
        )

        def tangent_position(index: int) -> float:
            x, y, _ = mesh["vertices"][index]
            return x * tangent[0] + y * tangent[1]

        best_width = 0.0
        triangle_sets = mesh["triangle_sets"]
        for start_left in start_candidates:
            for start_right in start_candidates:
                start_width = tangent_position(start_right) - tangent_position(
                    start_left
                )
                if start_width < minimum_observed_width:
                    continue
                for end_left in end_candidates:
                    left_start = mesh["vertices"][start_left]
                    left_end = mesh["vertices"][end_left]
                    if math.hypot(
                        left_start[0] - left_end[0],
                        left_start[1] - left_end[1],
                    ) > radial_tolerance:
                        continue
                    for end_right in end_candidates:
                        end_width = tangent_position(end_right) - tangent_position(
                            end_left
                        )
                        if end_width < minimum_observed_width:
                            continue
                        right_start = mesh["vertices"][start_right]
                        right_end = mesh["vertices"][end_right]
                        if math.hypot(
                            right_start[0] - right_end[0],
                            right_start[1] - right_end[1],
                        ) > radial_tolerance:
                            continue
                        triangulations = (
                            (
                                frozenset((start_left, start_right, end_left)),
                                frozenset((start_right, end_left, end_right)),
                            ),
                            (
                                frozenset((start_left, start_right, end_right)),
                                frozenset((start_left, end_left, end_right)),
                            ),
                        )
                        if any(
                            first in triangle_sets and second in triangle_sets
                            for first, second in triangulations
                        ):
                            best_width = max(
                                best_width, min(start_width, end_width)
                            )
        return best_width >= minimum_observed_width, best_width

    def point_in_triangle(
        point: tuple[float, float],
        triangle: tuple[
            tuple[float, float], tuple[float, float], tuple[float, float]
        ],
    ) -> bool:
        def cross(
            origin: tuple[float, float],
            left: tuple[float, float],
            right: tuple[float, float],
        ) -> float:
            return (left[0] - origin[0]) * (right[1] - origin[1]) - (
                left[1] - origin[1]
            ) * (right[0] - origin[0])

        signs = (
            cross(triangle[0], triangle[1], point),
            cross(triangle[1], triangle[2], point),
            cross(triangle[2], triangle[0], point),
        )
        tolerance = 1e-8
        return not (
            any(value < -tolerance for value in signs)
            and any(value > tolerance for value in signs)
        )

    for mesh in meshes:
        for label, target_z in (("start_faces", start), ("end_faces", end)):
            faces = []
            for triangle in mesh["triangles"]:
                if not all(index in mesh["main_component"] for index in triangle):
                    continue
                points = [mesh["vertices"][index] for index in triangle]
                if not all(abs(point[2] - target_z) <= axial_tolerance for point in points):
                    continue
                faces.append(tuple((point[0], point[1]) for point in points))
            mesh[label] = faces

    # End-cap faces alone are not enough to prove that a rib remains solid
    # through its height: two axial knives can be joined by tiny bridges only
    # at z=start/end and otherwise leave the nominal contact patch hollow.  Cut
    # the *referenced main solid* at several non-round interior planes and use
    # odd/even containment against the resulting closed contours.  The
    # deliberately uneven fractions avoid coinciding with exporter layer
    # vertices while still sampling the beginning, middle, and end of the rib.
    interior_slice_fractions = (0.251, 0.503, 0.757)
    interior_slice_z = tuple(
        start + (end - start) * fraction for fraction in interior_slice_fractions
    )

    def triangle_plane_segment(
        points: Sequence[tuple[float, float, float]], target_z: float
    ) -> tuple[tuple[float, float], tuple[float, float]] | None:
        intersections: list[tuple[float, float]] = []
        epsilon = 1e-9
        for left_index, right_index in ((0, 1), (1, 2), (2, 0)):
            left = points[left_index]
            right = points[right_index]
            left_delta = left[2] - target_z
            right_delta = right[2] - target_z
            if abs(left_delta) <= epsilon and abs(right_delta) <= epsilon:
                # Interior planes are chosen not to be coplanar with normal
                # exported faces.  If a pathological mesh still contains a
                # coplanar edge, the neighbouring non-coplanar face supplies
                # the actual boundary segment.
                continue
            if (left_delta > epsilon and right_delta > epsilon) or (
                left_delta < -epsilon and right_delta < -epsilon
            ):
                continue
            denominator = right[2] - left[2]
            if abs(denominator) <= epsilon:
                continue
            interpolation = (target_z - left[2]) / denominator
            if interpolation < -epsilon or interpolation > 1.0 + epsilon:
                continue
            interpolation = min(1.0, max(0.0, interpolation))
            point = (
                left[0] + interpolation * (right[0] - left[0]),
                left[1] + interpolation * (right[1] - left[1]),
            )
            if not any(
                math.hypot(point[0] - existing[0], point[1] - existing[1])
                <= epsilon
                for existing in intersections
            ):
                intersections.append(point)
        if len(intersections) < 2:
            return None
        if len(intersections) > 2:
            # A plane through a triangle vertex can yield three numerically
            # distinct points.  Retain the widest pair, which is the surface
            # contour for parity purposes.
            return max(
                (
                    (left, right)
                    for offset, left in enumerate(intersections)
                    for right in intersections[offset + 1 :]
                ),
                key=lambda pair: math.hypot(
                    pair[0][0] - pair[1][0], pair[0][1] - pair[1][1]
                ),
            )
        return intersections[0], intersections[1]

    for mesh in meshes:
        interior_sections: list[list[tuple[tuple[float, float], tuple[float, float]]]] = []
        for target_z in interior_slice_z:
            segments = []
            for triangle in mesh["triangles"]:
                if not all(index in mesh["main_component"] for index in triangle):
                    continue
                segment = triangle_plane_segment(
                    [mesh["vertices"][index] for index in triangle], target_z
                )
                if segment is not None:
                    segments.append(segment)
            interior_sections.append(segments)
        mesh["interior_sections"] = interior_sections

        # Precompute non-vertical projected triangles for exact vertical-line
        # occupancy.  A finite list of public z slices can always be spoofed
        # with paper-thin bridges placed at those known planes.  Intersecting
        # each contact sample's entire axial line with the closed solid proves
        # there is one uninterrupted inside interval from rib start to end.
        projected_triangles = []
        for triangle in mesh["triangles"]:
            if not all(index in mesh["main_component"] for index in triangle):
                continue
            points = [mesh["vertices"][index] for index in triangle]
            left, middle, right = points
            determinant = (
                (middle[0] - left[0]) * (right[1] - left[1])
                - (middle[1] - left[1]) * (right[0] - left[0])
            )
            if abs(determinant) <= 1e-12:
                # A vertical surface projects to a line and does not toggle
                # solid occupancy for a generic vertical sample.
                continue
            projected_triangles.append(
                {
                    "points": points,
                    "determinant": determinant,
                    "bounds": (
                        min(point[0] for point in points),
                        max(point[0] for point in points),
                        min(point[1] for point in points),
                        max(point[1] for point in points),
                    ),
                }
            )
        mesh["projected_triangles"] = projected_triangles

    def point_in_section(
        point: tuple[float, float],
        segments: Sequence[tuple[tuple[float, float], tuple[float, float]]],
    ) -> bool:
        x, y = point
        boundary_tolerance = 1e-8
        crossings = 0
        for left, right in segments:
            delta_x = right[0] - left[0]
            delta_y = right[1] - left[1]
            length_squared = delta_x * delta_x + delta_y * delta_y
            if length_squared <= boundary_tolerance * boundary_tolerance:
                continue
            projection = (
                (x - left[0]) * delta_x + (y - left[1]) * delta_y
            ) / length_squared
            projection = min(1.0, max(0.0, projection))
            closest = (
                left[0] + projection * delta_x,
                left[1] + projection * delta_y,
            )
            if math.hypot(x - closest[0], y - closest[1]) <= boundary_tolerance:
                return True
            # Half-open crossing convention counts a shared contour vertex
            # once, keeping parity stable across triangulated surface pieces.
            if (left[1] > y) == (right[1] > y):
                continue
            intersection_x = left[0] + (y - left[1]) * delta_x / delta_y
            if intersection_x > x:
                crossings += 1
        return crossings % 2 == 1

    def vertical_line_intersections(
        mesh: dict[str, Any], point: tuple[float, float]
    ) -> list[float]:
        x, y = point
        intersections: list[float] = []
        barycentric_tolerance = 1e-9
        for projected in mesh["projected_triangles"]:
            minimum_x, maximum_x, minimum_y, maximum_y = projected["bounds"]
            if (
                x < minimum_x - barycentric_tolerance
                or x > maximum_x + barycentric_tolerance
                or y < minimum_y - barycentric_tolerance
                or y > maximum_y + barycentric_tolerance
            ):
                continue
            left, middle, right = projected["points"]
            determinant = projected["determinant"]
            middle_weight = (
                (x - left[0]) * (right[1] - left[1])
                - (y - left[1]) * (right[0] - left[0])
            ) / determinant
            right_weight = (
                (middle[0] - left[0]) * (y - left[1])
                - (middle[1] - left[1]) * (x - left[0])
            ) / determinant
            left_weight = 1.0 - middle_weight - right_weight
            if min(left_weight, middle_weight, right_weight) < -barycentric_tolerance:
                continue
            intersections.append(
                left_weight * left[2]
                + middle_weight * middle[2]
                + right_weight * right[2]
            )
        intersections.sort()
        unique: list[float] = []
        merge_tolerance = 1e-6
        for value in intersections:
            if not unique or abs(value - unique[-1]) > merge_tolerance:
                unique.append(value)
        return unique

    def uninterrupted_axial_coverage(
        point: tuple[float, float]
    ) -> tuple[bool, float, int]:
        required_tolerance = max(1e-5, declared_tip_width * 0.0001)
        best_span = 0.0
        maximum_intersections = 0
        for mesh in meshes:
            intersections = vertical_line_intersections(mesh, point)
            maximum_intersections = max(maximum_intersections, len(intersections))
            # Starting below every surface is outside a closed solid; each
            # distinct crossing toggles occupancy, so consecutive pairs are
            # exact inside intervals along this XY contact column.
            for offset in range(0, len(intersections) - 1, 2):
                lower = intersections[offset]
                upper = intersections[offset + 1]
                best_span = max(best_span, upper - lower)
                if (
                    lower <= start + required_tolerance
                    and upper >= end - required_tolerance
                ):
                    return True, upper - lower, len(intersections)
        return False, best_span, maximum_intersections

    def continuous_cross_section(
        expected_angle: float, face_label: str
    ) -> tuple[bool, int]:
        samples: list[tuple[float, float]] = []
        for radial_fraction in (0.25, 0.50, 0.75):
            radius = tip_radius + protrusion * radial_fraction
            for angular_fraction in (-0.80, -0.40, 0.0, 0.40, 0.80):
                angle = math.radians(
                    expected_angle + angular_fraction * tip_angle / 2.0
                )
                samples.append((radius * math.cos(angle), radius * math.sin(angle)))
        hits = sum(
            any(
                point_in_triangle(sample, face)
                for mesh in meshes
                for face in mesh[face_label]
            )
            for sample in samples
        )
        return hits == len(samples), hits

    def continuous_interior_cross_sections(
        expected_angle: float,
    ) -> tuple[bool, list[int]]:
        samples: list[tuple[float, float]] = []
        for radial_fraction in (0.25, 0.50, 0.75):
            radius = tip_radius + protrusion * radial_fraction
            for angular_fraction in (-0.80, -0.40, 0.0, 0.40, 0.80):
                angle = math.radians(
                    expected_angle + angular_fraction * tip_angle / 2.0
                )
                samples.append((radius * math.cos(angle), radius * math.sin(angle)))
        hits_by_slice = [
            sum(
                any(
                    point_in_section(sample, mesh["interior_sections"][slice_index])
                    for mesh in meshes
                )
                for sample in samples
            )
            for slice_index in range(len(interior_slice_z))
        ]
        return all(hits == len(samples) for hits in hits_by_slice), hits_by_slice

    def continuous_axial_contact(
        expected_angle: float,
    ) -> tuple[bool, int, float, int]:
        samples: list[tuple[float, float]] = []
        for radial_fraction in (0.25, 0.50, 0.75):
            radius = tip_radius + protrusion * radial_fraction
            for angular_fraction in (-0.80, -0.40, 0.0, 0.40, 0.80):
                angle = math.radians(
                    expected_angle + angular_fraction * tip_angle / 2.0
                )
                samples.append((radius * math.cos(angle), radius * math.sin(angle)))
        coverage = [uninterrupted_axial_coverage(sample) for sample in samples]
        passed = [item for item in coverage if item[0]]
        return (
            len(passed) == len(samples),
            len(passed),
            min((item[1] for item in coverage), default=0.0),
            max((item[2] for item in coverage), default=0),
        )

    detected_positions: list[int] = []
    vertical_edge_hits = 0
    tip_vertex_indices: set[tuple[int, int]] = set()
    observed_tip_widths: list[float] = []
    observed_wall_widths: list[float] = []
    observed_full_height_tip_face_widths: list[float] = []
    cross_section_sample_hits: list[dict[str, Any]] = []
    axial_continuity_sample_hits: list[dict[str, Any]] = []
    minimum_observed_width = declared_tip_width * 0.90
    for position in range(count):
        expected_angle = position * 360.0 / count
        position_pairs: list[tuple[int, int, int, tuple[int, ...], tuple[int, ...]]] = []
        for mesh_index, mesh in enumerate(meshes):
            start_candidates = candidates(mesh, start, expected_angle)
            end_candidates = candidates(mesh, end, expected_angle)
            for start_index in start_candidates:
                for end_index in end_candidates:
                    edge = (
                        (start_index, end_index)
                        if start_index < end_index
                        else (end_index, start_index)
                    )
                    if edge not in mesh["edges"]:
                        continue
                    start_x, start_y, _ = mesh["vertices"][start_index]
                    end_x, end_y, _ = mesh["vertices"][end_index]
                    if math.hypot(start_x - end_x, start_y - end_y) > radial_tolerance:
                        continue
                    start_wall = wall_neighbors(mesh, start_index, start)
                    if not start_wall:
                        continue
                    end_wall = wall_neighbors(mesh, end_index, end)
                    if not end_wall:
                        continue
                    position_pairs.append(
                        (
                            mesh_index,
                            start_index,
                            end_index,
                            tuple(start_wall),
                            tuple(end_wall),
                        )
                    )
        tip_width = 0.0
        wall_width = 0.0
        for mesh_index in range(len(meshes)):
            mesh_pairs = [pair for pair in position_pairs if pair[0] == mesh_index]
            if not mesh_pairs:
                continue
            mesh = meshes[mesh_index]
            tip_width = max(
                tip_width,
                maximum_span(mesh, [pair[1] for pair in mesh_pairs]),
                maximum_span(mesh, [pair[2] for pair in mesh_pairs]),
            )
            wall_width = max(
                wall_width,
                maximum_span(
                    mesh,
                    [neighbor for pair in mesh_pairs for neighbor in pair[3]],
                ),
                maximum_span(
                    mesh,
                    [neighbor for pair in mesh_pairs for neighbor in pair[4]],
                ),
            )
        start_continuous, start_hits = continuous_cross_section(
            expected_angle, "start_faces"
        )
        end_continuous, end_hits = continuous_cross_section(
            expected_angle, "end_faces"
        )
        interior_continuous, interior_hits = continuous_interior_cross_sections(
            expected_angle
        )
        (
            axial_continuous,
            axial_hits,
            minimum_axial_span,
            maximum_axial_intersections,
        ) = continuous_axial_contact(expected_angle)
        tip_face_results = [
            full_height_tip_face(mesh, expected_angle) for mesh in meshes
        ]
        full_tip_face, full_tip_face_width = max(
            tip_face_results, key=lambda item: item[1], default=(False, 0.0)
        )
        if (
            position_pairs
            and tip_width >= minimum_observed_width
            and wall_width >= minimum_observed_width
            and start_continuous
            and end_continuous
            and interior_continuous
            and axial_continuous
            and full_tip_face
        ):
            detected_positions.append(position)
            vertical_edge_hits += len(position_pairs)
            observed_tip_widths.append(tip_width)
            observed_wall_widths.append(wall_width)
            observed_full_height_tip_face_widths.append(full_tip_face_width)
            cross_section_sample_hits.append(
                {
                    "position": position,
                    "start": start_hits,
                    "interior": interior_hits,
                    "end": end_hits,
                }
            )
            axial_continuity_sample_hits.append(
                {
                    "position": position,
                    "continuous_columns": axial_hits,
                    # The pass/fail decision above uses the unrounded value.
                    # Canonicalise only the published diagnostic: libm can
                    # otherwise spell the same 8 mm span as either 8.0 or
                    # 7.999999999999998 on different operating systems.
                    "minimum_inside_interval_mm": round(minimum_axial_span, 9),
                    "maximum_surface_intersections": maximum_axial_intersections,
                }
            )
            for mesh_index, start_index, end_index, _start_wall, _end_wall in position_pairs:
                tip_vertex_indices.add((mesh_index, start_index))
                tip_vertex_indices.add((mesh_index, end_index))

    if len(detected_positions) != count:
        raise ReleaseError(
            "native 3MF friction-rib mesh audit failed: expected "
            f"{count} integrated start-to-end positions, detected "
            f"{len(detected_positions)} across z={start:g}..{end:g}"
        )
    referenced_vertices = sum(len(mesh["referenced"]) for mesh in meshes)
    unused_vertices = sum(
        len(mesh["vertices"]) - len(mesh["referenced"]) for mesh in meshes
    )
    return {
        "status": "passed",
        "expected_rib_count": count,
        "detected_start_positions": len(detected_positions),
        "detected_end_positions": len(detected_positions),
        "integrated_vertical_edge_positions": len(detected_positions),
        "integrated_vertical_edge_hits": vertical_edge_hits,
        "tip_vertex_hits": len(tip_vertex_indices),
        "referenced_vertices": referenced_vertices,
        "unused_vertices": unused_vertices,
        "mesh_component_counts": [mesh["component_count"] for mesh in meshes],
        "main_component_vertices": [len(mesh["main_component"]) for mesh in meshes],
        "tip_radius_mm": tip_radius,
        "declared_tip_width_mm": declared_tip_width,
        "minimum_accepted_cross_section_width_mm": minimum_observed_width,
        "minimum_observed_tip_width_mm": min(observed_tip_widths),
        "minimum_observed_wall_attachment_width_mm": min(observed_wall_widths),
        "minimum_full_height_tip_face_width_mm": min(
            observed_full_height_tip_face_widths
        ),
        "cross_section_samples_per_end": 15,
        "interior_cross_section_z_mm": list(interior_slice_z),
        "cross_section_samples_per_interior_slice": 15,
        "cross_section_sample_hits": cross_section_sample_hits,
        "axial_continuity_samples_per_rib": 15,
        "axial_continuity_sample_hits": axial_continuity_sample_hits,
        "axial_span_mm": [start, end],
        "radial_tolerance_mm": radial_tolerance,
        "angular_tolerance_deg": angular_tolerance,
        "scope": (
            "referenced_connected_full_width_tip_faces_full_height_axial_contact_columns_"
            "and_start_interior_end_wall_cross_sections_physical_fit_requires_coupon"
        ),
    }


def _audit_material_assignments(
    path: Path,
    palette: Sequence[Any],
    *,
    expected_footprint_mm2: dict[str, float] | None = None,
    expected_masks: dict[str, Path] | None = None,
    expected_top_z_mm: dict[str, float] | None = None,
    canvas_size_mm: float | None = None,
    tolerance_pixels: int = 1,
    expected_visible_top_area_reference: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Require every used 3MF triangle colour to come from the active job.

    OpenSCAD can export a single watertight object with per-triangle base
    material assignments.  Object count alone therefore cannot prove that a
    multi-tone relief survived.  This gate checks the final model's material
    table and requires non-empty assignments for every required job colour.
    """

    try:
        with zipfile.ZipFile(path) as archive:
            root = ET.fromstring(archive.read("3D/3dmodel.model"))
    except (OSError, KeyError, zipfile.BadZipFile, ET.ParseError) as exc:
        raise ReleaseError(f"cannot inspect native 3MF material assignments: {exc}") from exc

    material_groups: dict[str, list[str]] = {}
    for node in root.iter():
        if node.tag.rsplit("}", 1)[-1] != "basematerials":
            continue
        group_id = node.attrib.get("id")
        if not group_id:
            continue
        colors: list[str] = []
        for child in node:
            if child.tag.rsplit("}", 1)[-1] != "base":
                continue
            value = str(child.attrib.get("displaycolor", "")).upper()
            if len(value) == 7:
                value += "FF"
            colors.append(value)
        material_groups[group_id] = colors

    assigned: dict[str, int] = {}
    projected_area: dict[str, float] = {}
    material_triangles: dict[
        str,
        list[
            tuple[
                tuple[float, float, float],
                tuple[float, float, float],
                tuple[float, float, float],
            ]
        ],
    ] = {}
    unassigned_triangles = 0
    for object_node in root.iter():
        if object_node.tag.rsplit("}", 1)[-1] != "object":
            continue
        object_pid = object_node.attrib.get("pid")
        object_index = object_node.attrib.get("pindex")
        mesh = next(
            (node for node in object_node if node.tag.rsplit("}", 1)[-1] == "mesh"),
            None,
        )
        if mesh is None:
            continue
        vertices_node = next(
            (node for node in mesh if node.tag.rsplit("}", 1)[-1] == "vertices"),
            None,
        )
        triangles_node = next(
            (node for node in mesh if node.tag.rsplit("}", 1)[-1] == "triangles"),
            None,
        )
        if vertices_node is None or triangles_node is None:
            raise ReleaseError("native 3MF material audit found an incomplete mesh")
        try:
            vertices = [
                tuple(float(node.attrib[axis]) for axis in "xyz")
                for node in vertices_node
                if node.tag.rsplit("}", 1)[-1] == "vertex"
            ]
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise ReleaseError("native 3MF material audit found an invalid vertex") from exc
        for node in triangles_node:
            if node.tag.rsplit("}", 1)[-1] != "triangle":
                continue
            group = material_groups.get(str(node.attrib.get("pid", object_pid or "")))
            first = node.attrib.get("p1", object_index)
            if group is None or first is None:
                unassigned_triangles += 1
                continue
            # The OpenSCAD native route emits one material per complete face.
            # A single mixed-property triangle must not be allowed to prove
            # that three full relief colours survived the export.
            property_indices_raw = (
                first,
                node.attrib.get("p2", first),
                node.attrib.get("p3", first),
            )
            if any(
                not isinstance(value, str) or not re.fullmatch(r"[0-9]+", value)
                for value in property_indices_raw
            ):
                raise ReleaseError(
                    "native 3MF triangle has a malformed or negative base-material index"
                )
            property_indices = tuple(int(value) for value in property_indices_raw)
            if any(index >= len(group) for index in property_indices):
                raise ReleaseError(
                    "native 3MF triangle references an out-of-range base material"
                )
            if len(set(property_indices)) != 1:
                raise ReleaseError(
                    "native 3MF contains a mixed-property triangle; canonical lens-cap faces "
                    "must use one material each"
                )
            try:
                color = group[property_indices[0]]
                geometry_indices = tuple(
                    int(node.attrib[name]) for name in ("v1", "v2", "v3")
                )
                if any(index < 0 or index >= len(vertices) for index in geometry_indices):
                    raise IndexError("out-of-range vertex")
                a, b, c = (vertices[index] for index in geometry_indices)
            except (TypeError, ValueError, IndexError) as exc:
                raise ReleaseError(
                    "native 3MF triangle references invalid geometry or base material"
                ) from exc
            assigned[color] = assigned.get(color, 0) + 1
            xy_area = abs(
                (b[0] - a[0]) * (c[1] - a[1])
                - (b[1] - a[1]) * (c[0] - a[0])
            ) / 2.0
            projected_area[color] = projected_area.get(color, 0.0) + xy_area
            material_triangles.setdefault(color, []).append((a, b, c))

    expected: dict[str, dict[str, Any]] = {}
    for entry in palette:
        try:
            rgb = tuple(int(channel) for channel in entry.rgb)
            color = "#%02X%02X%02XFF" % rgb
            expected[color] = {
                "name": str(entry.name),
                "required": bool(entry.required),
                "role": str(entry.role),
            }
        except (AttributeError, TypeError, ValueError) as exc:
            raise ReleaseError("job palette cannot be converted to a 3MF material audit") from exc

    unexpected = sorted(color for color, count in assigned.items() if count and color not in expected)
    if unexpected:
        raise ReleaseError(
            "native 3MF uses triangle colours outside the active job palette: "
            + ", ".join(unexpected)
        )
    if unassigned_triangles:
        raise ReleaseError(
            f"native 3MF has {unassigned_triangles} triangles without an auditable palette assignment"
        )
    expected_footprint_mm2 = expected_footprint_mm2 or {}
    inactive_used = sorted(
        details["name"]
        for color, details in expected.items()
        if details["role"] == "relief"
        and details["name"] in expected_footprint_mm2
        and float(expected_footprint_mm2[details["name"]]) <= 0.0
        and assigned.get(color, 0) > 0
    )
    if inactive_used:
        raise ReleaseError(
            "native 3MF assigns triangles to inactive zero-pixel palette colours: "
            + ", ".join(inactive_used)
        )
    missing = sorted(
        details["name"]
        for color, details in expected.items()
        if (
            details["required"]
            or (
                details["role"] == "relief"
                and float(expected_footprint_mm2.get(details["name"], 0.0)) > 0.0
            )
        )
        and assigned.get(color, 0) <= 0
    )
    if missing:
        raise ReleaseError(
            "native 3MF has no triangle assignments for required palette colours: "
            + ", ".join(missing)
        )
    insufficient_footprint: list[str] = []
    for color, details in expected.items():
        expected_area = float(expected_footprint_mm2.get(details["name"], 0.0))
        if not details["required"] and expected_area <= 0.0:
            continue
        minimum_area = expected_area * 0.20
        if expected_area > 0 and projected_area.get(color, 0.0) < minimum_area:
            insufficient_footprint.append(details["name"])
    if insufficient_footprint:
        raise ReleaseError(
            "native 3MF material footprint is implausibly small for required colours: "
            + ", ".join(sorted(insufficient_footprint))
        )

    # Triangle counts and aggregate areas cannot distinguish two relief
    # colours whose material-table entries were swapped.  When the canonical
    # masks are supplied, project each final 3MF material back onto the exact
    # shared canvas and compare it to its named mask.  The base colour is
    # intentionally excluded: it covers the mechanical body as well as the
    # artwork background, whereas each relief colour has a one-to-one mask.
    spatial: dict[str, Any] = {}
    visible_top_area_by_name: dict[str, float] = {}
    if expected_masks is not None:
        if expected_top_z_mm is None:
            raise ReleaseError("material spatial audit requires expected relief top Z values")
        if canvas_size_mm is None or not math.isfinite(float(canvas_size_mm)) or canvas_size_mm <= 0:
            raise ReleaseError("material spatial audit requires a positive canvas_size_mm")
        if tolerance_pixels < 0 or tolerance_pixels > 8:
            raise ReleaseError("material spatial audit tolerance must be in [0, 8] pixels")
        try:
            import numpy as np

            from scripts.audit_stl_projection import dilate, load_binary_mask, rasterize
        except (ImportError, ModuleNotFoundError) as exc:
            raise ReleaseError(
                "material spatial audit requires the installed Pillow/NumPy pipeline dependencies"
            ) from exc
        expected_names = set(expected_top_z_mm)
        active_relief_names = {
            details["name"]
            for details in expected.values()
            if details["role"] == "relief"
            and float(expected_footprint_mm2.get(details["name"], 0.0)) > 0.0
        }
        if expected_names != active_relief_names:
            raise ReleaseError(
                "material spatial audit relief set disagrees with active process masks: "
                f"{sorted(expected_names)!r} != {sorted(active_relief_names)!r}"
            )
        missing_masks = sorted(expected_names.difference(expected_masks))
        if missing_masks:
            raise ReleaseError(
                "material spatial audit is missing required relief masks: "
                + ", ".join(missing_masks)
            )
        for color, details in expected.items():
            name = details["name"]
            if details["role"] != "relief" or name not in expected_names:
                continue
            try:
                expected_mask = load_binary_mask(Path(expected_masks[name]))
                if name not in expected_top_z_mm:
                    raise ReleaseError(
                        f"material spatial audit is missing expected top Z for {name}"
                    )
                expected_z = float(expected_top_z_mm[name])
                if not math.isfinite(expected_z):
                    raise ReleaseError(
                        f"material spatial audit has non-finite expected top Z for {name}"
                    )
                palette_height = float(
                    next(
                        entry.height_mm
                        for entry in palette
                        if str(entry.name) == name
                    )
                )
                expected_base_z = expected_z - palette_height - 0.001
                z_tolerance = 0.005
                all_material_faces = material_triangles.get(color, [])
                out_of_z_faces = sum(
                    any(
                        vertex[2] < expected_base_z - z_tolerance
                        or vertex[2] > expected_z + z_tolerance
                        for vertex in (a, b, c)
                    )
                    for a, b, c in all_material_faces
                )
                allowed_xy = dilate(expected_mask, tolerance_pixels)
                resolution = int(expected_mask.shape[0])
                half = float(canvas_size_mm) / 2.0
                scale = resolution / float(canvas_size_mm)

                def point_allowed(point: tuple[float, float, float]) -> bool:
                    px = int(math.floor((point[0] + half) * scale))
                    py = int(math.floor((half - point[1]) * scale))
                    return (
                        0 <= px < resolution
                        and 0 <= py < resolution
                        and bool(allowed_xy[py, px])
                    )

                out_of_xy_faces = 0
                for a, b, c in all_material_faces:
                    centroid = (
                        (a[0] + b[0] + c[0]) / 3.0,
                        (a[1] + b[1] + c[1]) / 3.0,
                        (a[2] + b[2] + c[2]) / 3.0,
                    )
                    if not all(point_allowed(point) for point in (a, b, c, centroid)):
                        out_of_xy_faces += 1
                if out_of_z_faces or out_of_xy_faces:
                    raise ReleaseError(
                        "native 3MF relief material leaves its approved selector envelope for "
                        f"{name}: z_faces={out_of_z_faces}, xy_faces={out_of_xy_faces}"
                    )
                top_faces = []
                for a, b, c in all_material_faces:
                    cross_z = (b[0] - a[0]) * (c[1] - a[1]) - (
                        b[1] - a[1]
                    ) * (c[0] - a[0])
                    if cross_z <= 1e-12:
                        continue
                    if any(abs(vertex[2] - expected_z) > 0.005 for vertex in (a, b, c)):
                        continue
                    top_faces.append(
                        ((a[0], a[1]), (b[0], b[1]), (c[0], c[1]))
                    )
                triangles = np.asarray(top_faces, dtype=np.float64)
                if triangles.size == 0:
                    raise ReleaseError(
                        "native 3MF has no upward-facing triangles at the expected top Z for "
                        f"required relief colour {name}"
                    )
                triangles = triangles.reshape((-1, 3, 2))
                observed_visible_area = float(
                    sum(
                        (
                            (triangle[1][0] - triangle[0][0])
                            * (triangle[2][1] - triangle[0][1])
                            - (triangle[1][1] - triangle[0][1])
                            * (triangle[2][0] - triangle[0][0])
                        )
                        / 2.0
                        for triangle in top_faces
                    )
                )
                projected_mask = rasterize(
                    triangles,
                    expected_mask.shape[0],
                    float(canvas_size_mm),
                )
                projected_tolerant = dilate(projected_mask, tolerance_pixels)
                expected_tolerant = dilate(expected_mask, tolerance_pixels)
            except ReleaseError:
                raise
            except Exception as exc:
                raise ReleaseError(
                    f"cannot perform material spatial audit for {name}: {exc}"
                ) from exc
            missing_pixels = int((expected_mask & ~projected_tolerant).sum())
            extra_pixels = int((projected_mask & ~expected_tolerant).sum())
            intersection = int((projected_mask & expected_mask).sum())
            union = int((projected_mask | expected_mask).sum())
            raw_iou = 1.0 if union == 0 else intersection / union
            expected_area = float(expected_footprint_mm2.get(name, 0.0))
            observed_area = (
                observed_visible_area
                if expected_visible_top_area_reference is not None
                else float(projected_area.get(color, 0.0))
            )
            pixel_area = (float(canvas_size_mm) / expected_mask.shape[0]) ** 2
            area_tolerance = max(pixel_area * 8.0, expected_area * 0.005)
            area_delta = observed_area - expected_area
            direct_area_match = abs(area_delta) <= area_tolerance
            visible_top_area_by_name[name] = observed_visible_area
            spatial_record = {
                "mask": Path(expected_masks[name]).name,
                "mask_sha256": _sha256(Path(expected_masks[name])),
                "resolution_px": int(expected_mask.shape[0]),
                "expected_top_z_mm": expected_z,
                "expected_base_z_mm": expected_base_z,
                "out_of_z_envelope_triangles": out_of_z_faces,
                "out_of_xy_envelope_triangles": out_of_xy_faces,
                "projected_triangle_area_mm2": observed_area,
                "top_triangle_count": len(top_faces),
                "expected_pixels": int(expected_mask.sum()),
                "projected_pixels": int(projected_mask.sum()),
                "missing_pixels_outside_tolerance": missing_pixels,
                "extra_pixels_outside_tolerance": extra_pixels,
                "raw_iou": raw_iou,
                "tolerance_pixels": tolerance_pixels,
            }
            if expected_visible_top_area_reference is None:
                spatial_record["projected_area_tolerance_mm2"] = area_tolerance
            else:
                spatial_record.update(
                    {
                        "independent_pre_boolean_vector_area_mm2": expected_area,
                        "visible_area_delta_from_independent_vector_mm2": area_delta,
                        "legacy_pre_boolean_area_tolerance_mm2": area_tolerance,
                        "legacy_pre_boolean_direct_area_match": direct_area_match,
                        "area_comparison_basis": (
                            "final_visible_material_surface_vs_independent_pre_boolean_vector_layer"
                        ),
                        "area_comparison_disposition": (
                            "diagnostic_only_post_boolean_assembly_reference_is_authoritative"
                        ),
                    }
                )
            spatial[name] = spatial_record
            if missing_pixels or extra_pixels:
                raise ReleaseError(
                    "native 3MF material spatial audit disagrees with the named mask for "
                    f"{name}: missing={missing_pixels}, extra={extra_pixels}, iou={raw_iou:.6f}"
                )
            # The process report records each colour's independent SVG area,
            # before the relief layers are combined.  Bounded contour
            # simplification can make adjacent, otherwise disjoint palette
            # regions overlap by a small amount.  In the one-piece Boolean
            # assembly the taller/later relief owns that shared surface, so a
            # lower colour's *visible* material area is legitimately smaller
            # than its independent SVG area.  The named-mask spatial gate
            # above is the stronger proof: every intended pixel must be
            # covered within the declared vector/raster tolerance, and every
            # final material face must stay inside that same envelope.  Keep
            # the incomparable aggregate areas as diagnostics, but do not
            # reject a spatially bound material assignment because of this
            # pre-/post-Boolean basis mismatch.
            if not direct_area_match and expected_visible_top_area_reference is None:
                raise ReleaseError(
                    "native 3MF relief material projected area disagrees with its process vector "
                    f"for {name}: {observed_area:g} != {expected_area:g} mm2"
                )

    visible_top_area_binding: dict[str, Any] = {"status": "not_requested"}
    if expected_visible_top_area_reference is not None:
        if expected_masks is None or expected_top_z_mm is None:
            raise ReleaseError(
                "post-Boolean visible-area binding requires the named-mask spatial audit"
            )
        reference = expected_visible_top_area_reference
        if not isinstance(reference, dict):
            raise ReleaseError("post-Boolean visible-area reference is not passed")
        groups = reference.get("groups")
        if reference.get("status") != "passed" or not isinstance(groups, list):
            raise ReleaseError("post-Boolean visible-area reference is not passed")
        expected_names = set(expected_top_z_mm)
        grouped_names: list[str] = []
        binding_groups: list[dict[str, Any]] = []
        try:
            z_tolerance = float(reference["z_tolerance_mm"])
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise ReleaseError("post-Boolean visible-area reference has no valid Z tolerance") from exc
        for group in groups:
            if not isinstance(group, dict):
                raise ReleaseError("post-Boolean visible-area reference has an invalid group")
            names = group.get("palette_names")
            if (
                not isinstance(names, list)
                or not names
                or any(not isinstance(name, str) or not name for name in names)
                or len(set(names)) != len(names)
            ):
                raise ReleaseError("post-Boolean visible-area reference has invalid palette names")
            try:
                top_z = float(group["top_z_mm"])
                expected_visible_area = float(group["expected_visible_top_area_mm2"])
            except (KeyError, TypeError, ValueError, OverflowError) as exc:
                raise ReleaseError("post-Boolean visible-area reference has invalid dimensions") from exc
            if (
                not math.isfinite(top_z)
                or not math.isfinite(expected_visible_area)
                or expected_visible_area <= 0
            ):
                raise ReleaseError("post-Boolean visible-area reference has invalid dimensions")
            for name in names:
                if name not in expected_names or name not in visible_top_area_by_name:
                    raise ReleaseError(
                        f"post-Boolean visible-area reference names unknown palette {name!r}"
                    )
                if not math.isclose(
                    float(expected_top_z_mm[name]),
                    top_z,
                    rel_tol=0.0,
                    abs_tol=z_tolerance,
                ):
                    raise ReleaseError(
                        f"post-Boolean visible-area reference has the wrong top Z for {name}"
                    )
            grouped_names.extend(names)
            observed_visible_area = sum(visible_top_area_by_name[name] for name in names)
            area_tolerance = max(0.001, expected_visible_area * 0.00001)
            area_delta = observed_visible_area - expected_visible_area
            if abs(area_delta) > area_tolerance:
                raise ReleaseError(
                    "native 3MF visible relief material area disagrees with the hash-bound "
                    f"post-Boolean assembly for {', '.join(names)}: "
                    f"{observed_visible_area:g} != {expected_visible_area:g} mm2"
                )
            binding_groups.append(
                {
                    "palette_names": list(names),
                    "top_z_mm": top_z,
                    "expected_assembly_visible_top_area_mm2": expected_visible_area,
                    "observed_native_material_top_area_mm2": observed_visible_area,
                    "delta_mm2": area_delta,
                    "tolerance_mm2": area_tolerance,
                    "status": "passed",
                }
            )
        if len(grouped_names) != len(set(grouped_names)) or set(grouped_names) != expected_names:
            raise ReleaseError(
                "post-Boolean visible-area reference does not partition the active relief palette"
            )
        visible_top_area_binding = {
            "status": "passed",
            "reference_source": reference.get("source"),
            "reference_source_sha256": reference.get("source_sha256"),
            "external_openscad_report": reference.get("external_openscad_report"),
            "external_openscad_report_sha256": reference.get(
                "external_openscad_report_sha256"
            ),
            "scad_sha256": reference.get("scad_sha256"),
            "groups": binding_groups,
            "scope": "native_material_top_surfaces_vs_hash_bound_post_boolean_assembly",
        }
    result = {
        "status": "passed",
        "colors": {
            details["name"]: {
                "displaycolor": color,
                "role": details["role"],
                "required": details["required"],
                "active_in_process_mask": (
                    details["role"] == "base"
                    or float(expected_footprint_mm2.get(details["name"], 0.0)) > 0.0
                ),
                "triangle_count": assigned.get(color, 0),
                "projected_triangle_area_mm2": projected_area.get(color, 0.0),
                "expected_vector_footprint_mm2": float(
                    expected_footprint_mm2.get(details["name"], 0.0)
                ),
                "minimum_projected_area_ratio": 0.20,
            }
            for color, details in expected.items()
        },
        "unassigned_triangle_count": unassigned_triangles,
        "unexpected_used_colors": unexpected,
        "spatial_mask_binding": spatial,
        "scope": "final_3mf_triangle_material_assignments_and_named_relief_masks",
    }
    if expected_visible_top_area_reference is not None:
        result["visible_top_area_binding"] = visible_top_area_binding
        result["scope"] = (
            "final_3mf_triangle_material_assignments_named_relief_masks_"
            "and_hash_bound_post_boolean_visible_top_areas"
        )
    return result


def _expected_palette_footprints(config: Any) -> dict[str, float]:
    """Read the exact post-vectorisation XY footprint for each palette."""

    report_path = config.output_dir / "process-report.json"
    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseError(
            f"cannot read process report for material footprint audit: {report_path}"
        ) from exc
    mask_stats = payload.get("mask_stats") if isinstance(payload, dict) else None
    if not isinstance(mask_stats, dict):
        raise ReleaseError("process report lacks mask_stats for material footprint audit")
    footprints: dict[str, float] = {}
    for name, stats in mask_stats.items():
        if not isinstance(stats, dict):
            continue
        vector = stats.get("svg_vectorization")
        if not isinstance(vector, dict):
            raise ReleaseError(
                f"process mask_stats.{name}.svg_vectorization is missing"
            )
        try:
            area = float(vector["filled_area_mm2"])
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise ReleaseError(
                f"process mask_stats.{name}.svg_vectorization.filled_area_mm2 is invalid"
            ) from exc
        if not math.isfinite(area) or area < 0:
            raise ReleaseError(
                f"process mask_stats.{name}.svg_vectorization.filled_area_mm2 "
                "must be finite and non-negative"
            )
        footprints[str(name)] = area
    return footprints


def _projection_tolerance(config: Any) -> dict[str, float | int]:
    """Derive raster audit tolerance from the bounded vectorisation budget.

    Triangle rasterisation already needs one inclusive edge pixel. A contour
    deliberately simplified by at most half a source cell can touch the next
    raster cell, so that recorded source-space budget adds one. Printer nozzle
    size is intentionally irrelevant to this fidelity check.
    """

    report_path = config.output_dir / "process-report.json"
    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseError(
            f"cannot read process report for projection tolerance: {report_path}"
        ) from exc
    mask_stats = payload.get("mask_stats")
    if not isinstance(mask_stats, dict):
        raise ReleaseError("process report lacks mask_stats for projection tolerance")
    maximum = 0.0
    maximum_mm = 0.0
    for name, mask_details in mask_stats.items():
        stats = (
            mask_details.get("svg_vectorization")
            if isinstance(mask_details, dict)
            else None
        )
        if not isinstance(stats, dict):
            raise ReleaseError(
                f"process mask_stats.{name}.svg_vectorization is invalid"
            )
        try:
            deviation_px = float(stats["simplification_tolerance_px"])
            deviation_mm = float(stats["maximum_deviation_mm"])
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise ReleaseError(
                f"process mask_stats.{name}.svg_vectorization lacks a valid deviation budget"
            ) from exc
        if (
            not math.isfinite(deviation_px)
            or not math.isfinite(deviation_mm)
            or deviation_px < 0
            or deviation_px > 0.5 + 1e-9
            or deviation_mm < 0
            or deviation_mm > 0.10 + 1e-9
        ):
            raise ReleaseError(
                f"process mask_stats.{name}.svg_vectorization exceeds the bounded contour budget"
            )
        maximum = max(maximum, deviation_px)
        maximum_mm = max(maximum_mm, deviation_mm)
    return {
        "tolerance_pixels": 1 + int(math.ceil(maximum - 1e-12)),
        "triangle_rasterization_pixels": 1,
        "vectorization_budget_pixels": maximum,
        "vectorization_budget_mm": maximum_mm,
    }


def _audit_native_bounds(
    verification: dict[str, Any],
    geometry_report: dict[str, Any],
    *,
    tolerance_mm: float = 0.005,
) -> dict[str, Any]:
    """Bind final one-piece package dimensions to the current geometry report."""

    model = verification.get("model")
    bounds = model.get("bounds") if isinstance(model, dict) else None
    mechanical = geometry_report.get("mechanical")
    relief_entries = geometry_report.get("relief_entries")
    if not isinstance(bounds, dict) or not isinstance(mechanical, dict):
        raise ReleaseError("native 3MF or geometry report lacks auditable bounds")
    if not isinstance(relief_entries, list):
        raise ReleaseError("geometry report lacks relief_entries for native bounds audit")
    try:
        outer = float(geometry_report["outer_diameter_mm"])
        total_height = float(mechanical["total_height_mm"])
        relief_height = max(
            (float(entry["height_mm"]) for entry in relief_entries if isinstance(entry, dict)),
            default=0.0,
        )
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise ReleaseError("geometry report contains invalid native dimensions") from exc
    if not all(math.isfinite(value) for value in (outer, total_height, relief_height)):
        raise ReleaseError("geometry report contains non-finite native dimensions")
    if outer <= 0 or total_height <= 0 or relief_height < 0:
        raise ReleaseError("geometry report contains non-positive native dimensions")
    expected = {
        "min_mm": [-outer / 2.0, -outer / 2.0, 0.0],
        "max_mm": [
            outer / 2.0,
            outer / 2.0,
            total_height + relief_height + (0.001 if relief_height else 0.0),
        ],
    }
    expected["size_mm"] = [
        expected["max_mm"][axis] - expected["min_mm"][axis] for axis in range(3)
    ]
    observed: dict[str, list[float]] = {}
    for field in ("min_mm", "max_mm", "size_mm"):
        values = bounds.get(field)
        if not isinstance(values, list) or len(values) != 3:
            raise ReleaseError(f"native 3MF bounds.{field} is invalid")
        try:
            observed[field] = [float(value) for value in values]
        except (TypeError, ValueError, OverflowError) as exc:
            raise ReleaseError(f"native 3MF bounds.{field} is invalid") from exc
        if any(not math.isfinite(value) for value in observed[field]):
            raise ReleaseError(f"native 3MF bounds.{field} is non-finite")
        if any(
            not math.isclose(
                observed[field][axis],
                expected[field][axis],
                rel_tol=0.0,
                abs_tol=tolerance_mm,
            )
            for axis in range(3)
        ):
            raise ReleaseError(
                f"native 3MF bounds.{field} disagrees with current geometry report: "
                f"{observed[field]!r} != {expected[field]!r}"
            )
    return {
        "status": "passed",
        "observed": observed,
        "expected": expected,
        "tolerance_mm": tolerance_mm,
        "scope": "final_native_world_bounds_vs_current_geometry_report",
    }


def _expected_relief_top_z(geometry_report: dict[str, Any]) -> dict[str, float]:
    """Return the exact OpenSCAD top plane for every emitted relief selector."""

    mechanical = geometry_report.get("mechanical")
    entries = geometry_report.get("relief_entries")
    if not isinstance(mechanical, dict) or not isinstance(entries, list):
        raise ReleaseError("geometry report lacks relief Z inputs")
    try:
        total_height = float(mechanical["total_height_mm"])
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise ReleaseError("geometry report has invalid total_height_mm") from exc
    result: dict[str, float] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise ReleaseError("geometry report has an invalid relief entry")
        try:
            name = str(entry["name"])
            height = float(entry["height_mm"])
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise ReleaseError("geometry report has an invalid relief entry") from exc
        if not name or not math.isfinite(height) or height <= 0:
            raise ReleaseError("geometry report has an invalid relief name/height")
        result[name] = total_height + height + 0.001
    return result


def _assembly_visible_top_area_reference(
    config: Any,
    geometry_report: dict[str, Any],
    expected_top_z_mm: dict[str, float],
    *,
    z_tolerance_mm: float = 0.005,
) -> dict[str, Any]:
    """Measure post-Boolean relief top areas from the fresh assembly STL.

    Each process SVG is an independent pre-Boolean layer. Adjacent palette
    regions can overlap slightly after bounded contour simplification, and
    the one-piece CSG correctly gives that shared surface to one of the
    layers. The assembly STL is therefore the exact uncoloured geometry
    reference for the *visible* top area later assigned materials in the
    native 3MF. Its path and hash are rebound to the current OpenSCAD report
    before any triangle is trusted.
    """

    if (
        not math.isfinite(float(z_tolerance_mm))
        or z_tolerance_mm <= 0
        or z_tolerance_mm > 0.05
    ):
        raise ReleaseError("assembly visible-area Z tolerance is invalid")
    if not expected_top_z_mm:
        raise ReleaseError("assembly visible-area audit has no relief top planes")

    model_dir = (config.output_dir / "model").resolve()
    report_path = model_dir / "external-openscad-report.json"
    try:
        external = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseError(
            f"cannot read OpenSCAD provenance for assembly visible-area audit: {report_path}"
        ) from exc
    if (
        not isinstance(external, dict)
        or external.get("schema_version") != 1
        or external.get("adapter") != "openscad"
        or external.get("status") != "passed"
        or external.get("backend") != "Manifold"
    ):
        raise ReleaseError("assembly visible-area audit requires a passed OpenSCAD report")
    geometry_production_status = geometry_report.get("production_status")
    external_production_status = external.get("production_status")
    if (
        geometry_production_status is not None
        and external_production_status != geometry_production_status
    ):
        raise ReleaseError(
            "OpenSCAD assembly report does not inherit the current production status"
        )

    scad_name = geometry_report.get("scad_path")
    scad_sha256 = geometry_report.get("scad_sha256")
    if not isinstance(scad_name, str) or not isinstance(scad_sha256, str):
        raise ReleaseError("geometry report lacks the current SCAD provenance")
    scad_path = (model_dir / scad_name).resolve()
    try:
        scad_path.relative_to(model_dir)
    except ValueError as exc:
        raise ReleaseError("geometry report SCAD path leaves the current model directory") from exc
    if (
        not scad_path.is_file()
        or _sha256(scad_path).lower() != scad_sha256.lower()
        or external.get("model") != scad_path.name
        or str(external.get("model_sha256", "")).lower() != scad_sha256.lower()
    ):
        raise ReleaseError("OpenSCAD assembly provenance does not match the current geometry")

    parts = external.get("parts")
    assembly_record = parts.get("assembly") if isinstance(parts, dict) else None
    if (
        not isinstance(assembly_record, dict)
        or assembly_record.get("status") != "passed"
        or assembly_record.get("returncode") != 0
    ):
        raise ReleaseError("OpenSCAD report has no passed assembly selector")
    declared_path = assembly_record.get("path")
    declared_hash = assembly_record.get("sha256")
    if not isinstance(declared_path, str) or not isinstance(declared_hash, str):
        raise ReleaseError("OpenSCAD assembly selector lacks a path or hash")
    relative_assembly = Path(declared_path)
    if relative_assembly.is_absolute():
        raise ReleaseError("OpenSCAD assembly selector path must be model-relative")
    assembly_path = (model_dir / relative_assembly).resolve()
    expected_path = (
        model_dir / "mesh" / f"{config.job_slug}-assembly.stl"
    ).resolve()
    try:
        assembly_path.relative_to(model_dir)
    except ValueError as exc:
        raise ReleaseError("OpenSCAD assembly selector leaves the current model directory") from exc
    if assembly_path != expected_path:
        raise ReleaseError("OpenSCAD assembly selector is not the current job assembly")
    if not assembly_path.is_file() or _sha256(assembly_path).lower() != declared_hash.lower():
        raise ReleaseError("OpenSCAD assembly STL differs from its passed provenance record")
    format_check = assembly_record.get("format_check")
    canonicalization = (
        format_check.get("canonicalization") if isinstance(format_check, dict) else None
    )
    relief_check = (
        format_check.get("assembly_relief_check") if isinstance(format_check, dict) else None
    )
    if (
        not isinstance(format_check, dict)
        or format_check.get("size_exact") is not True
        or not isinstance(canonicalization, dict)
        or canonicalization.get("status") != "passed"
        or not isinstance(relief_check, dict)
        or relief_check.get("status") != "passed"
    ):
        raise ReleaseError("OpenSCAD assembly selector lacks passed canonical format checks")

    try:
        data = assembly_path.read_bytes()
        if len(data) < 84:
            raise ReleaseError("OpenSCAD assembly STL is truncated")
        triangle_count = struct.unpack_from("<I", data, 80)[0]
    except (OSError, struct.error) as exc:
        raise ReleaseError(f"cannot read OpenSCAD assembly STL: {assembly_path}") from exc
    if (
        triangle_count <= 0
        or triangle_count > 2_000_000
        or len(data) != 84 + triangle_count * 50
        or format_check.get("binary_triangle_count") != triangle_count
        or canonicalization.get("binary_triangle_count") != triangle_count
    ):
        raise ReleaseError("OpenSCAD assembly STL is not a bounded canonical binary STL")

    groups: list[dict[str, Any]] = []
    for name, raw_z in sorted(expected_top_z_mm.items(), key=lambda item: (item[1], item[0])):
        try:
            top_z = float(raw_z)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ReleaseError("assembly visible-area audit has an invalid relief top Z") from exc
        if not name or not math.isfinite(top_z):
            raise ReleaseError("assembly visible-area audit has an invalid relief top Z")
        matching = next(
            (
                group
                for group in groups
                if math.isclose(group["top_z_mm"], top_z, rel_tol=0.0, abs_tol=1e-9)
            ),
            None,
        )
        if matching is None:
            groups.append(
                {
                    "top_z_mm": top_z,
                    "palette_names": [name],
                    "upward_horizontal_triangle_count": 0,
                    "expected_visible_top_area_mm2": 0.0,
                }
            )
        else:
            matching["palette_names"].append(name)
    for left, right in zip(groups, groups[1:]):
        if right["top_z_mm"] - left["top_z_mm"] <= 2.0 * z_tolerance_mm:
            raise ReleaseError(
                "distinct relief top planes are too close for the assembly visible-area audit"
            )

    for index in range(triangle_count):
        offset = 84 + index * 50
        try:
            values = struct.unpack_from("<12f", data, offset)
        except struct.error as exc:  # pragma: no cover - exact length checked above
            raise ReleaseError("OpenSCAD assembly STL has a malformed triangle") from exc
        a = values[3:6]
        b = values[6:9]
        c = values[9:12]
        coordinates = (*a, *b, *c)
        if any(not math.isfinite(float(value)) for value in coordinates):
            raise ReleaseError("OpenSCAD assembly STL has a non-finite vertex")
        z_values = (float(a[2]), float(b[2]), float(c[2]))
        if max(z_values) - min(z_values) > 1e-5:
            continue
        matching_groups = [
            group
            for group in groups
            if all(
                abs(float(vertex[2]) - float(group["top_z_mm"])) <= z_tolerance_mm
                for vertex in (a, b, c)
            )
        ]
        if len(matching_groups) > 1:
            raise ReleaseError("one assembly face ambiguously matches multiple relief top planes")
        if not matching_groups:
            continue
        cross_z = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (
            c[0] - a[0]
        )
        if cross_z <= 1e-12:
            continue
        group = matching_groups[0]
        group["upward_horizontal_triangle_count"] += 1
        group["expected_visible_top_area_mm2"] += cross_z / 2.0

    for group in groups:
        if (
            group["upward_horizontal_triangle_count"] <= 0
            or not math.isfinite(group["expected_visible_top_area_mm2"])
            or group["expected_visible_top_area_mm2"] <= 0
        ):
            raise ReleaseError(
                "OpenSCAD assembly STL has no positive visible top area for relief plane "
                f"{group['top_z_mm']:g} mm"
            )
        group["palette_names"].sort()

    return {
        "status": "passed",
        "source": _portable(assembly_path, config.config_path.parent),
        "source_sha256": declared_hash.lower(),
        "external_openscad_report": _portable(report_path, config.config_path.parent),
        "external_openscad_report_sha256": _sha256(report_path),
        "scad_sha256": scad_sha256.lower(),
        "triangle_count": triangle_count,
        "z_tolerance_mm": float(z_tolerance_mm),
        "groups": groups,
        "scope": "hash_bound_post_boolean_assembly_upward_relief_top_surfaces",
    }


def _write_mask_voxel_relief(
    mask_path: Path,
    output: Path,
    *,
    face_diameter_mm: float,
    base_z_mm: float,
    height_mm: float,
) -> dict[str, Any]:
    """Extrude an approved binary mask as a closed union-of-pixels STL.

    Each occupied pixel contributes top/bottom facets and only its exposed
    side facets. Shared internal faces are omitted, so unlike a collection of
    touching SVG rectangles this representation is edge-manifold after STL's
    coordinate welding. The 0.001 mm overlap matches the canonical SCAD.
    """

    from PIL import Image

    try:
        image = Image.open(mask_path).convert("L")
        image.load()
    except (OSError, ValueError) as exc:
        raise ReleaseError(f"cannot decode Bambu relief mask {mask_path}: {exc}") from exc
    width, height = image.size
    if width < 2 or width != height:
        raise ReleaseError(f"Bambu relief mask must be square: {mask_path}")
    data = image.tobytes()
    values = set(data)
    if not values.issubset({0, 255}):
        raise ReleaseError(f"Bambu relief mask is not strict binary 0/255: {mask_path}")
    occupied = bytearray(1 if value == 255 else 0 for value in data)
    occupied_pixels = sum(occupied)
    if occupied_pixels <= 0:
        raise ReleaseError(f"Bambu relief mask is empty: {mask_path}")
    face = float(face_diameter_mm)
    z0 = float(base_z_mm)
    z1 = z0 + float(height_mm) + 0.001
    if not all(math.isfinite(value) for value in (face, z0, z1)) or face <= 0 or z1 <= z0:
        raise ReleaseError("Bambu relief voxel dimensions are invalid")
    step = face / width
    half = face / 2.0
    chamfer = min(0.005, step * 0.1)

    def is_occupied(row: int, column: int) -> bool:
        return (
            0 <= row < height
            and 0 <= column < width
            and bool(occupied[row * width + column])
        )

    def polygon_for(row: int, column: int) -> list[tuple[float, float]]:
        x_low = -half + column * step
        x_high = -half + (column + 1) * step
        y_high = half - row * step
        y_low = half - (row + 1) * step
        clip_bottom_left = (
            is_occupied(row + 1, column - 1)
            and not is_occupied(row, column - 1)
            and not is_occupied(row + 1, column)
        )
        clip_bottom_right = (
            is_occupied(row + 1, column + 1)
            and not is_occupied(row, column + 1)
            and not is_occupied(row + 1, column)
        )
        clip_top_right = (
            is_occupied(row - 1, column + 1)
            and not is_occupied(row, column + 1)
            and not is_occupied(row - 1, column)
        )
        clip_top_left = (
            is_occupied(row - 1, column - 1)
            and not is_occupied(row, column - 1)
            and not is_occupied(row - 1, column)
        )
        points: list[tuple[float, float]] = []
        points.extend(
            [(x_low + chamfer, y_low), (x_low, y_low + chamfer)]
            if clip_bottom_left
            else [(x_low, y_low)]
        )
        # Restore counter-clockwise order by walking bottom, right, top, left.
        points = points[:1]
        points.extend(
            [(x_high - chamfer, y_low), (x_high, y_low + chamfer)]
            if clip_bottom_right
            else [(x_high, y_low)]
        )
        points.extend(
            [(x_high, y_high - chamfer), (x_high - chamfer, y_high)]
            if clip_top_right
            else [(x_high, y_high)]
        )
        points.extend(
            [(x_low + chamfer, y_high), (x_low, y_high - chamfer)]
            if clip_top_left
            else [(x_low, y_high)]
        )
        if clip_bottom_left:
            points.append((x_low, y_low + chamfer))
        return points

    def boundary_edge_is_exposed(
        row: int,
        column: int,
        left: tuple[float, float],
        right: tuple[float, float],
    ) -> bool:
        x_low = -half + column * step
        x_high = -half + (column + 1) * step
        y_high = half - row * step
        y_low = half - (row + 1) * step
        if left[0] == right[0] == x_low:
            return not is_occupied(row, column - 1)
        if left[0] == right[0] == x_high:
            return not is_occupied(row, column + 1)
        if left[1] == right[1] == y_high:
            return not is_occupied(row - 1, column)
        if left[1] == right[1] == y_low:
            return not is_occupied(row + 1, column)
        return True

    triangle_count = 0
    exposed_sides = 0
    chamfered_corners = 0
    for row in range(height):
        for column in range(width):
            if not is_occupied(row, column):
                continue
            polygon = polygon_for(row, column)
            chamfered_corners += len(polygon) - 4
            triangle_count += 2 * (len(polygon) - 2)
            for index, point in enumerate(polygon):
                following = polygon[(index + 1) % len(polygon)]
                if boundary_edge_is_exposed(row, column, point, following):
                    exposed_sides += 1
                    triangle_count += 2
    if triangle_count <= 0 or triangle_count > 2_000_000:
        raise ReleaseError(
            f"Bambu relief voxel mesh has unsafe triangle count {triangle_count}"
        )

    def facet(
        stream: Any,
        normal: tuple[float, float, float],
        a: tuple[float, float, float],
        b: tuple[float, float, float],
        c: tuple[float, float, float],
    ) -> None:
        stream.write(struct.pack("<12fH", *normal, *a, *b, *c, 0))

    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix=f".{output.name}.", suffix=".tmp", dir=output.parent, delete=False
    ) as handle:
        temporary = Path(handle.name)
        handle.write(b"lens-cap-pipeline binary-mask relief"[:80].ljust(80, b"\0"))
        handle.write(struct.pack("<I", triangle_count))
        for row in range(height):
            for column in range(width):
                offset = row * width + column
                if not occupied[offset]:
                    continue
                polygon = polygon_for(row, column)
                top = [(x, y, z1) for x, y in polygon]
                bottom = [(x, y, z0) for x, y in polygon]
                for index in range(1, len(polygon) - 1):
                    facet(handle, (0.0, 0.0, 1.0), top[0], top[index], top[index + 1])
                    facet(
                        handle,
                        (0.0, 0.0, -1.0),
                        bottom[0],
                        bottom[index + 1],
                        bottom[index],
                    )
                for index, point in enumerate(polygon):
                    following = polygon[(index + 1) % len(polygon)]
                    if not boundary_edge_is_exposed(row, column, point, following):
                        continue
                    p0 = (point[0], point[1], z0)
                    q0 = (following[0], following[1], z0)
                    p1 = (point[0], point[1], z1)
                    q1 = (following[0], following[1], z1)
                    dx = following[0] - point[0]
                    dy = following[1] - point[1]
                    edge_length = math.hypot(dx, dy)
                    normal = (dy / edge_length, -dx / edge_length, 0.0)
                    facet(handle, normal, p0, q0, q1)
                    facet(handle, normal, p0, q1, p1)
    try:
        os.replace(temporary, output)
    finally:
        if temporary.exists():
            temporary.unlink()
    return {
        "status": "passed",
        "generator": "binary_mask_union_of_pixels_v1",
        "mask": mask_path.name,
        "mask_sha256": _sha256(mask_path),
        "occupied_pixels": occupied_pixels,
        "exposed_pixel_sides": exposed_sides,
        "chamfered_diagonal_contact_corners": chamfered_corners,
        "diagonal_contact_chamfer_mm": chamfer,
        "triangles": triangle_count,
        "bounds": {
            "size_mm": [face, face, z1 - z0],
            "z_mm": [z0, z1],
        },
        "sha256": _sha256(output),
    }


def _bambu_part_specs(config: Any) -> list[dict[str, Any]]:
    """Return the aligned base/relief STL set and ordered AMS palette."""

    process_path = config.output_dir / "process-report.json"
    try:
        process_payload = json.loads(process_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseError(f"cannot read process report for Bambu parts: {process_path}") from exc
    mask_stats = process_payload.get("mask_stats") if isinstance(process_payload, dict) else None
    if not isinstance(mask_stats, dict):
        raise ReleaseError("process report lacks mask_stats for Bambu parts")
    bases = [entry for entry in config.palette if str(entry.role) == "base"]
    if len(bases) != 1:
        raise ReleaseError("Bambu multipart export requires exactly one base palette entry")
    entries = [bases[0]] + [
        entry
        for entry in config.palette
        if str(entry.role) == "relief"
        and isinstance(mask_stats.get(str(entry.name)), dict)
        and int(mask_stats[str(entry.name)].get("pixels", 0)) > 0
    ]
    mesh_dir = config.output_dir / "model" / "mesh"
    specs: list[dict[str, Any]] = []
    for position, entry in enumerate(entries, start=1):
        suffix = "base" if str(entry.role) == "base" else f"{entry.name}_relief"
        path = mesh_dir / f"{config.job_slug}-{suffix}.stl"
        if not path.is_file() or path.stat().st_size <= 84:
            raise ReleaseError(f"Bambu multipart STL is missing or empty: {path}")
        rgb = tuple(int(value) for value in entry.rgb)
        specs.append(
            {
                "name": str(entry.name),
                "role": str(entry.role),
                "selector": "base" if str(entry.role) == "base" else f"{entry.name}_relief",
                "path": path,
                "filename": path.name,
                "extruder": position,
                "color": "#%02X%02X%02X" % rgb,
                "height_mm": float(entry.height_mm),
            }
        )
    if len(specs) < 2:
        raise ReleaseError("Bambu multipart export requires a base and at least one relief part")
    return specs


def _xml_attribute(node: ET.Element, name: str) -> str | None:
    for key, value in node.attrib.items():
        if key == name or key.rsplit("}", 1)[-1] == name:
            return value
    return None


def _numbers(value: Any, count: int, field: str) -> list[float]:
    if not isinstance(value, str):
        raise ReleaseError(f"{field} must be a {count}-number transform")
    try:
        result = [float(item) for item in value.split()]
    except (TypeError, ValueError, OverflowError) as exc:
        raise ReleaseError(f"{field} contains invalid numbers") from exc
    if len(result) != count or not all(math.isfinite(item) for item in result):
        raise ReleaseError(f"{field} must contain exactly {count} finite numbers")
    return result


def _mesh_bounds_from_object(obj: ET.Element, label: str) -> dict[str, list[float]]:
    mesh = next((node for node in obj if node.tag.rsplit("}", 1)[-1] == "mesh"), None)
    if mesh is None:
        raise ReleaseError(f"Bambu part object {label} has no direct mesh")
    vertices_node = next(
        (node for node in mesh if node.tag.rsplit("}", 1)[-1] == "vertices"), None
    )
    if vertices_node is None:
        raise ReleaseError(f"Bambu part object {label} has no vertices")
    vertices: list[tuple[float, float, float]] = []
    for node in vertices_node:
        if node.tag.rsplit("}", 1)[-1] != "vertex":
            continue
        try:
            vertex = tuple(float(_xml_attribute(node, axis) or "nan") for axis in "xyz")
        except (TypeError, ValueError, OverflowError) as exc:
            raise ReleaseError(f"Bambu part object {label} has an invalid vertex") from exc
        if not all(math.isfinite(value) for value in vertex):
            raise ReleaseError(f"Bambu part object {label} has a non-finite vertex")
        vertices.append(vertex)
    if not vertices:
        raise ReleaseError(f"Bambu part object {label} has an empty mesh")
    minimum = [min(vertex[axis] for vertex in vertices) for axis in range(3)]
    maximum = [max(vertex[axis] for vertex in vertices) for axis in range(3)]
    return {
        "min_mm": minimum,
        "max_mm": maximum,
        "size_mm": [maximum[axis] - minimum[axis] for axis in range(3)],
    }


def _surface_fingerprint(
    triangles: Any,
    *,
    translation: Sequence[float] = (0.0, 0.0, 0.0),
) -> dict[str, Any]:
    """Return an order/orientation-independent quantized triangle multiset hash.

    Bambu Studio recentres imported STL vertices and restores their original
    position with a component transform. A byte hash therefore cannot bind
    the project back to the audited STL. This fingerprint hashes world-space
    triangle coordinates at 0.0001 mm precision and ignores only vertex/facet
    ordering. It is a geometry integrity check, not a file-identity claim.
    """

    if len(translation) != 3 or not all(math.isfinite(float(v)) for v in translation):
        raise ReleaseError("surface fingerprint translation is invalid")
    scale = 10_000.0
    mask = (1 << 64) - 1
    xor_left = xor_right = sum_left = sum_right = 0
    count = 0
    for triangle in triangles:
        if len(triangle) != 3:
            raise ReleaseError("surface fingerprint found a malformed triangle")
        points: list[tuple[int, int, int]] = []
        for point in triangle:
            if len(point) != 3:
                raise ReleaseError("surface fingerprint found a malformed vertex")
            coordinates = tuple(
                float(point[axis]) + float(translation[axis]) for axis in range(3)
            )
            if not all(math.isfinite(value) for value in coordinates):
                raise ReleaseError("surface fingerprint found a non-finite vertex")
            points.append(tuple(int(round(value * scale)) for value in coordinates))
        points.sort()
        payload = struct.pack("<9q", *(value for point in points for value in point))
        digest = hashlib.blake2b(
            payload,
            digest_size=16,
            person=b"lenscap-mesh-v1",
        ).digest()
        left, right = struct.unpack("<QQ", digest)
        xor_left ^= left
        xor_right ^= right
        sum_left = (sum_left + left) & mask
        sum_right = (sum_right + right) & mask
        count += 1
    if count <= 0:
        raise ReleaseError("surface fingerprint found no triangles")
    combined = struct.pack("<5Q", count, xor_left, xor_right, sum_left, sum_right)
    return {
        "algorithm": "blake2b128-triangle-multiset-xor-sum-v1",
        "coordinate_quantum_mm": 0.0001,
        "triangle_count": count,
        "sha256": hashlib.sha256(combined).hexdigest(),
    }


def _binary_stl_surface_fingerprint(path: Path) -> dict[str, Any]:
    """Fingerprint one canonical binary STL emitted by this pipeline."""

    try:
        data = path.read_bytes()
        if len(data) < 84:
            raise ReleaseError(f"audited Bambu STL is truncated: {path}")
        triangle_count = struct.unpack_from("<I", data, 80)[0]
    except (OSError, struct.error) as exc:
        raise ReleaseError(f"cannot read audited Bambu STL {path}: {exc}") from exc
    if triangle_count <= 0 or len(data) != 84 + triangle_count * 50:
        raise ReleaseError(f"audited Bambu STL is not canonical binary STL: {path}")

    def triangles() -> Any:
        for index in range(triangle_count):
            offset = 84 + index * 50
            try:
                values = struct.unpack_from("<12f", data, offset)
            except struct.error as exc:  # pragma: no cover - length check guards this
                raise ReleaseError(f"audited Bambu STL is malformed: {path}") from exc
            yield (values[3:6], values[6:9], values[9:12])

    return _surface_fingerprint(triangles())


def _mesh_geometry_from_object(
    obj: ET.Element,
    label: str,
    *,
    translation: Sequence[float],
) -> dict[str, Any]:
    """Read one Bambu object mesh and bind its world-space surface geometry."""

    mesh = next((node for node in obj if node.tag.rsplit("}", 1)[-1] == "mesh"), None)
    if mesh is None:
        raise ReleaseError(f"Bambu part object {label} has no direct mesh")
    vertices_node = next(
        (node for node in mesh if node.tag.rsplit("}", 1)[-1] == "vertices"), None
    )
    triangles_node = next(
        (node for node in mesh if node.tag.rsplit("}", 1)[-1] == "triangles"), None
    )
    if vertices_node is None or triangles_node is None:
        raise ReleaseError(f"Bambu part object {label} has an incomplete mesh")
    vertices: list[tuple[float, float, float]] = []
    for node in vertices_node:
        if node.tag.rsplit("}", 1)[-1] != "vertex":
            continue
        try:
            vertex = tuple(float(_xml_attribute(node, axis) or "nan") for axis in "xyz")
        except (TypeError, ValueError, OverflowError) as exc:
            raise ReleaseError(f"Bambu part object {label} has an invalid vertex") from exc
        if not all(math.isfinite(value) for value in vertex):
            raise ReleaseError(f"Bambu part object {label} has a non-finite vertex")
        vertices.append(vertex)
    if not vertices:
        raise ReleaseError(f"Bambu part object {label} has an empty mesh")
    triangle_nodes = [
        node for node in triangles_node if node.tag.rsplit("}", 1)[-1] == "triangle"
    ]
    signed_volume = 0.0

    def triangles() -> Any:
        nonlocal signed_volume
        for node in triangle_nodes:
            try:
                indices = tuple(
                    int(_xml_attribute(node, name) or "")
                    for name in ("v1", "v2", "v3")
                )
            except (TypeError, ValueError, OverflowError) as exc:
                raise ReleaseError(f"Bambu part object {label} has an invalid triangle") from exc
            if len(set(indices)) != 3 or any(
                index < 0 or index >= len(vertices) for index in indices
            ):
                raise ReleaseError(
                    f"Bambu part object {label} has a degenerate or invalid triangle"
                )
            points = tuple(vertices[index] for index in indices)
            a, b, c = points
            signed_volume += (
                a[0] * (b[1] * c[2] - b[2] * c[1])
                - a[1] * (b[0] * c[2] - b[2] * c[0])
                + a[2] * (b[0] * c[1] - b[1] * c[0])
            ) / 6.0
            yield points

    fingerprint = _surface_fingerprint(triangles(), translation=translation)
    return {
        "bounds": _mesh_bounds_from_object(obj, label),
        "triangles": len(triangle_nodes),
        "absolute_signed_volume_mm3": abs(signed_volume),
        "surface_fingerprint": fingerprint,
    }


def _audit_bambu_mesh_report(verification: dict[str, Any]) -> dict[str, Any]:
    model = verification.get("model") if isinstance(verification, dict) else None
    if not isinstance(model, dict):
        raise ReleaseError("Bambu verification lacks model geometry")
    zero_fields = (
        "boundary_edges",
        "nonmanifold_edges",
        "inconsistent_orientation_edges",
        "unreferenced_vertices",
        "zero_area_triangles",
        "duplicate_triangles",
        "zero_volume_components",
    )
    bad: dict[str, int] = {}
    for field in zero_fields:
        try:
            value = int(model.get(field, -1))
        except (TypeError, ValueError, OverflowError) as exc:
            raise ReleaseError(f"Bambu verification field {field} is invalid") from exc
        if value != 0:
            bad[field] = value
    if bad:
        raise ReleaseError(f"Bambu project mesh is not solid: {bad}")
    if int(model.get("volume_components", 0)) <= 0:
        raise ReleaseError("Bambu project has no positive-volume component")
    return {
        "status": "passed",
        "scope": "all_bambu_part_meshes_closed_oriented_and_positive_volume",
        **{field: 0 for field in zero_fields},
        "positive_volume_components": int(model["volume_components"]),
        # Volume is a diagnostic after the positive-volume gate above. The
        # summation order inside Python's mesh verifier can differ by a few
        # ULPs across interpreter/platform builds, so publish fixed precision
        # far below any geometry tolerance without weakening the mesh checks.
        "absolute_volume_mm3": round(
            float(model.get("absolute_volume_mm3", 0.0)), 9
        ),
    }


def _prepare_closed_bambu_parts(
    config: Any,
    scad: Path,
    specs: Sequence[dict[str, Any]],
    *,
    mechanical: dict[str, Any],
    destination: Path,
    openscad: str,
    timeout: int,
) -> list[dict[str, Any]]:
    """Re-export every Bambu colour part through sanitized native Core 3MF."""

    destination.mkdir(parents=True, exist_ok=True)
    prepared: list[dict[str, Any]] = []
    for spec in specs:
        output = destination / str(spec["filename"])
        if str(spec["role"]) == "base":
            command = [
                sys.executable,
                str(ADAPTER),
                "openscad-stl",
                str(scad),
                str(output),
                "--render-part",
                "base",
                "--openscad",
                openscad,
                "--timeout",
                str(timeout),
                "--require-single-volume",
            ]
            run = _run(command, timeout=timeout + 120)
            if run["returncode"] != 0:
                raise ReleaseError(
                    "closed Bambu base export failed "
                    f"(rc={run['returncode']})\n{run['stdout'][-1200:]}\n{run['stderr'][-1200:]}"
                )
            generation = _last_json(run["stdout"])
        else:
            try:
                total_height = float(mechanical["total_height_mm"])
            except (KeyError, TypeError, ValueError, OverflowError) as exc:
                raise ReleaseError("geometry report lacks total_height_mm for Bambu relief") from exc
            generation = _write_mask_voxel_relief(
                config.output_dir / "masks" / f"{spec['name']}.png",
                output,
                face_diameter_mm=float(config.face_diameter_mm),
                base_z_mm=total_height,
                height_mm=float(spec["height_mm"]),
            )
            command = ["internal:binary_mask_union_of_pixels_v1"]

        verify_command = [sys.executable, str(ADAPTER), "verify-stl", str(output)]
        if str(spec["role"]) == "base":
            verify_command.append("--require-single-volume")
        verify_run = _run(verify_command, timeout=timeout + 120)
        if verify_run["returncode"] != 0:
            raise ReleaseError(
                f"closed Bambu part audit failed for {spec['selector']!r}: "
                f"{verify_run['stdout'][-1200:]}{verify_run['stderr'][-1200:]}"
            )
        mesh = _last_json(verify_run["stdout"])
        if (
            mesh.get("status") != "passed"
            or not output.is_file()
            or mesh.get("solid") is not True
            or int(mesh.get("boundary_edges", -1)) != 0
            or int(mesh.get("nonmanifold_edges", -1)) != 0
            or int(mesh.get("inconsistent_orientation_edges", -1)) != 0
            or int(mesh.get("zero_volume_components", -1)) != 0
        ):
            raise ReleaseError(
                f"closed Bambu part audit failed for {spec['selector']!r}"
            )
        prepared.append(
            {
                **spec,
                "path": output,
                "source_path": spec["path"],
                "sha256": _sha256(output),
                "mesh": {
                    **mesh,
                    "surface_fingerprint": _binary_stl_surface_fingerprint(output),
                },
                "generation": generation,
                "preparation_command": command,
                "preparation_manifest": Path(f"{output}.manifest.json"),
            }
        )
    return prepared


def _audit_bambu_project(
    path: Path,
    expected_parts: Sequence[dict[str, Any]],
    *,
    nozzle_mm: float,
) -> dict[str, Any]:
    """Require a real multipart/extruder Bambu project, not a flattened STL."""

    try:
        with zipfile.ZipFile(path) as archive:
            core_model = ET.fromstring(archive.read("3D/3dmodel.model"))
            object_model = ET.fromstring(archive.read("3D/Objects/object_1.model"))
            model_settings = ET.fromstring(
                archive.read("Metadata/model_settings.config")
            )
            project_settings = json.loads(
                archive.read("Metadata/project_settings.config").decode("utf-8")
            )
    except (
        OSError,
        KeyError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        zipfile.BadZipFile,
        ET.ParseError,
    ) as exc:
        raise ReleaseError(f"cannot inspect Bambu multipart project: {exc}") from exc
    if not isinstance(project_settings, dict):
        raise ReleaseError("Bambu project settings are not a JSON object")
    settings_objects = [node for node in model_settings if node.tag == "object"]
    if len(settings_objects) != 1:
        raise ReleaseError(
            "Bambu model_settings must contain exactly one assembly object"
        )
    settings_object_id = str(settings_objects[0].attrib.get("id", ""))
    observed: list[dict[str, Any]] = []
    for part in settings_objects[0].iter("part"):
        metadata = {
            child.attrib.get("key"): child.attrib.get("value")
            for child in part
            if child.tag == "metadata" and child.attrib.get("key")
        }
        observed.append(
            {
                "id": str(part.attrib.get("id", "")),
                "subtype": str(part.attrib.get("subtype", "")),
                "name": metadata.get("name"),
                "extruder": metadata.get("extruder"),
                "matrix": metadata.get("matrix"),
                "source_offset_x": metadata.get("source_offset_x"),
                "source_offset_y": metadata.get("source_offset_y"),
                "source_offset_z": metadata.get("source_offset_z"),
            }
        )
    if len(observed) != len(expected_parts):
        raise ReleaseError(
            "Bambu project part count disagrees with base/relief inputs: "
            f"{len(observed)} != {len(expected_parts)}"
        )
    observed_ids = [item["id"] for item in observed]
    if any(not value or not value.isdecimal() for value in observed_ids) or len(
        set(observed_ids)
    ) != len(observed_ids):
        raise ReleaseError(
            "Bambu model_settings part ids must be present, numeric, and unique"
        )
    if any(item["subtype"] != "normal_part" for item in observed):
        raise ReleaseError(
            "Bambu model_settings parts must all use subtype='normal_part'"
        )
    observed_by_id = {item["id"]: item for item in observed}
    if len(observed_by_id) != len(observed):
        raise ReleaseError("Bambu model_settings contains duplicate part ids")

    for actual in observed:
        if not isinstance(actual.get("name"), str) or not actual["name"]:
            raise ReleaseError(
                "Bambu project part metadata is missing its source name"
            )

    # Expand the Bambu Core component transforms and prove each exported mesh
    # still occupies exactly the world-space bounds of its audited STL. This
    # catches a self-consistent metadata/transform edit that merely moves a
    # relief away from the cap face.
    core_resources = next(
        (node for node in core_model if node.tag.rsplit("}", 1)[-1] == "resources"),
        None,
    )
    if core_resources is None:
        raise ReleaseError("Bambu Core project has no resources")
    core_objects = [
        node for node in core_resources if node.tag.rsplit("}", 1)[-1] == "object"
    ]
    assembly_objects = [
        node
        for node in core_objects
        if any(child.tag.rsplit("}", 1)[-1] == "components" for child in node)
    ]
    if len(assembly_objects) != 1 or len(core_objects) != 1:
        raise ReleaseError(
            "Bambu Core project must contain exactly one reachable assembly object"
        )
    assembly_object = assembly_objects[0]
    assembly_id = str(_xml_attribute(assembly_object, "id") or "")
    if not assembly_id or settings_object_id != assembly_id:
        raise ReleaseError(
            "Bambu model_settings assembly id disagrees with the Core assembly object"
        )
    components_node = next(
        child
        for child in assembly_object
        if child.tag.rsplit("}", 1)[-1] == "components"
    )
    core_components = [
        node
        for node in components_node
        if node.tag.rsplit("}", 1)[-1] == "component"
    ]
    if len(core_components) != len(expected_parts):
        raise ReleaseError("Bambu Core component count disagrees with audited parts")
    component_ids = [
        str(_xml_attribute(component, "objectid") or "")
        for component in core_components
    ]
    if (
        any(not value or not value.isdecimal() for value in component_ids)
        or len(set(component_ids)) != len(component_ids)
        or set(component_ids) != set(observed_by_id)
    ):
        raise ReleaseError(
            "Bambu Core component ids and model_settings part ids must match uniquely"
        )
    build_items = [
        node for node in core_model.iter() if node.tag.rsplit("}", 1)[-1] == "item"
    ]
    if len(build_items) != 1:
        raise ReleaseError("Bambu Core project must have exactly one build item")
    if str(_xml_attribute(build_items[0], "objectid") or "") != assembly_id:
        raise ReleaseError(
            "Bambu build item must reference the sole audited assembly object"
        )
    if str(_xml_attribute(build_items[0], "printable") or "") != "1":
        raise ReleaseError("Bambu build item must be explicitly printable='1'")
    build_transform = _numbers(
        _xml_attribute(build_items[0], "transform") or "1 0 0 0 1 0 0 0 1 0 0 0",
        12,
        "Bambu build transform",
    )
    identity_linear = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]
    if any(
        not math.isclose(actual, expected, rel_tol=0.0, abs_tol=1e-7)
        for actual, expected in zip(build_transform[:9], identity_linear, strict=True)
    ) or any(abs(value) > 1e-7 for value in build_transform[9:]):
        raise ReleaseError("Bambu build transform must be identity for aligned cap parts")
    object_nodes = [
        node
        for node in object_model.iter()
        if node.tag.rsplit("}", 1)[-1] == "object"
    ]
    object_ids = [str(_xml_attribute(node, "id") or "") for node in object_nodes]
    if (
        len(object_nodes) != len(expected_parts)
        or any(not value or not value.isdecimal() for value in object_ids)
        or len(set(object_ids)) != len(object_ids)
    ):
        raise ReleaseError(
            "Bambu object model contains unreferenced or missing part objects"
        )
    object_by_id = dict(zip(object_ids, object_nodes, strict=True))
    if set(object_by_id) != set(component_ids):
        raise ReleaseError(
            "Bambu object model ids disagree with the reachable Core components"
        )
    world_bounds: list[dict[str, list[float]]] = []
    for component, expected in zip(core_components, expected_parts, strict=True):
        path_value = (_xml_attribute(component, "path") or "").lstrip("/")
        if path_value != "3D/Objects/object_1.model":
            raise ReleaseError("Bambu component references an unexpected model part")
        object_id = str(_xml_attribute(component, "objectid") or "")
        if object_id not in object_by_id:
            raise ReleaseError(f"Bambu component references missing object {object_id!r}")
        actual = observed_by_id[object_id]
        if str(actual.get("extruder")) != str(expected["extruder"]):
            raise ReleaseError(
                f"Bambu part {object_id} lost its audited extruder assignment"
            )
        if Path(str(actual.get("name", ""))).name != expected["filename"]:
            raise ReleaseError(
                f"Bambu part {object_id} name disagrees with its audited STL input"
            )
        transform = _numbers(
            _xml_attribute(component, "transform") or "1 0 0 0 1 0 0 0 1 0 0 0",
            12,
            f"Bambu Core component {object_id} transform",
        )
        if any(
            not math.isclose(value, target, rel_tol=0.0, abs_tol=1e-7)
            for value, target in zip(transform[:9], identity_linear, strict=True)
        ):
            raise ReleaseError("Bambu cap parts may be translated but not rotated or scaled")
        translation = transform[9:12]
        matrix = _numbers(actual.get("matrix"), 16, "Bambu model_settings matrix")
        matrix_linear = [
            matrix[0], matrix[1], matrix[2],
            matrix[4], matrix[5], matrix[6],
            matrix[8], matrix[9], matrix[10],
        ]
        matrix_translation = [matrix[3], matrix[7], matrix[11]]
        if any(
            not math.isclose(value, target, rel_tol=0.0, abs_tol=1e-7)
            for value, target in zip(matrix_linear, identity_linear, strict=True)
        ) or any(
            not math.isclose(value, target, rel_tol=0.0, abs_tol=1e-5)
            for value, target in zip(matrix_translation, translation, strict=True)
        ):
            raise ReleaseError("Bambu Core and model_settings transforms disagree")
        try:
            offsets = [
                float(actual[f"source_offset_{axis}"]) for axis in "xyz"
            ]
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise ReleaseError("Bambu part source offsets are invalid") from exc
        if any(
            not math.isclose(value, target, rel_tol=0.0, abs_tol=1e-5)
            for value, target in zip(offsets, translation, strict=True)
        ):
            raise ReleaseError("Bambu part offsets disagree with its Core transform")
        geometry = _mesh_geometry_from_object(
            object_by_id[object_id],
            object_id,
            translation=translation,
        )
        local = geometry["bounds"]
        expanded = {
            "min_mm": [local["min_mm"][axis] + translation[axis] for axis in range(3)],
            "max_mm": [local["max_mm"][axis] + translation[axis] for axis in range(3)],
            "size_mm": list(local["size_mm"]),
        }
        expected_bounds = expected.get("mesh", {}).get("bounds")
        if not isinstance(expected_bounds, dict):
            raise ReleaseError("audited Bambu STL lacks bounds")
        for field in ("min_mm", "max_mm", "size_mm"):
            expected_values = expected_bounds.get(field)
            if not isinstance(expected_values, list) or len(expected_values) != 3:
                raise ReleaseError(f"audited Bambu STL has invalid {field}")
            if any(
                not math.isclose(
                    expanded[field][axis],
                    float(expected_values[axis]),
                    rel_tol=0.0,
                    abs_tol=5e-4,
                )
                for axis in range(3)
            ):
                raise ReleaseError(
                    f"Bambu world-space bounds drifted for {expected['filename']} ({field})"
                )
        expected_mesh = expected.get("mesh")
        if not isinstance(expected_mesh, dict):
            raise ReleaseError("audited Bambu STL lacks mesh integrity metadata")
        expected_fingerprint = expected_mesh.get("surface_fingerprint")
        if not isinstance(expected_fingerprint, dict):
            raise ReleaseError("audited Bambu STL lacks a surface fingerprint")
        if geometry["surface_fingerprint"] != expected_fingerprint:
            raise ReleaseError(
                f"Bambu surface geometry drifted for {expected['filename']}"
            )
        try:
            expected_volume = float(expected_mesh["absolute_volume_mm3"])
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise ReleaseError("audited Bambu STL lacks a valid absolute volume") from exc
        observed_volume = float(geometry["absolute_signed_volume_mm3"])
        if not math.isclose(
            observed_volume,
            expected_volume,
            rel_tol=1e-5,
            abs_tol=1e-4,
        ):
            raise ReleaseError(
                f"Bambu part volume drifted for {expected['filename']}: "
                f"{observed_volume:g} != {expected_volume:g} mm3"
            )
        world_bounds.append(expanded)
    colors = [str(value).upper() for value in project_settings.get("filament_colour", [])]
    expected_colors = [str(item["color"]).upper() for item in expected_parts]
    if colors != expected_colors:
        raise ReleaseError(
            f"Bambu project palette disagrees with the job: {colors!r} != {expected_colors!r}"
        )
    nozzle_values = project_settings.get("nozzle_diameter")
    if not isinstance(nozzle_values, list) or not nozzle_values:
        raise ReleaseError("Bambu project does not declare nozzle_diameter")
    try:
        nozzle_matches = all(
            math.isclose(float(value), float(nozzle_mm), rel_tol=0.0, abs_tol=1e-6)
            for value in nozzle_values
        )
    except (TypeError, ValueError, OverflowError) as exc:
        raise ReleaseError("Bambu project nozzle_diameter is invalid") from exc
    if not nozzle_matches:
        raise ReleaseError(
            f"Bambu project nozzle does not match job nozzle {float(nozzle_mm):g} mm"
        )
    return {
        "status": "passed",
        "part_count": len(observed),
        "parts": [
            {
                "name": expected["name"],
                "role": expected["role"],
                "source": expected["filename"],
                "extruder": expected["extruder"],
                "color": expected["color"],
            }
            for expected in expected_parts
        ],
        "nozzle_mm": float(nozzle_mm),
        "declared_nozzle_values_mm": [float(value) for value in nozzle_values],
        "world_bounds": world_bounds,
        "surface_fingerprints": [
            expected["mesh"]["surface_fingerprint"] for expected in expected_parts
        ],
        "scope": (
            "bambu_parts_extruders_palette_all_nozzles_core_transforms_world_bounds_volume_and_surface_fingerprints"
        ),
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
    tolerance = _projection_tolerance(config)
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
            str(tolerance["tolerance_pixels"]),
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
        "tolerance": tolerance,
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
    parser.add_argument(
        "--brief",
        type=Path,
        help="approved design-brief.json (default: job metadata path or nearest parent brief)",
    )
    parser.add_argument("--openscad", help="explicit OpenSCAD executable")
    parser.add_argument(
        "--bambu",
        choices=("never", "export", "slice"),
        default="never",
        help="optional multipart Bambu Studio stage; export/slice require three profile files",
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
    primary = report.get("primary_3mf") or {}
    lines = [
        f"status: {report['status']}",
        f"primary 3MF: {primary.get('output', 'UNVERIFIABLE')}",
        f"primary 3MF kind: {primary.get('kind', 'UNVERIFIABLE')}",
        f"native 3MF: {report.get('native_3mf', {}).get('output', 'UNVERIFIABLE')}",
    ]
    sliced = report.get("bambu_3mf")
    if sliced:
        lines.append(f"Bambu {sliced.get('mode', 'output')}: {sliced.get('output', 'UNVERIFIABLE')}")
    if report.get("reason"):
        lines.append(f"reason: {report['reason']}")
    warning = report.get("retention_warning")
    if warning:
        lines.append(f"retention warning: {warning}")
    lines.append(f"slicer: {report.get('slicer_status', 'not_requested')}")
    lines.append("fit: UNVERIFIABLE until a physical coupon is printed and measured")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config = args.config.expanduser().resolve()
    stage_dirs: list[Path] = []
    invalidated_report: Path | None = None
    initial_job_sha256: str | None = None
    if args.timeout <= 0:
        print("lens-cap build-3mf: --timeout must be positive", file=sys.stderr)
        return 2
    try:
        # With an explicit report path, invalidate an existing release marker
        # before even parsing the job.  A user commonly edits the TOML and
        # reruns with --force; if that edit made the TOML invalid, delaying
        # invalidation until after load_config would leave yesterday's
        # ``passed`` report looking current beside a broken job.
        if args.force and args.report:
            early_report = args.report.expanduser().resolve()
            if early_report.is_file():
                initial_job_sha256 = _sha256(config) if config.is_file() else None
                marker: dict[str, Any] = {
                    "schema_version": 1,
                    "status": "failed",
                    "failure_class": "transaction_invalidated",
                    "runner": "scripts/build_3mf.py",
                    "job": _portable(config, config.parent),
                    "reason": (
                        "replacement build started; previous passed report invalidated "
                        "before job parsing"
                    ),
                }
                if initial_job_sha256 is not None:
                    marker["job_sha256"] = initial_job_sha256
                _atomic_json_file(early_report, marker)
                invalidated_report = early_report
        if not config.is_file():
            raise ReleaseError(f"job config not found: {config}")
        # Import only after argument validation so ``--help`` stays usable on
        # a machine that has not installed NumPy/Pillow yet.
        from lens_cap_pipeline.config import load_config

        loaded = load_config(config)
        initial_job_sha256 = _sha256(config)
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
        brief_path = resolve_brief_path(loaded, args.brief)
        release_paths = _validate_release_paths(
            loaded,
            native=native,
            bambu_output=slice_output if args.bambu != "never" else None,
            report=report_path,
            brief=brief_path,
        )
        for path in release_paths:
            if path.exists() and not args.force:
                raise ReleaseError(f"refusing to overwrite {path}; pass --force")
        if (
            args.force
            and report_path.exists()
            and invalidated_report != report_path
        ):
            # The passed report is the release commit marker. Invalidate it
            # before replacing any files so a crash or later gate failure
            # cannot leave an old green report beside a new/edited job.
            _atomic_json_file(
                report_path,
                {
                    "schema_version": 1,
                    "status": "failed",
                    "failure_class": "transaction_invalidated",
                    "runner": "scripts/build_3mf.py",
                    "job": _portable(config, config.parent),
                    "job_sha256": initial_job_sha256,
                    "reason": "replacement build in progress; previous passed report invalidated",
                },
            )
            invalidated_report = report_path

        # A native package is only a publishable lens-cap result when the
        # current raster is explicitly paired with a reviewed, source-backed
        # design brief.  Run this semantic gate before checking desktop tools
        # so a host without OpenSCAD still receives the most fundamental
        # missing-handoff diagnosis first.
        try:
            brief_report = validate_design_brief(loaded, brief_path)
        except ValueError as exc:
            raise ReleaseError(f"design brief gate failed: {exc}") from exc

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
            raise ExternalDependencyUnavailable(
                "OpenSCAD is required for a native one-piece 3MF; install it, set "
                "[print].openscad_executable, or pass --openscad. "
                "A SCAD/handoff result is not reported as a 3MF."
            )
        # Resolve every requested external dependency before creating any
        # derivative or native package.  In particular, a missing Bambu
        # executable/profile must not leave a plausible-looking native 3MF
        # behind while the requested two-stage release reports failure.
        bambu: str | None = None
        if args.bambu != "never":
            bambu_hint = _configured_tool_hint(
                args.bambu_path,
                loaded.print.bambu_executable,
                config.parent,
            )
            bambu = _resolve_executable(
                bambu_hint,
                (
                    "BambuStudio",
                    "bambu-studio",
                    "/Applications/BambuStudio.app/Contents/MacOS/BambuStudio",
                ),
            )
            if not bambu:
                raise ExternalDependencyUnavailable(
                    "Bambu Studio was requested but could not be found"
                )
            profiles = (args.machine_profile, args.process_profile, args.filament_profile)
            if not all(profiles) or not all(
                path.expanduser().is_file() for path in profiles if path
            ):
                raise ExternalDependencyUnavailable(
                    "Bambu export/slice requires --machine-profile, --process-profile, "
                    "and --filament-profile so parts, extruders, palette, and nozzle can be audited"
                )
        # Do not create a job output tree until the required external tool has
        # been resolved.  A failed/unverifiable request should be side-effect
        # free, so callers cannot mistake an empty ``build/`` directory for a
        # partially successful 3MF release.
        for path in (native, slice_output if args.bambu != "never" else None, report_path):
            if path is not None:
                path.parent.mkdir(parents=True, exist_ok=True)
        build_payload, build_command = _build_job(config, force=args.force, openscad=openscad)
        if _sha256(config) != initial_job_sha256:
            raise ReleaseError("job config changed during the release build")
        source_binding_audit = _audit_build_source_binding(loaded, brief_report)
        if build_payload.get("status") != source_binding_audit["process_status"]:
            raise ReleaseError("build status does not match the validated process status")
        scad = model_dir / f"{loaded.job_slug}.scad"
        if not scad.is_file():
            raise ReleaseError(f"build did not produce the expected SCAD: {scad}")
        geometry_mechanical: dict[str, Any] = {}
        geometry_payload: dict[str, Any] = {}
        geometry_report_path = model_dir / "geometry-report.json"
        if geometry_report_path.is_file():
            try:
                geometry_payload = json.loads(geometry_report_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ReleaseError(f"cannot read geometry report: {geometry_report_path}") from exc
            if isinstance(geometry_payload, dict) and isinstance(geometry_payload.get("mechanical"), dict):
                geometry_mechanical = geometry_payload["mechanical"]
        if geometry_payload.get("production_status") != source_binding_audit["process_status"]:
            raise ReleaseError("geometry report did not inherit the process production status")
        if (
            geometry_payload.get("process_report_sha256")
            != source_binding_audit["process_report_sha256"]
        ):
            raise ReleaseError("geometry report is bound to a different process report")
        if (
            str(geometry_payload.get("art_source_sha256", "")).lower()
            != str(source_binding_audit["current_source_sha256"]).lower()
        ):
            raise ReleaseError("geometry report is bound to a different approved artwork")
        projection = _projection_audit(loaded, timeout=args.timeout)
        stage_native = _stage_path(native, stage_dirs)
        stage_bambu = (
            _stage_path(slice_output, stage_dirs) if args.bambu != "never" else None
        )
        stage_report = _stage_path(report_path, stage_dirs)
        native_command = [
            sys.executable,
            str(ADAPTER),
            "openscad",
            str(scad),
            str(stage_native),
            "--openscad",
            openscad,
            "--timeout",
            str(args.timeout),
            "--require-single-volume",
        ]
        native_report = _adapter(
            native_command,
            timeout=args.timeout + 120,
            require_closed=True,
            require_single_volume=True,
        )
        if native_report["verification"].get("has_embedded_gcode") is not False:
            raise ReleaseError("native 3MF must be unsliced and contain no embedded G-code")
        native_bounds_audit = _audit_native_bounds(
            native_report["verification"], geometry_payload
        )
        rib_mesh_audit = _audit_integrated_ribs(stage_native, geometry_mechanical)
        expected_relief_top_z = _expected_relief_top_z(geometry_payload)
        visible_top_area_reference = _assembly_visible_top_area_reference(
            loaded,
            geometry_payload,
            expected_relief_top_z,
        )
        material_audit = _audit_material_assignments(
            stage_native,
            loaded.palette,
            expected_footprint_mm2=_expected_palette_footprints(loaded),
            expected_masks={
                name: loaded.output_dir / "masks" / f"{name}.png"
                for name in expected_relief_top_z
            },
            expected_top_z_mm=expected_relief_top_z,
            canvas_size_mm=loaded.face_diameter_mm,
            tolerance_pixels=int(
                projection.get("tolerance", {}).get("tolerance_pixels", 1)
            ),
            expected_visible_top_area_reference=visible_top_area_reference,
        )
        public_native_command = [
            str(native) if item == str(stage_native) else item for item in native_command
        ]
        result: dict[str, Any] = {
            "schema_version": 1,
            "status": "passed",
            "artifact_status": "passed",
            "release_class": "native_model_verified",
            "publishable": True,
            "slice_policy": "explicit_only",
            "slicer_status": "not_requested",
            "runner": "scripts/build_3mf.py",
            "job": _portable(config, config.parent),
            "job_sha256": initial_job_sha256,
            "build": {
                "status": build_payload.get("status"),
                "command": [_portable(item, ROOT) if Path(item).is_absolute() else item for item in build_command["argv"]],
                "returncode": build_command["returncode"],
                "stderr_tail": build_command["stderr_tail"],
            },
            "openscad": _tool_label(openscad),
            "native_3mf": {
                "status": "passed",
                "output": _portable(native, config.parent),
                "manifest": _portable(Path(f"{native}.manifest.json"), config.parent),
                "sha256": native_report["verification"].get("sha256"),
                "bytes": native_report["verification"].get("bytes"),
                "model": native_report["verification"].get("model"),
                "package_verification": {
                    "status": "passed",
                    **native_report["verification"],
                },
                "command": [
                    _portable(item, config.parent) if Path(item).is_absolute() else item
                    for item in public_native_command
                ],
            },
            # The native Core package is the deterministic geometry/audit
            # master.  It is not a Bambu Studio project because it does not
            # carry printer/process/filament metadata.  Keep an explicit
            # primary-delivery pointer so callers cannot accidentally present
            # the internal Core package as a Bambu-ready project.
            "primary_3mf": {
                "status": "passed",
                "kind": "core_geometry_only",
                "output": _portable(native, config.parent),
                "sha256": native_report["verification"].get("sha256"),
                "bytes": native_report["verification"].get("bytes"),
                "bambu_project_config": False,
                "user_notice": (
                    "Portable 3MF Core geometry only; Bambu Studio may report "
                    "missing/invalid project config. Request --bambu export for "
                    "a Bambu project deliverable."
                ),
            },
            "design_brief": {
                "path": _portable(brief_report["path"], config.parent),
                "sha256": brief_report["sha256"],
                "candidate_path": _portable(brief_report["candidate_path"], config.parent),
                "candidate_sha256": brief_report["candidate_sha256"],
                "identity": brief_report["identity"],
                "display_text": brief_report["display_text"],
                "anchor_count": brief_report["anchor_count"],
                "culture_anchor_count": brief_report["culture_anchor_count"],
                "hero_anchor_index": brief_report["hero_anchor_index"],
                "hero_anchor_id": brief_report["hero_anchor_id"],
                "anchor_consequence_systems": brief_report[
                    "anchor_consequence_systems"
                ],
                "quality_reference_count": brief_report["quality_reference_count"],
                "completion_quality_checked": brief_report[
                    "completion_quality_checked"
                ],
                "physical_fit_checked": brief_report["physical_fit_checked"],
                "job_identity_binding_checked": brief_report[
                    "job_identity_binding_checked"
                ],
                "status": brief_report["status"],
            },
            "source_binding_audit": source_binding_audit,
            "projection": projection,
            "bambu_3mf": None,
            "fit_status": "unverifiable_until_coupon_measurement",
            # Keep the exact geometry-driving fit snapshot with the package.
            # This does not prove physical retention, but it makes enabled
            # ribs/count/profile/tip diameter auditable instead of reducing
            # them to a single warning string.
            "mechanical": geometry_mechanical,
            "friction_rib_mesh_audit": rib_mesh_audit,
            "material_assignment_audit": material_audit,
            "native_bounds_audit": native_bounds_audit,
            "retention_status": geometry_mechanical.get("friction_rib_retention_status"),
            "retention_warning": geometry_mechanical.get("friction_rib_fit_warning"),
            "notes": [
                "Artwork, mask, and relief projection gates are executed by the public build path.",
                "Native 3MF passed artifact checks but is unsliced; inspect a target-profile toolpath before claiming print readiness.",
                "Feature survival is evaluated only from the target slicer's actual toolpaths.",
                "A valid package does not prove physical fit; print and measure a same-material coupon.",
            ],
        }
        if args.bambu != "never":
            assert bambu is not None
            assert stage_bambu is not None
            declared_bambu_parts = _bambu_part_specs(loaded)
            bambu_part_dir = Path(tempfile.mkdtemp(prefix="lenscap-bambu-parts-"))
            stage_dirs.append(bambu_part_dir)
            bambu_parts = _prepare_closed_bambu_parts(
                loaded,
                scad,
                declared_bambu_parts,
                mechanical=geometry_mechanical,
                destination=bambu_part_dir,
                openscad=openscad,
                timeout=args.timeout,
            )
            bambu_command = [
                sys.executable,
                str(ADAPTER),
                "bambu",
                str(bambu_parts[0]["path"]),
                str(stage_bambu),
                "--mode",
                args.bambu,
                "--bambu",
                bambu,
                "--openscad",
                openscad,
                "--timeout",
                str(args.timeout),
                "--machine-profile",
                str(args.machine_profile.expanduser().resolve()),
                "--process-profile",
                str(args.process_profile.expanduser().resolve()),
                "--filament-profile",
                str(args.filament_profile.expanduser().resolve()),
            ]
            for part in bambu_parts[1:]:
                bambu_command.extend(("--part", str(part["path"])))
            for part in bambu_parts:
                bambu_command.extend(("--filament-color", str(part["color"])))
            sliced_report = _adapter(
                bambu_command,
                timeout=args.timeout + 180,
                require_slice=args.bambu == "slice",
            )
            bambu_mesh_audit = _audit_bambu_mesh_report(
                sliced_report["verification"]
            )
            bambu_audit = _audit_bambu_project(
                stage_bambu,
                bambu_parts,
                nozzle_mm=loaded.nozzle_mm,
            )
            adapter_manifest = sliced_report["adapter_manifest"]
            profile_inputs = adapter_manifest.get("profiles")
            profile_resolution = adapter_manifest.get("profile_resolution")
            effective_profile_audit = adapter_manifest.get("effective_profile_audit")
            expected_profile_keys = {"machine", "process", "filament"}
            if not isinstance(profile_inputs, dict) or set(profile_inputs) != expected_profile_keys:
                raise ReleaseError("Bambu adapter manifest lacks complete profile input evidence")
            if (
                not isinstance(profile_resolution, dict)
                or set(profile_resolution) != expected_profile_keys
            ):
                raise ReleaseError("Bambu adapter manifest lacks complete profile inheritance evidence")
            if (
                not isinstance(effective_profile_audit, dict)
                or effective_profile_audit.get("status") != "passed"
            ):
                raise ReleaseError("Bambu adapter effective profile audit did not pass")
            result["bambu_3mf"] = {
                "mode": args.bambu,
                "output": _portable(slice_output, config.parent),
                "manifest": _portable(Path(f"{slice_output}.manifest.json"), config.parent),
                "sha256": sliced_report["verification"].get("sha256"),
                "bytes": sliced_report["verification"].get("bytes"),
                "gcode_bytes": sliced_report["verification"].get("gcode_bytes"),
                "bambu": _tool_label(bambu),
                "profile_inputs": profile_inputs,
                "profile_resolution": profile_resolution,
                "effective_profile_audit": effective_profile_audit,
                "multipart_audit": bambu_audit,
                "mesh_audit": bambu_mesh_audit,
                "closed_part_preparation": [
                    {
                        "name": part["name"],
                        "role": part["role"],
                        "selector": part["selector"],
                        "filename": part["filename"],
                        "sha256": part["sha256"],
                        # The staged STL lives in a transaction directory which is
                        # intentionally deleted after publication.  Retain the
                        # stable basename, never that machine-local temporary path.
                        "mesh": {**part["mesh"], "path": part["filename"]},
                    }
                    for part in bambu_parts
                ],
            }
            result["slicer_status"] = (
                "sliced_profile_verified"
                if args.bambu == "slice"
                else "project_export_verified"
            )
            result["release_class"] = (
                "sliced_model_verified"
                if args.bambu == "slice"
                else "slicer_project_verified"
            )
            result["primary_3mf"] = {
                "status": "passed",
                "kind": (
                    "bambu_sliced_project"
                    if args.bambu == "slice"
                    else "bambu_project"
                ),
                "output": _portable(slice_output, config.parent),
                "sha256": sliced_report["verification"].get("sha256"),
                "bytes": sliced_report["verification"].get("bytes"),
                "bambu_project_config": True,
                "user_notice": (
                    "Primary Bambu Studio deliverable; native_3mf remains the "
                    "portable internal geometry/audit master."
                ),
            }
        release_context = {
            "release_status": result["status"],
            "reason": None,
            "slice_allowed": True,
            "publishable": True,
            "slicer_status": result["slicer_status"],
        }
        native_manifest = _retarget_adapter_manifest(
            stage_native,
            native,
            release_context=release_context,
        )
        staged_publications: list[tuple[Path, Path]] = [
            (stage_native, native),
            (native_manifest, Path(f"{native}.manifest.json")),
        ]
        if args.bambu != "never":
            assert stage_bambu is not None
            bambu_manifest = _retarget_adapter_manifest(
                stage_bambu,
                slice_output,
                release_context=release_context,
            )
            staged_publications.extend(
                (
                    (stage_bambu, slice_output),
                    (bambu_manifest, Path(f"{slice_output}.manifest.json")),
                )
            )
        stage_report.write_text(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        staged_publications.append((stage_report, report_path))
        if not args.force:
            appeared = [target for _, target in staged_publications if target.exists()]
            if appeared:
                raise ReleaseError(
                    "release target appeared during the build; refusing to replace it: "
                    + ", ".join(str(path) for path in appeared)
                )
        # Publish the release report last.  Its presence is the transaction's
        # commit marker; every preceding package and sidecar has already
        # passed the full post-export audit suite.
        if _sha256(config) != initial_job_sha256:
            raise ReleaseError("job config changed before release publication")
        if _sha256(Path(brief_report["path"])) != brief_report["sha256"]:
            raise ReleaseError("approved design brief changed before release publication")
        final_source_binding = _audit_build_source_binding(loaded, brief_report)
        binding_fields = (
            "current_source_sha256",
            "process_report_sha256",
            "process_status",
        )
        if any(
            final_source_binding.get(field) != source_binding_audit.get(field)
            for field in binding_fields
        ):
            raise ReleaseError(
                "source artwork or process classification changed before release publication"
            )
        if _sha256(stage_native) != native_report["verification"].get("sha256"):
            raise ReleaseError("native 3MF changed after final verification")
        if args.bambu != "never":
            assert stage_bambu is not None
            assert result["bambu_3mf"] is not None
            if _sha256(stage_bambu) != result["bambu_3mf"].get("sha256"):
                raise ReleaseError("Bambu 3MF changed after final verification")
        for staged, target in staged_publications:
            os.replace(staged, target)
        # Alpha migration: a successful current release supersedes the former
        # alternate prototype branch. Remove only its three deterministic,
        # generated filenames so the output directory exposes one 3MF path.
        for legacy_output in (
            loaded.output_dir / "model" / "3mf-prototype-unverified.json",
            loaded.output_dir
            / "model"
            / f"{loaded.job_slug}-prototype-unverified-native.3mf",
            loaded.output_dir
            / "model"
            / f"{loaded.job_slug}-prototype-unverified-native.3mf.manifest.json",
        ):
            legacy_output.unlink(missing_ok=True)
        for directory in stage_dirs:
            shutil.rmtree(directory, ignore_errors=True)
        stage_dirs.clear()
        output = result if args.json else _human(result)
        print(json.dumps(output, ensure_ascii=False, indent=2) if isinstance(output, dict) else output)
        return 0
    except (OSError, ValueError, KeyError, json.JSONDecodeError, ReleaseError) as exc:
        for directory in stage_dirs:
            shutil.rmtree(directory, ignore_errors=True)
        # Emit a small machine-readable failure when requested.  No partial
        # release report is written, so a failed run cannot look publishable.
        unavailable = isinstance(exc, ExternalDependencyUnavailable)
        failure = {
            "status": "unverifiable" if unavailable else "failed",
            "failure_class": (
                "external_dependency_unavailable" if unavailable else "validation_failed"
            ),
            "runner": "scripts/build_3mf.py",
            "reason": str(exc),
        }
        if invalidated_report is not None:
            try:
                _atomic_json_file(
                    invalidated_report,
                    {
                        "schema_version": 1,
                        **failure,
                        "job": _portable(config, config.parent),
                        "job_sha256": initial_job_sha256,
                    },
                )
            except OSError:
                pass
        if args.json:
            print(json.dumps(failure, ensure_ascii=False, indent=2))
        else:
            print(f"lens-cap build-3mf: {str(exc)}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
