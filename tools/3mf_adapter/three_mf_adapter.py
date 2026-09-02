#!/usr/bin/env python3
"""Small, dependency-free 3MF adapter.

This module deliberately lives outside :mod:`lens_cap_pipeline`.  It is an
optional hand-off tool for installations that have OpenSCAD and/or Bambu
Studio available.  The ``standard`` route writes a minimal 3MF Core package
using only the Python standard library; the ``bambu`` route invokes the
installed Bambu Studio CLI and records the exact invocation in a manifest.

The generated geometry is never taken from a user archive.  The ``fixture``
command creates a tiny, closed cube in this file for smoke testing.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import os
import posixpath
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence

CORE_MODEL = "3D/3dmodel.model"
CONTENT_TYPES = "[Content_Types].xml"
RELS = "_rels/.rels"
MODEL_RELS = "3D/_rels/3dmodel.model.rels"
# A 3MF Core package needs the content-types part, the package relationship,
# and the model part.  ``3D/_rels/3dmodel.model.rels`` is useful for some
# slicers but optional in the Core profile (OpenSCAD's native 3MF exporter
# intentionally omits it), so it must not be treated as a universal gate.
REQUIRED_CORE_PARTS = (CONTENT_TYPES, RELS, CORE_MODEL)
CORE_NS = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
# ``__file__`` is stable when the adapter is invoked from either this checkout
# or its parent directory.  Manifests use this anchor so a caller's current
# working directory does not leak into checked-in provenance files.
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Mesh:
    """Indexed triangle mesh in millimetres."""

    vertices: tuple[tuple[float, float, float], ...]
    triangles: tuple[tuple[int, int, int], ...]
    dropped_degenerate: int = 0


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _finite_triplet(values: Sequence[float]) -> bool:
    return all(math.isfinite(value) for value in values)


def _mesh_from_triangles(
    triangles: Iterable[Sequence[Sequence[float]]], *, drop_degenerate: bool = False
) -> Mesh:
    """Deduplicate STL vertices and reject malformed/degenerate triangles."""

    vertices: list[tuple[float, float, float]] = []
    indices: list[tuple[int, int, int]] = []
    lookup: dict[tuple[float, float, float], int] = {}
    dropped_degenerate = 0

    def index(vertex: Sequence[float]) -> int:
        values = tuple(float(value) for value in vertex)
        if len(values) != 3 or not _finite_triplet(values):
            raise ValueError(f"non-finite or malformed vertex: {vertex!r}")
        # STL is decimal text in most workflows.  A small canonicalisation
        # keeps equivalent values from producing accidental duplicate points,
        # while retaining sub-micron detail for normal printer dimensions.
        key = tuple(round(value, 9) for value in values)
        if key not in lookup:
            lookup[key] = len(vertices)
            vertices.append(values)
        return lookup[key]

    for triangle in triangles:
        if len(triangle) != 3:
            raise ValueError("a triangle must contain exactly three vertices")
        tri = tuple(index(vertex) for vertex in triangle)
        if len(set(tri)) != 3:
            if drop_degenerate:
                dropped_degenerate += 1
                continue
            raise ValueError("degenerate triangle with repeated vertex")
        a, b, c = (vertices[item] for item in tri)
        ab = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
        ac = (c[0] - a[0], c[1] - a[1], c[2] - a[2])
        cross = (
            ab[1] * ac[2] - ab[2] * ac[1],
            ab[2] * ac[0] - ab[0] * ac[2],
            ab[0] * ac[1] - ab[1] * ac[0],
        )
        if sum(value * value for value in cross) <= 1e-20:
            if drop_degenerate:
                dropped_degenerate += 1
                continue
            raise ValueError("zero-area triangle")
        indices.append(tri)

    if not indices:
        raise ValueError("mesh contains no triangles")
    return Mesh(tuple(vertices), tuple(indices), dropped_degenerate)


def _parse_binary_stl(data: bytes, *, drop_degenerate: bool = False) -> Mesh | None:
    if len(data) < 84:
        return None
    count = struct.unpack_from("<I", data, 80)[0]
    if count <= 0 or 84 + count * 50 != len(data):
        return None
    triangles: list[tuple[tuple[float, float, float], ...]] = []
    offset = 84
    try:
        for _ in range(count):
            # normal occupies bytes 0..11; three vertices start at byte 12.
            values = struct.unpack_from("<12f", data, offset)
            triangles.append((values[3:6], values[6:9], values[9:12]))
            offset += 50
    except struct.error as exc:
        raise ValueError(f"malformed binary STL triangle record: {exc}") from exc
    return _mesh_from_triangles(triangles, drop_degenerate=drop_degenerate)


_VERTEX_RE = re.compile(
    rb"\bvertex\s+"
    rb"([-+]?\d+(?:\.\d*)?(?:[eE][-+]?\d+)?)\s+"
    rb"([-+]?\d+(?:\.\d*)?(?:[eE][-+]?\d+)?)\s+"
    rb"([-+]?\d+(?:\.\d*)?(?:[eE][-+]?\d+)?)\b",
    re.IGNORECASE,
)


def read_stl(path: Path, *, drop_degenerate: bool = False) -> Mesh:
    """Read binary or ASCII STL without third-party packages."""

    data = path.read_bytes()
    binary = _parse_binary_stl(data, drop_degenerate=drop_degenerate)
    if binary is not None:
        return binary
    matches = _VERTEX_RE.findall(data)
    if len(matches) % 3:
        raise ValueError(f"ASCII STL has {len(matches)} vertices (not divisible by 3): {path}")
    triangles = [
        tuple(tuple(float(value) for value in matches[offset + part]) for part in range(3))
        for offset in range(0, len(matches), 3)
    ]
    return _mesh_from_triangles(triangles, drop_degenerate=drop_degenerate)


def mesh_is_closed(mesh: Mesh) -> tuple[bool, dict[str, int]]:
    edges: dict[tuple[int, int], int] = {}
    for triangle in mesh.triangles:
        for left, right in (
            (triangle[0], triangle[1]),
            (triangle[1], triangle[2]),
            (triangle[2], triangle[0]),
        ):
            edge = (left, right) if left < right else (right, left)
            edges[edge] = edges.get(edge, 0) + 1
    boundary = sum(count == 1 for count in edges.values())
    nonmanifold = sum(count > 2 for count in edges.values())
    return boundary == 0 and nonmanifold == 0, {
        "unique_edges": len(edges),
        "boundary_edges": boundary,
        "nonmanifold_edges": nonmanifold,
    }


def mesh_bounds(mesh: Mesh) -> dict[str, list[float]]:
    """Return axis-aligned millimetre bounds for provenance and review."""

    if not mesh.vertices:
        raise ValueError("mesh contains no vertices")
    minimum = [min(vertex[axis] for vertex in mesh.vertices) for axis in range(3)]
    maximum = [max(vertex[axis] for vertex in mesh.vertices) for axis in range(3)]
    return {
        "min_mm": minimum,
        "max_mm": maximum,
        "size_mm": [maximum[axis] - minimum[axis] for axis in range(3)],
    }


def _xml_model(mesh: Mesh, title: str) -> bytes:
    safe_title = html.escape(title, quote=True)
    vertex_xml = "\n".join(
        f'      <vertex x="{x:.9f}" y="{y:.9f}" z="{z:.9f}" />'
        for x, y, z in mesh.vertices
    )
    triangle_xml = "\n".join(
        f'      <triangle v1="{a}" v2="{b}" v3="{c}" />' for a, b, c in mesh.triangles
    )
    text = f'''<?xml version="1.0" encoding="UTF-8"?>
<model unit="millimeter" xml:lang="en-US" xmlns="{CORE_NS}">
  <metadata name="Application">lens-cap-pipeline 3MF adapter</metadata>
  <metadata name="Title">{safe_title}</metadata>
  <resources>
    <object id="1" type="model" name="{safe_title}">
      <mesh>
        <vertices>
{vertex_xml}
        </vertices>
        <triangles>
{triangle_xml}
        </triangles>
      </mesh>
    </object>
  </resources>
  <build>
    <item objectid="1" />
  </build>
</model>
'''
    return text.encode("utf-8")


def _zip_entry(name: str, payload: bytes) -> tuple[zipfile.ZipInfo, bytes]:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o100644 << 16
    info.create_system = 3
    return info, payload


def write_standard_3mf(
    mesh: Mesh,
    output: Path,
    *,
    title: str = "fixture cube",
    require_closed: bool = True,
) -> dict[str, object]:
    """Write a deterministic 3MF Core package and return verification data."""

    closed, edge_report = mesh_is_closed(mesh)
    if require_closed and not closed:
        raise ValueError(f"mesh is not closed/manifold: {edge_report}")
    content_types = b'''<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml" />
  <Override PartName="/3D/3dmodel.model"
            ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml" />
</Types>
'''
    rels = b'''<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rel-1"
                Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"
                Target="/3D/3dmodel.model" />
</Relationships>
'''
    model_rels = b'''<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships" />
'''
    entries = {
        CONTENT_TYPES: content_types,
        RELS: rels,
        CORE_MODEL: _xml_model(mesh, title),
        MODEL_RELS: model_rels,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name in sorted(entries):
            info, payload = _zip_entry(name, entries[name])
            archive.writestr(info, payload)
    return {
        "closed": closed,
        "closure_required": require_closed,
        "bounds": mesh_bounds(mesh),
        **edge_report,
        "sha256": sha256(output),
        "bytes": output.stat().st_size,
    }


def _cube_triangles(size: float = 20.0) -> tuple[tuple[tuple[float, float, float], ...], ...]:
    """Return outward-oriented triangles for a cube from (0,0,0) to size."""

    s = float(size)
    v = {
        "000": (0.0, 0.0, 0.0),
        "100": (s, 0.0, 0.0),
        "110": (s, s, 0.0),
        "010": (0.0, s, 0.0),
        "001": (0.0, 0.0, s),
        "101": (s, 0.0, s),
        "111": (s, s, s),
        "011": (0.0, s, s),
    }
    # Each pair is counter-clockwise as viewed from outside.
    return (
        (v["000"], v["010"], v["110"]),
        (v["000"], v["110"], v["100"]),
        (v["001"], v["101"], v["111"]),
        (v["001"], v["111"], v["011"]),
        (v["000"], v["100"], v["101"]),
        (v["000"], v["101"], v["001"]),
        (v["010"], v["011"], v["111"]),
        (v["010"], v["111"], v["110"]),
        (v["000"], v["001"], v["011"]),
        (v["000"], v["011"], v["010"]),
        (v["100"], v["110"], v["111"]),
        (v["100"], v["111"], v["101"]),
    )


def write_ascii_stl(mesh: Mesh, output: Path, *, name: str = "lenscap_fixture") -> None:
    lines = [f"solid {name}"]
    for a, b, c in mesh.triangles:
        va, vb, vc = (mesh.vertices[index] for index in (a, b, c))
        # Normals are optional in STL; zero is accepted and avoids a second
        # floating-point calculation in this deterministic source fixture.
        lines.append("  facet normal 0 0 0")
        lines.append("    outer loop")
        for x, y, z in (va, vb, vc):
            lines.append(f"      vertex {x:.9f} {y:.9f} {z:.9f}")
        lines.extend(("    endloop", "  endfacet"))
    lines.append(f"endsolid {name}")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + "\n", encoding="ascii", newline="\n")


def _resolve_executable(configured: str | None, candidates: Sequence[str]) -> str | None:
    # ``fixture --bambu`` is a boolean request; do not accidentally pass that
    # flag value to ``Path`` when no explicit executable was supplied.
    configured_path = configured if isinstance(configured, str) else None
    # An explicit executable is authoritative.  Falling back to an unrelated
    # system binary after a typo makes provenance and clean-room failures look
    # like successful runs.  This mirrors the bridge's resolver semantics.
    if configured_path:
        path = Path(configured_path)
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
        found = shutil.which(configured_path)
        return found if found else None
    values = list(candidates)
    for value in values:
        if not value:
            continue
        path = Path(value)
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
        found = shutil.which(value)
        if found:
            return found
    return None


def _tool_version(executable: str) -> str | None:
    for args in (("--version",), ("--help",)):
        result = subprocess.run([executable, *args], check=False, capture_output=True, text=True)
        text = (result.stdout + "\n" + result.stderr).strip()
        if text:
            # Bambu Studio prefixes its useful version with a startup trace
            # line when launched headlessly; prefer the explicit banner.
            for line in text.splitlines():
                if re.search(r"(?:BambuStudio|bambu-studio)[-_]?\d", line, re.IGNORECASE):
                    return line.strip()[:240]
            return text.splitlines()[0][:240]
    return None


def _run(command: Sequence[str], *, timeout: int) -> tuple[int, str, str]:
    try:
        result = subprocess.run(
            list(command), check=False, capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired as exc:
        return 124, (exc.stdout or ""), f"timeout after {timeout}s\n{exc.stderr or ''}"
    return result.returncode, result.stdout, result.stderr


def _redact_temp_paths(command: Sequence[str], work_dir: Path) -> list[str]:
    """Make an argv suitable for a checked-in provenance manifest."""

    root = str(work_dir)
    return [item.replace(root, "<temporary-workdir>") for item in command]


def _portable_path(value: str | Path) -> str:
    """Return a path suitable for a portable sidecar manifest.

    Paths inside the checkout are emitted relative to its root.  Paths outside
    it (for example a local profile or a temporary input) retain only a
    descriptive basename under ``<external>``; this keeps provenance useful
    without recording a developer's home directory or machine layout.
    """

    text = str(value)
    if not text:
        return text
    path = Path(text)
    # Resolve relative paths against the caller's cwd when possible so a run
    # from the parent directory still records the same repository-relative
    # name.  Keep non-existent symbolic option values untouched.
    resolved = (Path.cwd() / path).resolve() if not path.is_absolute() else path.resolve()
    if path.is_absolute():
        # Keep an app-bundle-relative suffix for bundled profiles/resources;
        # this is more actionable than reducing every profile to a basename.
        for candidate in (text, str(resolved)):
            for bundle in ("BambuStudio.app", "OpenSCAD.app"):
                marker = f"/{bundle}/"
                if marker in candidate:
                    return f"<{bundle}>/{candidate.split(marker, 1)[1]}"
    for anchor in (REPOSITORY_ROOT, Path.cwd().resolve()):
        try:
            return resolved.relative_to(anchor).as_posix()
        except ValueError:
            continue
    if not path.is_absolute():
        return text.replace(os.sep, "/")
    return f"<external>/{resolved.name}"


def _portable_executable(value: str | Path) -> str:
    """Normalize known app-bundle executables without leaking install paths."""

    text = str(value)
    candidates = [text]
    if Path(text).is_absolute():
        # Homebrew commonly installs an ``openscad`` symlink into the app
        # bundle.  Resolve it for a stable bundle label, while retaining the
        # original basename for genuinely external executables.
        candidates.append(str(Path(text).resolve()))
    for candidate in candidates:
        for bundle in ("BambuStudio.app", "OpenSCAD.app"):
            marker = f"/{bundle}/"
            if marker in candidate:
                return f"<{bundle}>/{candidate.split(marker, 1)[1]}"
    if Path(text).is_absolute():
        return f"<external>/{Path(text).name}"
    return _portable_path(text)


def _portable_argument(value: str, *, executable: bool = False) -> str:
    """Normalize one argv item, including semicolon-joined profile paths."""

    if executable:
        return _portable_executable(value)
    # Bambu accepts machine and process profiles as one ``A;B`` argv item.
    # Normalize each component independently while leaving ordinary strings
    # and option values untouched.
    parts = value.split(";")
    return ";".join(_portable_path(part) if Path(part).is_absolute() else part for part in parts)


def _portable_command(command: Sequence[str]) -> list[str]:
    return [
        _portable_argument(item, executable=index == 0)
        for index, item in enumerate(command)
    ]


def _portable_verification(report: dict[str, object]) -> dict[str, object]:
    """Copy a verification report while normalizing its path field."""

    normalized = dict(report)
    if isinstance(normalized.get("path"), str):
        normalized["path"] = _portable_path(normalized["path"])
    return normalized


def _xml_local(tag: str) -> str:
    """Return an XML local name, ignoring an optional namespace URI."""

    return tag.rsplit("}", 1)[-1]


def _xml_attr(element: ET.Element, name: str) -> str | None:
    """Read an attribute whether or not its producer namespace-qualified it."""

    for key, value in element.attrib.items():
        if key == name or _xml_local(key) == name:
            return value
    return None


def _validate_model_part(
    payload: bytes, *, part_name: str, package_names: set[str]
) -> dict[str, int]:
    """Validate one 3MF model XML part and its mesh/component references."""

    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise ValueError(f"invalid XML in 3MF model part {part_name}: {exc}") from exc
    if _xml_local(root.tag) != "model":
        raise ValueError(f"3MF model part {part_name} has root {_xml_local(root.tag)!r}, expected model")
    resources = next((child for child in root if _xml_local(child.tag) == "resources"), None)
    if resources is None:
        raise ValueError(f"3MF model part {part_name} lacks resources")
    objects = [element for element in resources.iter() if _xml_local(element.tag) == "object"]
    object_ids: set[str] = set()
    mesh_vertices = 0
    mesh_triangles = 0
    mesh_parts = 0
    minimum = [float("inf")] * 3
    maximum = [float("-inf")] * 3
    for obj in objects:
        object_id = _xml_attr(obj, "id")
        if not object_id or object_id in object_ids:
            raise ValueError(f"3MF model part {part_name} has duplicate/missing object id")
        object_ids.add(object_id)
        mesh = next((child for child in obj if _xml_local(child.tag) == "mesh"), None)
        if mesh is not None:
            vertices_node = next(
                (child for child in mesh if _xml_local(child.tag) == "vertices"), None
            )
            triangles_node = next(
                (child for child in mesh if _xml_local(child.tag) == "triangles"), None
            )
            if vertices_node is None or triangles_node is None:
                raise ValueError(f"3MF mesh in {part_name} lacks vertices or triangles")
            vertices = [
                child for child in vertices_node if _xml_local(child.tag) == "vertex"
            ]
            triangles = [
                child for child in triangles_node if _xml_local(child.tag) == "triangle"
            ]
            if not vertices or not triangles:
                raise ValueError(f"3MF mesh in {part_name} is empty")
            for vertex in vertices:
                try:
                    coordinates = tuple(float(_xml_attr(vertex, axis) or "nan") for axis in "xyz")
                except ValueError as exc:
                    raise ValueError(f"3MF mesh in {part_name} has malformed vertex") from exc
                if not _finite_triplet(coordinates):
                    raise ValueError(f"3MF mesh in {part_name} has non-finite vertex")
                for axis, value in enumerate(coordinates):
                    minimum[axis] = min(minimum[axis], value)
                    maximum[axis] = max(maximum[axis], value)
            for triangle in triangles:
                try:
                    indices = tuple(
                        int(_xml_attr(triangle, name) or "-1") for name in ("v1", "v2", "v3")
                    )
                except ValueError as exc:
                    raise ValueError(f"3MF mesh in {part_name} has malformed triangle") from exc
                if any(index < 0 or index >= len(vertices) for index in indices):
                    raise ValueError(f"3MF mesh in {part_name} has out-of-range triangle index")
            mesh_parts += 1
            mesh_vertices += len(vertices)
            mesh_triangles += len(triangles)
        components = next((child for child in obj if _xml_local(child.tag) == "components"), None)
        if components is not None:
            for component in components.iter():
                if _xml_local(component.tag) != "component":
                    continue
                target = _xml_attr(component, "path")
                if not target:
                    raise ValueError(f"3MF component in {part_name} lacks a path")
                target = target.lstrip("/")
                if target not in package_names:
                    raise ValueError(f"3MF component in {part_name} references missing part {target}")
    if not objects:
        raise ValueError(f"3MF model part {part_name} has no objects")
    build = next((child for child in root if _xml_local(child.tag) == "build"), None)
    if part_name == CORE_MODEL and build is None:
        raise ValueError("3MF core model lacks build")
    build_items = 0
    if build is not None:
        for item in build.iter():
            if _xml_local(item.tag) != "item":
                continue
            object_id = _xml_attr(item, "objectid")
            if object_id not in object_ids:
                raise ValueError(f"3MF build item in {part_name} references missing object {object_id!r}")
            build_items += 1
    if part_name == CORE_MODEL and build_items == 0:
        raise ValueError("3MF core model has an empty build")
    bounds = None
    if mesh_vertices:
        bounds = {
            "min_mm": minimum,
            "max_mm": maximum,
            "size_mm": [maximum[axis] - minimum[axis] for axis in range(3)],
        }
    return {
        "objects": len(objects),
        "mesh_parts": mesh_parts,
        "vertices": mesh_vertices,
        "triangles": mesh_triangles,
        "build_items": build_items,
        "bounds": bounds,
    }


def verify_3mf(path: Path, *, require_slice: bool = False) -> dict[str, object]:
    """Check ZIP integrity, XML structure, mesh indices, and optional G-code."""

    with zipfile.ZipFile(path) as archive:
        bad = archive.testzip()
        names = set(archive.namelist())
        missing = [item for item in REQUIRED_CORE_PARTS if item not in names]
        if bad:
            raise ValueError(f"CRC failure in 3MF entry {bad}")
        if missing:
            raise ValueError(f"missing required 3MF entries: {missing}")
        # Parse package relationship/content-type parts as XML rather than
        # accepting a byte substring that could occur in arbitrary metadata.
        package_roots: dict[str, ET.Element] = {}
        for part_name in (CONTENT_TYPES, RELS):
            try:
                package_root = ET.fromstring(archive.read(part_name))
            except ET.ParseError as exc:
                raise ValueError(f"invalid XML in 3MF package part {part_name}: {exc}") from exc
            expected_root = "Types" if part_name == CONTENT_TYPES else "Relationships"
            if _xml_local(package_root.tag) != expected_root:
                raise ValueError(
                    f"3MF package part {part_name} has root {_xml_local(package_root.tag)!r}"
                )
            package_roots[part_name] = package_root
        types_root = package_roots[CONTENT_TYPES]
        model_content_type = "application/vnd.ms-package.3dmanufacturing-3dmodel+xml"
        model_type_declared = any(
            (_xml_attr(element, "Extension") or "").lower() == "model"
            and _xml_attr(element, "ContentType") == model_content_type
            for element in types_root
            if _xml_local(element.tag) == "Default"
        ) or any(
            _xml_attr(element, "PartName").lstrip("/") == CORE_MODEL
            and _xml_attr(element, "ContentType") == model_content_type
            for element in types_root
            if _xml_local(element.tag) == "Override"
            and _xml_attr(element, "PartName")
        )
        if not model_type_declared:
            raise ValueError("3MF content types do not declare the Core model part")
        root_relationship = package_roots[RELS]
        model_target_declared = any(
            posixpath.normpath((_xml_attr(element, "Target") or "").lstrip("/")) == CORE_MODEL
            for element in root_relationship
            if _xml_local(element.tag) == "Relationship"
        )
        if not model_target_declared:
            raise ValueError("3MF package relationships do not target the Core model part")
        model_reports = []
        model_names = sorted(name for name in names if name.lower().endswith(".model"))
        package_names = set(names)
        for model_name in model_names:
            model_reports.append(
                _validate_model_part(
                    archive.read(model_name), part_name=model_name, package_names=package_names
                )
            )
        if CORE_MODEL not in model_names:
            raise ValueError("3MF package has no core model part")
        sliced_names = sorted(name for name in names if name.lower().endswith(".gcode"))
        sliced = bool(sliced_names)
        if require_slice and not sliced:
            raise ValueError("sliced 3MF has no embedded G-code")
        gcode_bytes = sum(archive.getinfo(name).file_size for name in sliced_names)
        if require_slice and gcode_bytes == 0:
            raise ValueError("sliced 3MF contains only empty G-code entries")
    mesh_report = {
        "parts": len(model_reports),
        "objects": sum(report["objects"] for report in model_reports),
        "mesh_parts": sum(report["mesh_parts"] for report in model_reports),
        "vertices": sum(report["vertices"] for report in model_reports),
        "triangles": sum(report["triangles"] for report in model_reports),
        "build_items": sum(report["build_items"] for report in model_reports),
    }
    bounds_reports = [report["bounds"] for report in model_reports if report.get("bounds")]
    if bounds_reports:
        minimum = [min(float(item["min_mm"][axis]) for item in bounds_reports) for axis in range(3)]
        maximum = [max(float(item["max_mm"][axis]) for item in bounds_reports) for axis in range(3)]
        mesh_report["bounds"] = {
            "min_mm": minimum,
            "max_mm": maximum,
            "size_mm": [maximum[axis] - minimum[axis] for axis in range(3)],
        }
    return {
        "path": str(path),
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
        "entries": sorted(names),
        "has_embedded_gcode": sliced,
        "gcode_bytes": gcode_bytes,
        "model": mesh_report,
    }


def _manifest(path: Path, payload: dict[str, object]) -> Path:
    manifest_path = path.with_suffix(path.suffix + ".manifest.json")
    manifest_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return manifest_path


def standard_command(args: argparse.Namespace) -> int:
    mesh = read_stl(Path(args.input), drop_degenerate=args.drop_degenerate)
    report = write_standard_3mf(
        mesh,
        Path(args.output),
        title=args.title,
        require_closed=not args.allow_nonmanifold,
    )
    verified = verify_3mf(Path(args.output))
    payload = {
        "adapter": "standard-core-3mf",
        "adapter_version": 1,
        "python": sys.version.split()[0],
        "input": _portable_path(Path(args.input)),
        "input_sha256": sha256(Path(args.input)),
        "output": _portable_path(Path(args.output)),
        "mesh": {
            "vertices": len(mesh.vertices),
            "triangles": len(mesh.triangles),
            "dropped_degenerate": mesh.dropped_degenerate,
            **report,
        },
        "verification": _portable_verification(verified),
        "determinism": "byte-stable for identical input/title under the same Python/zlib implementation",
    }
    manifest = _manifest(Path(args.output), payload)
    print(json.dumps({"output": str(args.output), "manifest": str(manifest), **report}, indent=2))
    return 0


def openscad_command(args: argparse.Namespace) -> int:
    """Export a SCAD assembly to a native 3MF Core package.

    This is the portable middle path between the dependency-free STL writer
    and the Bambu project exporter.  OpenSCAD performs the boolean union and
    writes the 3MF directly, avoiding the duplicate/coincident facet problem
    that many colour-relief assemblies expose when flattened to STL first.
    """

    input_path = Path(args.input).resolve()
    if input_path.suffix.lower() != ".scad":
        raise RuntimeError("openscad mode requires a .scad input")
    if not input_path.is_file():
        raise RuntimeError(f"SCAD input is not a regular file: {input_path}")
    output_path = Path(args.output).resolve()
    openscad = _resolve_executable(
        args.openscad,
        ("openscad", "/Applications/OpenSCAD.app/Contents/MacOS/OpenSCAD"),
    )
    if not openscad:
        raise RuntimeError("OpenSCAD CLI not found; install it or pass --openscad")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="lenscap-3mf-openscad-") as temp:
        work_dir = Path(temp)
        generated = work_dir / output_path.name
        command = [openscad, "--backend", "Manifold"]
        if args.render_part:
            command.extend(["-D", f"render_part={json.dumps(args.render_part)}"])
        command.extend(["-o", str(generated), str(input_path)])
        code, stdout, stderr = _run(command, timeout=args.timeout)
        if code != 0 or not generated.is_file():
            raise RuntimeError(
                f"OpenSCAD 3MF export failed (rc={code}); command={command!r}\n{stdout}\n{stderr}"
            )
        output_path.write_bytes(generated.read_bytes())
        verified = verify_3mf(output_path)
        payload = {
            "adapter": "openscad-native-3mf",
            "adapter_version": 1,
            "python": sys.version.split()[0],
            "input": _portable_path(input_path),
            "input_sha256": sha256(input_path),
            "output": _portable_path(output_path),
            "render_part": args.render_part or "assembly (SCAD default)",
            "openscad": _portable_executable(openscad),
            "openscad_version": _tool_version(openscad),
            "command": _portable_command(_redact_temp_paths(command, work_dir)),
            "command_exit_code": code,
            "stdout_tail": stdout[-4000:],
            "stderr_tail": stderr[-4000:],
            "verification": _portable_verification(verified),
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "determinism": (
                "geometry/command reproducible; OpenSCAD may vary UUIDs, XML metadata, "
                "and exporter details by release"
            ),
        }
    manifest = _manifest(output_path, payload)
    print(json.dumps({"output": str(output_path), "manifest": str(manifest), **verified}, indent=2))
    return 0


def _prepare_bambu_input(
    input_path: Path,
    openscad: str | None,
    work_dir: Path,
    timeout: int,
) -> tuple[Path, list[str], dict[str, object]]:
    if input_path.suffix.lower() != ".scad":
        return input_path, [], {"input_kind": input_path.suffix.lower().lstrip(".") or "unknown"}
    if not openscad:
        raise RuntimeError("SCAD input requires OpenSCAD; pass --openscad or install openscad")
    stl = work_dir / "model.stl"
    command = [openscad, "--backend", "Manifold", "-q", "-o", str(stl), str(input_path)]
    code, stdout, stderr = _run(command, timeout=timeout)
    if code != 0 or not stl.is_file():
        raise RuntimeError(f"OpenSCAD export failed (rc={code})\n{stdout}\n{stderr}")
    return stl, command, {
        "input_kind": "scad",
        "openscad": _portable_executable(openscad),
        "openscad_version": _tool_version(openscad),
        "openscad_stdout": stdout[-4000:],
        "openscad_stderr": stderr[-4000:],
    }


def bambu_command(args: argparse.Namespace) -> int:
    input_path = Path(args.input).resolve()
    output_path = Path(args.output).resolve()
    bambu = _resolve_executable(
        args.bambu,
        ("bambu-studio", "BambuStudio", "/Applications/BambuStudio.app/Contents/MacOS/BambuStudio"),
    )
    if not bambu:
        raise RuntimeError("Bambu Studio CLI not found; install it or pass --bambu")
    openscad = _resolve_executable(
        args.openscad,
        ("openscad", "/Applications/OpenSCAD.app/Contents/MacOS/OpenSCAD"),
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="lenscap-3mf-adapter-") as temp:
        work_dir = Path(temp)
        prepared, scad_command, prep = _prepare_bambu_input(input_path, openscad, work_dir, args.timeout)
        out_dir = work_dir / "out"
        data_dir = work_dir / "data"
        out_dir.mkdir()
        data_dir.mkdir()
        command = [bambu, "--datadir", "<temporary-datadir>", "--outputdir", "<temporary-outputdir>"]
        executable_command = [bambu, "--datadir", str(data_dir), "--outputdir", str(out_dir)]
        if args.mode == "slice":
            if not args.machine_profile or not args.process_profile or not args.filament_profile:
                raise RuntimeError(
                    "slice mode requires --machine-profile, --process-profile, and --filament-profile"
                )
            command.extend(
                [
                    "--load-settings",
                    f"{Path(args.machine_profile).resolve()};{Path(args.process_profile).resolve()}",
                    "--load-filaments",
                    str(Path(args.filament_profile).resolve()),
                    "--orient",
                    "0",
                    "--arrange",
                    "1",
                    "--slice",
                    "0",
                ]
            )
        command.extend(["--export-3mf", output_path.name, str(prepared)])
        executable_command.extend(command[5:])
        code, stdout, stderr = _run(executable_command, timeout=args.timeout)
        generated = out_dir / output_path.name
        if code != 0 or not generated.is_file():
            raise RuntimeError(
                f"Bambu Studio failed (rc={code}); command={command!r}\n{stdout}\n{stderr}"
            )
        output_path.write_bytes(generated.read_bytes())
        result_payload: object = None
        result_file = out_dir / "result.json"
        if result_file.is_file():
            try:
                result_payload = json.loads(result_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                result_payload = {"unreadable": True}
        verified = verify_3mf(output_path, require_slice=args.mode == "slice")
        payload = {
            "adapter": "bambu-studio-3mf",
            "adapter_version": 1,
            "python": sys.version.split()[0],
            "input": _portable_path(input_path),
            "input_sha256": sha256(input_path),
            "output": _portable_path(output_path),
            "mode": args.mode,
            "bambu": _portable_executable(bambu),
            "bambu_version": _tool_version(bambu),
            # ``command`` is portable/readable; the temporary directories are
            # intentionally redacted because they change on every run.
            "command": _portable_command(_redact_temp_paths(command, work_dir)),
            "command_exit_code": code,
            "stdout_tail": stdout[-4000:],
            "stderr_tail": stderr[-4000:],
            "preparation": prep,
            "bambu_result": result_payload,
            "verification": _portable_verification(verified),
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "determinism": (
                "input/command reproducible; Bambu UUIDs, PNGs, timestamps, and G-code metadata "
                "may vary by release/run"
            ),
            "profiles_are_external": True,
        }
        if scad_command:
            payload["openscad_command"] = _portable_command(
                _redact_temp_paths(scad_command, work_dir)
            )
    manifest = _manifest(output_path, payload)
    print(json.dumps({"output": str(output_path), "manifest": str(manifest), **verified}, indent=2))
    return 0


def fixture_command(args: argparse.Namespace) -> int:
    destination = Path(args.output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    mesh = _mesh_from_triangles(_cube_triangles(args.size))
    stl = destination / "fixture-cube.stl"
    write_ascii_stl(mesh, stl)
    standard = destination / "fixture-cube-standard.3mf"
    report = write_standard_3mf(mesh, standard, title="lens-cap-pipeline fixture cube")
    verify_3mf(standard)
    print(json.dumps({"stl": str(stl), "standard_3mf": str(standard), "mesh": report}, indent=2))
    if args.bambu_requested:
        # Reuse the normal path so profile requirements and provenance are
        # identical to a real job.  Avoid importing this script as a package.
        bambu_args = argparse.Namespace(**vars(args))
        bambu_args.input = str(stl)
        bambu_args.output = str(destination / "fixture-cube-bambu.3mf")
        bambu_args.bambu = args.bambu_path
        bambu_command(bambu_args)
    return 0


def verify_command(args: argparse.Namespace) -> int:
    print(json.dumps(verify_3mf(Path(args.input), require_slice=args.require_slice), indent=2))
    return 0


def parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--timeout", type=int, default=600, help="external-tool timeout in seconds")
    root = argparse.ArgumentParser(description=__doc__, parents=[common])
    sub = root.add_subparsers(dest="command", required=True)

    standard = sub.add_parser("standard", help="write deterministic Core 3MF from an STL")
    standard.add_argument("input")
    standard.add_argument("output")
    standard.add_argument("--title", default="lens-cap-pipeline model")
    standard.add_argument(
        "--drop-degenerate",
        action="store_true",
        help="drop zero-area STL facets (recorded in the manifest)",
    )
    standard.add_argument(
        "--allow-nonmanifold",
        action="store_true",
        help="write an open/non-manifold mesh and report its edge counts",
    )
    standard.set_defaults(func=standard_command)

    openscad = sub.add_parser("openscad", help="export a SCAD assembly to native 3MF")
    openscad.add_argument("input", help="SCAD source")
    openscad.add_argument("output", help="output .3mf")
    openscad.add_argument("--openscad", help="explicit OpenSCAD executable")
    openscad.add_argument(
        "--render-part",
        help="optional generated-SCAD selector (for example assembly or fit_ring)",
    )
    openscad.add_argument(
        "--timeout",
        type=int,
        default=argparse.SUPPRESS,
        help="external-tool timeout in seconds (also accepted before the subcommand)",
    )
    openscad.set_defaults(func=openscad_command)

    bambu = sub.add_parser("bambu", help="export (or slice) through Bambu Studio CLI")
    bambu.add_argument("input", help="STL or SCAD")
    bambu.add_argument("output", help="output .3mf")
    bambu.add_argument("--mode", choices=("export", "slice"), default="export")
    bambu.add_argument("--bambu")
    bambu.add_argument("--openscad")
    bambu.add_argument("--machine-profile")
    bambu.add_argument("--process-profile")
    bambu.add_argument("--filament-profile")
    bambu.add_argument(
        "--timeout",
        type=int,
        default=argparse.SUPPRESS,
        help="external-tool timeout in seconds (also accepted before the subcommand)",
    )
    bambu.set_defaults(func=bambu_command)

    fixture = sub.add_parser("fixture", help="generate a public-domain cube smoke fixture")
    fixture.add_argument("output_dir")
    fixture.add_argument("--size", type=float, default=20.0)
    fixture.add_argument(
        "--bambu",
        dest="bambu_requested",
        action="store_true",
        help="also invoke Bambu Studio export",
    )
    fixture.add_argument("--mode", choices=("export", "slice"), default="export")
    fixture.add_argument("--bambu-path", dest="bambu_path")
    fixture.add_argument("--openscad")
    fixture.add_argument("--machine-profile")
    fixture.add_argument("--process-profile")
    fixture.add_argument("--filament-profile")
    fixture.add_argument(
        "--timeout",
        type=int,
        default=argparse.SUPPRESS,
        help="external-tool timeout in seconds (also accepted before the subcommand)",
    )
    fixture.set_defaults(func=fixture_command)

    verify = sub.add_parser("verify", help="verify a 3MF ZIP/Core package")
    verify.add_argument("input")
    verify.add_argument("--require-slice", action="store_true")
    verify.set_defaults(func=verify_command)
    return root


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        return int(args.func(args))
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, zipfile.BadZipFile) as exc:
        print(f"3mf-adapter: error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
