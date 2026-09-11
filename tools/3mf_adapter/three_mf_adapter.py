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
import csv
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
import uuid
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


def mesh_solid_report(mesh: Mesh) -> dict[str, object]:
    """Audit edge closure, orientation, face connectivity, and volume."""

    edge_triangles: dict[tuple[int, int], list[int]] = {}
    edge_orientation: dict[tuple[int, int], int] = {}
    triangle_volumes: list[float] = []
    for triangle_number, triangle in enumerate(mesh.triangles):
        cross, volume = _triangle_cross_and_volume(mesh.vertices, triangle)
        if sum(value * value for value in cross) <= 1e-20:
            raise ValueError("mesh contains a zero-area triangle")
        triangle_volumes.append(volume)
        for left, right in (
            (triangle[0], triangle[1]),
            (triangle[1], triangle[2]),
            (triangle[2], triangle[0]),
        ):
            edge = (left, right) if left < right else (right, left)
            edge_triangles.setdefault(edge, []).append(triangle_number)
            edge_orientation[edge] = edge_orientation.get(edge, 0) + (
                1 if left < right else -1
            )
    boundary = sum(len(linked) == 1 for linked in edge_triangles.values())
    nonmanifold = sum(len(linked) > 2 for linked in edge_triangles.values())
    inconsistent = sum(
        len(edge_triangles[edge]) == 2 and balance != 0
        for edge, balance in edge_orientation.items()
    )
    triangle_adjacency: list[set[int]] = [set() for _ in mesh.triangles]
    for linked in edge_triangles.values():
        for left in linked:
            triangle_adjacency[left].update(right for right in linked if right != left)
    remaining = set(range(len(mesh.triangles)))
    component_volumes: list[float] = []
    while remaining:
        component = {remaining.pop()}
        stack = list(component)
        while stack:
            current = stack.pop()
            for neighbor in triangle_adjacency[current]:
                if neighbor in remaining:
                    remaining.remove(neighbor)
                    component.add(neighbor)
                    stack.append(neighbor)
        component_volumes.append(sum(triangle_volumes[index] for index in component))
    scale = max(mesh_bounds(mesh)["size_mm"])
    volume_epsilon = max(1e-12, scale**3 * 1e-15)
    zero_volume = sum(abs(value) <= volume_epsilon for value in component_volumes)
    positive_volume = len(component_volumes) - zero_volume
    solid = (
        boundary == 0
        and nonmanifold == 0
        and inconsistent == 0
        and zero_volume == 0
        and positive_volume > 0
    )
    return {
        "solid": solid,
        "unique_edges": len(edge_triangles),
        "boundary_edges": boundary,
        "nonmanifold_edges": nonmanifold,
        "inconsistent_orientation_edges": inconsistent,
        "face_connected_components": len(component_volumes),
        "positive_volume_components": positive_volume,
        "zero_volume_components": zero_volume,
        "absolute_volume_mm3": sum(abs(value) for value in component_volumes),
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


def _triangle_cross_and_volume(
    vertices: Sequence[Sequence[float]], triangle: Sequence[int]
) -> tuple[tuple[float, float, float], float]:
    """Return twice-area vector and signed tetrahedral volume in mm³."""

    a, b, c = (vertices[index] for index in triangle)
    ab = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
    ac = (c[0] - a[0], c[1] - a[1], c[2] - a[2])
    cross = (
        ab[1] * ac[2] - ab[2] * ac[1],
        ab[2] * ac[0] - ab[0] * ac[2],
        ab[0] * ac[1] - ab[1] * ac[0],
    )
    volume = (
        a[0] * (b[1] * c[2] - b[2] * c[1])
        - a[1] * (b[0] * c[2] - b[2] * c[0])
        + a[2] * (b[0] * c[1] - b[1] * c[0])
    ) / 6.0
    return cross, volume


def _sanitize_openscad_core(path: Path) -> dict[str, object]:
    """Remove zero-volume exporter shells and retriangulate collinear facets.

    OpenSCAD can emit a tiny, detached, zero-thickness shell at a colour-mask
    boundary and occasionally triangulates a straight boundary with one
    collinear face.  This repair is deliberately narrow and independent of
    printer resolution: discard only whole zero-volume components, and replace
    a zero-area face plus its long-edge neighbour with an exact subdivision of
    that neighbour at the already-existing middle vertex. No vertex is moved;
    positive-area surfaces and their material properties remain unchanged.
    Ambiguous or merely near-collinear cases fail instead of nudging artwork.
    """

    try:
        with zipfile.ZipFile(path) as source:
            infos = source.infolist()
            entries = {info.filename: source.read(info.filename) for info in infos}
    except (OSError, zipfile.BadZipFile) as exc:
        raise ValueError(f"cannot sanitize OpenSCAD 3MF {path}: {exc}") from exc
    try:
        root = ET.fromstring(entries[CORE_MODEL])
    except (KeyError, ET.ParseError) as exc:
        raise ValueError(f"cannot parse OpenSCAD Core model for sanitization: {exc}") from exc

    # OpenSCAD emits a wall-clock CreationDate and random production-extension
    # UUIDs.  They do not describe geometry, materials, or print intent, but
    # they otherwise make two identical exports hash differently.  Remove the
    # timestamp and replace UUID values after mesh repair with UUIDv5 values
    # derived from the canonical model content.
    removed_volatile_metadata = 0
    for child in list(root):
        if (
            _xml_local(child.tag) == "metadata"
            and str(child.attrib.get("name", "")).casefold() == "creationdate"
        ):
            root.remove(child)
            removed_volatile_metadata += 1

    dropped_components = 0
    dropped_triangles = 0
    dropped_vertices = 0
    retriangulated_collinear_triangles = 0
    collapsed_zero_length_edges = 0
    dropped_collapsed_triangles = 0
    for mesh in (node for node in root.iter() if _xml_local(node.tag) == "mesh"):
        vertices_node = next(
            (node for node in mesh if _xml_local(node.tag) == "vertices"), None
        )
        triangles_node = next(
            (node for node in mesh if _xml_local(node.tag) == "triangles"), None
        )
        if vertices_node is None or triangles_node is None:
            raise ValueError("OpenSCAD Core mesh lacks vertices or triangles")
        vertex_nodes = [
            node for node in vertices_node if _xml_local(node.tag) == "vertex"
        ]
        triangle_nodes = [
            node for node in triangles_node if _xml_local(node.tag) == "triangle"
        ]
        coordinates = [
            tuple(float(_xml_attr(node, axis) or "nan") for axis in "xyz")
            for node in vertex_nodes
        ]
        triangle_indices = [
            tuple(int(_xml_attr(node, name) or "-1") for name in ("v1", "v2", "v3"))
            for node in triangle_nodes
        ]
        if not coordinates or not triangle_indices:
            raise ValueError("OpenSCAD Core mesh is empty")
        if any(not _finite_triplet(vertex) for vertex in coordinates):
            raise ValueError("OpenSCAD Core mesh has a non-finite vertex")
        if any(
            len(set(triangle)) != 3
            or any(index < 0 or index >= len(coordinates) for index in triangle)
            for triangle in triangle_indices
        ):
            raise ValueError("OpenSCAD Core mesh has invalid triangle indices")

        # An exporter may serialize both ends of an existing edge to exactly
        # the same coordinate. Collapse only those zero-length *edges*, never
        # globally weld coincident vertices: unrelated relief contacts can
        # deliberately have separate IDs at the same point. All positive-area
        # facets retain their coordinates and properties; only repeated-index
        # (necessarily zero-area) facets are discarded. Strict post-export
        # manifold validation still rejects an unsafe edge contraction.
        parents = list(range(len(coordinates)))

        def representative(index: int) -> int:
            while parents[index] != index:
                parents[index] = parents[parents[index]]
                index = parents[index]
            return index

        for triangle in triangle_indices:
            for left, right in ((triangle[0], triangle[1]), (triangle[1], triangle[2]), (triangle[2], triangle[0])):
                if coordinates[left] != coordinates[right]:
                    continue
                left_root, right_root = representative(left), representative(right)
                if left_root != right_root:
                    parents[max(left_root, right_root)] = min(left_root, right_root)
                    collapsed_zero_length_edges += 1
        collapsed_indices = []
        collapsed_nodes = []
        for triangle, node in zip(triangle_indices, triangle_nodes, strict=True):
            resolved = tuple(representative(index) for index in triangle)
            if len(set(resolved)) < 3:
                cross, _ = _triangle_cross_and_volume(coordinates, triangle)
                if any(value != 0 for value in cross):
                    raise ValueError("zero-length edge contraction would remove a positive-area facet")
                dropped_collapsed_triangles += 1
                continue
            collapsed_indices.append(resolved)
            collapsed_nodes.append(node)
        triangle_indices = collapsed_indices
        triangle_nodes = collapsed_nodes

        adjacency: list[set[int]] = [set() for _ in coordinates]
        for triangle in triangle_indices:
            a, b, c = triangle
            adjacency[a].update((b, c))
            adjacency[b].update((a, c))
            adjacency[c].update((a, b))
        referenced = {index for triangle in triangle_indices for index in triangle}
        remaining = set(referenced)
        components: list[set[int]] = []
        component_by_vertex: dict[int, int] = {}
        while remaining:
            component = {remaining.pop()}
            stack = list(component)
            while stack:
                current = stack.pop()
                for neighbor in adjacency[current]:
                    if neighbor in remaining:
                        remaining.remove(neighbor)
                        component.add(neighbor)
                        stack.append(neighbor)
            component_id = len(components)
            components.append(component)
            for index in component:
                component_by_vertex[index] = component_id
        component_triangles: list[list[int]] = [[] for _ in components]
        component_volumes = [0.0] * len(components)
        for triangle_number, triangle in enumerate(triangle_indices):
            component_id = component_by_vertex[triangle[0]]
            component_triangles[component_id].append(triangle_number)
            _, volume = _triangle_cross_and_volume(coordinates, triangle)
            component_volumes[component_id] += volume
        kept_components: set[int] = set()
        for component_id, (component, volume) in enumerate(
            zip(components, component_volumes, strict=True)
        ):
            minimum = [min(coordinates[index][axis] for index in component) for axis in range(3)]
            maximum = [max(coordinates[index][axis] for index in component) for axis in range(3)]
            size = [maximum[axis] - minimum[axis] for axis in range(3)]
            scale = max(size, default=0.0)
            epsilon = max(1e-12, scale**3 * 1e-15)
            if any(value <= 1e-9 for value in size) or abs(volume) <= epsilon:
                dropped_components += 1
                dropped_triangles += len(component_triangles[component_id])
            else:
                kept_components.add(component_id)
        kept_triangle_numbers = [
            index
            for index, triangle in enumerate(triangle_indices)
            if component_by_vertex[triangle[0]] in kept_components
        ]
        if not kept_triangle_numbers:
            raise ValueError("OpenSCAD sanitization removed every zero-volume component")

        # Keep a conforming triangulation without removing a boundary point.
        # Simply dropping a collinear triangle leaves a T-junction: its long
        # edge belongs to one face while the two short edges belong to others.
        edge_to_triangles: dict[tuple[int, int], set[int]] = {}

        def update_edges(triangle_number: int, *, add: bool) -> None:
            triangle = triangle_indices[triangle_number]
            for left, right in (
                (triangle[0], triangle[1]),
                (triangle[1], triangle[2]),
                (triangle[2], triangle[0]),
            ):
                edge = (left, right) if left < right else (right, left)
                if add:
                    edge_to_triangles.setdefault(edge, set()).add(triangle_number)
                else:
                    edge_to_triangles[edge].discard(triangle_number)

        kept = set(kept_triangle_numbers)
        for triangle_number in kept_triangle_numbers:
            update_edges(triangle_number, add=True)
        for triangle_number in kept_triangle_numbers:
            if triangle_number not in kept:
                continue
            triangle = triangle_indices[triangle_number]
            cross, _ = _triangle_cross_and_volume(coordinates, triangle)
            if sum(value * value for value in cross) > 1e-20:
                continue
            if any(value != 0.0 for value in cross):
                raise ValueError("cannot retriangulate a merely near-collinear OpenSCAD face without changing its surface")
            pairs = (
                (triangle[0], triangle[1], triangle[2]),
                (triangle[1], triangle[2], triangle[0]),
                (triangle[2], triangle[0], triangle[1]),
            )
            endpoint_a, endpoint_b, middle = max(
                pairs,
                key=lambda item: sum(
                    (coordinates[item[0]][axis] - coordinates[item[1]][axis]) ** 2
                    for axis in range(3)
                ),
            )
            line = [
                coordinates[endpoint_b][axis] - coordinates[endpoint_a][axis]
                for axis in range(3)
            ]
            length_squared = sum(value * value for value in line)
            along = sum(
                (coordinates[middle][axis] - coordinates[endpoint_a][axis]) * line[axis]
                for axis in range(3)
            )
            if not 0 < along < length_squared:
                raise ValueError("collinear OpenSCAD face lacks a strict interior edge vertex")
            edge = tuple(sorted((endpoint_a, endpoint_b)))
            neighbors = edge_to_triangles.get(edge, set()) - {triangle_number}
            if len(neighbors) != 1:
                raise ValueError("collinear OpenSCAD face lacks one unambiguous long-edge neighbour")
            neighbor_number = next(iter(neighbors))
            neighbor = triangle_indices[neighbor_number]
            neighbor_cross, _ = _triangle_cross_and_volume(coordinates, neighbor)
            if sum(value * value for value in neighbor_cross) <= 1e-20:
                raise ValueError("collinear OpenSCAD long-edge neighbour is also degenerate")
            node = triangle_nodes[neighbor_number]
            property_one = _xml_attr(node, "p1")
            if any(
                (_xml_attr(node, name) or property_one) != property_one
                for name in ("p2", "p3")
            ):
                raise ValueError("cannot subdivide nonuniform vertex material properties without interpolation")
            for first, second, third in (
                (neighbor[0], neighbor[1], neighbor[2]),
                (neighbor[1], neighbor[2], neighbor[0]),
                (neighbor[2], neighbor[0], neighbor[1]),
            ):
                if {first, second} == {endpoint_a, endpoint_b}:
                    children = ((first, middle, third), (middle, second, third))
                    break
            child_crosses = [
                _triangle_cross_and_volume(coordinates, child)[0] for child in children
            ]
            if any(
                sum(value * value for value in child_cross) <= 1e-20
                or sum(a * b for a, b in zip(child_cross, neighbor_cross, strict=True)) <= 0
                for child_cross in child_crosses
            ):
                raise ValueError("collinear subdivision would create degenerate or reversed facets")
            if any(
                not math.isclose(
                    sum(child_cross[axis] for child_cross in child_crosses),
                    neighbor_cross[axis], rel_tol=1e-12, abs_tol=1e-12,
                )
                for axis in range(3)
            ):
                raise ValueError("collinear subdivision did not preserve the original oriented area")
            update_edges(triangle_number, add=False)
            update_edges(neighbor_number, add=False)
            kept.remove(triangle_number)
            triangle_indices[neighbor_number] = children[0]
            triangle_indices.append(children[1])
            triangle_nodes.append(ET.Element(node.tag, dict(node.attrib)))
            new_number = len(triangle_indices) - 1
            kept.add(new_number)
            update_edges(neighbor_number, add=True)
            update_edges(new_number, add=True)
            retriangulated_collinear_triangles += 1
        kept_triangle_numbers = sorted(kept)

        kept_vertices = sorted(
            {
                index
                for triangle_number in kept_triangle_numbers
                for index in triangle_indices[triangle_number]
            }
        )
        remap = {old: new for new, old in enumerate(kept_vertices)}
        dropped_vertices += len(vertex_nodes) - len(kept_vertices)
        for node in list(vertices_node):
            if _xml_local(node.tag) == "vertex":
                vertices_node.remove(node)
        for old_index in kept_vertices:
            node = vertex_nodes[old_index]
            # Preserve the producer's coordinate strings exactly; even a
            # harmless decimal reformat is unnecessary for an index-only fix.
            vertices_node.append(node)
        for node in list(triangles_node):
            if _xml_local(node.tag) == "triangle":
                triangles_node.remove(node)
        for triangle_number in kept_triangle_numbers:
            node = triangle_nodes[triangle_number]
            for name, old_index in zip(
                ("v1", "v2", "v3"), triangle_indices[triangle_number], strict=True
            ):
                node.set(name, str(remap[old_index]))
            triangles_node.append(node)

    uuid_attributes: list[tuple[ET.Element, str]] = []
    for node in root.iter():
        for attribute in tuple(node.attrib):
            if _xml_local(attribute).casefold() == "uuid":
                uuid_attributes.append((node, attribute))
                node.set(attribute, "00000000-0000-0000-0000-000000000000")
    semantic_seed = hashlib.sha256(ET.tostring(root, encoding="utf-8")).hexdigest()
    for index, (node, attribute) in enumerate(uuid_attributes):
        label = (
            f"lens-cap-pipeline:openscad-core:{semantic_seed}:{index}:"
            f"{_xml_local(node.tag)}:{node.attrib.get('id', '')}:"
            f"{node.attrib.get('objectid', '')}"
        )
        node.set(attribute, str(uuid.uuid5(uuid.NAMESPACE_URL, label)))

    entries[CORE_MODEL] = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    with tempfile.NamedTemporaryFile(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, delete=False
    ) as handle:
        temporary = Path(handle.name)
    try:
        with zipfile.ZipFile(temporary, "w") as target:
            for name in sorted(entries):
                info, payload = _zip_entry(name, entries[name])
                target.writestr(info, payload)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return {
        "status": "passed",
        "sanitizer_version": 2,
        "dropped_zero_volume_components": dropped_components,
        "dropped_triangles": dropped_triangles,
        "dropped_vertices": dropped_vertices,
        "retriangulated_collinear_triangles": retriangulated_collinear_triangles,
        "collapsed_zero_length_edges": collapsed_zero_length_edges,
        "dropped_collapsed_triangles": dropped_collapsed_triangles,
        "nudged_collinear_triangles": 0,
        "maximum_nudge_mm": 0.0,
        "vertex_coordinates_preserved": True,
        "removed_volatile_metadata": removed_volatile_metadata,
        "canonicalized_uuid_attributes": len(uuid_attributes),
        "archive_entries_canonicalized": len(entries),
        "scope": (
            "zero_volume_components_zero_length_edges_exact_vertex_collinear_retriangulation_and_"
            "nonsemantic_openscad_metadata"
        ),
    }


def _validate_model_part(
    payload: bytes,
    *,
    part_name: str,
    package_names: set[str],
    require_closed: bool,
) -> dict[str, object]:
    """Validate one 3MF model XML part and its mesh/component references."""

    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise ValueError(f"invalid XML in 3MF model part {part_name}: {exc}") from exc
    if _xml_local(root.tag) != "model":
        raise ValueError(f"3MF model part {part_name} has root {_xml_local(root.tag)!r}, expected model")
    unit = (_xml_attr(root, "unit") or "").strip().casefold()
    if unit != "millimeter":
        raise ValueError(
            f"3MF model part {part_name} must declare unit='millimeter', got {unit or 'missing'!r}"
        )
    resources = next((child for child in root if _xml_local(child.tag) == "resources"), None)
    if resources is None:
        raise ValueError(f"3MF model part {part_name} lacks resources")
    property_child_names = {
        "basematerials": "base",
        "colorgroup": "color",
        "texture2dgroup": "tex2coord",
        "compositematerials": "composite",
        "multiproperties": "multi",
    }
    property_resource_sizes: dict[str, int] = {}
    for resource in resources:
        local_name = _xml_local(resource.tag)
        child_name = property_child_names.get(local_name)
        if child_name is None:
            continue
        resource_id = _xml_attr(resource, "id")
        if not resource_id or resource_id in property_resource_sizes:
            raise ValueError(
                f"3MF model part {part_name} has duplicate/missing property resource id"
            )
        count = sum(_xml_local(child.tag) == child_name for child in resource)
        if count <= 0:
            raise ValueError(
                f"3MF property resource {resource_id!r} in {part_name} is empty"
            )
        property_resource_sizes[resource_id] = count

    def property_index(raw: str | None, *, label: str, size: int) -> int:
        if raw is None or not re.fullmatch(r"[0-9]+", raw):
            raise ValueError(f"{label} in {part_name} is malformed or negative")
        value = int(raw)
        if value >= size:
            raise ValueError(f"{label} in {part_name} is out of range")
        return value

    objects = [element for element in resources.iter() if _xml_local(element.tag) == "object"]
    object_ids: set[str] = set()
    mesh_object_ids: set[str] = set()
    mesh_vertices = 0
    mesh_triangles = 0
    mesh_parts = 0
    mesh_boundary_edges = 0
    mesh_nonmanifold_edges = 0
    mesh_inconsistent_orientation_edges = 0
    mesh_components = 0
    mesh_unused_vertices = 0
    mesh_zero_area_triangles = 0
    mesh_duplicate_triangles = 0
    mesh_zero_volume_components = 0
    mesh_volume_components = 0
    mesh_absolute_volume_mm3 = 0.0
    component_references = 0
    transformed_components = 0
    transformed_build_items = 0
    minimum = [float("inf")] * 3
    maximum = [float("-inf")] * 3
    for obj in objects:
        object_id = _xml_attr(obj, "id")
        if not object_id or object_id in object_ids:
            raise ValueError(f"3MF model part {part_name} has duplicate/missing object id")
        object_ids.add(object_id)
        object_pid = _xml_attr(obj, "pid")
        object_pindex = _xml_attr(obj, "pindex")
        if object_pid is None and object_pindex is not None:
            raise ValueError(
                f"3MF object {object_id!r} in {part_name} has pindex without pid"
            )
        if object_pid is not None:
            if object_pid not in property_resource_sizes:
                raise ValueError(
                    f"3MF object {object_id!r} in {part_name} references missing property resource"
                )
            property_index(
                object_pindex,
                label=f"3MF object {object_id!r} pindex",
                size=property_resource_sizes[object_pid],
            )
        mesh = next((child for child in obj if _xml_local(child.tag) == "mesh"), None)
        if mesh is not None:
            mesh_object_ids.add(object_id)
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
            coordinates_by_index: list[tuple[float, float, float]] = []
            for vertex in vertices:
                try:
                    coordinates = tuple(float(_xml_attr(vertex, axis) or "nan") for axis in "xyz")
                except ValueError as exc:
                    raise ValueError(f"3MF mesh in {part_name} has malformed vertex") from exc
                if not _finite_triplet(coordinates):
                    raise ValueError(f"3MF mesh in {part_name} has non-finite vertex")
                coordinates_by_index.append(coordinates)
                for axis, value in enumerate(coordinates):
                    minimum[axis] = min(minimum[axis], value)
                    maximum[axis] = max(maximum[axis], value)
            referenced: set[int] = set()
            edge_counts: dict[tuple[int, int], int] = {}
            edge_orientation: dict[tuple[int, int], int] = {}
            edge_triangles: dict[tuple[int, int], list[int]] = {}
            adjacency: list[set[int]] = [set() for _ in vertices]
            parsed_triangles: list[tuple[int, int, int]] = []
            triangle_volumes: list[float] = []
            seen_faces: set[tuple[int, int, int]] = set()
            zero_area_triangles = 0
            duplicate_triangles = 0
            for triangle_number, triangle in enumerate(triangles):
                try:
                    indices = tuple(
                        int(_xml_attr(triangle, name) or "-1") for name in ("v1", "v2", "v3")
                    )
                except ValueError as exc:
                    raise ValueError(f"3MF mesh in {part_name} has malformed triangle") from exc
                if any(index < 0 or index >= len(vertices) for index in indices):
                    raise ValueError(f"3MF mesh in {part_name} has out-of-range triangle index")
                if len(set(indices)) != 3:
                    raise ValueError(f"3MF mesh in {part_name} has a repeated-index triangle")
                triangle_pid = _xml_attr(triangle, "pid")
                raw_property_values = [
                    _xml_attr(triangle, name) for name in ("p1", "p2", "p3")
                ]
                effective_pid = triangle_pid if triangle_pid is not None else object_pid
                has_triangle_property = any(value is not None for value in raw_property_values)
                if effective_pid is None:
                    if has_triangle_property:
                        raise ValueError(
                            f"3MF triangle in {part_name} has property indices without pid"
                        )
                else:
                    if effective_pid not in property_resource_sizes:
                        raise ValueError(
                            f"3MF triangle in {part_name} references missing property resource"
                        )
                    first_property = (
                        raw_property_values[0]
                        if raw_property_values[0] is not None
                        else object_pindex
                    )
                    size = property_resource_sizes[effective_pid]
                    property_index(
                        first_property,
                        label="3MF triangle p1",
                        size=size,
                    )
                    for property_name, raw_property in zip(
                        ("p2", "p3"), raw_property_values[1:], strict=True
                    ):
                        if raw_property is not None:
                            property_index(
                                raw_property,
                                label=f"3MF triangle {property_name}",
                                size=size,
                            )
                face_key = tuple(sorted(indices))
                if face_key in seen_faces:
                    duplicate_triangles += 1
                seen_faces.add(face_key)
                cross, signed_volume = _triangle_cross_and_volume(
                    coordinates_by_index, indices
                )
                if sum(value * value for value in cross) <= 1e-20:
                    zero_area_triangles += 1
                parsed_triangles.append(indices)
                triangle_volumes.append(signed_volume)
                referenced.update(indices)
                for left, right in (
                    (indices[0], indices[1]),
                    (indices[1], indices[2]),
                    (indices[2], indices[0]),
                ):
                    edge = (left, right) if left < right else (right, left)
                    edge_counts[edge] = edge_counts.get(edge, 0) + 1
                    edge_triangles.setdefault(edge, []).append(triangle_number)
                    edge_orientation[edge] = edge_orientation.get(edge, 0) + (
                        1 if left < right else -1
                    )
                    adjacency[left].add(right)
                    adjacency[right].add(left)
            unused = len(vertices) - len(referenced)
            if require_closed and unused:
                raise ValueError(
                    f"3MF mesh in {part_name} has {unused} unreferenced vertices"
                )
            boundary_edges = sum(value == 1 for value in edge_counts.values())
            nonmanifold_edges = sum(value > 2 for value in edge_counts.values())
            inconsistent_orientation_edges = sum(
                edge_counts[edge] == 2 and balance != 0
                for edge, balance in edge_orientation.items()
            )
            if require_closed and (boundary_edges or nonmanifold_edges):
                raise ValueError(
                    f"3MF mesh in {part_name} is not edge-closed/manifold: "
                    f"boundary_edges={boundary_edges}, nonmanifold_edges={nonmanifold_edges}"
                )
            if require_closed and inconsistent_orientation_edges:
                raise ValueError(
                    f"3MF mesh in {part_name} has {inconsistent_orientation_edges} "
                    "inconsistently oriented shared edges"
                )
            # Components are connected through shared *edges*, not merely a
            # shared vertex. Two solids that touch at one point are not a
            # printable one-piece volume.
            triangle_adjacency: list[set[int]] = [set() for _ in parsed_triangles]
            for linked in edge_triangles.values():
                for left in linked:
                    triangle_adjacency[left].update(
                        right for right in linked if right != left
                    )
            remaining_triangles = set(range(len(parsed_triangles)))
            component_triangle_lists: list[set[int]] = []
            component_vertices_list: list[set[int]] = []
            while remaining_triangles:
                component_triangles = {remaining_triangles.pop()}
                stack = list(component_triangles)
                while stack:
                    current = stack.pop()
                    for neighbor in triangle_adjacency[current]:
                        if neighbor in remaining_triangles:
                            remaining_triangles.remove(neighbor)
                            component_triangles.add(neighbor)
                            stack.append(neighbor)
                component_triangle_lists.append(component_triangles)
                component_vertices_list.append(
                    {
                        vertex
                        for triangle_index in component_triangles
                        for vertex in parsed_triangles[triangle_index]
                    }
                )
            component_count = len(component_vertices_list)
            component_signed_volumes = [
                sum(triangle_volumes[index] for index in component_triangles)
                for component_triangles in component_triangle_lists
            ]
            zero_volume_components = 0
            for component, signed_volume in zip(
                component_vertices_list, component_signed_volumes, strict=True
            ):
                component_minimum = [
                    min(coordinates_by_index[index][axis] for index in component)
                    for axis in range(3)
                ]
                component_maximum = [
                    max(coordinates_by_index[index][axis] for index in component)
                    for axis in range(3)
                ]
                component_size = [
                    component_maximum[axis] - component_minimum[axis]
                    for axis in range(3)
                ]
                scale = max(component_size, default=0.0)
                volume_epsilon = max(1e-12, scale**3 * 1e-15)
                if (
                    any(size <= 1e-9 for size in component_size)
                    or abs(signed_volume) <= volume_epsilon
                ):
                    zero_volume_components += 1
                else:
                    mesh_absolute_volume_mm3 += abs(signed_volume)
                    mesh_volume_components += 1
            if require_closed and duplicate_triangles:
                raise ValueError(
                    f"3MF mesh in {part_name} has {duplicate_triangles} duplicate triangles"
                )
            if require_closed and zero_area_triangles:
                raise ValueError(
                    f"3MF mesh in {part_name} has {zero_area_triangles} zero-area triangles"
                )
            if require_closed and zero_volume_components:
                raise ValueError(
                    f"3MF mesh in {part_name} has {zero_volume_components} zero-volume components"
                )
            mesh_parts += 1
            mesh_vertices += len(vertices)
            mesh_triangles += len(triangles)
            mesh_boundary_edges += boundary_edges
            mesh_nonmanifold_edges += nonmanifold_edges
            mesh_inconsistent_orientation_edges += inconsistent_orientation_edges
            mesh_components += component_count
            mesh_unused_vertices += unused
            mesh_zero_area_triangles += zero_area_triangles
            mesh_duplicate_triangles += duplicate_triangles
            mesh_zero_volume_components += zero_volume_components
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
                component_references += 1
                if _xml_attr(component, "transform"):
                    transformed_components += 1
            if require_closed:
                raise ValueError(
                    f"strict native verification does not accept component objects in {part_name}"
                )
    if not objects:
        raise ValueError(f"3MF model part {part_name} has no objects")
    build = next((child for child in root if _xml_local(child.tag) == "build"), None)
    if part_name == CORE_MODEL and build is None:
        raise ValueError("3MF core model lacks build")
    build_items = 0
    build_object_ids: list[str] = []
    if build is not None:
        for item in build.iter():
            if _xml_local(item.tag) != "item":
                continue
            object_id = _xml_attr(item, "objectid")
            if object_id not in object_ids:
                raise ValueError(f"3MF build item in {part_name} references missing object {object_id!r}")
            if _xml_attr(item, "transform"):
                transformed_build_items += 1
                if require_closed:
                    raise ValueError(
                        f"strict native verification does not accept build transforms in {part_name}"
                    )
            build_items += 1
            build_object_ids.append(str(object_id))
    if part_name == CORE_MODEL and build_items == 0:
        raise ValueError("3MF core model has an empty build")
    if require_closed and part_name == CORE_MODEL:
        if build_items != 1:
            raise ValueError(
                f"strict native verification requires one build item, got {build_items}"
            )
        if build_object_ids[0] not in mesh_object_ids:
            raise ValueError("strict native build item does not resolve to a direct mesh")
        if mesh_object_ids != set(build_object_ids):
            raise ValueError("strict native model contains unbuilt or duplicate mesh resources")
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
        "boundary_edges": mesh_boundary_edges,
        "nonmanifold_edges": mesh_nonmanifold_edges,
        "inconsistent_orientation_edges": mesh_inconsistent_orientation_edges,
        "mesh_components": mesh_components,
        "unreferenced_vertices": mesh_unused_vertices,
        "zero_area_triangles": mesh_zero_area_triangles,
        "duplicate_triangles": mesh_duplicate_triangles,
        "zero_volume_components": mesh_zero_volume_components,
        "volume_components": mesh_volume_components,
        "absolute_volume_mm3": mesh_absolute_volume_mm3,
        "component_references": component_references,
        "transformed_components": transformed_components,
        "transformed_build_items": transformed_build_items,
        "unit": unit,
        "build_items": build_items,
        "bounds": bounds,
    }


