#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Audit relief STL footprints against approved same-canvas masks.

This small, renderer-agnostic final gate checks the top-view XY footprint of
each exported relief STL against its named binary mask, writes a visual
difference image, and records hashes in a portable JSON report. It does not
claim manifoldness, layer adhesion, or physical fit.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import struct
import tempfile
from pathlib import Path
from typing import Any, Iterable

import numpy as np
from PIL import Image, ImageDraw

AUDIT_VERSION = "0.1.0"
MAX_TRIANGLES = 2_000_000


class ProjectionAuditError(RuntimeError):
    """Raised when projection inputs are malformed or unsafe."""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def portable_path(path: Path, root: Path) -> str:
    """Return a report path relative to the report directory when possible."""
    resolved = path.expanduser().resolve()
    base = root.expanduser().resolve()
    try:
        return Path(os.path.relpath(resolved, base)).as_posix()
    except (OSError, ValueError):
        return str(resolved)


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    with tempfile.NamedTemporaryFile(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
    ) as handle:
        temporary = Path(handle.name)
        handle.write(payload.encode("utf-8"))
    try:
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _atomic_image(path: Path, image: Image.Image) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=path.parent, prefix=f".{path.name}.", suffix=".png", delete=False
    ) as handle:
        temporary = Path(handle.name)
    try:
        image.save(temporary, format="PNG")
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def parse_named_path(value: str) -> tuple[str, Path]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("use NAME=PATH")
    name, raw_path = value.split("=", 1)
    name = name.strip()
    if not name:
        raise argparse.ArgumentTypeError("the name cannot be empty")
    return name, Path(raw_path).expanduser()


def load_binary_mask(path: Path) -> np.ndarray:
    if not path.is_file():
        raise ProjectionAuditError(f"expected mask is not a regular file: {path}")
    try:
        image = Image.open(path)
        image.load()
    except Exception as exc:
        raise ProjectionAuditError(f"cannot decode expected mask {path}: {exc}") from exc
    gray = np.asarray(image.convert("L"), dtype=np.uint8)
    values = set(map(int, np.unique(gray)))
    if not values.issubset({0, 255}):
        raise ProjectionAuditError(f"expected mask is not strict binary 0/255: {path}")
    if gray.ndim != 2 or gray.shape[0] != gray.shape[1]:
        raise ProjectionAuditError(f"expected mask must be square: {path}")
    return gray == 255


def _finite_triangles(triangles: np.ndarray, path: Path) -> np.ndarray:
    if triangles.ndim != 3 or triangles.shape[1:] != (3, 2):
        raise ProjectionAuditError(f"invalid triangle array in {path}")
    if not np.isfinite(triangles).all():
        raise ProjectionAuditError(f"STL contains non-finite coordinates: {path}")
    return triangles


def stl_triangles_xy(path: Path) -> np.ndarray:
    """Read binary or ASCII STL and return an N-by-3-by-2 XY triangle array."""
    if not path.is_file():
        raise ProjectionAuditError(f"mesh is not a regular file: {path}")
    data = path.read_bytes()
    if len(data) >= 84:
        count = struct.unpack_from("<I", data, 80)[0]
        expected_size = 84 + 50 * count
        if expected_size == len(data):
            if count <= 0:
                raise ProjectionAuditError(f"binary STL has no triangles: {path}")
            if count > MAX_TRIANGLES:
                raise ProjectionAuditError(f"STL exceeds the {MAX_TRIANGLES} triangle safety limit: {path}")
            triangles = np.empty((count, 3, 2), dtype=np.float64)
            for index in range(count):
                values = struct.unpack_from("<12fH", data, 84 + 50 * index)
                for vertex in range(3):
                    triangles[index, vertex] = values[3 + vertex * 3 : 5 + vertex * 3]
            return _finite_triangles(triangles, path)

    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ProjectionAuditError(f"unrecognized STL encoding: {path}") from exc
    vertices: list[tuple[float, float]] = []
    for line in text.splitlines():
        fields = line.strip().split()
        if len(fields) == 4 and fields[0].lower() == "vertex":
            try:
                x, y = float(fields[1]), float(fields[2])
            except ValueError as exc:
                raise ProjectionAuditError(f"invalid ASCII STL vertex in {path}") from exc
            if not math.isfinite(x) or not math.isfinite(y):
                raise ProjectionAuditError(f"ASCII STL contains non-finite coordinates: {path}")
            vertices.append((x, y))
            if len(vertices) > MAX_TRIANGLES * 3:
                raise ProjectionAuditError(f"STL exceeds the {MAX_TRIANGLES} triangle safety limit: {path}")
    if not vertices or len(vertices) % 3:
        raise ProjectionAuditError(f"ASCII STL has an invalid triangle stream: {path}")
    return np.asarray(vertices, dtype=np.float64).reshape(-1, 3, 2)


