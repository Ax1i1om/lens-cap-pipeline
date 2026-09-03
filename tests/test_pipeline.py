from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from lens_cap_pipeline.config import ConfigError, PaletteSpec, load_config
from lens_cap_pipeline.process import ProcessError, _minimum_relief_feature_audit, process
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


def test_minimum_feature_gate_rejects_hairline_below_nozzle() -> None:
    palettes = (
        PaletteSpec("black", 0, (0, 0, 0), "base", 0.0, True),
        PaletteSpec("gray", 1, (116, 116, 113), "relief", 0.4, True),
    )
    inside = np.ones((64, 64), dtype=bool)
    labels = np.zeros((64, 64), dtype=np.int32)
    labels[8:24, 8:24] = 1
    labels[30:55, 40] = 1
    failed = _minimum_relief_feature_audit(
        labels,
        inside,
        palettes,
        face_diameter_mm=9.5,
        grid_size=64,
        nozzle_mm=0.2,
        allowed_area_px=8,
        allowed_dimension_px=3,
    )
    assert 1.0 < failed["minimum_feature_px"] < 2.0
    assert failed["support_kernel_px"] == 2
    assert failed["status"] == "failed"
    assert failed["colors"]["gray"]["largest_violation_dimension_px"] == 25

    labels[30:55, 41] = 1
    passed = _minimum_relief_feature_audit(
        labels,
        inside,
        palettes,
        face_diameter_mm=9.5,
        grid_size=64,
        nozzle_mm=0.2,
        allowed_area_px=8,
        allowed_dimension_px=3,
    )
    assert passed["status"] == "passed"


def test_minimum_feature_gate_rejects_long_sub_nozzle_negative_channel() -> None:
    palettes = (
        PaletteSpec("black", 0, (0, 0, 0), "base", 0.0, True),
        PaletteSpec("gray", 1, (116, 116, 113), "relief", 0.4, True),
    )
    inside = np.ones((100, 100), dtype=bool)
    labels = np.ones((100, 100), dtype=np.int32)
    labels[10:90, 50] = 0

    report = _minimum_relief_feature_audit(
        labels,
        inside,
        palettes,
        face_diameter_mm=10.0,
        grid_size=100,
        nozzle_mm=0.2,
        allowed_area_px=8,
        allowed_dimension_px=3,
    )

    assert report["status"] == "failed"
    assert report["colors"]["black"]["role"] == "base"
    assert report["colors"]["black"]["largest_violation_dimension_px"] == 80


def test_minimum_feature_gate_rejects_many_sub_nozzle_negative_dots() -> None:
    palettes = (
        PaletteSpec("black", 0, (0, 0, 0), "base", 0.0, True),
        PaletteSpec("gray", 1, (116, 116, 113), "relief", 0.4, True),
    )
    inside = np.ones((100, 100), dtype=bool)
    labels = np.ones((100, 100), dtype=np.int32)
    for y in range(10, 95, 5):
        for x in range(10, 95, 5):
            labels[y, x] = 0

    report = _minimum_relief_feature_audit(
        labels,
        inside,
        palettes,
        face_diameter_mm=10.0,
        grid_size=100,
        nozzle_mm=0.2,
        allowed_area_px=8,
        allowed_dimension_px=3,
    )

    assert report["status"] == "failed"
    assert report["colors"]["black"]["unsupported_components"] == 289
    assert report["colors"]["black"]["aggregate_cleanup_budget_exceeded"] is True


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