_GCODE_NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"


def _bambu_gcode_blocks(text: str, *, name: str) -> dict[str, str]:
    """Return the three non-overlapping Bambu blocks from one G-code file."""

    matches: dict[str, tuple[re.Match[str], re.Match[str]]] = {}
    for label in ("HEADER", "CONFIG", "EXECUTABLE"):
        starts = list(
            re.finditer(rf"(?m)^;[ \t]*{label}_BLOCK_START[ \t]*$", text)
        )
        ends = list(
            re.finditer(rf"(?m)^;[ \t]*{label}_BLOCK_END[ \t]*$", text)
        )
        if len(starts) != 1 or len(ends) != 1 or starts[0].end() > ends[0].start():
            raise ValueError(
                f"sliced G-code has malformed {label.lower()} block markers: {name}"
            )
        matches[label] = (starts[0], ends[0])
    header_start, header_end = matches["HEADER"]
    config_start, config_end = matches["CONFIG"]
    executable_start, executable_end = matches["EXECUTABLE"]
    if not (
        header_end.end() <= config_start.start()
        and config_end.end() <= executable_start.start()
    ):
        raise ValueError(f"sliced G-code has overlapping or reordered Bambu blocks: {name}")
    return {
        "header": text[header_start.end() : header_end.start()],
        "config": text[config_start.end() : config_end.start()],
        "executable": text[executable_start.end() : executable_end.start()],
    }