def rasterize(
    triangles: np.ndarray,
    resolution: int,
    canvas_size_mm: float,
    *,
    mirror_x: bool = False,
    mirror_y: bool = False,
) -> np.ndarray:
    """Rasterize top-view triangles into the mask's square pixel coordinates."""
    if resolution < 2:
        raise ProjectionAuditError("mask resolution must be at least 2")
    if canvas_size_mm <= 0:
        raise ProjectionAuditError("canvas size must be positive")
    image = Image.new("1", (resolution, resolution), 0)
    draw = ImageDraw.Draw(image)
    scale = resolution / canvas_size_mm
    half = canvas_size_mm / 2.0
    for triangle in triangles:
        points: list[tuple[float, float]] = []
        for x, y in triangle:
            if mirror_x:
                x = -x
            if mirror_y:
                y = -y
            points.append(((x + half) * scale, (half - y) * scale))
        draw.polygon(points, fill=1)
    return np.asarray(image, dtype=bool)


def dilate(mask: np.ndarray, radius: int) -> np.ndarray:
    if radius < 0 or radius > 8:
        raise ProjectionAuditError("tolerance radius must be in [0, 8]")
    if radius == 0:
        return mask.copy()
    height, width = mask.shape
    padded = np.pad(mask, radius)
    result = np.zeros_like(mask)
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            result |= padded[
                radius + dy : radius + dy + height,
                radius + dx : radius + dx + width,
            ]
    return result


