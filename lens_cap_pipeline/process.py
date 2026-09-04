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
import math
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

PROCESS_VERSION = "0.3.0"


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


def _minimum_relief_feature_audit(
    labels: np.ndarray,
    inside: np.ndarray,
    palettes: tuple[PaletteSpec, ...],
    *,
    face_diameter_mm: float,
    grid_size: int,
    nozzle_mm: float,
    allowed_area_px: int,
    allowed_dimension_px: int,
) -> dict[str, Any]:
    """Reject meaningful positive strokes or negative channels below nozzle width.

    This is a conservative two-dimensional manufacturability gate, not a
    promise that a particular slicer will reproduce every boundary pixel. A
    square opening is intentionally deterministic across supported Pillow
    versions. Tiny corner losses no larger than the already-declared cleanup
    limits are reported but tolerated; a long sub-nozzle stroke is not.  The
    base colour is audited as negative artwork as well, so a hairline groove
    cut through a broad relief field cannot evade the positive-only checks.
    """

    pixel_pitch_mm = float(face_diameter_mm) / int(grid_size)
    feature_width_px = float(nozzle_mm) / pixel_pitch_mm
    kernel_px = max(1, int(math.ceil(feature_width_px - 1e-12)))
    colors: dict[str, Any] = {}
    violation_count = 0
    # Ignore only the rasterized outer-circle fringe when examining the base
    # colour.  That fringe is a mechanical safe boundary rather than artwork;
    # internal base-colour channels remain in ``interior`` and are audited.
    interior = inside.copy()
    for _ in range(kernel_px):
        padded = np.pad(interior, 1, constant_values=False)
        interior = (
            padded[1:-1, 1:-1]
            & padded[:-2, 1:-1]
            & padded[2:, 1:-1]
            & padded[1:-1, :-2]
            & padded[1:-1, 2:]
        )

    for palette in palettes:
        mask = inside & (labels == palette.index)
        pixels = int(np.count_nonzero(mask))
        if pixels == 0 or kernel_px == 1:
            colors[palette.name] = {
                "role": palette.role,
                "pixels": pixels,
                "unsupported_pixels": 0,
                "unsupported_components": 0,
                "violating_components": 0,
                "aggregate_cleanup_budget_exceeded": False,
                "largest_violation_area_px": 0,
                "largest_violation_dimension_px": 0,
                "status": "not_present" if pixels == 0 else "passed",
            }
            continue
        # Mark the union of every all-positive k×k block using an integral
        # image plus a rectangle difference array. Unlike an odd-only Pillow
        # min filter, this handles thresholds between one and two pixels: a
        # one-pixel hairline fails while a two-pixel stroke can survive.
        numeric = mask.astype(np.int32)
        integral = np.pad(numeric, ((1, 0), (1, 0))).cumsum(0).cumsum(1)
        block_sums = (
            integral[kernel_px:, kernel_px:]
            - integral[:-kernel_px, kernel_px:]
            - integral[kernel_px:, :-kernel_px]
            + integral[:-kernel_px, :-kernel_px]
        )
        valid_y, valid_x = np.nonzero(block_sums == kernel_px * kernel_px)
        difference = np.zeros((mask.shape[0] + 1, mask.shape[1] + 1), dtype=np.int32)
        np.add.at(difference, (valid_y, valid_x), 1)
        np.add.at(difference, (valid_y + kernel_px, valid_x), -1)
        np.add.at(difference, (valid_y, valid_x + kernel_px), -1)
        np.add.at(
            difference,
            (valid_y + kernel_px, valid_x + kernel_px),
            1,
        )
        opened_mask = (difference.cumsum(0).cumsum(1)[:-1, :-1] > 0) & mask
        unsupported = mask & ~opened_mask
        if palette.role == "base":
            unsupported &= interior
        unsupported_components = _components(unsupported)
        violating: list[tuple[int, int]] = []
        for component in unsupported_components:
            ys = [cell[0] for cell in component]
            xs = [cell[1] for cell in component]
            dimension = max(max(ys) - min(ys) + 1, max(xs) - min(xs) + 1)
            if len(component) > allowed_area_px or dimension > allowed_dimension_px:
                violating.append((len(component), dimension))
        unsupported_pixels = int(np.count_nonzero(unsupported))
        aggregate_budget_exceeded = bool(
            unsupported_pixels > allowed_area_px
            or len(unsupported_components) > allowed_dimension_px
        )
        violation_count += len(violating) + int(aggregate_budget_exceeded)
        colors[palette.name] = {
            "role": palette.role,
            "pixels": pixels,
            "unsupported_pixels": unsupported_pixels,
            "unsupported_components": len(unsupported_components),
            "violating_components": len(violating),
            "aggregate_cleanup_budget_exceeded": aggregate_budget_exceeded,
            "largest_violation_area_px": max((item[0] for item in violating), default=0),
            "largest_violation_dimension_px": max((item[1] for item in violating), default=0),
            "status": "failed" if violating or aggregate_budget_exceeded else "passed",
        }
    return {
        "status": "failed" if violation_count else "passed",
        "minimum_feature_mm": float(nozzle_mm),
        "pixel_pitch_mm": pixel_pitch_mm,
        "minimum_feature_px": feature_width_px,
        "support_kernel_px": kernel_px,
        "allowed_cleanup_area_px": int(allowed_area_px),
        "allowed_cleanup_dimension_px": int(allowed_dimension_px),
        "violating_components": violation_count,
        "colors": colors,
        "scope": "conservative_2d_positive_stroke_and_negative_channel_nozzle_width_opening_not_a_physical_fit_proof",
    }