def _single_gcode_header_value(block: str, key: str, *, name: str) -> str:
    matches = re.findall(
        rf"(?mi)^;[ \t]*{re.escape(key)}[ \t]*:[ \t]*(.*?)[ \t]*$",
        block,
    )
    if len(matches) != 1 or not matches[0].strip():
        raise ValueError(f"sliced G-code header lacks one {key!r} value: {name}")
    return matches[0].strip()


def _finite_gcode_float(value: str, *, label: str, name: str) -> float:
    if re.fullmatch(_GCODE_NUMBER, value.strip()) is None:
        raise ValueError(f"sliced G-code has invalid {label}: {name}")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"sliced G-code has non-finite {label}: {name}")
    return result


def _gcode_config_assignments(block: str) -> dict[str, list[str]]:
    assignments: dict[str, list[str]] = {}
    pattern = r"(?m)^;[ \t]*([A-Za-z][A-Za-z0-9_]*)[ \t]*=[ \t]*(.*?)[ \t]*$"
    for match in re.finditer(pattern, block):
        assignments.setdefault(match.group(1).casefold(), []).append(match.group(2).strip())
    return assignments


def _one_gcode_config_value(
    assignments: dict[str, list[str]], key: str, *, name: str
) -> str:
    values = assignments.get(key.casefold(), [])
    if len(values) != 1 or not values[0]:
        raise ValueError(f"sliced G-code config lacks one {key!r} value: {name}")
    return values[0]


def _unquoted_gcode_value(value: str) -> str:
    result = value.strip()
    if len(result) >= 2 and result[0] == result[-1] and result[0] in {'"', "'"}:
        return result[1:-1].strip()
    return result


def _gcode_positive_numeric_list(value: str, *, key: str, name: str) -> list[float]:
    """Parse vendor comma-separated numeric vectors without dropping entries."""
    values = [
        _finite_gcode_float(token.strip(), label=f"config {key}", name=name)
        for token in _unquoted_gcode_value(value).split(",")
    ]
    if any(number <= 0 for number in values):
        raise ValueError(f"sliced G-code config {key} must be positive: {name}")
    return values


def _gcode_profile_value_list(value: str, *, key: str, name: str) -> list[str]:
    """Read Bambu's semicolon-delimited, optionally quoted profile strings."""
    try:
        values = next(csv.reader([value], delimiter=";", escapechar="\\", strict=True, skipinitialspace=True))
    except (csv.Error, StopIteration) as exc:
        raise ValueError(f"sliced G-code has invalid config {key}: {name}") from exc
    values = [item.strip() for item in values]
    if not values or any(not item for item in values):
        raise ValueError(f"sliced G-code has empty material/profile configuration: {name}")
    return values


