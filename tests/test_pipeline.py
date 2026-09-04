from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from lens_cap_pipeline.config import ConfigError, load_config, template_config
from lens_cap_pipeline.process import (
    ProcessError,
    _polygon_area,
    _simplify_closed_contour,
    _trace_mask_contours,
    _write_svg,
    process,
)
from lens_cap_pipeline.validate import validate_job


def _job(tmp_path: Path, *, circle: bool = True) -> Path:
    image = Image.new("RGB", (64, 64), (17, 18, 17))
    draw = ImageDraw.Draw(image)
    draw.ellipse((4, 4, 59, 59), fill=(242, 231, 211))
    draw.rectangle((25, 25, 38, 38), fill=(17, 18, 17))
    source = tmp_path / "master.png"
    image.save(source)
    circle_text = "\n[circle]\ncenter_px=[32,32]\nradius_px=28\n" if circle else ""
    config = tmp_path / "job.toml"
    config.write_text(
        f'''job_slug = "test-cap"
source_art = "master.png"
output_dir = "build"
face_diameter_mm = 52.0
grid_size = 128
nozzle_mm = 0.2
safe_border_mm = 0.3
{circle_text}
[prefilter]
name = "median"
size = 5
radius = 0.8

[cleanup]
enabled = true
max_area_px = 8
max_dimension_px = 3
ring_px = 2
dominance = 0.6
apply_to = ["relief"]

[palette.black]
index = 0
rgb = [17, 18, 17]
role = "base"
height_mm = 0.0
required = true

[palette.ivory]
index = 1
rgb = [242, 231, 211]
role = "relief"
height_mm = 0.4
required = true

[palette.red]
index = 2
rgb = [190, 31, 35]
role = "relief"
height_mm = 0.8
required = false

[palette.red.detect]
channel = "r"
minimum = 80
deltas = {{ g = 45, b = 40 }}
''',
        encoding="utf-8",
    )
    return config


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_default_grid_is_stable_and_independent_of_nozzle() -> None:
    fine = template_config("master.png", 95.0, nozzle_mm=0.2)
    coarse = template_config("master.png", 95.0, nozzle_mm=0.8)
    assert fine["grid_size"] == coarse["grid_size"] == 1000
    assert fine["prefilter"]["name"] == "none"
    assert fine["cleanup"]["enabled"] is False


def test_print_and_runtime_settings_do_not_change_artifact_config_digest(
    tmp_path: Path,
) -> None:
    config = load_config(_job(tmp_path))
    overridden = replace(
        config,
        nozzle_mm=0.4,
        nozzle_explicit=True,
        print=replace(
            config.print,
            nozzle_mm=0.4,
            layer_height_mm=0.2,
            printer="Bambu Lab A1 mini",
            openscad_executable="/Applications/OpenSCAD.app/Contents/MacOS/OpenSCAD",
            bambu_executable="/Applications/BambuStudio.app/Contents/MacOS/BambuStudio",
            filament_slots=("PLA black", "PLA ivory"),
        ),
    )

    assert overridden.digest() == config.digest()


def test_nozzle_change_cannot_change_pre_slicer_artwork_or_vectors(
    tmp_path: Path,
) -> None:
    original = _job(tmp_path).read_text(encoding="utf-8")
    fine_path = tmp_path / "fine.toml"
    coarse_path = tmp_path / "coarse.toml"
    fine_path.write_text(
        original.replace('output_dir = "build"', 'output_dir = "fine-build"'),
        encoding="utf-8",
    )
    coarse_path.write_text(
        original.replace('output_dir = "build"', 'output_dir = "coarse-build"')
        .replace("nozzle_mm = 0.2", "nozzle_mm = 0.4"),
        encoding="utf-8",
    )

    fine = load_config(fine_path)
    coarse = load_config(coarse_path)
    fine_report = process(fine)
    coarse_report = process(coarse)

    relative_outputs = [
        "process-master.png",
        "masks/black.png",
        "masks/ivory.png",
        "vector/black.svg",
        "vector/ivory.svg",
    ]
    for relative in relative_outputs:
        assert _sha(fine.output_dir / relative) == _sha(coarse.output_dir / relative)
    assert fine.digest() == replace(coarse, output_dir=fine.output_dir).digest()
    assert "minimum_relief_feature_audit" not in fine_report
    assert "minimum_relief_feature_audit" not in coarse_report
    assert "nozzle_mm" not in fine_report["face"]
    assert "nozzle_mm" not in coarse_report["face"]


def test_pixel_union_contours_keep_holes_and_split_diagonal_contacts() -> None:
    ring = np.zeros((10, 10), dtype=bool)
    ring[1:9, 1:9] = True
    ring[4:6, 4:6] = False
    contours = _trace_mask_contours(ring)
    assert len(contours) == 2
    assert all(len(contour) >= 4 for contour in contours)
    assert sorted(_polygon_area(contour) for contour in contours) == [-4.0, 64.0]
    assert sum(_polygon_area(contour) for contour in contours) == float(ring.sum())

    diagonal = np.zeros((4, 4), dtype=bool)
    diagonal[1, 1] = True
    diagonal[2, 2] = True
    diagonal_contours = _trace_mask_contours(diagonal)
    assert len(diagonal_contours) == 2
    assert not (set(diagonal_contours[0]) & set(diagonal_contours[1]))
    assert all(_polygon_area(contour) == 0.96875 for contour in diagonal_contours)