def _trace_mask_contours(mask: np.ndarray) -> list[list[tuple[float, float]]]:
    """Trace the exact directed boundary of a binary pixel union.

    Standard marching squares cuts 0.5 pixel from every true ninety-degree
    corner and turns an isolated square pixel into a diamond.  That is
    unacceptable for typography.  Here every true pixel remains a unit square
    and only its exposed edges are emitted.  Foreground stays on the right of
    each directed edge, giving positive outer rings and negative holes in
    SVG's downward-Y coordinates.

    Checkerboard contacts otherwise leave two polygons touching at one point,
    which can become non-manifold after extrusion.  At only those ambiguous
    vertices each incident foreground corner is clipped by 1/4 px.  Normal
    font corners and rectangular blocks remain exact.
    """

    values = np.asarray(mask, dtype=bool)
    if values.ndim != 2:
        raise ValueError("SVG mask must be a two-dimensional array")
    if not np.any(values):
        return []
    above = np.zeros_like(values)
    above[1:] = values[:-1]
    below = np.zeros_like(values)
    below[:-1] = values[1:]
    left = np.zeros_like(values)
    left[:, 1:] = values[:, :-1]
    right = np.zeros_like(values)
    right[:, :-1] = values[:, 1:]

    # In image coordinates (+Y down), these directions walk clockwise around
    # an outer boundary and counter-clockwise around a hole.
    edges: set[tuple[tuple[int, int], tuple[int, int]]] = set()
    for y_raw, x_raw in zip(*np.nonzero(values & ~above), strict=True):
        y, x = int(y_raw), int(x_raw)
        edges.add(((x, y), (x + 1, y)))
    for y_raw, x_raw in zip(*np.nonzero(values & ~right), strict=True):
        y, x = int(y_raw), int(x_raw)
        edges.add(((x + 1, y), (x + 1, y + 1)))
    for y_raw, x_raw in zip(*np.nonzero(values & ~below), strict=True):
        y, x = int(y_raw), int(x_raw)
        edges.add(((x + 1, y + 1), (x, y + 1)))
    for y_raw, x_raw in zip(*np.nonzero(values & ~left), strict=True):
        y, x = int(y_raw), int(x_raw)
        edges.add(((x, y + 1), (x, y)))

    outgoing: dict[tuple[int, int], list[tuple[int, int]]] = {}
    incoming_count: Counter[tuple[int, int]] = Counter()
    for start, end in sorted(edges):
        outgoing.setdefault(start, []).append(end)
        incoming_count[end] += 1
    invalid = sorted(
        vertex
        for vertex in set(outgoing) | set(incoming_count)
        if len(outgoing.get(vertex, ())) != incoming_count.get(vertex, 0)
        or len(outgoing.get(vertex, ())) not in (1, 2)
    )
    if invalid:
        raise ProcessError(
            "pixel-union contour topology is not balanced at "
            f"{len(invalid)} vertex/vertices"
        )
    ambiguous = {
        vertex for vertex, destinations in outgoing.items() if len(destinations) == 2
    }

    def turn_rank(
        previous: tuple[int, int],
        vertex: tuple[int, int],
        destination: tuple[int, int],
    ) -> tuple[int, tuple[int, int]]:
        incoming = (vertex[0] - previous[0], vertex[1] - previous[1])
        candidate = (destination[0] - vertex[0], destination[1] - vertex[1])
        directions = (
            (-incoming[1], incoming[0]),  # right turn: preserve 4-connectivity
            incoming,
            (incoming[1], -incoming[0]),
            (-incoming[0], -incoming[1]),
        )
        try:
            rank = directions.index(candidate)
        except ValueError as exc:
            raise ProcessError("pixel-union contour contains a non-unit edge") from exc
        return rank, destination

    unused = set(edges)
    raw_contours: list[list[tuple[int, int]]] = []
    while unused:
        start, current = min(
            unused, key=lambda edge: (edge[0][1], edge[0][0], edge[1])
        )
        previous = start
        contour = [start]
        unused.remove((start, current))
        while current != start:
            contour.append(current)
            candidates = [
                destination
                for destination in outgoing.get(current, ())
                if (current, destination) in unused
            ]
            if not candidates:
                raise ProcessError("pixel-union contour could not be closed")
            following = min(
                candidates,
                key=lambda destination: turn_rank(previous, current, destination),
            )
            unused.remove((current, following))
            previous, current = current, following
        if len(contour) < 3:
            raise ProcessError("pixel-union tracing emitted a degenerate contour")
        raw_contours.append(contour)

    pixel_area = sum(
        _polygon_area([(float(x), float(y)) for x, y in contour])
        for contour in raw_contours
    )
    if not math.isclose(pixel_area, float(np.count_nonzero(values)), abs_tol=1e-9):
        raise ProcessError(
            "pixel-union contours do not preserve the binary-mask area: "
            f"{pixel_area:g} != {int(np.count_nonzero(values))} px2"
        )

    # Clip only the checkerboard contact itself. All coordinates stay on a
    # deterministic quarter-pixel lattice.
    chamfer = 0.25
    contours: list[list[tuple[float, float]]] = []
    for raw in raw_contours:
        contour: list[tuple[float, float]] = []
        for index, point in enumerate(raw):
            if point not in ambiguous:
                contour.append((float(point[0]), float(point[1])))
                continue
            before = raw[index - 1]
            after = raw[(index + 1) % len(raw)]
            contour.extend(
                (
                    (
                        point[0] + chamfer * (before[0] - point[0]),
                        point[1] + chamfer * (before[1] - point[1]),
                    ),
                    (
                        point[0] + chamfer * (after[0] - point[0]),
                        point[1] + chamfer * (after[1] - point[1]),
                    ),
                )
            )
        anchor = min(
            range(len(contour)),
            key=lambda index: (contour[index][1], contour[index][0]),
        )
        contours.append(contour[anchor:] + contour[:anchor])
    contours.sort(
        key=lambda contour: (
            min(point[1] for point in contour),
            min(point[0] for point in contour),
            max(point[1] for point in contour),
            max(point[0] for point in contour),
            len(contour),
            contour,
        )
    )
    return contours