def _audit_bambu_gcode(
    payload: bytes,
    *,
    name: str,
    nozzle_diameters: Sequence[float],
) -> dict[str, object]:
    """Audit semantic layer/toolpath evidence instead of trusting slicer comments."""

    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"sliced G-code is not UTF-8 text: {name}") from exc
    blocks = _bambu_gcode_blocks(text, name=name)
    header_max_z = _finite_gcode_float(
        _single_gcode_header_value(blocks["header"], "max_z_height", name=name),
        label="max height metadata",
        name=name,
    )
    layer_text = _single_gcode_header_value(
        blocks["header"], "total layer number", name=name
    )
    if not layer_text.isdecimal():
        raise ValueError(f"sliced G-code has invalid total layer count: {name}")
    declared_layer_count = int(layer_text)
    if header_max_z <= 0 or declared_layer_count <= 0:
        raise ValueError(f"sliced G-code has invalid build metadata: {name}")

    # Bambu also emits ``LAYER_HEIGHT`` for individual features (for example a
    # bridge laid at a different Z) inside a layer.  Only the declaration that
    # belongs to each CHANGE_LAYER block describes the layer stack.  Binding Z,
    # height, and the sequential progress marker within the same block avoids
    # both that false positive and accepting an unrelated bag of comments.
    layer_markers = list(
        re.finditer(r"(?mi)^;[ \t]*CHANGE_LAYER[ \t]*$", blocks["executable"])
    )
    all_progress_markers = list(
        re.finditer(
            r"(?mi)^;[ \t]*layer num/total_layer_count[ \t]*:[ \t]*"
            r"([0-9]+)/([0-9]+)[ \t]*$",
            blocks["executable"],
        )
    )
    has_progress_markers = bool(all_progress_markers)
    declared_z: list[float] = []
    declared_heights: list[float] = []
    progress_values: list[tuple[int, int]] = []
    for index, marker in enumerate(layer_markers):
        segment_end = (
            layer_markers[index + 1].start()
            if index + 1 < len(layer_markers)
            else len(blocks["executable"])
        )
        segment = blocks["executable"][marker.end() : segment_end]
        layer_header_matches = list(
            re.finditer(
                rf"(?mi)^;[ \t]*Z_HEIGHT[ \t]*:[ \t]*({_GCODE_NUMBER})[ \t]*$"
                rf"\r?\n(?:[ \t]*\r?\n)*"
                rf"^;[ \t]*LAYER_HEIGHT[ \t]*:[ \t]*({_GCODE_NUMBER})[ \t]*$",
                segment,
            )
        )
        progress_matches = list(
            re.finditer(
                r"(?mi)^;[ \t]*layer num/total_layer_count[ \t]*:[ \t]*"
                r"([0-9]+)/([0-9]+)[ \t]*$",
                segment,
            )
        )
        if len(layer_header_matches) != 1 or (
            has_progress_markers and len(progress_matches) != 1
        ) or (not has_progress_markers and progress_matches):
            raise ValueError(
                "sliced G-code layer structure does not match its declared total: "
                f"{name}"
            )
        layer_header = layer_header_matches[0]
        declared_z.append(
            _finite_gcode_float(
                layer_header.group(1), label="declared layer Z", name=name
            )
        )
        declared_heights.append(
            _finite_gcode_float(
                layer_header.group(2),
                label="declared layer height",
                name=name,
            )
        )
        if progress_matches:
            progress_values.append(
                (int(progress_matches[0].group(1)), int(progress_matches[0].group(2)))
            )
    if (
        len(layer_markers) != declared_layer_count
        or len(all_progress_markers) not in {0, declared_layer_count}
        or (
            has_progress_markers
            and progress_values
            != [
                (index, declared_layer_count)
                for index in range(1, declared_layer_count + 1)
            ]
        )
    ):
        raise ValueError(
            "sliced G-code layer structure does not match its declared total: "
            f"{name}"
        )
    if any(value <= 0 for value in declared_heights) or any(
        right <= left for left, right in zip(declared_z, declared_z[1:])
    ):
        raise ValueError(f"sliced G-code layer structure is not strictly increasing: {name}")

    assignments = _gcode_config_assignments(blocks["config"])
    numeric_config: dict[str, float] = {}
    for key in (
        "layer_height",
        "initial_layer_print_height",
    ):
        numeric_config[key] = _finite_gcode_float(
            _unquoted_gcode_value(
                _one_gcode_config_value(assignments, key, name=name)
            ),
            label=f"config {key}",
            name=name,
        )
        if numeric_config[key] <= 0:
            raise ValueError(f"sliced G-code config {key} must be positive: {name}")
    diameter_config = {
        key: _gcode_positive_numeric_list(
            _one_gcode_config_value(assignments, key, name=name), key=key, name=name,
        ) for key in ("nozzle_diameter", "filament_diameter")
    }
    text_values = {
        key: _gcode_profile_value_list(
            _one_gcode_config_value(assignments, key, name=name), key=key, name=name,
        )
        for key in (
            "filament_type",
            "print_settings_id",
            "printer_settings_id",
            "filament_settings_id",
        )
    }
    if not nozzle_diameters or any(not math.isfinite(nozzle) or nozzle <= 0 for nozzle in nozzle_diameters):
        raise ValueError(f"sliced G-code audit lacks valid expected nozzle diameters: {name}")
    if any(
        not any(
            math.isclose(configured, nozzle, rel_tol=0.0, abs_tol=1e-6)
            for nozzle in nozzle_diameters
        )
        for configured in diameter_config["nozzle_diameter"]
    ):
        raise ValueError(f"sliced G-code nozzle disagrees with slice metadata: {name}")

    nominal_height = numeric_config["layer_height"]
    first_height = numeric_config["initial_layer_print_height"]
    layer_tolerance = max(0.002, min(nozzle_diameters) * 0.02)
    derived_heights = [
        declared_z[0],
        *(right - left for left, right in zip(declared_z, declared_z[1:])),
    ]
    if any(
        not math.isclose(declared, derived, rel_tol=0.0, abs_tol=layer_tolerance)
        for declared, derived in zip(declared_heights, derived_heights, strict=True)
    ):
        raise ValueError(f"sliced G-code layer height comments disagree with layer Z: {name}")
    if not math.isclose(declared_z[0], first_height, rel_tol=0.0, abs_tol=layer_tolerance):
        raise ValueError(f"sliced G-code first layer disagrees with its config: {name}")
    maximum_reasonable_layer = max(
        max(nozzle_diameters) * 2.0,
        nominal_height * 2.5,
        0.5,
    )
    if max(declared_heights) > maximum_reasonable_layer:
        raise ValueError(f"sliced G-code contains an unreasonable layer height: {name}")
    if not math.isclose(
        declared_z[-1], header_max_z, rel_tol=0.0, abs_tol=layer_tolerance
    ):
        raise ValueError(f"sliced G-code final layer disagrees with max height: {name}")

    xyz_absolute: bool | None = None
    extrusion_absolute: bool | None = None
    millimetre_units = False
    position: dict[str, float | None] = {"X": None, "Y": None, "Z": None, "E": 0.0}
    positive_path_moves = 0
    positive_extrusion = 0.0
    path_length = 0.0
    path_points: list[tuple[float, float]] = []
    path_z: list[float] = []
    saw_xyz_mode = False
    saw_extrusion_mode = False
    active_layer_index = -1
    startup_xy_extrusion_moves = 0
    for raw_line in blocks["executable"].splitlines():
        if re.fullmatch(r";[ \t]*CHANGE_LAYER[ \t]*", raw_line, re.IGNORECASE):
            active_layer_index += 1
        code = raw_line.split(";", 1)[0].strip()
        match = re.match(r"^(?:N\d+\s+)?([GMT]\d+(?:\.\d+)?)\b", code, re.IGNORECASE)
        if match is None:
            continue
        command = match.group(1).upper()
        if command == "G90":
            xyz_absolute = True
            saw_xyz_mode = True
            continue
        if command == "G91":
            xyz_absolute = False
            saw_xyz_mode = True
            continue
        if command == "M82":
            extrusion_absolute = True
            saw_extrusion_mode = True
            continue
        if command == "M83":
            extrusion_absolute = False
            saw_extrusion_mode = True
            continue
        if command == "G21":
            millimetre_units = True
            continue
        if command == "G20":
            millimetre_units = False
            continue
        values = {
            axis.upper(): float(value)
            for axis, value in re.findall(
                rf"(?:^|\s)([XYZE])({_GCODE_NUMBER})(?=\s|$)",
                code,
                re.IGNORECASE,
            )
        }
        if any(not math.isfinite(value) for value in values.values()):
            raise ValueError(f"sliced G-code contains a non-finite coordinate: {name}")
        if command == "G92":
            for axis, value in values.items():
                position[axis] = value
            continue
        if command not in {"G0", "G1"}:
            continue
        before = dict(position)
        if xyz_absolute is not None:
            for axis in "XYZ":
                if axis not in values:
                    continue
                prior = position[axis]
                position[axis] = (
                    values[axis]
                    if xyz_absolute or prior is None
                    else float(prior) + values[axis]
                )
        extrusion_delta = 0.0
        if "E" in values and extrusion_absolute is not None:
            prior_e = float(position["E"] or 0.0)
            if extrusion_absolute:
                extrusion_delta = values["E"] - prior_e
                position["E"] = values["E"]
            else:
                extrusion_delta = values["E"]
                position["E"] = prior_e + values["E"]
        before_x, before_y = before["X"], before["Y"]
        after_x, after_y, after_z = position["X"], position["Y"], position["Z"]
        if (
            extrusion_delta <= 1e-9
            or before_x is None
            or before_y is None
            or after_x is None
            or after_y is None
            or after_z is None
        ):
            continue
        distance = math.hypot(float(after_x) - float(before_x), float(after_y) - float(before_y))
        if distance <= max(1e-6, min(nozzle_diameters) * 1e-4):
            continue
        # H2C's startup purge may deposit at Z5.8 before the first actual
        # CHANGE_LAYER. Track its modes/position but do not misclassify it as
        # a model layer. Reverse-Z checks remain unchanged within the print.
        if active_layer_index < 0:
            startup_xy_extrusion_moves += 1
            continue
        positive_path_moves += 1
        positive_extrusion += extrusion_delta
        path_length += distance
        path_points.extend(
            ((float(before_x), float(before_y)), (float(after_x), float(after_y)))
        )
        path_z.append(float(after_z))

    if not (saw_xyz_mode and saw_extrusion_mode and millimetre_units):
        raise ValueError(f"sliced G-code toolpath lacks explicit millimetre/motion modes: {name}")
    if not path_z or any(value <= 0 for value in path_z):
        raise ValueError(f"sliced G-code has no positive-Z extrusion toolpath: {name}")
    z_tolerance = max(0.0005, min(nozzle_diameters) * 0.005)
    actual_z: list[float] = []
    per_layer_moves: list[int] = []
    for value in path_z:
        if actual_z and value < actual_z[-1] - z_tolerance:
            raise ValueError(f"sliced G-code extrusion layers move backwards in Z: {name}")
        if not actual_z or value > actual_z[-1] + z_tolerance:
            actual_z.append(value)
            per_layer_moves.append(1)
        else:
            per_layer_moves[-1] += 1
    if len(actual_z) != declared_layer_count or any(
        not math.isclose(actual, declared, rel_tol=0.0, abs_tol=z_tolerance)
        for actual, declared in zip(actual_z, declared_z, strict=True)
    ):
        raise ValueError(
            "sliced G-code layer structure lacks extrusion on every declared layer: "
            f"{name}"
        )
    if positive_path_moves < max(20, declared_layer_count) or min(per_layer_moves) < 1:
        raise ValueError(f"sliced G-code has too few deposited paths for its layers: {name}")
    if positive_extrusion <= 0 or not math.isfinite(path_length):
        raise ValueError(f"sliced G-code has invalid positive extrusion totals: {name}")
    x_values = [point[0] for point in path_points]
    y_values = [point[1] for point in path_points]
    x_span = max(x_values) - min(x_values)
    y_span = max(y_values) - min(y_values)
    minimum_span = max(1.0, min(nozzle_diameters) * 5.0)
    quantization = max(0.01, min(nozzle_diameters) * 0.05)
    unique_xy = {
        (round(x / quantization), round(y / quantization)) for x, y in path_points
    }
    minimum_unique_xy = max(12, min(declared_layer_count, 64))
    if (
        x_span < minimum_span
        or y_span < minimum_span
        or len(unique_xy) < minimum_unique_xy
        or path_length <= max(x_span, y_span)
    ):
        raise ValueError(f"sliced G-code extrusion paths lack spatial range/diversity: {name}")
    return {
        "header_max_z_mm": header_max_z,
        "declared_layer_count": declared_layer_count,
        "observed_extrusion_layer_count": len(actual_z),
        "first_layer_z_mm": actual_z[0],
        "last_layer_z_mm": actual_z[-1],
        "minimum_layer_height_mm": min(declared_heights),
        "maximum_layer_height_mm": max(declared_heights),
        "positive_xy_extrusion_moves": positive_path_moves,
        "startup_xy_extrusion_moves_excluded_from_layers": startup_xy_extrusion_moves,
        "positive_extrusion_mm": positive_extrusion,
        "extrusion_path_length_mm": path_length,
        "unique_extrusion_xy_points": len(unique_xy),
        "extrusion_xy_min_mm": [min(x_values), min(y_values)],
        "extrusion_xy_max_mm": [max(x_values), max(y_values)],
        "extrusion_xy_size_mm": [x_span, y_span],
        "config": {
            "layer_height_mm": nominal_height,
            "initial_layer_height_mm": first_height,
            # Legacy scalar fields retain the first value for existing report
            # consumers; validation below binds the complete ordered vectors.
            "nozzle_diameter_mm": diameter_config["nozzle_diameter"][0],
            "filament_diameter_mm": diameter_config["filament_diameter"][0],
            "nozzle_diameters_mm": diameter_config["nozzle_diameter"],
            "filament_diameters_mm": diameter_config["filament_diameter"],
            **{key: values[0] for key, values in text_values.items()},
            "profile_values": text_values,
        },
    }


