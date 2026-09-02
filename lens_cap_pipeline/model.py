"""Stable, parameterised OpenSCAD model generation.

The model stage deliberately knows nothing about a particular lens brand.  It
consumes the masks/SVGs and palette emitted by :mod:`lens_cap_pipeline.process`
and writes one generic cup-shaped cap source plus a fit-ring selector.  Artwork
is imported from the shared SVG canvas; no text, logo, or motif is retyped in
this file.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import PipelineConfig

MODEL_VERSION = "0.1.0"


class ModelError(RuntimeError):
    """Raised when mechanical inputs or process artifacts are unsafe."""


@dataclass(frozen=True)
class ModelResult:
    """Paths and report returned by :func:`generate_model`."""

    model_dir: Path
    scad_path: Path
    geometry_report_path: Path
    report: dict[str, Any]


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
    ) as handle:
        temporary = Path(handle.name)
        handle.write(text.encode("utf-8"))
    try:
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _scad_string(value: str) -> str:
    # JSON string syntax is accepted by OpenSCAD and safely escapes paths.
    return json.dumps(value.replace("\\", "/"), ensure_ascii=False)


def _mechanical_values(config: PipelineConfig) -> dict[str, Any]:
    """Resolve fitted-cap dimensions without importing a previous job."""

    raw = getattr(config, "raw", {}) or {}
    fit = getattr(config, "fit", None)
    if not isinstance(raw, dict):
        raw = {}

    measured = getattr(config, "measured_diameter_mm", None)
    if measured is None:
        measured = raw.get("measured_diameter_mm")
    if measured is None and isinstance(raw.get("fit"), dict):
        measured = raw["fit"].get("measured_diameter_mm")
    if measured is None:
        # A fitted model must not silently treat the decorative face diameter
        # as the gripping diameter.  Require an explicit measurement.
        raise ModelError("measured_diameter_mm is required before generating a fitted cap model")
    measured = float(measured)
    if measured <= 0:
        raise ModelError("measured_diameter_mm must be positive")

    def fit_value(name: str, default: Any) -> Any:
        if fit is not None and hasattr(fit, name):
            return getattr(fit, name)
        nested = raw.get("fit")
        if isinstance(nested, dict) and name in nested:
            return nested[name]
        return default

    foam_status = str(fit_value("foam_liner_status", raw.get("foam_liner_status", "none"))).lower()
    if foam_status not in {"none", "foam"}:
        raise ModelError("foam_liner_status must be 'none' or 'foam'")
    liner = fit_value("liner_thickness_mm", raw.get("liner_thickness_mm"))
    compression = float(fit_value("compression_fraction", raw.get("compression_fraction", 0.20)))
    if foam_status == "foam":
        if liner is None or float(liner) <= 0:
            raise ModelError("liner_thickness_mm is required when foam_liner_status='foam'")
        liner = float(liner)
        if not 0 <= compression < 1:
            raise ModelError("compression_fraction must be in [0,1)")
        cavity = measured + 2.0 * liner * (1.0 - compression)
        radial_compression = liner * compression
    else:
        liner = None if liner is None else float(liner)
        bare_clearance = float(fit_value("bare_clearance_mm", raw.get("bare_clearance_mm", 0.40)))
        if bare_clearance < 0:
            raise ModelError("bare_clearance_mm must be non-negative")
        cavity = measured + bare_clearance
        radial_compression = 0.0

    wall = float(fit_value("wall_thickness_mm", 2.4))
    bottom = float(fit_value("bottom_thickness_mm", 2.0))
    side = float(fit_value("side_height_mm", 14.0))
    if wall < 0.4 or bottom <= 0 or side <= 0:
        raise ModelError("wall, bottom, and side dimensions are invalid")
    total = bottom + side
    if cavity <= 0 or cavity + 2 * wall <= cavity:
        raise ModelError("derived cavity/outer diameter is invalid")
    return {
        "measured_diameter_mm": measured,
        "foam_liner_status": foam_status,
        "liner_thickness_mm": liner,
        "compression_fraction": compression,
        "radial_compression_mm": radial_compression,
        "bare_clearance_mm": float(fit_value("bare_clearance_mm", raw.get("bare_clearance_mm", 0.40))),
        "cavity_diameter_mm": cavity,
        "wall_thickness_mm": wall,
        "bottom_thickness_mm": bottom,
        "side_height_mm": side,
        "total_height_mm": total,
        "retention_strategy": str(fit_value("retention_strategy", "auto")),
        "compression_is_assumption": bool(fit_value("compression_is_assumption", True)),
    }


def _relief_entries(config: PipelineConfig, process_result: Any, model_dir: Path) -> list[dict[str, Any]]:
    vectors = getattr(process_result, "vectors", {})
    process_report = getattr(process_result, "report", {}) or {}
    report_stats = process_report.get("mask_stats", {}) if isinstance(process_report, dict) else {}
    output_root = config.output_dir.resolve()
    entries: list[dict[str, Any]] = []
    for palette in config.palette:
        if palette.role != "relief" or palette.height_mm <= 0:
            continue
        vector = vectors.get(palette.name)
        if vector is None:
            raise ModelError(f"process result has no SVG for palette colour {palette.name!r}")
        vector_path = Path(vector).resolve()
        if not vector_path.is_file():
            raise ModelError(f"missing relief SVG: {vector_path}")
        try:
            vector_path.relative_to(output_root)
        except ValueError as exc:
            # A process report must not redirect the model stage to a
            # neighbouring job or an arbitrary filesystem path.
            raise ModelError(
                f"relief SVG for {palette.name!r} is outside the current output directory"
            ) from exc
        expected_stats = report_stats.get(palette.name) if isinstance(report_stats, dict) else None
        expected_hash = expected_stats.get("svg_sha256") if isinstance(expected_stats, dict) else None
        actual_hash = _sha256(vector_path)
        # The bundled process schema records a hash for every SVG.  Requiring
        # that field here prevents a hand-edited or partial process report
        # from redirecting the model stage to an untracked relief mask.
        if not isinstance(expected_hash, str) or not expected_hash:
            raise ModelError(
                f"process report has no SVG hash for palette colour {palette.name!r}; rerun the process stage"
            )
        if expected_hash.lower() != actual_hash.lower():
            raise ModelError(
                f"relief SVG for {palette.name!r} differs from the passed process report; rerun process"
            )
        entries.append(
            {
                "index": palette.index,
                "name": palette.name,
                "hex": "#%02X%02X%02X" % palette.rgb,
                "height_mm": float(palette.height_mm),
                "svg": Path(os.path.relpath(vector_path, model_dir)).as_posix(),
                "svg_sha256": actual_hash,
            }
        )
    return entries


def _module_name(name: str, index: int) -> str:
    safe = "".join(ch if ch.isalnum() else "_" for ch in name)
    return f"relief_{index}_{safe}".strip("_") or f"relief_{index}"


def generate_model(
    config: PipelineConfig,
    process_result: Any,
    *,
    force: bool = False,
) -> ModelResult:
    """Generate a generic one-piece cap SCAD and a machine-readable report.

    ``process_result`` is intentionally duck-typed so callers can use the
    stable ``ProcessResult`` returned by either a future adapter or this
    package.  The process report must have passed before geometry is emitted.
    """

    # ``process()`` in the compact core returns its report dict directly,
    # whereas adapters may return a ProcessResult object.  Accept both forms
    # so the model stage remains a stable interchange point.
    if isinstance(process_result, dict):
        process_report = process_result
        output_data = process_report.get("outputs", {}) or {}
        if not isinstance(output_data, dict):
            output_data = {}
        svg_data = output_data.get("svgs", {}) or {}
        if not isinstance(svg_data, dict):
            svg_data = {}
        vectors = {
            name: (config.output_dir / value)
            for name, value in svg_data.items()
            if isinstance(value, str)
        }
        if not vectors:
            vectors = {
                palette.name: config.output_dir / "vector" / f"{palette.name}.svg"
                for palette in config.palette
            }
        process_result = type(
            "ProcessResultView",
            (),
            {
                "vectors": vectors,
                "report": process_report,
                "report_path": config.output_dir / "process-report.json",
            },
        )()
    else:
        process_report = getattr(process_result, "report", {}) or {}
    if not isinstance(process_report, dict):
        raise ModelError("process report must be a JSON object; rerun the process stage")
    if process_report.get("status") != "passed":
        raise ModelError("art process report is not passed; refusing to generate geometry")
    requested_assembly = str(getattr(config, "assembly_mode", "auto")).lower()
    if requested_assembly not in {"auto", "integrated_part"}:
        # This canonical adapter emits one connected, integrated cap.  Do not
        # silently reinterpret an explicit separate-parts/inlay request as
        # that geometry; a future adapter can implement those modes behind a
        # separate, explicit contract.
        raise ModelError(
            f"assembly_mode={requested_assembly!r} is not supported by the one-piece model; "
            "use 'auto' or 'integrated_part'"
        )
    expected_digest = config.digest()
    report_digest = process_report.get("config_sha256")
    if not isinstance(report_digest, str) or report_digest != expected_digest:
        raise ModelError("process report belongs to a different config; rerun the process stage")
    mechanical = _mechanical_values(config)
    face_diameter = float(config.face_diameter_mm)
    if face_diameter <= 0:
        raise ModelError("face_diameter_mm must be positive")
    if face_diameter > mechanical["cavity_diameter_mm"] + 2 * mechanical["wall_thickness_mm"]:
        raise ModelError("face_diameter_mm cannot exceed the derived outer cap diameter")
    face_section = process_report.get("face")
    reported_face = face_section.get("diameter_mm") if isinstance(face_section, dict) else None
    if reported_face is None:
        output_section = process_report.get("output")
        reported_face = output_section.get("face_target_mm") if isinstance(output_section, dict) else None
    if reported_face is None or abs(float(reported_face) - face_diameter) > 1e-6:
        raise ModelError(
            "process face diameter does not match model face_diameter_mm; rerun process with the same config"
        )
    source_section = process_report.get("source")
    if not isinstance(source_section, dict):
        raise ModelError("process report has no source lock; rerun the process stage")
    declared_source_hash = getattr(config, "source_sha256", None)
    process_source_hash = source_section.get("sha256")
    if not isinstance(process_source_hash, str):
        raise ModelError("process report has no source hash; rerun the process stage")
    try:
        current_source_hash = _sha256(config.source_path)
    except OSError as exc:
        raise ModelError("current source artwork is unavailable; restore it before modelling") from exc
    if current_source_hash.lower() != process_source_hash.lower():
        raise ModelError("current source artwork differs from the passed process report; rerun process")
    if declared_source_hash and str(declared_source_hash).lower() != str(process_source_hash).lower():
        raise ModelError("process source hash does not match the config source lock")
    if abs(face_diameter - mechanical["measured_diameter_mm"]) > 1e-6:
        # A different decorative face diameter is allowed, but it is made
        # explicit in the report instead of silently coupling the two values.
        face_relation = "explicit_override"
    else:
        face_relation = "derived_from_measured_diameter"

    model_dir = (config.output_dir / "model").resolve()
    scad_path = model_dir / f"{config.job_slug}.scad"
    report_path = model_dir / "geometry-report.json"
    if not force and (scad_path.exists() or report_path.exists()):
        raise ModelError(f"model output already exists: {model_dir}; use force=True to replace it")
    model_dir.mkdir(parents=True, exist_ok=True)
    entries = _relief_entries(config, process_result, model_dir)
    try:
        base = config.base
    except (AttributeError, StopIteration) as exc:
        raise ModelError("config must declare exactly one base palette colour") from exc

    modules: list[str] = []
    calls: list[str] = []
    selectors: list[str] = []
    for entry in entries:
        module = _module_name(entry["name"], int(entry["index"]))
        modules.append(
            f'''module {module}() {{
    // Imported SVG is the shared process canvas; do not center or recrop it.
    translate([-panel_diameter / 2, -panel_diameter / 2, total_height])
        linear_extrude(height={entry["height_mm"]:.6g} + eps, convexity=10)
            import(file={_scad_string(entry["svg"])}, center=false);
}}'''
        )
        calls.append(f'    color({_scad_string(entry["hex"])}) {module}();')
        selectors.append(f'else if (render_part == "{entry["name"]}_relief")\n    {module}();')

    module_text = "\n\n".join(modules) or "// No positive-relief palette entries were configured."
    assembly_text = "\n".join(calls) or "    // no positive relief"
    selector_text = "\n".join(selectors)
    fit = mechanical
    base_hex = "#%02X%02X%02X" % base.rgb
    text = f'''// Generated by lens-cap-pipeline model {MODEL_VERSION}
// Job: {config.job_slug}
// Artwork is imported from process-stage shared SVG masks.  No brand/model
// text is retyped here.  Build and test a fit ring before a full print.

// -------------------- dimensions (mm) --------------------
lens_outer_diameter = {fit["measured_diameter_mm"]:.6g};
face_target_mm = {face_diameter:.6g};
foam_liner_status = {_scad_string(fit["foam_liner_status"])};
liner_thickness = {float(fit["liner_thickness_mm"] or 0):.6g};
compression_fraction = {fit["compression_fraction"]:.6g};
bare_clearance_mm = {fit["bare_clearance_mm"]:.6g};
cavity_diameter = {fit["cavity_diameter_mm"]:.6g};
wall_thickness = {fit["wall_thickness_mm"]:.6g};
bottom_thickness = {fit["bottom_thickness_mm"]:.6g};
side_height = {fit["side_height_mm"]:.6g};
total_height = {fit["total_height_mm"]:.6g};
panel_diameter = face_target_mm;
panel_base_thickness = 0.80;
segments = 360;
eps = 0.001;
base_color = {_scad_string(base_hex)};
render_part = "assembly";

// The cavity opens downward (z=0); the closed floor/front face is at z=total_height.
module cap_body() {{
    difference() {{
        cylinder(d=cavity_diameter + 2 * wall_thickness, h=total_height, $fn=segments);
        cylinder(d=cavity_diameter, h=side_height + eps, $fn=segments);
    }}
}}

module face_base() {{
    // The body floor is structural; this thin disk defines the artwork base
    // and keeps the process face diameter independent from the outer rim.
    translate([0, 0, total_height - panel_base_thickness])
        cylinder(d=panel_diameter, h=panel_base_thickness + eps, $fn=segments);
}}

module base_part() {{
    color(base_color) union() {{ cap_body(); face_base(); }}
}}

{module_text}

module assembly() {{
    base_part();
{assembly_text}
}}

module fit_ring(target_diameter=cavity_diameter, ring_height=8) {{
    assert(target_diameter > 0, "fit ring diameter must be positive");
    difference() {{
        cylinder(d=target_diameter + 2 * wall_thickness, h=ring_height, $fn=segments);
        cylinder(d=target_diameter, h=ring_height + eps, $fn=segments);
    }}
}}

// Select a geometry for OpenSCAD export.  The default is the integrated
// one-piece assembly; individual relief selectors retain the shared canvas.
if (render_part == "assembly")
    assembly();
else if (render_part == "base")
    base_part();
else if (render_part == "cap_body")
    cap_body();
else if (render_part == "face_base")
    face_base();
else if (render_part == "fit_ring")
    fit_ring();
{selector_text}
else
    assembly();
'''
    _atomic_text(scad_path, text)

    process_report_path = getattr(process_result, "report_path", None)
    if process_report_path is None:
        # The compact dict adapter and most callers place the report at the
        # canonical output path.  Resolve that fallback so the provenance hash
        # is retained without embedding an absolute path in the report.
        candidate_report = config.output_dir / "process-report.json"
        if candidate_report.is_file():
            process_report_path = candidate_report
    process_report_hash = None
    if process_report_path and Path(process_report_path).is_file():
        process_report_hash = _sha256(Path(process_report_path))
    process_report_rel = None
    if process_report_path:
        process_report_rel = Path(os.path.relpath(Path(process_report_path).resolve(), model_dir)).as_posix()
    geometry = {
        "schema_version": 1,
        "model_version": MODEL_VERSION,
        "status": "passed",
        "job_slug": config.job_slug,
        # Paths in committed/audited reports are relative to the report's
        # directory.  ModelResult still exposes the resolved Path for callers
        # that need to invoke OpenSCAD.
        "scad_path": scad_path.name,
        "scad_sha256": _sha256(scad_path),
        "process_report": process_report_rel,
        "process_report_sha256": process_report_hash,
        "art_source_sha256": process_source_hash,
        "face_target_mm": face_diameter,
        "face_target_relation": face_relation,
        "mechanical": fit,
        "outer_diameter_mm": fit["cavity_diameter_mm"] + 2 * fit["wall_thickness_mm"],
        "panel_base_thickness_mm": 0.80,
        "segments": 360,
        "assembly_mode_requested": requested_assembly,
        "assembly_mode_resolved": "integrated_part",
        "relief_entries": entries,
        "render_part_selectors": ["assembly", "base", "cap_body", "face_base", "fit_ring"]
        + [f'{entry["name"]}_relief' for entry in entries],
        "shared_svg_canvas": True,
        "physical_fit": "UNVERIFIABLE until a fit coupon is tested",
    }
    _atomic_text(report_path, json.dumps(geometry, ensure_ascii=False, indent=2) + "\n")
    model_manifest = {
        "schema_version": 1,
        "manifest_type": "lens-cap-pipeline-model",
        "job_slug": config.job_slug,
        "config_sha256": config.digest(),
        "process_report_sha256": process_report_hash,
        "geometry_report_sha256": _sha256(report_path),
        "scad_sha256": _sha256(scad_path),
        "status": "passed",
        "selectors": geometry["render_part_selectors"],
    }
    _atomic_text(model_dir / "model-manifest.json", json.dumps(model_manifest, ensure_ascii=False, indent=2) + "\n")
    return ModelResult(model_dir, scad_path, report_path, geometry)


# The alias keeps the common naming used by earlier prototypes and lets a
# caller migrate without changing its CLI/API import.
generate_scad = generate_model