def _point_segment_distance(
    point: tuple[float, float],
    start: tuple[float, float],
    end: tuple[float, float],
) -> float:
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    if dx == 0 and dy == 0:
        return math.hypot(point[0] - start[0], point[1] - start[1])
    position = max(
        0.0,
        min(
            1.0,
            ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy)
            / (dx * dx + dy * dy),
        ),
    )
    return math.hypot(
        point[0] - (start[0] + position * dx),
        point[1] - (start[1] + position * dy),
    )


def _rdp_open(
    points: list[tuple[float, float]], tolerance: float
) -> list[tuple[float, float]]:
    if len(points) <= 2 or tolerance <= 0:
        return list(points)
    keep = {0, len(points) - 1}
    stack = [(0, len(points) - 1)]
    while stack:
        first, last = stack.pop()
        if last <= first + 1:
            continue
        maximum = -1.0
        selected = -1
        for index in range(first + 1, last):
            distance = _point_segment_distance(points[index], points[first], points[last])
            if distance > maximum:
                maximum = distance
                selected = index
        if selected >= 0 and maximum > tolerance:
            keep.add(selected)
            stack.extend(((first, selected), (selected, last)))
    return [points[index] for index in sorted(keep)]


def _polygon_area(points: list[tuple[float, float]]) -> float:
    return 0.5 * sum(
        left[0] * right[1] - right[0] * left[1]
        for left, right in zip(points, points[1:] + points[:1])
    )