def _assembled_model_bounds(archive: zipfile.ZipFile) -> dict[str, list[float]]:
    """Measure built vertices after every nested Core/Production transform.

    Local resource bounds are intentionally retained in legacy mesh reports;
    they are not the placed model envelope used to bind a sliced G-code file.
    """
    roots = {name: ET.fromstring(archive.read(name)) for name in archive.namelist() if name.lower().endswith(".model")}
    objects = {
        name: {_xml_attr(obj, "id"): obj for obj in root.iter() if _xml_local(obj.tag) == "object"}
        for name, root in roots.items()
    }
    minimum, maximum = [math.inf] * 3, [-math.inf] * 3
    identity = (1., 0., 0., 0., 1., 0., 0., 0., 1., 0., 0., 0.)

    def matrix(node: ET.Element) -> tuple[float, ...]:
        raw = _xml_attr(node, "transform")
        if raw is None:
            return identity
        try:
            values = tuple(float(value) for value in raw.split())
        except ValueError as exc:
            raise ValueError("assembled model has a malformed transform") from exc
        if len(values) != 12 or not all(math.isfinite(value) for value in values):
            raise ValueError("assembled model has a malformed/non-finite transform")
        return values

    def target(node: ET.Element, current: str) -> str:
        path = _xml_attr(node, "path")
        if path is None:
            return current
        direct = posixpath.normpath(path.lstrip("/"))
        if direct in roots:
            return direct
        return posixpath.normpath(posixpath.join(posixpath.dirname(current), path))

    def visit(part: str, object_id: str | None, transforms: tuple[tuple[float, ...], ...], seen: frozenset[tuple[str, str | None]]) -> None:
        key = (part, object_id)
        if key in seen:
            raise ValueError("assembled model has cyclic component references")
        obj = objects.get(part, {}).get(object_id)
        if obj is None:
            raise ValueError(f"assembled model references missing object {part}:{object_id}")
        for child in obj:
            if _xml_local(child.tag) == "mesh":
                vertices = next(node for node in child if _xml_local(node.tag) == "vertices")
                triangles = next(node for node in child if _xml_local(node.tag) == "triangles")
                used = {int(_xml_attr(face, axis)) for face in triangles for axis in ("v1", "v2", "v3")}
                for index in used:
                    point = tuple(float(_xml_attr(vertices[index], axis)) for axis in "xyz")
                    for transform in transforms:
                        point = tuple(sum(point[row] * transform[row * 3 + axis] for row in range(3)) + transform[9 + axis] for axis in range(3))
                    if not all(math.isfinite(value) for value in point):
                        raise ValueError("assembled model has non-finite transformed vertices")
                    for axis, value in enumerate(point):
                        minimum[axis], maximum[axis] = min(minimum[axis], value), max(maximum[axis], value)
            elif _xml_local(child.tag) == "components":
                for component in child:
                    visit(target(component, part), _xml_attr(component, "objectid"), (matrix(component),) + transforms, seen | {key})

    build = next((node for node in roots[CORE_MODEL] if _xml_local(node.tag) == "build"), None)
    if build is None:
        raise ValueError("assembled model lacks a build")
    for item in build:
        visit(target(item, CORE_MODEL), _xml_attr(item, "objectid"), (matrix(item),), frozenset())
    if not all(math.isfinite(value) for value in minimum + maximum):
        raise ValueError("assembled model has no built vertices")
    return {"min_mm": minimum, "max_mm": maximum, "size_mm": [high - low for low, high in zip(minimum, maximum, strict=True)]}