def test_pixel_union_simplification_preserves_rectangular_type_corners() -> None:
    mask = np.zeros((10, 12), dtype=bool)
    mask[2:7, 3:9] = True

    contours = _trace_mask_contours(mask)
    assert len(contours) == 1
    simplified = _simplify_closed_contour(contours[0], 0.4)

    assert simplified == [
        (3.0, 2.0),
        (9.0, 2.0),
        (9.0, 7.0),
        (3.0, 7.0),
    ]
    assert _polygon_area(simplified) == 30.0


def test_pixel_union_traces_every_three_by_three_topology_without_shared_vertices() -> None:
    for bits in range(1 << 9):
        mask = np.asarray(
            [(bits >> index) & 1 for index in range(9)], dtype=bool
        ).reshape(3, 3)
        contours = _trace_mask_contours(mask)
        filled_area = sum(_polygon_area(contour) for contour in contours)
        assert -1e-9 <= filled_area <= float(mask.sum()) + 1e-9
        for left_index, left in enumerate(contours):
            for right in contours[:left_index]:
                assert not (set(left) & set(right))


def test_svg_vectorization_uses_smooth_contours_not_pixel_rectangles(
    tmp_path: Path,
) -> None:
    yy, xx = np.indices((96, 96), dtype=np.float64)
    mask = np.hypot(xx - 47.5, yy - 47.5) <= 34.0
    target = tmp_path / "circle.svg"

    stats = _write_svg(mask, target, 19.2, "#F2E7D3")
    payload = target.read_text(encoding="utf-8")

    assert stats["algorithm"] == "directed-pixel-union-quarter-chamfer-rdp"
    assert stats["contours"] == 1
    assert stats["vertices_after"] < stats["vertices_before"]
    assert stats["maximum_deviation_mm"] <= 0.10 + 1e-9
    assert abs(stats["filled_area_delta_mm2"]) < 0.5
    assert 'fill-rule="evenodd"' in payload
    assert 'shape-rendering="geometricPrecision"' in payload
    assert "h1v1" not in payload
    assert stats["diagonal_segments"] > 0


def test_normalized_artifact_config_excludes_print_settings(tmp_path: Path) -> None:
    config_path = _job(tmp_path)

    config = load_config(config_path)
    report = process(config)
    assert report["status"] == "passed"
    normalized = json.loads(
        (config.output_dir / "config.normalized.json").read_text(encoding="utf-8")
    )
    assert "nozzle_mm" not in normalized
    assert "print" not in normalized


def test_process_emits_safe_partition_and_report(tmp_path: Path) -> None:
    config = load_config(_job(tmp_path))
    report = process(config)
    assert report["status"] == "passed"
    assert report["checks"]["overflow_guard"] is True
    assert report["checks"]["source_polarity"] is True
    assert report["checks"]["role_partition"] is True
    assert report["checks"]["color_partition"] is True
    assert report["checks"]["required_palette_outputs"] is True
    output = config.output_dir
    rgba = np.asarray(Image.open(output / "process-master.png").convert("RGBA"))
    outside = np.asarray(Image.open(output / "outside.png").convert("L")) > 0
    assert np.all(rgba[outside, 3] == 0)
    assert report["circle"]["coordinate_preserving"] is True
    assert json.loads((output / "source-lock.json").read_text())["sha256"] == _sha(config.source_path)


def test_repeated_force_run_is_byte_stable(tmp_path: Path) -> None:
    config = load_config(_job(tmp_path))
    first = process(config)
    first_report_bytes = (config.output_dir / "process-report.json").read_bytes()
    first_hashes = {
        name: _sha(config.output_dir / path)
        for name, path in first["outputs"]["masks"].items()
    }
    second = process(config, force=True)
    second_report_bytes = (config.output_dir / "process-report.json").read_bytes()
    second_hashes = {
        name: _sha(config.output_dir / path)
        for name, path in second["outputs"]["masks"].items()
    }
    assert first_hashes == second_hashes
    assert first_report_bytes == second_report_bytes
    assert first["config_sha256"] == second["config_sha256"]


def test_process_refuses_implicit_overwrite(tmp_path: Path) -> None:
    config = load_config(_job(tmp_path))
    process(config)
    try:
        process(config)
    except ProcessError as exc:
        assert "--force" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("process unexpectedly overwrote an existing job")


def test_missing_source_fails_at_config_gate(tmp_path: Path) -> None:
    config = _job(tmp_path)
    missing = config.read_text(encoding="utf-8").replace(
        'source_art = "master.png"', 'source_art = "missing.png"'
    )
    config.write_text(missing, encoding="utf-8")
    try:
        load_config(config)
    except ConfigError as exc:
        assert "does not exist" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("missing source was accepted")