def _without_collinear_vertices(
    points: list[tuple[float, float]],
) -> list[tuple[float, float]]:
    """Remove zero-error vertices on straight runs of a closed polygon."""

    if len(points) <= 3:
        return list(points)
    result = [
        point
        for index, point in enumerate(points)
        if not math.isclose(
            (point[0] - points[index - 1][0])
            * (points[(index + 1) % len(points)][1] - point[1])
            - (point[1] - points[index - 1][1])
            * (points[(index + 1) % len(points)][0] - point[0]),
            0.0,
            abs_tol=1e-12,
        )
    ]
    return result if len(result) >= 3 else list(points)


def _protected_right_angle_indices(
    points: list[tuple[float, float]], minimum_run_px: float
) -> list[int]:
    """Locate printable orthogonal corners that contour smoothing must retain."""

    protected: list[int] = []
    for index, point in enumerate(points):
        previous = points[index - 1]
        following = points[(index + 1) % len(points)]
        incoming = (point[0] - previous[0], point[1] - previous[1])
        outgoing = (following[0] - point[0], following[1] - point[1])
        incoming_axis = math.isclose(incoming[0], 0.0) != math.isclose(
            incoming[1], 0.0
        )
        outgoing_axis = math.isclose(outgoing[0], 0.0) != math.isclose(
            outgoing[1], 0.0
        )
        perpendicular = math.isclose(
            incoming[0] * outgoing[0] + incoming[1] * outgoing[1],
            0.0,
            abs_tol=1e-12,
        )
        if (
            incoming_axis
            and outgoing_axis
            and perpendicular
            and math.hypot(*incoming) >= minimum_run_px
            and math.hypot(*outgoing) >= minimum_run_px
        ):
            protected.append(index)
    return protected


def _simplify_closed_contour(
    points: list[tuple[float, float]],
    tolerance: float,
    *,
    hard_corner_run_px: float = 2.0,
) -> list[tuple[float, float]]:
    """Simplify a closed contour while pinning printable right-angle corners."""

    if len(points) <= 3:
        return list(points)
    compact = _without_collinear_vertices(points)
    protected = _protected_right_angle_indices(compact, hard_corner_run_px)
    if len(protected) >= 2:
        anchor = protected[0]
        rotated = compact[anchor:] + compact[:anchor]
        protected = sorted((index - anchor) % len(compact) for index in protected)
        closed = rotated + [rotated[0]]
        bounds = protected + [len(rotated)]
        simplified: list[tuple[float, float]] = []
        for first, last in zip(bounds, bounds[1:]):
            simplified.extend(_rdp_open(closed[first : last + 1], tolerance)[:-1])
        if len(simplified) >= 3:
            original_area = _polygon_area(compact)
            simplified_area = _polygon_area(simplified)
            if (
                original_area != 0
                and simplified_area != 0
                and original_area * simplified_area > 0
            ):
                return simplified

    anchor = min(range(len(compact)), key=lambda index: compact[index])
    rotated = compact[anchor:] + compact[:anchor]
    split = max(
        range(1, len(rotated)),
        key=lambda index: (
            (rotated[index][0] - rotated[0][0]) ** 2
            + (rotated[index][1] - rotated[0][1]) ** 2,
            -index,
        ),
    )
    first = _rdp_open(rotated[: split + 1], tolerance)
    second = _rdp_open(rotated[split:] + [rotated[0]], tolerance)
    simplified = first[:-1] + second[:-1]
    if len(simplified) < 3:
        return list(points)
    original_area = _polygon_area(compact)
    simplified_area = _polygon_area(simplified)
    if original_area == 0 or simplified_area == 0 or original_area * simplified_area < 0:
        return compact
    return simplified


def _svg_number(value: float) -> str:
    rounded = round(value * 4.0) / 4.0
    if math.isclose(rounded, round(rounded), abs_tol=1e-12):
        return str(int(round(rounded)))
    return f"{rounded:.2f}".rstrip("0").rstrip(".")