def verify_3mf(
    path: Path,
    *,
    require_slice: bool = False,
    require_closed: bool = False,
    require_single_volume: bool = False,
) -> dict[str, object]:
    """Check a 3MF package, optionally enforcing direct closed native geometry.

    Bambu projects commonly represent aligned, touching colour parts as an
    assembly whose aggregate Core mesh has shared/non-manifold edges.  That is
    valid project semantics and is audited separately.  ``require_closed`` is
    therefore reserved for the canonical, unsliced, direct-mesh one-piece
    package.
    """

    if require_single_volume:
        require_closed = True
    slice_audit: dict[str, object] | None = None
    assembled_bounds: dict[str, list[float]] | None = None
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
        if require_closed and model_names != [CORE_MODEL]:
            raise ValueError(
                "strict native verification requires exactly one Core model part"
            )
        package_names = set(names)
        for model_name in model_names:
            model_reports.append(
                _validate_model_part(
                    archive.read(model_name),
                    part_name=model_name,
                    package_names=package_names,
                    require_closed=require_closed,
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
        if require_slice:
            assembled_bounds = _assembled_model_bounds(archive)
            slice_info_name = "Metadata/slice_info.config"
            model_settings_name = "Metadata/model_settings.config"
            if slice_info_name not in names or model_settings_name not in names:
                raise ValueError(
                    "sliced 3MF requires slice_info and model_settings metadata"
                )
            try:
                slice_info = ET.fromstring(archive.read(slice_info_name))
                model_settings = ET.fromstring(archive.read(model_settings_name))
            except ET.ParseError as exc:
                raise ValueError(f"sliced 3MF contains invalid slice metadata: {exc}") from exc
            declared_gcode = {
                str(node.attrib.get("value"))
                for node in model_settings.iter("metadata")
                if node.attrib.get("key") == "gcode_file" and node.attrib.get("value")
            }
            if declared_gcode != set(sliced_names):
                raise ValueError(
                    "model_settings gcode_file bindings do not match embedded G-code"
                )
            slice_plates = list(slice_info.iter("plate"))
            if not slice_plates:
                raise ValueError("slice_info contains no plate")
            slice_objects = [node for plate in slice_plates for node in plate.findall("object")]
            if not slice_objects or any(
                not str(node.attrib.get("name", "")).strip()
                or str(node.attrib.get("skipped", "")).casefold() != "false"
                for node in slice_objects
            ):
                raise ValueError("slice_info lacks a non-skipped named print object")
            used_filaments = [
                node
                for plate in slice_plates
                for node in plate.findall("filament")
                if str(node.attrib.get("used_for_object", "")).casefold() == "true"
            ]
            if not used_filaments:
                raise ValueError("slice_info declares no filament used for the object")
            nozzles = [node for plate in slice_plates for node in plate.findall("nozzle")]
            try:
                nozzle_values = [
                    float(node.attrib.get("nozzle_diameter", "nan"))
                    for node in nozzles
                ]
                if not nozzle_values or any(
                    not math.isfinite(value) or value <= 0 for value in nozzle_values
                ):
                    raise ValueError
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError("slice_info declares no valid nozzle diameter") from exc

            project_settings_name = "Metadata/project_settings.config"
            if project_settings_name not in names:
                raise ValueError("sliced 3MF requires Bambu project settings metadata")
            try:
                project_settings = json.loads(
                    archive.read(project_settings_name).decode("utf-8-sig")
                )
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("sliced 3MF has invalid Bambu project settings") from exc
            if not isinstance(project_settings, dict):
                raise ValueError("sliced 3MF project settings must be a JSON object")

            def project_values(key: str) -> list[object]:
                raw = project_settings.get(key)
                if isinstance(raw, list):
                    return list(raw)
                return [] if raw is None else [raw]

            project_numbers: dict[str, list[float]] = {}
            for key in (
                "layer_height",
                "initial_layer_print_height",
                "nozzle_diameter",
                "filament_diameter",
            ):
                try:
                    values = [float(value) for value in project_values(key)]
                except (TypeError, ValueError, OverflowError) as exc:
                    raise ValueError(
                        f"sliced 3MF project settings have invalid {key!r}"
                    ) from exc
                if not values or any(not math.isfinite(value) or value <= 0 for value in values):
                    raise ValueError(
                        f"sliced 3MF project settings lack positive {key!r}"
                    )
                project_numbers[key] = values
            project_text: dict[str, set[str]] = {}
            project_text_ordered: dict[str, list[str]] = {}
            for key in (
                "filament_type",
                "print_settings_id",
                "printer_settings_id",
                "filament_settings_id",
            ):
                values = {
                    str(value).strip()
                    for value in project_values(key)
                    if str(value).strip()
                }
                if not values:
                    raise ValueError(
                        f"sliced 3MF project settings lack non-empty {key!r}"
                    )
                project_text[key] = values
                project_text_ordered[key] = [str(value).strip() for value in project_values(key)]
            if any(
                not any(
                    math.isclose(nozzle, configured, rel_tol=0.0, abs_tol=1e-6)
                    for configured in project_numbers["nozzle_diameter"]
                )
                for nozzle in nozzle_values
            ):
                raise ValueError("slice_info nozzle disagrees with Bambu project settings")
            used_filament_types = {
                str(node.attrib.get("type", "")).strip()
                for node in used_filaments
                if str(node.attrib.get("type", "")).strip()
            }
            if not used_filament_types or not {
                value.casefold() for value in used_filament_types
            }.issubset({value.casefold() for value in project_text["filament_type"]}):
                raise ValueError(
                    "slice_info filament type disagrees with Bambu project settings"
                )

            plate_layer_counts: list[int | None] = []
            for plate in slice_plates:
                layer_range_nodes = list(plate.findall("./layer_filament_lists/layer_filament_list"))
                if not layer_range_nodes:
                    plate_layer_counts.append(None)
                    continue
                maximum_layer = -1
                for node in layer_range_nodes:
                    raw = str(node.attrib.get("layer_ranges", "")).strip()
                    if re.fullmatch(r"\d+(?:\s+\d+)+", raw) is None:
                        raise ValueError("slice_info has malformed layer filament ranges")
                    endpoints = [int(value) for value in raw.split()]
                    if len(endpoints) % 2 or any(
                        start > end
                        for start, end in zip(endpoints[::2], endpoints[1::2], strict=True)
                    ):
                        raise ValueError("slice_info has invalid layer filament ranges")
                    maximum_layer = max(maximum_layer, *endpoints[1::2])
                plate_layer_counts.append(maximum_layer + 1)

            gcode_digests: dict[str, str] = {}
            gcode_semantics: list[dict[str, object]] = []
            for gcode_name in sliced_names:
                payload = archive.read(gcode_name)
                digest_name = gcode_name + ".md5"
                if digest_name not in names:
                    raise ValueError(f"sliced G-code lacks MD5 sidecar: {gcode_name}")
                expected_digest = archive.read(digest_name).decode("ascii", errors="strict").strip()
                if not re.fullmatch(r"[0-9a-fA-F]{32}", expected_digest):
                    raise ValueError(f"sliced G-code has an invalid MD5 sidecar: {gcode_name}")
                actual_digest = hashlib.md5(payload, usedforsecurity=False).hexdigest()
                if actual_digest.casefold() != expected_digest.casefold():
                    raise ValueError(f"sliced G-code MD5 mismatch: {gcode_name}")
                gcode_digests[gcode_name] = actual_digest
                semantic = _audit_bambu_gcode(
                    payload,
                    name=gcode_name,
                    nozzle_diameters=nozzle_values,
                )
                config = semantic["config"]
                assert isinstance(config, dict)
                for key, project_key in (
                    ("layer_height_mm", "layer_height"),
                    ("initial_layer_height_mm", "initial_layer_print_height"),
                ):
                    if not any(
                        math.isclose(
                            float(config[key]),
                            value,
                            rel_tol=0.0,
                            abs_tol=1e-6,
                        )
                        for value in project_numbers[project_key]
                    ):
                        raise ValueError(
                            f"sliced G-code {key} disagrees with project settings: "
                            f"{gcode_name}"
                        )
                for key, project_key in (
                    ("nozzle_diameters_mm", "nozzle_diameter"),
                    ("filament_diameters_mm", "filament_diameter"),
                ):
                    actual = config[key]
                    expected = project_numbers[project_key]
                    if len(actual) != len(expected) or any(
                        not math.isclose(float(left), right, rel_tol=0.0, abs_tol=1e-6)
                        for left, right in zip(actual, expected, strict=True)
                    ):
                        raise ValueError(f"sliced G-code {key} disagrees with ordered project settings: {gcode_name}")
                for key in (
                    "filament_type",
                    "print_settings_id",
                    "printer_settings_id",
                    "filament_settings_id",
                ):
                    if config["profile_values"][key] != project_text_ordered[key]:
                        raise ValueError(
                            f"sliced G-code {key} disagrees with ordered project settings: "
                            f"{gcode_name}"
                        )
                gcode_semantics.append(semantic)
            if len(gcode_semantics) == len(plate_layer_counts) == 1:
                metadata_layer_count = plate_layer_counts[0]
                if (
                    metadata_layer_count is not None
                    and metadata_layer_count
                    != int(gcode_semantics[0]["declared_layer_count"])
                ):
                    raise ValueError(
                        "slice_info layer ranges disagree with embedded G-code layer count"
                    )
            slice_audit = {
                "status": "passed",
                "gcode_files": sliced_names,
                "gcode_md5": gcode_digests,
                "plate_count": len(slice_plates),
                "print_object_count": len(slice_objects),
                "used_object_filament_ids": [
                    str(node.attrib.get("id", "")) for node in used_filaments
                ],
                "nozzle_diameters_mm": nozzle_values,
                "extrusion_move_count": sum(
                    int(item["positive_xy_extrusion_moves"])
                    for item in gcode_semantics
                ),
                "header_max_z_mm": max(
                    float(item["header_max_z_mm"]) for item in gcode_semantics
                ),
                "gcode_semantics": gcode_semantics,
                "project_settings_audit": {
                    "status": "passed",
                    "layer_height_mm": project_numbers["layer_height"],
                    "initial_layer_height_mm": project_numbers[
                        "initial_layer_print_height"
                    ],
                    "nozzle_diameter_mm": project_numbers["nozzle_diameter"],
                    "filament_diameter_mm": project_numbers["filament_diameter"],
                    "filament_type": sorted(project_text["filament_type"]),
                },
                "slice_info_layer_counts": plate_layer_counts,
                "scope": (
                    "gcode_md5_bambu_blocks_config_layers_monotonic_z_positive_"
                    "extrusion_path_range_diversity_slice_info_project_settings_"
                    "object_filament_nozzle_and_model_bounds"
                ),
            }
    mesh_report = {
        "parts": len(model_reports),
        "objects": sum(report["objects"] for report in model_reports),
        "mesh_parts": sum(report["mesh_parts"] for report in model_reports),
        "vertices": sum(report["vertices"] for report in model_reports),
        "triangles": sum(report["triangles"] for report in model_reports),
        "boundary_edges": sum(report["boundary_edges"] for report in model_reports),
        "nonmanifold_edges": sum(report["nonmanifold_edges"] for report in model_reports),
        "inconsistent_orientation_edges": sum(
            report["inconsistent_orientation_edges"] for report in model_reports
        ),
        "mesh_components": sum(report["mesh_components"] for report in model_reports),
        "unreferenced_vertices": sum(
            report["unreferenced_vertices"] for report in model_reports
        ),
        "component_references": sum(
            report["component_references"] for report in model_reports
        ),
        "transformed_components": sum(
            report["transformed_components"] for report in model_reports
        ),
        "transformed_build_items": sum(
            report["transformed_build_items"] for report in model_reports
        ),
        "zero_area_triangles": sum(
            report["zero_area_triangles"] for report in model_reports
        ),
        "duplicate_triangles": sum(
            report["duplicate_triangles"] for report in model_reports
        ),
        "zero_volume_components": sum(
            report["zero_volume_components"] for report in model_reports
        ),
        "volume_components": sum(
            report["volume_components"] for report in model_reports
        ),
        "absolute_volume_mm3": sum(
            report["absolute_volume_mm3"] for report in model_reports
        ),
        "build_items": sum(report["build_items"] for report in model_reports),
    }
    if require_single_volume and mesh_report["volume_components"] != 1:
        raise ValueError(
            "strict one-piece verification requires exactly one positive-volume "
            f"component, got {mesh_report['volume_components']}"
        )
    bounds_reports = [report["bounds"] for report in model_reports if report.get("bounds")]
    if bounds_reports:
        minimum = [min(float(item["min_mm"][axis]) for item in bounds_reports) for axis in range(3)]
        maximum = [max(float(item["max_mm"][axis]) for item in bounds_reports) for axis in range(3)]
        mesh_report["bounds"] = {
            "min_mm": minimum,
            "max_mm": maximum,
            "size_mm": [maximum[axis] - minimum[axis] for axis in range(3)],
        }
    if require_slice:
        assert slice_audit is not None
        bounds = assembled_bounds
        if not isinstance(bounds, dict):
            raise ValueError("sliced 3MF has no model bounds for G-code binding")
        model_height = float(bounds["max_mm"][2])
        slice_audit["assembled_model_bounds"] = bounds
        header_height = float(slice_audit["header_max_z_mm"])
        if not math.isclose(model_height, header_height, rel_tol=0.0, abs_tol=0.5):
            raise ValueError(
                "sliced G-code max height disagrees with the embedded model: "
                f"{header_height:g} != {model_height:g} mm"
            )
        semantics = slice_audit.get("gcode_semantics")
        if not isinstance(semantics, list) or not semantics:
            raise ValueError("sliced 3MF lacks semantic G-code audit results")
        model_xy_size = [float(bounds["size_mm"][axis]) for axis in range(2)]
        gcode_min = [
            min(float(item["extrusion_xy_min_mm"][axis]) for item in semantics)
            for axis in range(2)
        ]
        gcode_max = [
            max(float(item["extrusion_xy_max_mm"][axis]) for item in semantics)
            for axis in range(2)
        ]
        gcode_xy_size = [
            gcode_max[axis] - gcode_min[axis] for axis in range(2)
        ]
        nozzle_values = [float(value) for value in slice_audit["nozzle_diameters_mm"]]
        for axis, label in enumerate(("X", "Y")):
            model_span = model_xy_size[axis]
            toolpath_span = gcode_xy_size[axis]
            if model_span <= 0:
                raise ValueError(f"sliced 3MF model has no positive {label} span")
            if toolpath_span < max(min(nozzle_values) * 5.0, model_span * 0.1):
                raise ValueError(
                    f"sliced G-code {label} extrusion span is too small for the model"
                )
            if toolpath_span > max(model_span * 5.0, model_span + 100.0):
                raise ValueError(
                    f"sliced G-code {label} extrusion span is implausible for the model"
                )
        total_path_length = sum(
            float(item["extrusion_path_length_mm"]) for item in semantics
        )
        if total_path_length <= max(model_xy_size):
            raise ValueError("sliced G-code deposited path is too short for the model bounds")
        for item in semantics:
            config = item["config"]
            assert isinstance(config, dict)
            nominal = float(config["layer_height_mm"])
            first = float(config["initial_layer_height_mm"])
            minimum = float(item["minimum_layer_height_mm"])
            maximum = float(item["maximum_layer_height_mm"])
            tolerance = max(0.002, min(nozzle_values) * 0.02)
            if math.isclose(minimum, nominal, rel_tol=0.0, abs_tol=tolerance) and math.isclose(
                maximum, nominal, rel_tol=0.0, abs_tol=tolerance
            ):
                estimated_layers = 1 + round(max(0.0, model_height - first) / nominal)
                if abs(estimated_layers - int(item["declared_layer_count"])) > 1:
                    raise ValueError(
                        "sliced G-code layer count disagrees with model height and layer config"
                    )
        slice_audit["model_height_mm"] = model_height
        slice_audit["model_xy_size_mm"] = model_xy_size
        slice_audit["extrusion_xy_min_mm"] = gcode_min
        slice_audit["extrusion_xy_max_mm"] = gcode_max
        slice_audit["extrusion_xy_size_mm"] = gcode_xy_size
        slice_audit["extrusion_path_length_mm"] = total_path_length
    return {
        "path": str(path),
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
        "entries": sorted(names),
        "has_embedded_gcode": sliced,
        "gcode_bytes": gcode_bytes,
        "slice_audit": slice_audit,
        "closed_direct_mesh_required": require_closed,
        "single_positive_volume_required": require_single_volume,
        "model": mesh_report,
    }


def _read_direct_core_mesh(path: Path) -> Mesh:
    """Read the one directly-built Core mesh after strict package checks."""

    verify_3mf(path, require_closed=True)
    with zipfile.ZipFile(path) as archive:
        root = ET.fromstring(archive.read(CORE_MODEL))
    mesh_nodes = [node for node in root.iter() if _xml_local(node.tag) == "mesh"]
    if len(mesh_nodes) != 1:
        raise ValueError(f"direct Core conversion requires one mesh, got {len(mesh_nodes)}")
    mesh_node = mesh_nodes[0]
    vertices_node = next(
        (node for node in mesh_node if _xml_local(node.tag) == "vertices"), None
    )
    triangles_node = next(
        (node for node in mesh_node if _xml_local(node.tag) == "triangles"), None
    )
    if vertices_node is None or triangles_node is None:
        raise ValueError("direct Core mesh lacks vertices or triangles")
    vertices = tuple(
        tuple(float(_xml_attr(node, axis) or "nan") for axis in "xyz")
        for node in vertices_node
        if _xml_local(node.tag) == "vertex"
    )
    triangles = tuple(
        tuple(int(_xml_attr(node, name) or "-1") for name in ("v1", "v2", "v3"))
        for node in triangles_node
        if _xml_local(node.tag) == "triangle"
    )
    return Mesh(vertices=vertices, triangles=triangles)


def write_binary_stl(mesh: Mesh, output: Path) -> None:
    """Write a deterministic binary STL from an already-audited mesh."""

    if len(mesh.triangles) > 0xFFFFFFFF:
        raise ValueError("mesh has too many triangles for binary STL")
    output.parent.mkdir(parents=True, exist_ok=True)
    header = b"lens-cap-pipeline audited Core mesh"[:80].ljust(80, b"\0")
    with output.open("wb") as stream:
        stream.write(header)
        stream.write(struct.pack("<I", len(mesh.triangles)))
        for triangle in mesh.triangles:
            a, b, c = (mesh.vertices[index] for index in triangle)
            cross, _ = _triangle_cross_and_volume(mesh.vertices, triangle)
            length = math.sqrt(sum(value * value for value in cross))
            if length <= 1e-10:
                raise ValueError("cannot write a zero-area triangle to STL")
            normal = tuple(value / length for value in cross)
            stream.write(
                struct.pack(
                    "<12fH",
                    *normal,
                    *a,
                    *b,
                    *c,
                    0,
                )
            )


def _manifest(path: Path, payload: dict[str, object]) -> Path:
    manifest_path = path.with_suffix(path.suffix + ".manifest.json")
    manifest_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return manifest_path


def _bambu_flush_values(settings: dict[str, object], field: str) -> list[object]:
    values = settings.get(field, [])
    if not isinstance(values, list):
        raise ValueError(f"Bambu {field} must be a list")
    for value in values:
        try:
            valid = not isinstance(value, bool) and math.isfinite(float(value)) and float(value) >= 0
        except (TypeError, ValueError, OverflowError):
            valid = False
        if not valid:
            raise ValueError(f"Bambu {field} contains an invalid purge volume: {value!r}")
    return values


def _audit_bambu_flush_configuration(settings: dict[str, object], count: int) -> dict[str, object]:
    matrix = _bambu_flush_values(settings, "flush_volumes_matrix")
    vector = _bambu_flush_values(settings, "flush_volumes_vector")
    if len(matrix) != count * count or len(vector) != 2 * count:
        raise ValueError(
            f"Bambu purge volumes do not match {count} filament slots: "
            f"matrix={len(matrix)} (expected {count * count}), "
            f"vector={len(vector)} (expected {2 * count})"
        )
    # Bambu's preset JSON reader expects numeric options as string tokens.
    # JSON number tokens pass our numerical validation but its native loader
    # rejects a mixed array before slicing.
    if any(not isinstance(value, str) for value in (*matrix, *vector)):
        raise ValueError("Bambu purge volumes must be serialized as JSON string tokens")
    return {"status": "passed", "filament_slots": count,
            "matrix_length": len(matrix), "vector_length": len(vector),
            "json_scalar_type": "string"}


def _resize_bambu_flush_configuration(settings: dict[str, object], count: int) -> dict[str, object]:
    """Extend Bambu's four-slot defaults without losing directional purge pairs.

    Vendor defaults are 140 mm^3 load/unload and 280 mm^3 per transition,
    with zero diagonal. These are defaults, not a color-calibrated estimate.
    https://github.com/bambulab/BambuStudio/blob/master/src/libslic3r/PrintConfig.cpp
    """
    if count < 1:
        raise ValueError("Bambu purge configuration requires at least one filament slot")
    matrix = _bambu_flush_values(settings, "flush_volumes_matrix")
    vector = _bambu_flush_values(settings, "flush_volumes_vector")
    old_count = math.isqrt(len(matrix))
    if old_count * old_count != len(matrix) or len(vector) % 2:
        raise ValueError("Bambu purge matrix must be square and load/unload vector must have even length")
    # Remap row-major indices: appending to a flat 4x4 list would corrupt all
    # transitions after its first row when the palette grows to five colors.
    settings["flush_volumes_matrix"] = [
        str(matrix[row * old_count + column])
        if row < old_count and column < old_count
        else ("0" if row == column else "280")
        for row in range(count) for column in range(count)
    ]
    settings["flush_volumes_vector"] = [
        str(vector[index]) if index < len(vector) else "140" for index in range(2 * count)
    ]
    return {
        **_audit_bambu_flush_configuration(settings, count),
        "previous_matrix_slots": old_count,
        "previous_vector_slots": len(vector) // 2,
        "preserved_matrix_values": min(old_count, count) ** 2,
        "preserved_vector_values": min(len(vector), 2 * count),
        "new_transition_default_mm3": 280,
        "new_load_unload_default_mm3": 140,
        "defaults_source": "BambuStudio/src/libslic3r/PrintConfig.cpp",
        "scope": "cardinality_and_finite_nonnegative_volumes_not_color_calibration",
    }


def _patch_bambu_project_colors(
    path: Path, colors: Sequence[str], *, filament_count: int | None = None,
) -> dict[str, object]:
    """Set palette and matching purge tables in an official Bambu project."""

    normalized: list[str] = []
    for value in colors:
        color = str(value).strip().upper()
        if not re.fullmatch(r"#[0-9A-F]{6}", color):
            raise ValueError(f"invalid Bambu filament colour: {value!r}")
        normalized.append(color)
    count = filament_count if filament_count is not None else len(normalized)
    if normalized and len(normalized) != count:
        raise ValueError("Bambu palette does not match the requested filament count")
    if not normalized and not count:
        return {"status": "not_requested", "colors": []}
    settings_name = "Metadata/project_settings.config"
    try:
        with zipfile.ZipFile(path) as source:
            infos = source.infolist()
            if settings_name not in source.namelist():
                raise ValueError("Bambu project has no Metadata/project_settings.config")
            settings = json.loads(source.read(settings_name).decode("utf-8"))
            if not isinstance(settings, dict):
                raise ValueError("Bambu project settings are not a JSON object")
            if normalized:
                settings["filament_colour"] = normalized
                settings["default_filament_colour"] = normalized
            flush_configuration = _resize_bambu_flush_configuration(settings, count)
            replacement = (
                json.dumps(settings, ensure_ascii=False, separators=(",", ":")) + "\n"
            ).encode("utf-8")
            entries = [
                (
                    info,
                    replacement
                    if info.filename == settings_name
                    else source.read(info.filename),
                )
                for info in infos
            ]
        with tempfile.NamedTemporaryFile(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, delete=False
        ) as handle:
            temporary = Path(handle.name)
        try:
            with zipfile.ZipFile(temporary, "w") as target:
                for info, payload in entries:
                    target.writestr(info, payload)
            os.replace(temporary, path)
        finally:
            if temporary.exists():
                temporary.unlink()
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, zipfile.BadZipFile) as exc:
        raise ValueError(f"cannot patch Bambu project palette: {exc}") from exc
    return {"status": "passed", "colors": normalized, "flush_configuration": flush_configuration}


def standard_command(args: argparse.Namespace) -> int:
    mesh = read_stl(Path(args.input), drop_degenerate=args.drop_degenerate)
    report = write_standard_3mf(
        mesh,
        Path(args.output),
        title=args.title,
        require_closed=not args.allow_nonmanifold,
    )
    verified = verify_3mf(
        Path(args.output), require_closed=not args.allow_nonmanifold
    )
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


def verify_stl_command(args: argparse.Namespace) -> int:
    mesh = read_stl(Path(args.input))
    report = mesh_solid_report(mesh)
    if not report["solid"]:
        raise ValueError(f"STL is not a closed oriented positive-volume solid: {report}")
    if args.require_single_volume and report["positive_volume_components"] != 1:
        raise ValueError(
            "STL requires exactly one positive-volume component, got "
            f"{report['positive_volume_components']}"
        )
    print(
        json.dumps(
            {
                "status": "passed",
                "path": str(Path(args.input)),
                "sha256": sha256(Path(args.input)),
                "vertices": len(mesh.vertices),
                "triangles": len(mesh.triangles),
                "bounds": mesh_bounds(mesh),
                **report,
            },
            indent=2,
        )
    )
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
        sanitization = _sanitize_openscad_core(generated)
        verified = verify_3mf(
            generated,
            require_closed=True,
            require_single_volume=args.require_single_volume,
        )
        output_path.write_bytes(generated.read_bytes())
        verified["path"] = str(output_path)
        payload = {
            "adapter": "openscad-native-3mf",
            "adapter_version": 2,
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
            "sanitization": sanitization,
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "determinism": (
                "byte-stable for identical OpenSCAD geometry under the same "
                "OpenSCAD/Python/zlib toolchain after nonsemantic metadata canonicalization; "
                "exporter geometry may vary by OpenSCAD release"
            ),
        }
    manifest = _manifest(output_path, payload)
    print(json.dumps({"output": str(output_path), "manifest": str(manifest), **verified}, indent=2))
    return 0


def openscad_stl_command(args: argparse.Namespace) -> int:
    """Export a selector through audited Core 3MF, then convert it to STL.

    Direct OpenSCAD STL output can contain degenerate/non-manifold mask
    facets even when its native Core exporter represents the same solids
    correctly.  The Bambu multipart path uses this conversion so every input
    colour part is independently closed before Bambu Studio sees it.
    """

    input_path = Path(args.input).resolve()
    output_path = Path(args.output).resolve()
    if input_path.suffix.casefold() != ".scad":
        raise RuntimeError("openscad-stl mode requires a .scad input")
    if output_path.suffix.casefold() != ".stl":
        raise RuntimeError("openscad-stl output must end in .stl")
    if not input_path.is_file():
        raise RuntimeError(f"SCAD input is not a regular file: {input_path}")
    openscad = _resolve_executable(
        args.openscad,
        ("openscad", "/Applications/OpenSCAD.app/Contents/MacOS/OpenSCAD"),
    )
    if not openscad:
        raise RuntimeError("OpenSCAD CLI not found; install it or pass --openscad")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="lenscap-openscad-stl-") as temp:
        work_dir = Path(temp)
        core_path = work_dir / "selector.3mf"
        staged_stl = work_dir / output_path.name
        command = [
            openscad,
            "--backend",
            "Manifold",
            "-D",
            f"render_part={json.dumps(args.render_part)}",
            "-o",
            str(core_path),
            str(input_path),
        ]
        code, stdout, stderr = _run(command, timeout=args.timeout)
        if code != 0 or not core_path.is_file():
            raise RuntimeError(
                f"OpenSCAD selector export failed (rc={code}); command={command!r}\n"
                f"{stdout}\n{stderr}"
            )
        sanitization = _sanitize_openscad_core(core_path)
        core_verification = verify_3mf(
            core_path,
            require_closed=True,
            require_single_volume=args.require_single_volume,
        )
        mesh = _read_direct_core_mesh(core_path)
        closed, closure = mesh_is_closed(mesh)
        if not closed:
            raise ValueError(f"Core-to-STL source is not closed: {closure}")
        write_binary_stl(mesh, staged_stl)
        roundtrip = read_stl(staged_stl)
        roundtrip_closed, roundtrip_closure = mesh_is_closed(roundtrip)
        if not roundtrip_closed:
            raise ValueError(f"converted STL is not closed: {roundtrip_closure}")
        os.replace(staged_stl, output_path)
        payload = {
            "adapter": "openscad-core-to-binary-stl",
            "adapter_version": 2,
            "python": sys.version.split()[0],
            "input": _portable_path(input_path),
            "input_sha256": sha256(input_path),
            "output": _portable_path(output_path),
            "output_sha256": sha256(output_path),
            "render_part": args.render_part,
            "openscad": _portable_executable(openscad),
            "openscad_version": _tool_version(openscad),
            "command": _portable_command(_redact_temp_paths(command, work_dir)),
            "command_exit_code": code,
            "stdout_tail": stdout[-4000:],
            "stderr_tail": stderr[-4000:],
            "sanitization": sanitization,
            "core_verification": _portable_verification(core_verification),
            "mesh": {
                "vertices": len(roundtrip.vertices),
                "triangles": len(roundtrip.triangles),
                "closed": roundtrip_closed,
                "bounds": mesh_bounds(roundtrip),
                **roundtrip_closure,
            },
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        }
    manifest = _manifest(output_path, payload)
    print(
        json.dumps(
            {
                "output": str(output_path),
                "manifest": str(manifest),
                "status": "passed",
                "mesh": payload["mesh"],
                "sanitization": sanitization,
            },
            indent=2,
        )
    )
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