def _read_role_report(path: Path, report_root: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ProjectionAuditError(f"role-audit report is not a regular file: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProjectionAuditError(f"cannot parse role-audit report {path}: {exc}") from exc
    if not isinstance(value, dict) or value.get("status") != "passed":
        raise ProjectionAuditError("role-audit report must be a passed JSON object")
    return {"path": portable_path(path, report_root), "sha256": sha256(path)}


def audit_meshes(
    meshes: Iterable[tuple[str, Path]],
    expected_masks: Iterable[tuple[str, Path]],
    *,
    canvas_size_mm: float,
    tolerance_pixels: int = 1,
    mirror_x: bool = False,
    mirror_y: bool = False,
    output_report: Path,
    output_dir: Path,
    role_audit_report: Path | None = None,
) -> dict[str, Any]:
    """Run the projection audit and always write a machine-readable report."""
    if canvas_size_mm <= 0:
        raise ProjectionAuditError("canvas size must be positive")
    if tolerance_pixels < 0 or tolerance_pixels > 8:
        raise ProjectionAuditError("tolerance pixels must be in [0, 8]")
    mesh_list = list(meshes)
    mask_list = list(expected_masks)
    mesh_map = dict(mesh_list)
    mask_map = dict(mask_list)
    if len(mesh_map) != len(mesh_list) or len(mask_map) != len(mask_list):
        raise ProjectionAuditError("mesh and mask names must be unique")
    if not mesh_map or set(mesh_map) != set(mask_map):
        raise ProjectionAuditError("mesh and expected-mask names must match and be non-empty")
    report_root = output_report.parent.resolve()
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    output_report.parent.mkdir(parents=True, exist_ok=True)
    resolved_inputs = {path.resolve() for path in (*mesh_map.values(), *mask_map.values())}
    if role_audit_report is not None:
        resolved_inputs.add(role_audit_report.resolve())
    if output_report.resolve() in resolved_inputs:
        raise ProjectionAuditError("output report must not overwrite an input")

    role_record = _read_role_report(role_audit_report, report_root) if role_audit_report else None
    records: dict[str, Any] = {}
    failures: list[str] = []
    expected_shape: tuple[int, int] | None = None

    for name in sorted(mesh_map):
        expected = load_binary_mask(mask_map[name])
        if expected_shape is None:
            expected_shape = expected.shape
        elif expected.shape != expected_shape:
            raise ProjectionAuditError("expected masks must have identical dimensions")
        triangles = stl_triangles_xy(mesh_map[name])
        projected = rasterize(
            triangles,
            expected.shape[0],
            canvas_size_mm,
            mirror_x=mirror_x,
            mirror_y=mirror_y,
        )
        projected_tolerant = dilate(projected, tolerance_pixels)
        expected_tolerant = dilate(expected, tolerance_pixels)
        missing_mask = expected & ~projected_tolerant
        extra_mask = projected & ~expected_tolerant
        missing = int(missing_mask.sum())
        extra = int(extra_mask.sum())
        intersection = int((projected & expected).sum())
        union = int((projected | expected).sum())
        raw_iou = 1.0 if union == 0 else intersection / union
        if missing or extra:
            failures.append(f"{name}: missing={missing}, extra={extra}")

        diff = np.zeros((*expected.shape, 3), dtype=np.uint8)
        diff[expected & projected] = [245, 245, 245]
        diff[missing_mask] = [55, 200, 90]
        diff[extra_mask] = [230, 75, 75]
        diff_path = output_dir / f"{name}-projection-diff.png"
        _atomic_image(diff_path, Image.fromarray(diff, mode="RGB"))
        records[name] = {
            "mesh": portable_path(mesh_map[name], report_root),
            "mesh_sha256": sha256(mesh_map[name]),
            "mesh_triangles": int(len(triangles)),
            "expected_mask": portable_path(mask_map[name], report_root),
            "expected_mask_sha256": sha256(mask_map[name]),
            "expected_pixels": int(expected.sum()),
            "projected_pixels": int(projected.sum()),
            "raw_iou": round(raw_iou, 8),
            "expected_pixels_outside_tolerance": missing,
            "projected_pixels_outside_tolerance": extra,
            "diff": portable_path(diff_path, report_root),
        }

    report: dict[str, Any] = {
        "schema_version": 1,
        "audit_version": AUDIT_VERSION,
        "status": "failed" if failures else "passed",
        "canvas_size_mm": float(canvas_size_mm),
        "resolution": expected_shape[0] if expected_shape else None,
        "tolerance_pixels": int(tolerance_pixels),
        "mirror_x": bool(mirror_x),
        "mirror_y": bool(mirror_y),
        "colors": records,
        "failures": failures,
    }
    if role_record is not None:
        report["role_audit_report"] = role_record
    _atomic_json(output_report, report)
    if failures:
        raise ProjectionAuditError("; ".join(failures))
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare relief STL top-view footprints with approved masks."
    )
    parser.add_argument("--mesh", action="append", required=True, type=parse_named_path, metavar="NAME=PATH")
    parser.add_argument(
        "--expected-mask",
        action="append",
        required=True,
        type=parse_named_path,
        metavar="NAME=PATH",
    )
    parser.add_argument("--canvas-size-mm", required=True, type=float)
    parser.add_argument("--tolerance-pixels", type=int, default=1)
    parser.add_argument("--mirror-x", action="store_true")
    parser.add_argument("--mirror-y", action="store_true")
    parser.add_argument("--role-audit-report", type=Path)
    parser.add_argument("--output-report", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        audit_meshes(
            args.mesh,
            args.expected_mask,
            canvas_size_mm=args.canvas_size_mm,
            tolerance_pixels=args.tolerance_pixels,
            mirror_x=args.mirror_x,
            mirror_y=args.mirror_y,
            role_audit_report=args.role_audit_report,
            output_report=args.output_report,
            output_dir=args.output_dir,
        )
    except ProjectionAuditError as exc:
        print(f"STL projection audit failed: {exc}")
        return 1
    report = json.loads(args.output_report.read_text(encoding="utf-8"))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