def _write_svg(
    mask: np.ndarray,
    path: Path,
    face_mm: float,
    fill: str,
    *,
    nozzle_mm: float,
) -> dict[str, Any]:
    contours = _trace_mask_contours(mask)
    pixel_pitch_mm = float(face_mm) / int(mask.shape[1])
    # Use at most half a raster cell and remain below one nozzle.
    # Printable orthogonal corners are pinned separately; the remaining budget
    # turns digital stair runs into short diagonals without inventing detail.
    tolerance_mm = min(0.10, float(nozzle_mm) * 0.50, pixel_pitch_mm * 0.50)
    tolerance_px = tolerance_mm / pixel_pitch_mm
    hard_corner_run_px = max(
        2.0, math.ceil(float(nozzle_mm) / pixel_pitch_mm - 1e-12)
    )
    simplified = [
        _simplify_closed_contour(
            contour,
            tolerance_px,
            hard_corner_run_px=hard_corner_run_px,
        )
        for contour in contours
    ]
    commands = "".join(
        "M"
        + "L".join(f"{_svg_number(x)} {_svg_number(y)}" for x, y in contour)
        + "Z"
        for contour in simplified
    )
    svg = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{face_mm:g}mm" height="{face_mm:g}mm" '
        f'viewBox="0 0 {mask.shape[1]} {mask.shape[0]}" shape-rendering="geometricPrecision">\n'
        f'  <path fill="{fill}" fill-rule="evenodd" d="{commands}"/>\n'
        '</svg>\n'
    )
    _atomic_text(path, svg)
    diagonal_segments = sum(
        1
        for contour in simplified
        for left, right in zip(contour, contour[1:] + contour[:1])
        if not math.isclose(left[0], right[0]) and not math.isclose(left[1], right[1])
    )
    filled_area_px2 = sum(_polygon_area(contour) for contour in simplified)
    if filled_area_px2 < -1e-9:
        raise ProcessError("pixel-union SVG has a negative compound filled area")
    source_area_px2 = float(np.count_nonzero(mask))
    return {
        "algorithm": "directed-pixel-union-quarter-chamfer-rdp",
        "contours": len(simplified),
        "vertices_before": sum(len(contour) for contour in contours),
        "vertices_after": sum(len(contour) for contour in simplified),
        "diagonal_segments": diagonal_segments,
        "pixel_pitch_mm": pixel_pitch_mm,
        "coordinate_quantum_mm": pixel_pitch_mm / 4.0,
        "simplification_tolerance_px": tolerance_px,
        "maximum_deviation_mm": tolerance_mm,
        "protected_corner_minimum_run_px": hard_corner_run_px,
        "source_pixel_area_mm2": source_area_px2 * pixel_pitch_mm * pixel_pitch_mm,
        "filled_area_mm2": filled_area_px2 * pixel_pitch_mm * pixel_pitch_mm,
        "filled_area_delta_mm2": (filled_area_px2 - source_area_px2)
        * pixel_pitch_mm
        * pixel_pitch_mm,
        "fill_rule": "evenodd",
    }


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
    minimum_feature_audit = _minimum_relief_feature_audit(
        labels,
        inside,
        config.palette,
        face_diameter_mm=config.face_diameter_mm,
        grid_size=config.grid_size,
        nozzle_mm=config.nozzle_mm,
        allowed_area_px=config.cleanup.max_area_px,
        allowed_dimension_px=config.cleanup.max_dimension_px,
    )

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
    svg_vectorization: dict[str, dict[str, Any]] = {}
    mask_stats: dict[str, Any] = {}
    for p in config.palette:
        mask = inside & (labels == p.index)
        mask_path = masks_dir / f"{p.name}.png"
        _atomic_image(Image.fromarray(np.where(mask, 255, 0).astype(np.uint8), mode="L"), mask_path)
        svg_path = vector_dir / f"{p.name}.svg"
        svg_vectorization[p.name] = _write_svg(
            mask,
            svg_path,
            config.face_diameter_mm,
            _hex(p.rgb),
            nozzle_mm=config.nozzle_mm,
        )
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
            "svg_contours": svg_vectorization[p.name]["contours"],
            "svg_vertices": svg_vectorization[p.name]["vertices_after"],
            "svg_vectorization": svg_vectorization[p.name],
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
        svg_nonempty = int(stats["svg_contours"]) > 0
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
        "minimum_relief_feature_width": minimum_feature_audit["status"] == "passed",
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
        "svg_vectorization": svg_vectorization,
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
        "minimum_relief_feature_audit": minimum_feature_audit,
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