def test_metadata_must_be_json_serialisable(tmp_path: Path) -> None:
    config = _job(tmp_path)
    text = config.read_text(encoding="utf-8")
    text = text.replace(
        "face_diameter_mm = 52.0",
        "face_diameter_mm = 52.0\nmetadata = { captured_at = 2026-09-02T12:00:00Z }",
    )
    config.write_text(text, encoding="utf-8")
    try:
        load_config(config)
    except ConfigError as exc:
        assert "JSON-serialisable" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("non-JSON metadata was accepted")


def test_adapter_envelope_metadata_must_match_measured_diameter(tmp_path: Path) -> None:
    config = _job(tmp_path)
    text = config.read_text(encoding="utf-8").replace(
        "face_diameter_mm = 52.0",
        """face_diameter_mm = 52.0
measured_diameter_mm = 85.0

[metadata]
adapter_nominal_ring_mm = 77.0
adapter_radial_wall_mm = 2.5""",
    )
    config.write_text(text, encoding="utf-8")
    try:
        load_config(config)
    except ConfigError as exc:
        assert "nominal_ring" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("a mismatched adapter envelope was accepted")


def test_palette_names_are_casefold_unique_for_output_files(tmp_path: Path) -> None:
    config = _job(tmp_path)
    config.write_text(
        config.read_text(encoding="utf-8").replace("[palette.ivory]", "[palette.BLACK]"),
        encoding="utf-8",
    )
    try:
        load_config(config)
    except ConfigError as exc:
        assert "ignoring case" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("case-colliding palette names were accepted")


def test_validate_rejects_role_mask_semantic_mismatch(tmp_path: Path) -> None:
    config = load_config(_job(tmp_path))
    process(config)
    output = config.output_dir
    # Keep the file valid and binary, but deliberately claim that the base
    # role covers the relief pixels.  A simple existence check would miss
    # this; the semantic union check must fail.
    relief = Image.open(output / "relief.png").convert("L")
    relief.save(output / "base.png")
    report = validate_job(config)
    assert report["status"] == "failed"
    assert report["checks"]["role_masks"]["status"] == "failed"
    assert report["checks"]["role_masks"]["semantics"]["base_matches_palette_union"] is False


def test_validate_rejects_empty_required_palette_output(tmp_path: Path) -> None:
    config = load_config(_job(tmp_path))
    process(config)
    required_mask = config.output_dir / "masks" / "black.png"
    Image.new("L", (config.grid_size, config.grid_size), 0).save(required_mask)
    report = validate_job(config)
    assert report["status"] == "failed"
    required = report["checks"]["required_palette_outputs"]
    assert required["status"] == "failed"
    assert required["colors"]["black"]["required"] is True
    assert required["colors"]["black"]["nonempty"] is False


def test_validate_fails_closed_on_incomplete_process_marker(tmp_path: Path) -> None:
    config = load_config(_job(tmp_path))
    process(config)
    (config.output_dir / ".process-in-progress").write_text("interrupted\n", encoding="utf-8")
    report = validate_job(config)
    assert report["status"] == "failed"
    assert report["checks"]["process_transaction"]["status"] == "failed"


def test_validate_rejects_nonobject_json_reports(tmp_path: Path) -> None:
    config = load_config(_job(tmp_path))
    process(config)
    (config.output_dir / "process-report.json").write_text("[]\n", encoding="utf-8")
    report = validate_job(config)
    assert report["status"] == "failed"
    assert report["checks"]["process_report"]["status"] == "failed"
    assert "root must be an object" in report["checks"]["process_report"]["reason"]


def test_validate_rejects_malformed_nested_report_objects(tmp_path: Path) -> None:
    config = load_config(_job(tmp_path))
    process(config)
    report_path = config.output_dir / "process-report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["source"] = []
    report_path.write_text(json.dumps(report), encoding="utf-8")
    result = validate_job(config)
    assert result["status"] == "failed"
    assert result["checks"]["source_lock"]["status"] == "failed"


def test_clone_relative_source_path_is_preserved(tmp_path: Path) -> None:
    art_dir = tmp_path / "art"
    job_dir = tmp_path / "jobs" / "lens"
    art_dir.mkdir(parents=True)
    job_dir.mkdir(parents=True)
    image = Image.new("RGB", (64, 64), (17, 18, 17))
    ImageDraw.Draw(image).ellipse((4, 4, 59, 59), fill=(242, 231, 211))
    source = art_dir / "master.png"
    image.save(source)
    config = job_dir / "job.toml"
    config.write_text(
        _job(tmp_path).read_text(encoding="utf-8")
        .replace('source_art = "master.png"', 'source_art = "../../art/master.png"')
        .replace('output_dir = "build"', 'output_dir = "build-relative"'),
        encoding="utf-8",
    )
    loaded = load_config(config)
    assert loaded.portable_public()["source_art"] == "../../art/master.png"
    report = process(loaded)
    assert report["status"] == "passed"
    lock = json.loads((loaded.output_dir / "source-lock.json").read_text(encoding="utf-8"))
    assert lock["path"] == "../../art/master.png"
