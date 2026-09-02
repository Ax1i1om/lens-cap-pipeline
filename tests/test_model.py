from __future__ import annotations

import json
from pathlib import Path

from test_pipeline import _job

from lens_cap_pipeline.config import load_config
from lens_cap_pipeline.external import write_bambu_handoff
from lens_cap_pipeline.model import ModelError, generate_model
from lens_cap_pipeline.process import process
from lens_cap_pipeline.validate import validate_job


def test_model_consumes_process_without_retyping_art(tmp_path: Path) -> None:
    config_path = _job(tmp_path)
    text = config_path.read_text(encoding="utf-8")
    text = text.replace("face_diameter_mm = 52.0", "face_diameter_mm = 95.0\nmeasured_diameter_mm = 95.0")
    text = text.replace("safe_border_mm = 0.3", "safe_border_mm = 0.4")
    text += """
[fit]
foam_liner_status = "foam"
liner_thickness_mm = 1.5
compression_fraction = 0.20
wall_thickness_mm = 2.4
bottom_thickness_mm = 2.0
side_height_mm = 14.0
"""
    config_path.write_text(text, encoding="utf-8")
    config = load_config(config_path)
    process_report = process(config)
    model = generate_model(config, process_report)
    assert model.scad_path.is_file()
    scad = model.scad_path.read_text(encoding="utf-8")
    assert "CARL ZEISS" not in scad
    assert "SONNAR" not in scad
    assert "import(file=" in scad
    geometry = json.loads(model.geometry_report_path.read_text(encoding="utf-8"))
    assert geometry["mechanical"]["cavity_diameter_mm"] == 97.4
    assert geometry["shared_svg_canvas"] is True
    validation = validate_job(config)
    assert validation["status"] == "passed"
    assert validation["checks"]["geometry"]["status"] == "passed"


def test_model_rejects_a_tampered_process_svg(tmp_path: Path) -> None:
    config_path = _job(tmp_path)
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            "face_diameter_mm = 52.0",
            "face_diameter_mm = 95.0\nmeasured_diameter_mm = 95.0",
        )
        + '\n[fit]\nfoam_liner_status = "foam"\nliner_thickness_mm = 1.5\n',
        encoding="utf-8",
    )
    config = load_config(config_path)
    process_report = process(config)
    svg_path = config.output_dir / "vector" / "ivory.svg"
    svg_path.write_text(svg_path.read_text(encoding="utf-8") + "<!-- tampered -->\n", encoding="utf-8")
    try:
        generate_model(config, process_report)
    except ModelError as exc:
        assert "differs from the passed process report" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("tampered SVG was accepted by the model stage")


def test_model_rejects_a_changed_source_after_process(tmp_path: Path) -> None:
    """The public model API must not bypass the source-lock gate."""
    config_path = _job(tmp_path)
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            "face_diameter_mm = 52.0",
            "face_diameter_mm = 95.0\nmeasured_diameter_mm = 95.0",
        )
        + '\n[fit]\nfoam_liner_status = "foam"\nliner_thickness_mm = 1.5\n',
        encoding="utf-8",
    )
    config = load_config(config_path)
    process_report = process(config)
    # Appending bytes changes the current source hash without changing the
    # in-memory report returned by the completed process stage.
    with (config.source_path).open("ab") as handle:
        handle.write(b"source replacement sentinel")
    try:
        generate_model(config, process_report, force=True)
    except ModelError as exc:
        assert "differs from the passed process report" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("model API accepted a substituted source artwork")


def test_model_requires_svg_hashes_from_process_report(tmp_path: Path) -> None:
    """A passed-looking report without per-colour provenance is incomplete."""
    config_path = _job(tmp_path)
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            "face_diameter_mm = 52.0",
            "face_diameter_mm = 95.0\nmeasured_diameter_mm = 95.0",
        )
        + '\n[fit]\nfoam_liner_status = "foam"\nliner_thickness_mm = 1.5\n',
        encoding="utf-8",
    )
    config = load_config(config_path)
    process_report = process(config)
    del process_report["mask_stats"]["ivory"]["svg_sha256"]
    try:
        generate_model(config, process_report, force=True)
    except ModelError as exc:
        assert "no SVG hash" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("model accepted a process report without SVG provenance")


def test_bambu_handoff_ignores_nonfile_stl_entry(tmp_path: Path) -> None:
    """A malformed export directory must not crash handoff hashing."""
    config_path = _job(tmp_path)
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            "face_diameter_mm = 52.0",
            "face_diameter_mm = 95.0\nmeasured_diameter_mm = 95.0",
        )
        + '\n[fit]\nfoam_liner_status = "foam"\nliner_thickness_mm = 1.5\n',
        encoding="utf-8",
    )
    config = load_config(config_path)
    process_report = process(config)
    model = generate_model(config, process_report)
    mesh_dir = model.model_dir / "mesh"
    mesh_dir.mkdir(parents=True, exist_ok=True)
    (mesh_dir / f"{config.job_slug}-assembly.stl").mkdir()

    handoff = write_bambu_handoff(config, model)
    assert handoff.report["status"] == "unverifiable"
    ignored = handoff.report["ignored_stl_inputs"]
    assert ignored and ignored[0]["reason"] == "STL input is not a regular file"
