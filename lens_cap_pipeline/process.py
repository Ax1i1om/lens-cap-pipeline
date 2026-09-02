"""Deterministic artwork-to-process-master conversion.

This module contains the part of the lens-cap workflow that must be stable
across machines and across lens jobs.  It never invents typography or moves a
motif: the source image is sampled on its declared circular coordinate system,
quantised to an explicitly declared palette, and emitted as a transparent
RGBA master plus masks/SVGs suitable for a relief modeller.

The implementation intentionally uses ``int32`` for RGB arithmetic and
``int64`` for squared distances.  The former Jena helper used ``int16`` and
silently overflowed when squaring deltas; this module makes that class of bug
impossible to overlook by recording and checking the dtypes in the report.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import platform
import sys
import tempfile
from collections import Counter, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import PIL
from PIL import Image, ImageFilter

from .config import CircleSpec, PaletteSpec, PipelineConfig

PROCESS_VERSION = "0.2.0"


class ProcessError(RuntimeError):
    """Raised when a process master cannot satisfy the safety gates."""


def _runtime_info() -> dict[str, str]:
    """Record decoder/arithmetic versions that can affect pixel output."""
    return {
        "python": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "numpy": np.__version__,
        "pillow": getattr(PIL, "__version__", "unknown"),
        "byteorder": sys.byteorder,
    }


@dataclass(frozen=True)
class CircleGeometry:
    center_x: float
    center_y: float
    radius: float
    inferred_center: bool
    inferred_radius: bool


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(data)
    try:
        temporary.replace(path)
    finally:
        # If a cross-device/permission error prevents the atomic rename, do
        # not leave a misleading generated-looking temporary artifact behind.
        if temporary.exists():
            temporary.unlink()


def _atomic_text(path: Path, text: str) -> None:
    _atomic_bytes(path, text.encode("utf-8"))


def _atomic_image(image: Image.Image, path: Path, *, format: str | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    suffix = path.suffix.lower()
    image_format = format or ("PNG" if suffix == ".png" else None)
    if image_format is None:
        raise ValueError(f"cannot infer image format for {path}")
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as handle:
        temporary = Path(handle.name)
    try:
        image.save(temporary, format=image_format)
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _hex(rgb: Iterable[int]) -> str:
    values = tuple(int(v) for v in rgb)
    return "#" + "".join(f"{v:02X}" for v in values)


def _circle_for(source: Image.Image, spec: CircleSpec, alpha: np.ndarray | None = None) -> CircleGeometry:
    width, height = source.size
    inferred_center = spec.center_px is None
    inferred_radius = spec.radius_px is None
    if (inferred_center or inferred_radius) and alpha is not None:
        alpha_binary = bool(np.all((alpha == 0) | (alpha == 255)))
        if not alpha_binary:
            raise ProcessError(
                "circle inference requires a binary alpha boundary; declare center_px/radius_px "
                "or provide an alpha mask containing only 0 and 255"
            )
    if spec.center_px is not None:
        center_x, center_y = spec.center_px
    elif alpha is not None and np.any(alpha < 255) and np.any(alpha > 0):
        # A transparent master carries its own exclusion boundary.  Infer a
        # circle only from the non-zero alpha bounding box; this keeps the
        # coordinate map deterministic while refusing ambiguous opaque crops.
        ys, xs = np.nonzero(alpha > 0)
        center_x = (float(xs.min()) + float(xs.max())) / 2.0
        center_y = (float(ys.min()) + float(ys.max())) / 2.0
    else:
        raise ProcessError(
            "opaque artwork needs an explicit circle.center_px and circle.radius_px; "
            "use a binary-alpha PNG when the circle is to be inferred"
        )
    if spec.radius_px is not None:
        radius = spec.radius_px
    elif alpha is not None and np.any(alpha > 0):
        ys, xs = np.nonzero(alpha > 0)
        radius = min(float(xs.max() - xs.min() + 1), float(ys.max() - ys.min() + 1)) / 2.0 - 0.5
    else:
        raise ProcessError("cannot infer a circle without an explicit radius or non-empty alpha mask")
    if radius <= 0:
        raise ProcessError("circle radius must be positive")
    if not spec.allow_outside:
        if center_x - radius < -0.5 or center_x + radius > width - 0.5 or center_y - radius < -0.5 or center_y + radius > height - 0.5:
            raise ProcessError(
                "declared circle extends outside the source image; set circle.allow_outside=true only when this is intentional"
            )
    return CircleGeometry(float(center_x), float(center_y), float(radius), inferred_center, inferred_radius)


def _prefilter(source: Image.Image, config: PipelineConfig) -> tuple[Image.Image, str]:
    spec = config.prefilter
    if spec.name == "none":
        return source, "none"
    if spec.name == "median":
        return source.filter(ImageFilter.MedianFilter(size=spec.size)), f"median_{spec.size}"
    if spec.name == "gaussian":
        return source.filter(ImageFilter.GaussianBlur(spec.radius)), f"gaussian_{spec.radius:g}"
    raise ProcessError(f"unsupported prefilter {spec.name!r}")


def _detector_mask(arr: np.ndarray, spec: PaletteSpec) -> np.ndarray | None:
    detector = spec.detector
    if detector is None:
        return None
    channels = {"r": arr[:, :, 0], "g": arr[:, :, 1], "b": arr[:, :, 2]}
    channel = str(detector["channel"]).lower()
    mask = channels[channel] >= int(detector["minimum"])
    for other, amount in detector.get("deltas", {}).items():
        # Keep detector arithmetic in int32 as well.  The range is small for
        # RGB, but using one explicit dtype throughout prevents a future
        # channel-normalisation change from reintroducing narrow-integer bugs.
        mask &= channels[channel].astype(np.int32) - channels[other].astype(np.int32) >= int(amount)
    return mask


def _classify(source: Image.Image, filtered: Image.Image, palettes: tuple[PaletteSpec, ...]) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    raw = np.asarray(source, dtype=np.int32)
    arr = np.asarray(filtered, dtype=np.int32)
    if raw.ndim != 3 or raw.shape[2] != 3:
        raise ProcessError("source artwork must decode as RGB")
    palette_rgb = np.asarray([p.rgb for p in palettes], dtype=np.int32)
    # Never change these casts to int16: RGB differences can square to values
    # greater than 32767.  Summation is explicitly int64 as a second guard.
    # Work in fixed-size row chunks so the advertised 4096x4096/16-colour
    # envelope cannot allocate a multi-gigabyte ``H x W x palette x RGB``
    # temporary.  Each chunk follows the same vectorised arithmetic and
    # argmin tie rules as a whole-image implementation.
    height, width = arr.shape[:2]
    labels = np.empty((height, width), dtype=np.uint8)
    chunk_rows = 256
    detector_hits: dict[str, int] = {
        palette.name: 0 for palette in palettes if palette.detector is not None
    }
    distance_sum = 0
    distance_max = 0
    pixel_count = height * width
    for y0 in range(0, height, chunk_rows):
        y1 = min(height, y0 + chunk_rows)
        chunk = arr[y0:y1]
        deltas = chunk[:, :, None, :] - palette_rgb[None, None, :, :]
        distances = np.sum(deltas.astype(np.int64) ** 2, axis=3, dtype=np.int64)
        chunk_labels = np.argmin(distances, axis=2).astype(np.uint8)
        # Explicit detectors are applied in palette-index order.  A later
        # entry may intentionally override nearest-colour classification (for
        # example a saturated red registration mark), while the order remains
        # reproducible.
        for palette in palettes:
            mask = _detector_mask(chunk, palette)
            if mask is not None:
                chunk_labels[mask] = palette.index
                detector_hits[palette.name] = detector_hits.get(palette.name, 0) + int(mask.sum())
        labels[y0:y1] = chunk_labels
        nearest = np.min(distances, axis=2)
        distance_sum += int(np.sum(nearest, dtype=np.int64))
        distance_max = max(distance_max, int(np.max(nearest)))
    metadata = {
        "arithmetic_dtype": str(arr.dtype),
        "distance_dtype": "int64",
        "overflow_guard": bool(arr.dtype == np.int32),
        "chunk_rows": chunk_rows,
        "prefiltered_rgb_sha256": hashlib.sha256(arr.tobytes()).hexdigest(),
        "detector_hits": detector_hits,
        "nearest_distance": {
            "mean": float(distance_sum / pixel_count) if pixel_count else 0.0,
            "max": distance_max,
        },
    }
    return labels, raw, metadata


def _sample_polarity(
    raw: np.ndarray,
    filtered: np.ndarray,
    source_labels: np.ndarray,
    inside: np.ndarray,
    palettes: tuple[PaletteSpec, ...],
) -> dict[str, Any]:
    """Compare raw source interiors with expected palette roles.

    A single pixel is a fragile probe: a median filter can legitimately change
    a pixel on a one-pixel boundary even when the surrounding artwork is
    correct.  We therefore choose a deterministic representative from the
    nearest raw-colour candidates and inspect a small local window.  A true
    black/ivory inversion still fails because the filtered window's majority
    role disagrees with the source colour; ordinary edge antialiasing does not
    fail the gate.
    """
    result: dict[str, Any] = {"passed": True, "samples": {}}
    height, width = source_labels.shape
    radius = 2
    max_candidates = 8192
    for palette in palettes:
        target = np.asarray(palette.rgb, dtype=np.int32)
        sample_delta = (raw - target).astype(np.int64)
        sample_dist = np.sum(sample_delta * sample_delta, axis=2, dtype=np.int64)
        sample_dist = np.where(inside, sample_dist, np.iinfo(np.int64).max)
        minimum = int(sample_dist.min())
        if minimum == np.iinfo(np.int64).max:
            sample = {"present": False, "required": palette.required, "passed": not palette.required}
            result["samples"][palette.name] = sample
            result["passed"] = bool(result["passed"] and sample["passed"])
            continue
        # Include nearby antialiased samples but never an unrelated colour
        # field.  The bound is deliberately small relative to the old
        # int16-overflow failure (which could make a 0-valued pixel appear
        # closer to ivory than black).
        tolerance = max(64, int(round(minimum * 0.10)))
        candidate_mask = sample_dist <= minimum + tolerance
        # A polarity probe must come from a region that the classifier labels
        # as this palette.  This prevents a median window at the edge of a
        # small black mark from being selected merely because it happens to be
        # the middle row-major candidate.  If no such point exists, retain the
        # raw candidates so an inversion is reported as a failed probe rather
        # than being mistaken for an absent optional colour.
        classified_mask = candidate_mask & inside & (source_labels == palette.index)
        candidate_pool = classified_mask if np.any(classified_mask) else candidate_mask
        # Work with flat indices first: ``argwhere`` allocates two int64
        # columns for every candidate and can consume hundreds of MiB on a
        # 4k source with a broad flat colour field.
        flat_candidates = np.flatnonzero(candidate_pool)
        if flat_candidates.size == 0:
            sample = {"present": False, "required": palette.required, "passed": not palette.required}
            result["samples"][palette.name] = sample
            result["passed"] = bool(result["passed"] and sample["passed"])
            continue
        # Large flat fields can contain millions of candidates.  Evaluate a
        # deterministic, evenly spaced subset; a local-share maximum is much
        # more stable than selecting the middle row-major pixel and remains
        # bounded for large source images.
        if flat_candidates.size > max_candidates:
            selected_indices = np.linspace(0, flat_candidates.size - 1, max_candidates, dtype=np.int64)
            flat_candidates = flat_candidates[selected_indices]

        best: tuple[float, int, float, int, int, int, int] | None = None
        best_stats: tuple[int, int, int, int] | None = None
        for flat_index in flat_candidates:
            sy, sx = divmod(int(flat_index), width)
            y0, y1 = max(0, sy - radius), min(height, sy + radius + 1)
            x0, x1 = max(0, sx - radius), min(width, sx + radius + 1)
            window_inside = inside[y0:y1, x0:x1]
            window_labels = source_labels[y0:y1, x0:x1][window_inside]
            counts = np.bincount(window_labels.astype(np.int64), minlength=len(palettes))
            expected_count = int(counts[palette.index]) if counts.size > palette.index else 0
            valid_count = int(window_labels.size)
            share = expected_count / float(valid_count) if valid_count else 0.0
            # Prefer a high expected-colour share, then more expected pixels,
            # then a point nearer the image centre.  The final coordinates are
            # deterministic tie-breakers independent of Python hash order.
            centre_distance = (sy - (height - 1) / 2.0) ** 2 + (sx - (width - 1) / 2.0) ** 2
            rank = (share, expected_count, -centre_distance, -sy, -sx, -valid_count, -int(source_labels[sy, sx]))
            if best is None or rank > best:
                best = rank
                best_stats = (sy, sx, expected_count, valid_count)
        assert best is not None and best_stats is not None
        sy, sx, expected_count, valid_count = best_stats
        y0, y1 = max(0, sy - radius), min(height, sy + radius + 1)
        x0, x1 = max(0, sx - radius), min(width, sx + radius + 1)
        window_labels = source_labels[y0:y1, x0:x1][inside[y0:y1, x0:x1]]
        counts = np.bincount(window_labels.astype(np.int64), minlength=len(palettes))
        observed = int(np.argmax(counts)) if counts.size else int(source_labels[sy, sx])
        share = expected_count / float(valid_count) if valid_count else 0.0
        # Require a meaningful local share and an agreeing centre label.  A
        # strict 50% majority rejects legitimate narrow outlines that occupy
        # only a couple of pixels at the declared circle edge; 25% still
        # rejects one-pixel noise while catching a broad polarity inversion.
        centre_label = int(source_labels[sy, sx])
        passed = bool(
            valid_count > 0
            and centre_label == palette.index
            and share >= 0.25
        )
        sample = {
            "present": True,
            "required": palette.required,
            "source_coordinate_px": [sx, sy],
            "source_rgb": raw[sy, sx].tolist(),
            "filtered_rgb": filtered[sy, sx].tolist(),
            "observed_index": observed,
            "centre_index": centre_label,
            "expected_index": palette.index,
            "window_radius_px": radius,
            "window_expected_share": share,
            "nearest_raw_distance": minimum,
            "candidate_count": int(np.count_nonzero(candidate_mask)),
            "classified_candidate_count": int(np.count_nonzero(classified_mask)),
            "passed": passed,
        }
        result["samples"][palette.name] = sample
        if palette.required:
            result["passed"] = bool(result["passed"] and passed)
    return result


def _sample_to_circle(
    source_labels: np.ndarray,
    geometry: CircleGeometry,
    grid: int,
    source_inside: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    """Sample source labels into a square grid centred on the declared circle."""
    out_center = (grid - 1) / 2.0
    out_radius = (grid - 1) / 2.0
    axis = np.arange(grid, dtype=np.float64)
    source_x = np.rint(geometry.center_x + (axis - out_center) * geometry.radius / out_radius).astype(np.int64)
    source_y = np.rint(geometry.center_y + (axis - out_center) * geometry.radius / out_radius).astype(np.int64)
    source_x = np.clip(source_x, 0, source_labels.shape[1] - 1)
    source_y = np.clip(source_y, 0, source_labels.shape[0] - 1)
    labels = source_labels[np.ix_(source_y, source_x)].copy()
    yy, xx = np.indices((grid, grid), dtype=np.float64)
    radial = np.hypot(xx - out_center, yy - out_center)
    inside = radial <= out_radius
    if source_inside is not None:
        sampled_inside = source_inside[np.ix_(source_y, source_x)]
        inside &= sampled_inside
    labels[~inside] = 0
    return labels, inside, {
        "output_center_px": [out_center, out_center],
        "output_radius_px": out_radius,
        "source_sampling": "nearest pixel on declared circle-normalized canvas",
    }


def _components(mask: np.ndarray) -> list[list[tuple[int, int]]]:
    """Return deterministic 8-connected components for a boolean mask."""
    seen = np.zeros(mask.shape, dtype=bool)
    result: list[list[tuple[int, int]]] = []
    height, width = mask.shape
    for y, x in zip(*np.nonzero(mask)):
        y, x = int(y), int(x)
        if seen[y, x]:
            continue
        seen[y, x] = True
        queue: deque[tuple[int, int]] = deque([(y, x)])
        cells: list[tuple[int, int]] = []
        while queue:
            cy, cx = queue.popleft()
            cells.append((cy, cx))
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    if not (dx or dy):
                        continue
                    ny, nx = cy + dy, cx + dx
                    if 0 <= ny < height and 0 <= nx < width and mask[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True
                        queue.append((ny, nx))
        result.append(cells)
    return result


def _cleanup_islands(
    labels: np.ndarray,
    inside: np.ndarray,
    palettes: tuple[PaletteSpec, ...],
    spec: Any,
) -> dict[str, Any]:
    names = {p.index: p.name for p in palettes}
    roles = {p.index: p.role for p in palettes}
    selected = set(spec.apply_to)
    if "all" in selected:
        selected = {"base", "relief"}
    changed = {p.name: 0 for p in palettes}
    components_seen = {p.name: 0 for p in palettes}
    if not spec.enabled:
        return {"enabled": False, "changed_pixels": changed, "components_seen": components_seen}
    height, width = labels.shape
    for target in range(len(palettes)):
        if roles[target] not in selected:
            continue
        candidate = (labels == target) & inside
        for cells in _components(candidate):
            components_seen[names[target]] += 1
            if len(cells) > spec.max_area_px:
                continue
            ys = np.asarray([cell[0] for cell in cells], dtype=np.int32)
            xs = np.asarray([cell[1] for cell in cells], dtype=np.int32)
            if max(int(ys.max() - ys.min() + 1), int(xs.max() - xs.min() + 1)) > spec.max_dimension_px:
                continue
            y0, y1 = int(ys.min()), int(ys.max())
            x0, x1 = int(xs.min()), int(xs.max())
            yy0, yy1 = max(0, y0 - spec.ring_px), min(height - 1, y1 + spec.ring_px)
            xx0, xx1 = max(0, x0 - spec.ring_px), min(width - 1, x1 + spec.ring_px)
            ring = labels[yy0 : yy1 + 1, xx0 : xx1 + 1]
            ring_inside = inside[yy0 : yy1 + 1, xx0 : xx1 + 1]
            alternatives = ring[(ring_inside) & (ring != target)]
            if alternatives.size == 0:
                continue
            alternate, count = Counter(alternatives.tolist()).most_common(1)[0]
            if count / float(alternatives.size) < spec.dominance:
                continue
            labels[ys, xs] = int(alternate)
            changed[names[target]] += len(cells)
    return {
        "enabled": True,
        "max_area_px": spec.max_area_px,
        "max_dimension_px": spec.max_dimension_px,
        "ring_px": spec.ring_px,
        "dominance": spec.dominance,
        "apply_to": list(spec.apply_to),
        "changed_pixels": changed,
        "components_seen": components_seen,
    }


def _merged_rectangles(mask: np.ndarray) -> list[tuple[int, int, int, int]]:
    active: dict[tuple[int, int], int] = {}
    output: list[tuple[int, int, int, int]] = []
    for y, row in enumerate(mask):
        padded = np.concatenate(([False], row, [False]))
        edges = np.flatnonzero(padded[1:] != padded[:-1])
        # Sort instead of iterating a set: Python's hash seed is intentionally
        # randomized, and unsorted rectangle order would make SVG bytes (and
        # therefore job hashes) vary between otherwise identical runs.
        runs = sorted((int(edges[i]), int(edges[i + 1])) for i in range(0, len(edges), 2))
        for run, start_y in list(active.items()):
            if run not in runs:
                output.append((run[0], start_y, run[1] - run[0], y - start_y))
                del active[run]
        for run in runs:
            active.setdefault(run, y)
    height = mask.shape[0]
    for run, start_y in active.items():
        output.append((run[0], start_y, run[1] - run[0], height - start_y))
    return output


def _write_svg(mask: np.ndarray, path: Path, face_mm: float, fill: str) -> int:
    rectangles = _merged_rectangles(mask)
    commands = "".join(f"M{x} {y}h{w}v{h}h{-w}z" for x, y, w, h in rectangles)
    svg = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{face_mm:g}mm" height="{face_mm:g}mm" '
        f'viewBox="0 0 {mask.shape[1]} {mask.shape[0]}" shape-rendering="crispEdges">\n'
        f'  <path fill="{fill}" fill-rule="nonzero" d="{commands}"/>\n'
        '</svg>\n'
    )
    _atomic_text(path, svg)
    return len(rectangles)


def _relative(path: Path, root: Path) -> str:
    try:
        return Path(os.path.relpath(path.resolve(), root.resolve())).as_posix()
    except (ValueError, OSError):
        return str(path.resolve())


def process(config: PipelineConfig, *, force: bool = False) -> dict[str, Any]:
    """Run the deterministic process stage and return its JSON report."""
    report_path = config.output_dir / "process-report.json"
    if report_path.exists() and not force:
        raise ProcessError(f"output already exists: {report_path}; use --force to replace generated derivatives")
    config.output_dir.mkdir(parents=True, exist_ok=True)
    # A marker makes an interrupted/exceptional run fail closed.  We do not
    # delete old generated files (which may be useful for recovery); instead,
    # validation and stage reuse can see that the current transaction did not
    # reach its final atomic report write.  The marker is removed only after a
    # fully passed report and manifest have been written below.
    in_progress = config.output_dir / ".process-in-progress"
    if in_progress.exists() and not force:
        raise ProcessError(
            f"an incomplete process transaction is present: {in_progress}; use --force to rebuild"
        )
    _atomic_text(
        in_progress,
        json.dumps(
            {"stage": "process", "config_sha256": config.digest()},
            ensure_ascii=False,
            sort_keys=True,
        )
        + "\n",
    )
    masks_dir = config.output_dir / "masks"
    vector_dir = config.output_dir / "vector"
    masks_dir.mkdir(parents=True, exist_ok=True)
    vector_dir.mkdir(parents=True, exist_ok=True)

    # Decode the exact byte snapshot whose hash is recorded below.  Reading
    # and hashing the path in separate operations would let a sync tool replace
    # the artwork between decode and locking, producing a misleading report.
    source_bytes = config.source_path.read_bytes()
    source_digest = hashlib.sha256(source_bytes).hexdigest()
    with Image.open(io.BytesIO(source_bytes)) as opened:
        source_rgba = opened.convert("RGBA")
    if config.source_sha256 is not None and source_digest.lower() != config.source_sha256.lower():
        raise ProcessError(
            "source_sha256 does not match the immutable artwork; refusing to process a substituted file"
        )
    alpha = np.asarray(source_rgba.getchannel("A"), dtype=np.uint8)
    source = source_rgba.convert("RGB")
    geometry = _circle_for(source, config.circle, alpha)
    filtered_image, filter_name = _prefilter(source, config)
    source_labels, raw, classification = _classify(source, filtered_image, config.palette)
    filtered = np.asarray(filtered_image, dtype=np.int32)
    yy, xx = np.indices(source_labels.shape, dtype=np.float64)
    geometric_inside = np.hypot(xx - geometry.center_x, yy - geometry.center_y) <= geometry.radius
    alpha_inside = alpha > 0
    source_inside = geometric_inside & alpha_inside
    polarity = _sample_polarity(raw, filtered, source_labels, source_inside, config.palette)

    labels, inside, mapping = _sample_to_circle(source_labels, geometry, config.grid_size, source_inside)
    output_radius = float(mapping["output_radius_px"])
    safe_px = config.safe_border_mm / (config.face_diameter_mm / config.grid_size)
    output_yy, output_xx = np.indices(labels.shape, dtype=np.float64)
    radial = np.hypot(output_xx - mapping["output_center_px"][0], output_yy - mapping["output_center_px"][1])
    safe_border = inside & (radial > output_radius - safe_px) if safe_px > 0 else np.zeros_like(inside)
    base_index = config.base.index
    labels[~inside] = base_index
    labels[safe_border] = base_index
    before_counts = {p.name: int(np.count_nonzero((labels == p.index) & inside)) for p in config.palette}
    cleanup = _cleanup_islands(labels, inside, config.palette, config.cleanup)
    # Cleanup is never allowed to put a positive-relief pixel into the plain
    # outer rim.  Any such attempted change is deterministically corrected and
    # counted in the report.
    relief_indices = {p.index for p in config.relief}
    safe_relief_before = int(np.count_nonzero(safe_border & np.isin(labels, list(relief_indices))))
    labels[safe_border] = base_index
    cleanup["safe_border_corrections"] = safe_relief_before
    after_counts = {p.name: int(np.count_nonzero((labels == p.index) & inside)) for p in config.palette}

    palette_array = np.asarray([p.rgb for p in config.palette], dtype=np.uint8)
    rgba = np.zeros((config.grid_size, config.grid_size, 4), dtype=np.uint8)
    rgba[:, :, :3] = palette_array[labels]
    rgba[inside, 3] = 255
    process_master_path = config.output_dir / "process-master.png"
    _atomic_image(Image.fromarray(rgba, mode="RGBA"), process_master_path)
    preview = np.zeros((config.grid_size, config.grid_size, 3), dtype=np.uint8)
    preview[:, :, :] = palette_array[base_index]
    preview[inside] = palette_array[labels[inside]]
    preview_path = config.output_dir / "process-preview.png"
    _atomic_image(Image.fromarray(preview, mode="RGB"), preview_path)

    # Explicit role masks and a false-colour overlay make the semantic mapping
    # inspectable before a CAD/relief tool is allowed to consume it.
    outside = ~inside
    base_mask = inside & (labels == base_index)
    relief_mask = inside & np.isin(labels, list(relief_indices))
    role_masks: dict[str, np.ndarray] = {"outside": outside, "base": base_mask, "relief": relief_mask, "safe-border": safe_border}
    role_paths: dict[str, str] = {}
    for name, mask in role_masks.items():
        path = config.output_dir / f"{name}.png"
        _atomic_image(Image.fromarray(np.where(mask, 255, 0).astype(np.uint8), mode="L"), path)
        role_paths[name] = _relative(path, config.output_dir)
    overlay = np.zeros_like(rgba)
    overlay[base_mask, :3] = palette_array[base_index]
    for p in config.relief:
        overlay[inside & (labels == p.index), :3] = p.rgb
    overlay[inside, 3] = 255
    overlay_path = config.output_dir / "role-overlay.png"
    _atomic_image(Image.fromarray(overlay, mode="RGBA"), overlay_path)

    mask_paths: dict[str, str] = {}
    svg_paths: dict[str, str] = {}
    svg_counts: dict[str, int] = {}
    mask_stats: dict[str, Any] = {}
    for p in config.palette:
        mask = inside & (labels == p.index)
        mask_path = masks_dir / f"{p.name}.png"
        _atomic_image(Image.fromarray(np.where(mask, 255, 0).astype(np.uint8), mode="L"), mask_path)
        svg_path = vector_dir / f"{p.name}.svg"
        svg_counts[p.name] = _write_svg(mask, svg_path, config.face_diameter_mm, _hex(p.rgb))
        mask_paths[p.name] = _relative(mask_path, config.output_dir)
        svg_paths[p.name] = _relative(svg_path, config.output_dir)
        ys, xs = np.nonzero(mask)
        mask_stats[p.name] = {
            "pixels": int(np.count_nonzero(mask)),
            "bbox_px": None if len(xs) == 0 else [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())],
            "role": p.role,
            "height_mm": p.height_mm,
            "mask_sha256": sha256_file(mask_path),
            "svg_sha256": sha256_file(svg_path),
            "svg_rectangles": svg_counts[p.name],
        }

    # A palette entry marked ``required`` is a contract, not a hint: the
    # final post-cleanup mask and its vector handoff must both contain a
    # positive feature.  Keeping this check in the process report makes a
    # missing/erased required mark fail before any CAD stage consumes it.
    required_outputs: dict[str, Any] = {}
    for p in config.palette:
        stats = mask_stats[p.name]
        mask_present = (config.output_dir / "masks" / f"{p.name}.png").is_file()
        svg_present = (config.output_dir / "vector" / f"{p.name}.svg").is_file()
        nonempty = int(stats["pixels"]) > 0
        svg_nonempty = int(stats["svg_rectangles"]) > 0
        if p.required:
            palette_status = bool(mask_present and svg_present and nonempty and svg_nonempty)
        else:
            palette_status = True
        required_outputs[p.name] = {
            "required": bool(p.required),
            "mask_present": mask_present,
            "svg_present": svg_present,
            "svg_nonempty": svg_nonempty,
            "pixels": int(stats["pixels"]),
            "nonempty": nonempty,
            "status": "passed" if palette_status else "failed" if p.required else "not_required",
        }

    partition = outside.astype(np.uint8) + base_mask.astype(np.uint8) + relief_mask.astype(np.uint8)
    color_stack = np.stack([inside & (labels == p.index) for p in config.palette], axis=0)
    color_overlap = np.sum(color_stack.astype(np.uint8), axis=0)
    checks = {
        "overflow_guard": classification["overflow_guard"],
        "source_polarity": bool(polarity["passed"]),
        "role_partition": bool(np.all(partition == 1)),
        "color_partition": bool(np.all(color_overlap[inside] == 1) and not np.any(color_overlap[~inside])),
        "safe_border_base_only": bool(not np.any(safe_border & relief_mask)),
        "outside_alpha_zero": bool(np.all(rgba[~inside, 3] == 0)),
        "required_palette_outputs": bool(
            all(item["status"] == "passed" for item in required_outputs.values() if item["required"])
        ),
    }
    status = "passed" if all(checks.values()) else "failed"
    config_copy_path = config.output_dir / "config.normalized.json"
    # Keep the archived normalized manifest clone-portable.  Runtime code
    # still uses config.source_path/output_dir (resolved Paths); only the
    # serialized audit representation is path-sanitized.
    _atomic_text(config_copy_path, json.dumps(config.portable_public(), ensure_ascii=False, indent=2) + "\n")
    source_lock_path = config.output_dir / "source-lock.json"
    source_lock = {
        "path": _relative(config.source_path, config.config_path.parent),
        "sha256": source_digest,
        "dimensions_px": list(source.size),
        "alpha_mask_present": bool(np.any(alpha < 255)),
    }
    _atomic_text(source_lock_path, json.dumps(source_lock, ensure_ascii=False, indent=2) + "\n")

    outputs: dict[str, Any] = {
        "process_master": {"path": _relative(process_master_path, config.output_dir), "sha256": sha256_file(process_master_path)},
        "preview": _relative(preview_path, config.output_dir),
        "role_overlay": _relative(overlay_path, config.output_dir),
        "role_masks": role_paths,
        "masks": mask_paths,
        "svgs": svg_paths,
        "svg_rectangle_counts": svg_counts,
        "config_normalized": _relative(config_copy_path, config.output_dir),
        "source_lock": _relative(source_lock_path, config.output_dir),
        "run_manifest": "run-manifest.json",
    }
    report: dict[str, Any] = {
        "schema_version": 1,
        "process_version": PROCESS_VERSION,
        "runtime": _runtime_info(),
        "status": status,
        "job_slug": config.job_slug,
        "config_sha256": config.digest(),
        "config_path": _relative(config.config_path, config.config_path.parent),
        "source": source_lock,
        "inside_mode": "explicit_circle" if config.circle.center_px is not None else "alpha_mask",
        "face": {
            "diameter_mm": config.face_diameter_mm,
            "grid": [config.grid_size, config.grid_size],
            "safe_border_mm": config.safe_border_mm,
            "safe_border_px": safe_px,
            "nozzle_mm": config.nozzle_mm,
            "nozzle_explicit": config.nozzle_explicit,
        },
        "circle": {
            "source_center_px": [geometry.center_x, geometry.center_y],
            "source_radius_px": geometry.radius,
            "center_inferred": geometry.inferred_center,
            "radius_inferred": geometry.inferred_radius,
            **mapping,
            "coordinate_preserving": True,
            "alpha_mask_used": bool(np.any(alpha < 255)),
        },
        "classification": {
            "prefilter": filter_name,
            **classification,
            "polarity_sanity": polarity,
        },
        "cleanup": {
            **cleanup,
            "before_pixel_counts": before_counts,
            "after_pixel_counts": after_counts,
            "only_declared_components": True,
        },
        "palette": {
            p.name: {**p.public(), "hex": _hex(p.rgb)} for p in config.palette
        },
        "mask_stats": mask_stats,
        "required_palette_outputs": required_outputs,
        "roles": {
            "outside": "outside/no_geometry",
            "base": [p.name for p in config.palette if p.role == "base"],
            "positive_relief": [p.name for p in config.relief],
        },
        "checks": checks,
        "outputs": outputs,
        "mechanical": {
            "measured_diameter_mm": config.measured_diameter_mm,
            "face_target_mm": config.face_diameter_mm,
            "fit": config.fit.public(),
            "assembly_mode": config.assembly_mode,
        },
        "metadata": config.metadata,
    }
    _atomic_text(report_path, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    manifest = {
        "schema_version": 1,
        "manifest_type": "lens-cap-pipeline-run",
        "pipeline_version": PROCESS_VERSION,
        "runtime": report["runtime"],
        "job_slug": config.job_slug,
        "config_sha256": config.digest(),
        "source": source_lock,
        "artwork_fidelity": {
            "source_read_only": True,
            "coordinate_preserving": True,
            "no_redraw_or_retype": True,
        },
        "mechanical": report["mechanical"],
        "outputs": outputs,
        "process_report_sha256": sha256_file(report_path),
        "status": status,
    }
    _atomic_text(config.output_dir / "run-manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    if status != "passed":
        raise ProcessError(f"process safety checks failed; inspect {report_path}")
    # Keep the marker while any exception is propagating; only a completely
    # passed transaction is allowed to clear it.
    in_progress.unlink(missing_ok=True)
    return report