def _resolve_profile_inheritance(path: Path) -> dict[str, object]:
    """Materialize a Bambu JSON profile and record every inherited input.

    Bambu's CLI accepts a leaf JSON path but, with an isolated data directory,
    can retain the leaf's display name while silently falling back to 0.4 mm
    process defaults.  Resolve sibling ``inherits`` / ``include`` dependencies so the
    executable receives a complete profile rather than a misleading label.

    Bambu's PresetBundle.cpp applies the inherited config, then sparse include
    templates in list order, and finally the leaf's own settings. Includes are
    configuration fragments, so their profile metadata must not replace the
    leaf identity. In particular H2C keeps its machine G-code in templates.
    """

    chain: list[dict[str, str]] = []
    active: set[Path] = set()
    profile_metadata = {
        "name", "type", "from", "setting_id", "filament_id", "instantiation",
        "description", "version", "inherits", "include",
    }

    def dependency(current: Path, name: str, relation: str) -> Path:
        direct = current.parent / f"{name}.json"
        if direct.is_file():
            return direct
        for candidate in sorted(current.parent.glob("*.json")):
            try:
                candidate_payload = json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                continue
            if isinstance(candidate_payload, dict) and candidate_payload.get("name") == name:
                return candidate
        raise RuntimeError(
            f"cannot resolve Bambu profile {relation} {name!r} beside {current}"
        )

    def read(current: Path) -> dict[str, object]:
        current = current.resolve()
        if current in active:
            raise RuntimeError(f"Bambu profile inheritance cycle at {current}")
        active.add(current)
        try:
            payload = json.loads(current.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"cannot read Bambu profile {current}: {exc}") from exc
        if not isinstance(payload, dict):
            raise RuntimeError(f"Bambu profile must be a JSON object: {current}")
        parent_name = payload.get("inherits")
        merged: dict[str, object] = {}
        if isinstance(parent_name, str) and parent_name.strip():
            parent_name = parent_name.strip()
            merged.update(read(dependency(current, parent_name, "parent")))
        includes = payload.get("include", [])
        if isinstance(includes, str):
            includes = [includes]
        if not isinstance(includes, list) or any(
            not isinstance(name, str) or not name.strip() for name in includes
        ):
            raise RuntimeError(f"invalid Bambu profile include list: {current}")
        for name in includes:
            included = read(dependency(current, name.strip(), "include"))
            # Templates carry only their explicit settings, not compiled
            # default configuration values or the including preset's identity.
            merged.update({key: value for key, value in included.items() if key not in profile_metadata})
        merged.update(
            {key: value for key, value in payload.items() if key not in {"inherits", "include"}}
        )
        chain.append(
            {
                "path": _portable_path(current),
                "sha256": sha256(current),
                "name": str(payload.get("name", current.stem)),
            }
        )
        active.remove(current)
        return merged

    effective = read(path)
    return {"resolver_version": 2, "effective": effective, "chain": chain}


def _setting_values(value: object) -> list[object]:
    return list(value) if isinstance(value, list) else [value]


def _settings_equal(actual: object, expected: object) -> bool:
    actual_values = _setting_values(actual)
    expected_values = _setting_values(expected)
    if len(expected_values) == 1 and len(actual_values) > 1:
        expected_values *= len(actual_values)
    if len(actual_values) == 1 and len(expected_values) > 1:
        actual_values *= len(expected_values)
    if len(actual_values) != len(expected_values):
        return False
    for left, right in zip(actual_values, expected_values, strict=True):
        try:
            left_number = float(left)
            right_number = float(right)
        except (TypeError, ValueError, OverflowError):
            if str(left) != str(right):
                return False
        else:
            if not math.isclose(left_number, right_number, rel_tol=0.0, abs_tol=1e-7):
                return False
    return True


def _filament_slot_override(value: str) -> tuple[int, Path]:
    slot, separator, raw_path = value.partition("=")
    if not separator or not slot.isdigit() or int(slot) < 1 or not raw_path.strip():
        raise argparse.ArgumentTypeError("filament override must be a positive SLOT=PATH")
    return int(slot), Path(raw_path).expanduser().resolve()


def _filament_slot_labels(
    overrides: Sequence[tuple[int, Path]], count: int
) -> tuple[list[str], dict[str, Path]]:
    labels = ["filament"] * count
    paths: dict[str, Path] = {}
    for slot, path in overrides:
        if not 1 <= slot <= count:
            raise RuntimeError(f"filament slot {slot} is outside the {count} Bambu input parts")
        label = f"filament_slot_{slot}"
        if label in paths:
            raise RuntimeError(f"duplicate filament override for slot {slot}")
        paths[label] = path
        labels[slot - 1] = label
    return labels, paths


def _audit_effective_bambu_profiles(
    path: Path, resolved_profiles: dict[str, dict[str, object]],
    filament_slot_labels: Sequence[str] | None = None,
) -> dict[str, object]:
    """Prove the exported project uses the resolved machine/process settings."""

    with zipfile.ZipFile(path) as archive:
        try:
            project = json.loads(
                archive.read("Metadata/project_settings.config").decode("utf-8")
            )
        except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"cannot audit Bambu effective project settings: {exc}") from exc
    if not isinstance(project, dict):
        raise RuntimeError("Bambu project settings are not a JSON object")
    critical = {
        "machine": ("nozzle_diameter", "min_layer_height", "max_layer_height"),
        "process": (
            "layer_height",
            "initial_layer_print_height",
            "line_width",
            "inner_wall_line_width",
            "outer_wall_line_width",
            "initial_layer_line_width",
            "wall_loops",
        ),
        "filament": ("filament_type", "filament_diameter"),
    }
    checked: dict[str, dict[str, object]] = {}
    for label, fields in critical.items():
        if label == "filament" and filament_slot_labels is not None:
            continue
        if label not in resolved_profiles:
            continue
        effective = resolved_profiles[label].get("effective")
        if not isinstance(effective, dict):
            raise RuntimeError(f"resolved Bambu {label} profile is invalid")
        if label == "machine":
            fields = (*fields, *(
                field for field in ("machine_start_gcode", "machine_end_gcode", "change_filament_gcode")
                if field in effective
            ))
        checked[label] = {}
        for field in fields:
            if field not in effective:
                raise RuntimeError(
                    f"resolved Bambu {label} profile lacks critical field {field}"
                )
            if field not in project or not _settings_equal(project[field], effective[field]):
                raise RuntimeError(
                    f"Bambu project effective {field} does not match the resolved {label} profile: "
                    f"{project.get(field)!r} != {effective[field]!r}"
                )
            checked[label][field] = {
                "expected": effective[field],
                "observed": project[field],
            }
    if filament_slot_labels is not None:
        expected_fields: dict[str, list[object]] = {
            "filament_settings_id": [], "filament_type": [], "filament_diameter": [],
        }
        for label in filament_slot_labels:
            effective = resolved_profiles[label]["effective"]
            assert isinstance(effective, dict)
            for field in expected_fields:
                source_field = "name" if field == "filament_settings_id" else field
                values = _setting_values(effective.get(source_field))
                if not values or any(value is None for value in values) or len({str(value) for value in values}) != 1:
                    raise RuntimeError(f"Bambu {label} lacks a uniform per-slot {source_field}")
                expected_fields[field].append(values[0])
        checked["filament_slots"] = {}
        for field, expected in expected_fields.items():
            actual = project.get(field)
            if not isinstance(actual, list) or len(actual) != len(expected) or not _settings_equal(actual, expected):
                raise RuntimeError(f"Bambu per-slot {field} does not match requested filament profiles: {actual!r} != {expected!r}")
            checked["filament_slots"][field] = {"expected": expected, "observed": actual}
        try:
            checked["flush_configuration"] = _audit_bambu_flush_configuration(project, len(filament_slot_labels))
        except ValueError as exc:
            raise RuntimeError(str(exc)) from exc
    if "machine" in resolved_profiles and "process" in resolved_profiles:
        machine = resolved_profiles["machine"]["effective"]
        process = resolved_profiles["process"]["effective"]
        assert isinstance(machine, dict) and isinstance(process, dict)
        maximum_layer = max(float(value) for value in _setting_values(machine["max_layer_height"]))
        for field in ("layer_height", "initial_layer_print_height"):
            if max(float(value) for value in _setting_values(process[field])) > maximum_layer + 1e-7:
                raise RuntimeError(
                    f"resolved Bambu {field} exceeds machine max_layer_height"
                )
    return {
        "status": "passed",
        "checked": checked,
        "scope": "resolved_profile_chain_and_effective_project_settings",
    }


def bambu_command(args: argparse.Namespace) -> int:
    input_path = Path(args.input).resolve()
    additional_parts = [Path(value).resolve() for value in (args.part or [])]
    input_paths = [input_path, *additional_parts]
    for part in input_paths:
        if not part.is_file():
            raise RuntimeError(f"Bambu input part is missing: {part}")
    multipart = len(input_paths) > 1
    if multipart and any(path.suffix.lower() != ".stl" for path in input_paths):
        raise RuntimeError("multipart Bambu mode requires already-audited STL parts")
    if args.filament_color and len(args.filament_color) != len(input_paths):
        raise RuntimeError("--filament-color must be repeated exactly once per Bambu input part")
    slot_labels, slot_paths = _filament_slot_labels(
        getattr(args, "slot_filament_profile", []), len(input_paths)
    )
    if slot_paths and not args.filament_profile:
        raise RuntimeError("filament slot overrides require a default --filament-profile")
    output_path = Path(args.output).resolve()
    bambu = _resolve_executable(
        args.bambu,
        ("bambu-studio", "BambuStudio", "/Applications/BambuStudio.app/Contents/MacOS/BambuStudio"),
    )
    if not bambu:
        raise RuntimeError("Bambu Studio CLI not found; install it or pass --bambu")
    profile_paths: dict[str, Path] = {}
    for label, raw in (
        ("machine", args.machine_profile),
        ("process", args.process_profile),
        ("filament", args.filament_profile),
        *slot_paths.items(),
    ):
        if raw is None:
            continue
        resolved = Path(raw).expanduser().resolve()
        if not resolved.is_file():
            raise RuntimeError(f"Bambu {label} profile is missing: {resolved}")
        profile_paths[label] = resolved
    profile_hashes_before = {
        label: sha256(path) for label, path in profile_paths.items()
    }
    resolved_profiles = {
        label: _resolve_profile_inheritance(path)
        for label, path in profile_paths.items()
    }
    openscad = _resolve_executable(
        args.openscad,
        ("openscad", "/Applications/OpenSCAD.app/Contents/MacOS/OpenSCAD"),
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="lenscap-3mf-adapter-") as temp:
        work_dir = Path(temp)
        materialized_profiles: dict[str, Path] = {}
        for label, resolved in resolved_profiles.items():
            effective = resolved.get("effective")
            if not isinstance(effective, dict):
                raise RuntimeError(f"resolved Bambu {label} profile is invalid")
            materialized = work_dir / f"resolved-{label}-profile.json"
            materialized.write_text(
                json.dumps(effective, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            materialized_profiles[label] = materialized
        if multipart:
            prepared_paths = input_paths
            scad_command: list[str] = []
            prep: dict[str, object] = {
                "input_kind": "multipart_stl",
                "part_count": len(input_paths),
                "parts": [
                    {"path": _portable_path(path), "sha256": sha256(path)}
                    for path in input_paths
                ],
            }
        else:
            prepared, scad_command, prep = _prepare_bambu_input(
                input_path, openscad, work_dir, args.timeout
            )
            prepared_paths = [prepared]
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
                ]
            )
        elif bool(args.machine_profile) != bool(args.process_profile):
            raise RuntimeError(
                "Bambu export requires both --machine-profile and --process-profile when either is supplied"
            )
        elif args.machine_profile and args.process_profile:
            command.extend(
                [
                    "--load-settings",
                    f"{Path(args.machine_profile).resolve()};{Path(args.process_profile).resolve()}",
                ]
            )
        if multipart:
            if not args.filament_profile:
                raise RuntimeError("multipart Bambu mode requires --filament-profile")
            command.extend(
                [
                    "--load-filaments",
                    ";".join(str(profile_paths[label]) for label in slot_labels),
                    "--load-filament-ids",
                    ",".join(str(index) for index in range(1, len(prepared_paths) + 1)),
                    "--assemble",
                    "--allow-multicolor-oneplate",
                    "--orient",
                    "0",
                    "--arrange",
                    "1",
                    "--allow-rotations=0",
                ]
            )
        elif args.filament_profile:
            command.extend(
                [
                    "--load-filaments",
                    str(profile_paths[slot_labels[0]]),
                ]
            )
            if args.mode == "slice":
                command.extend(["--orient", "0", "--arrange", "1"])
        if args.mode == "slice":
            command.extend(["--slice", "0"])
        command.extend(
            ["--export-3mf", output_path.name, *(str(path) for path in prepared_paths)]
        )
        executable_tail: list[str] = []
        profile_replacements = {
            str(path): str(materialized_profiles[label])
            for label, path in profile_paths.items()
        }
        for raw_value in command[5:]:
            value = str(raw_value)
            for original, materialized in profile_replacements.items():
                value = value.replace(original, materialized)
            executable_tail.append(value)
        executable_command.extend(executable_tail)
        code, stdout, stderr = _run(executable_command, timeout=args.timeout)
        profile_hashes_after = {
            label: sha256(path) for label, path in profile_paths.items()
        }
        if profile_hashes_after != profile_hashes_before:
            raise RuntimeError(
                "a Bambu profile changed while Bambu Studio was running; "
                "refusing a non-reproducible export"
            )
        generated = out_dir / output_path.name
        if code != 0 or not generated.is_file():
            raise RuntimeError(
                f"Bambu Studio failed (rc={code}); command={command!r}\n{stdout}\n{stderr}"
            )
        output_path.write_bytes(generated.read_bytes())
        palette_patch = _patch_bambu_project_colors(
            output_path, args.filament_color or [], filament_count=len(slot_labels),
        )
        effective_profile_audit = _audit_effective_bambu_profiles(
            output_path, resolved_profiles,
            slot_labels if "filament" in resolved_profiles else None,
        )
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
            "adapter_version": 6,
            "python": sys.version.split()[0],
            "input": _portable_path(input_path),
            "input_sha256": sha256(input_path),
            "inputs": [
                {"path": _portable_path(path), "sha256": sha256(path)}
                for path in input_paths
            ],
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
            "multipart": multipart,
            "palette_patch": palette_patch,
            "bambu_result": result_payload,
            "verification": _portable_verification(verified),
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "determinism": (
                "input/command reproducible; Bambu UUIDs, PNGs, timestamps, and G-code metadata "
                "may vary by release/run"
            ),
            "profiles_are_external": True,
            "filament_slot_profiles": [
                {"slot": slot, "profile_key": label}
                for slot, label in enumerate(slot_labels, start=1)
            ] if "filament" in resolved_profiles else [],
            "profiles": {
                label: {
                    "path": _portable_path(path),
                    "sha256": profile_hashes_before[label],
                    "post_run_sha256": profile_hashes_after[label],
                }
                for label, path in profile_paths.items()
            },
            "profile_resolution": {
                label: {"resolver_version": resolved["resolver_version"], "chain": resolved["chain"]}
                for label, resolved in resolved_profiles.items()
            },
            "effective_profile_audit": effective_profile_audit,
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
    print(
        json.dumps(
            verify_3mf(
                Path(args.input),
                require_slice=args.require_slice,
                require_closed=args.require_closed,
                require_single_volume=args.require_single_volume,
            ),
            indent=2,
        )
    )
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

    verify_stl = sub.add_parser("verify-stl", help="verify a closed oriented STL solid")
    verify_stl.add_argument("input")
    verify_stl.add_argument("--require-single-volume", action="store_true")
    verify_stl.set_defaults(func=verify_stl_command)

    openscad = sub.add_parser("openscad", help="export a SCAD assembly to native 3MF")
    openscad.add_argument("input", help="SCAD source")
    openscad.add_argument("output", help="output .3mf")
    openscad.add_argument("--openscad", help="explicit OpenSCAD executable")
    openscad.add_argument(
        "--render-part",
        help="optional generated-SCAD selector (for example assembly or fit_ring)",
    )
    openscad.add_argument(
        "--require-single-volume",
        action="store_true",
        help="require exactly one positive-volume connected component",
    )
    openscad.add_argument(
        "--timeout",
        type=int,
        default=argparse.SUPPRESS,
        help="external-tool timeout in seconds (also accepted before the subcommand)",
    )
    openscad.set_defaults(func=openscad_command)

    openscad_stl = sub.add_parser(
        "openscad-stl",
        help="export a selector through sanitized Core 3MF to a closed binary STL",
    )
    openscad_stl.add_argument("input", help="SCAD source")
    openscad_stl.add_argument("output", help="output .stl")
    openscad_stl.add_argument("--openscad", help="explicit OpenSCAD executable")
    openscad_stl.add_argument("--render-part", required=True)
    openscad_stl.add_argument("--require-single-volume", action="store_true")
    openscad_stl.add_argument(
        "--timeout",
        type=int,
        default=argparse.SUPPRESS,
        help="external-tool timeout in seconds (also accepted before the subcommand)",
    )
    openscad_stl.set_defaults(func=openscad_stl_command)

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
        "--slot-filament-profile", action="append", default=[], type=_filament_slot_override,
        metavar="SLOT=PATH", help="override one-based part/filament slot; repeat for mixed materials",
    )
    bambu.add_argument(
        "--part",
        action="append",
        default=[],
        help="additional aligned STL part; repeat for a multipart AMS project",
    )
    bambu.add_argument(
        "--filament-color",
        action="append",
        default=[],
        help="ordered #RRGGBB project colour; repeat once per multipart STL",
    )
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
    verify.add_argument(
        "--require-closed",
        action="store_true",
        help="require direct, untransformed, edge-closed native mesh geometry",
    )
    verify.add_argument(
        "--require-single-volume",
        action="store_true",
        help="also require exactly one positive-volume connected component",
    )
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
