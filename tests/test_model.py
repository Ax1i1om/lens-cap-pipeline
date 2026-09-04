from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw
from test_pipeline import _job

from lens_cap_pipeline.config import ConfigError, friction_rib_profile_defaults, load_config
from lens_cap_pipeline.external import write_bambu_handoff
from lens_cap_pipeline.model import ModelError, _mechanical_values, generate_model
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
    # The model is intentionally brand-agnostic. It must import the current
    # job's SVG masks and never retype a maker-specific string or glyph; this
    # remains valid for any manifest brand, not just historical fixtures.
    assert "text(" not in scad
    assert "import(file=" in scad
    geometry = json.loads(model.geometry_report_path.read_text(encoding="utf-8"))
    assert geometry["mechanical"]["cavity_diameter_mm"] == 97.4
    assert geometry["shared_svg_canvas"] is True
    validation = validate_job(config)
    assert validation["status"] == "passed"
    assert validation["checks"]["geometry"]["status"] == "passed"
    assert validation["checks"]["geometry"]["mechanical_parameter_checks"]["friction_rib_angle_deg"] is True


def test_nozzle_change_does_not_change_generated_scad(tmp_path: Path) -> None:
    original = _job(tmp_path).read_text(encoding="utf-8").replace(
        "face_diameter_mm = 52.0",
        "face_diameter_mm = 52.0\nmeasured_diameter_mm = 52.0",
    )
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
    fine_model = generate_model(fine, process(fine))
    coarse_model = generate_model(coarse, process(coarse))

    assert fine_model.scad_path.read_bytes() == coarse_model.scad_path.read_bytes()


def test_native_geometry_accepts_declared_narrow_rib_without_nozzle_logic(
    tmp_path: Path,
) -> None:
    config_path = _job(tmp_path)
    text = config_path.read_text(encoding="utf-8").replace(
        "face_diameter_mm = 52.0",
        "face_diameter_mm = 52.0\nmeasured_diameter_mm = 52.0",
    )
    text += '''
[fit]
foam_liner_status = "none"
friction_ribs_enabled = true
friction_rib_width_mm = 0.2
'''
    config_path.write_text(text, encoding="utf-8")
    config = load_config(config_path)

    model = generate_model(config, process(config))

    assert model.report["mechanical"]["friction_rib_tip_width_mm"] < 0.2
    assert "friction_rib_width_advisory" not in model.report["mechanical"]
    assert "nozzle_mm" not in model.report["mechanical"]


def test_model_accepts_integrity_pass_and_rejects_tampered_checks(tmp_path: Path) -> None:
    config_path = _job(tmp_path)
    source = Image.new("RGB", (64, 64), (17, 18, 17))
    source_draw = ImageDraw.Draw(source)
    source_draw.ellipse((4, 4, 59, 59), fill=(242, 231, 211))
    source_draw.rectangle((20, 20, 43, 43), fill=(17, 18, 17))
    source_draw.line((24, 31, 39, 31), fill=(242, 231, 211), width=1)
    source.save(tmp_path / "master.png")
    text = config_path.read_text(encoding="utf-8")
    text = text.replace(
        "face_diameter_mm = 52.0",
        "face_diameter_mm = 6.4\nmeasured_diameter_mm = 6.4",
    )
    text = text.replace("grid_size = 128", "grid_size = 64")
    text = text.replace('name = "median"', 'name = "none"')
    text = text.replace("[cleanup]\nenabled = true", "[cleanup]\nenabled = false")
    text += '''
[fit]
foam_liner_status = "none"
friction_ribs_enabled = true
friction_ribs_explicit = false
'''
    config_path.write_text(text, encoding="utf-8")
    config = load_config(config_path)
    process_report = process(config)
    assert process_report["status"] == "passed"
    assert "minimum_relief_feature_audit" not in process_report

    tampered = copy.deepcopy(process_report)
    tampered["checks"]["role_partition"] = False
    with pytest.raises(ModelError, match="integrity gate"):
        generate_model(config, tampered)

    model = generate_model(config, process_report)
    assert model.report["status"] == "passed"
    assert model.report["production_status"] == "passed"
    validation = validate_job(config)
    assert validation["status"] == "passed"
    assert validation["checks"]["process_report"]["status"] == "passed"
    assert validation["checks"]["geometry"]["status"] == "passed"
    assert validation["checks"]["model_manifest"]["status"] == "passed"


def test_model_includes_default_inner_friction_ribs_in_body_and_coupon(tmp_path: Path) -> None:
    config_path = _job(tmp_path)
    text = config_path.read_text(encoding="utf-8").replace(
        "face_diameter_mm = 52.0",
        "face_diameter_mm = 95.0\nmeasured_diameter_mm = 95.0",
    )
    text += '\n[fit]\nfoam_liner_status = "foam"\nliner_thickness_mm = 1.5\n'
    config_path.write_text(text, encoding="utf-8")
    config = load_config(config_path)
    result = generate_model(config, process(config))
    scad = result.scad_path.read_text(encoding="utf-8")
    assert "friction_ribs_enabled = true;" in scad
    assert "friction_rib_count = 12;" in scad
    assert "module friction_rib_set" in scad
    assert "friction_rib_angle_deg" in scad
    assert "linear_extrude(height=rib_height" in scad
    # Both the full body and the short fit coupon call the same retention
    # profile; the coupon intentionally clips the axial span to its height.
    assert scad.count("friction_rib_set(") >= 2
    mechanical = result.report["mechanical"]
    assert mechanical["friction_ribs_enabled"] is True
    assert mechanical["friction_rib_tip_diameter_mm"] == 97.2
    assert mechanical["friction_rib_bare_interference_mm"] is None
    assert mechanical["foam_local_compression_fraction"] == (0.20 + 0.10 / 1.5)


def test_bare_wall_omits_foam_compression_assumption(tmp_path: Path) -> None:
    """A hand-written no-foam job must not inherit the foam default."""

    config_path = _job(tmp_path)
    text = config_path.read_text(encoding="utf-8").replace(
        "face_diameter_mm = 52.0",
        "face_diameter_mm = 95.0\nmeasured_diameter_mm = 95.0",
    )
    text += '\n[fit]\nfoam_liner_status = "none"\n'
    config_path.write_text(text, encoding="utf-8")
    config = load_config(config_path)
    assert config.fit.compression_fraction == 0.0
    assert config.fit.compression_is_assumption is False
    mechanical = _mechanical_values(config)
    assert mechanical["compression_fraction"] == 0.0
    assert mechanical["compression_is_assumption"] is False
    assert mechanical["foam_local_compression_fraction"] is None


def test_model_adds_parameterised_front_outer_chamfer_without_moving_artwork(
    tmp_path: Path,
) -> None:
    config_path = _job(tmp_path)
    text = config_path.read_text(encoding="utf-8").replace(
        "face_diameter_mm = 52.0",
        "face_diameter_mm = 95.0\nmeasured_diameter_mm = 95.0",
    )
    text += '''
[fit]
foam_liner_status = "none"
front_outer_chamfer_mm = 0.30
'''
    config_path.write_text(text, encoding="utf-8")
    config = load_config(config_path)

    result = generate_model(config, process(config))
    mechanical = result.report["mechanical"]
    scad = result.scad_path.read_text(encoding="utf-8")

    assert mechanical["front_outer_chamfer_mm"] == pytest.approx(0.30)
    assert mechanical["front_outer_top_diameter_mm"] == pytest.approx(99.6)
    assert "front_outer_chamfer_mm = 0.3;" in scad
    assert "module cap_outer_envelope()" in scad
    assert "d2=front_outer_top_diameter" in scad
    # The artwork remains on the original shared canvas and Z datum.
    assert "panel_diameter = face_target_mm;" in scad
    assert "translate([-panel_diameter / 2, -panel_diameter / 2, total_height])" in scad


@pytest.mark.parametrize("value", [-0.1, 2.0, 2.4])
def test_front_outer_chamfer_must_fit_within_wall_and_floor(
    tmp_path: Path, value: float
) -> None:
    config_path = _job(tmp_path)
    text = config_path.read_text(encoding="utf-8").replace(
        "face_diameter_mm = 52.0",
        "face_diameter_mm = 95.0\nmeasured_diameter_mm = 95.0",
    )
    text += f'''
[fit]
foam_liner_status = "none"
front_outer_chamfer_mm = {value}
'''
    config_path.write_text(text, encoding="utf-8")

    with pytest.raises(ConfigError, match="front_outer_chamfer_mm"):
        load_config(config_path)


def test_front_outer_chamfer_must_leave_the_artwork_face_supported(
    tmp_path: Path,
) -> None:
    config_path = _job(tmp_path)
    text = config_path.read_text(encoding="utf-8").replace(
        "face_diameter_mm = 52.0",
        "face_diameter_mm = 99.8\nmeasured_diameter_mm = 95.0",
    )
    text += '''
[fit]
foam_liner_status = "none"
front_outer_chamfer_mm = 0.30
'''
    config_path.write_text(text, encoding="utf-8")

    with pytest.raises(ConfigError, match="front_outer_chamfer_mm"):
        load_config(config_path)


def test_wide_tapered_profile_matches_reference_proportions(tmp_path: Path) -> None:
    """The opt-in wide profile resolves six broad, 8-degree wedges."""
    config_path = _job(tmp_path)
    text = config_path.read_text(encoding="utf-8").replace(
        "face_diameter_mm = 52.0",
        "face_diameter_mm = 95.0\nmeasured_diameter_mm = 95.0",
    )
    text += """
[fit]
foam_liner_status = "foam"
liner_thickness_mm = 1.5
friction_rib_profile = "wide_tapered"
"""
    config_path.write_text(text, encoding="utf-8")
    config = load_config(config_path)
    # The width is derived from the compressed 97.4 mm cavity, not the
    # nominal lens diameter, so the generated angle remains eight degrees.
    assert config.fit.friction_rib_profile == "wide_tapered"
    assert config.fit.friction_rib_count == 6
    assert config.fit.friction_rib_width_mm == pytest.approx(6.7998027658)
    assert config.fit.friction_rib_protrusion_mm == pytest.approx(0.30)
    assert config.fit.friction_rib_height_mm == pytest.approx(12.5)
    result = generate_model(config, process(config))
    mechanical = result.report["mechanical"]
    assert mechanical["friction_rib_profile"] == "wide_tapered"
    assert mechanical["friction_rib_angle_deg"] == pytest.approx(8.0)
    assert mechanical["friction_rib_tip_angle_deg"] == pytest.approx(4.4)
    assert mechanical["foam_local_compression_fraction"] == pytest.approx(0.40)
    scad = result.scad_path.read_text(encoding="utf-8")
    assert 'friction_rib_profile = "wide_tapered";' in scad
    assert "friction_rib_count = 6;" in scad


def test_profile_defaults_do_not_override_explicit_rib_fields(tmp_path: Path) -> None:
    config_path = _job(tmp_path)
    text = config_path.read_text(encoding="utf-8").replace(
        "face_diameter_mm = 52.0",
        "face_diameter_mm = 95.0\nmeasured_diameter_mm = 95.0",
    )
    text += """
[fit]
foam_liner_status = "none"
friction_rib_profile = "wide_tapered"
friction_rib_protrusion_mm = 0.55
friction_rib_height_mm = 12.5
"""
    config_path.write_text(text, encoding="utf-8")
    config = load_config(config_path)
    assert config.fit.friction_rib_profile == "wide_tapered"
    assert config.fit.friction_rib_protrusion_mm == pytest.approx(0.55)
    assert config.fit.friction_rib_height_mm == pytest.approx(12.5)
    # Omitted count/width still come from the selected profile.
    assert config.fit.friction_rib_count == 6
    assert config.fit.friction_rib_width_mm == pytest.approx(6.6601764256)


def test_unknown_friction_rib_profile_is_rejected(tmp_path: Path) -> None:
    config_path = _job(tmp_path)
    config_path.write_text(
        config_path.read_text(encoding="utf-8")
        + '\n[fit]\nfriction_rib_profile = "maker_specific"\n',
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="friction_rib_profile"):
        load_config(config_path)


def test_wide_profile_uses_face_as_provisional_scale_without_measurement(tmp_path: Path) -> None:
    config_path = _job(tmp_path)
    config_path.write_text(
        config_path.read_text(encoding="utf-8")
        + '\n[fit]\nfriction_rib_profile = "wide_tapered"\n',
        encoding="utf-8",
    )
    config = load_config(config_path)
    # Process-only jobs have no measured mating diameter yet.  The parser can
    # still emit a deterministic starter width from the face diameter; the
    # fitted model gate requires a real measurement before using it.
    assert config.measured_diameter_mm is None
    assert config.fit.friction_rib_width_mm == pytest.approx(
        3.6582101122  # pi * (52 + .4) * 8 / 360
    )


def test_profile_defaults_reject_derived_width_overflow() -> None:
    # A finite but extreme diameter can overflow the 8-degree arc-length
    # calculation.  The public helper must fail before a caller serialises
    # ``inf`` into a TOML or OpenSCAD file.
    with pytest.raises(ValueError, match="defaults must be finite"):
        friction_rib_profile_defaults("wide_tapered", 1e308, 14.0)


def test_model_can_disable_inner_friction_ribs_without_changing_artwork(tmp_path: Path) -> None:
    config_path = _job(tmp_path)
    text = config_path.read_text(encoding="utf-8").replace(
        "face_diameter_mm = 52.0",
        "face_diameter_mm = 95.0\nmeasured_diameter_mm = 95.0",
    )
    text += """
[fit]
foam_liner_status = "foam"
liner_thickness_mm = 1.5
friction_ribs_enabled = false
friction_ribs_explicit = true
friction_rib_count = 1
friction_rib_width_mm = 0.01
friction_rib_height_mm = 999
friction_rib_start_mm = -20
"""
    config_path.write_text(text, encoding="utf-8")
    config = load_config(config_path)
    assert config.fit.friction_ribs_enabled is False
    # Disabled ribs do not impose geometry-specific constraints on their
    # unused dimensions; this permits a clean smooth-wall override.
    result = generate_model(config, process(config))
    scad = result.scad_path.read_text(encoding="utf-8")
    assert "friction_ribs_enabled = false;" in scad
    assert result.report["mechanical"]["friction_ribs_enabled"] is False
    assert result.report["mechanical"]["foam_local_compression_fraction"] == 0.20
    assert result.report["mechanical"]["friction_rib_tip_diameter_mm"] is None
    assert result.report["mechanical"]["friction_rib_bare_interference_mm"] is None
    assert validate_job(config)["checks"]["geometry"]["mechanical_parameters_match"] is True


def test_model_reports_signed_bare_rib_interference(tmp_path: Path) -> None:
    config_path = _job(tmp_path)
    text = config_path.read_text(encoding="utf-8").replace(
        "face_diameter_mm = 52.0",
        "face_diameter_mm = 95.0\nmeasured_diameter_mm = 95.0",
    )
    text += '\n[fit]\nfoam_liner_status = "none"\n'
    config_path.write_text(text, encoding="utf-8")
    result = generate_model(load_config(config_path), process(load_config(config_path)))
    # Default 0.40 mm diametral bare clearance minus 0.20 mm rib reduction
    # leaves -0.20 mm nominal interference (i.e. 0.20 mm clearance).
    assert result.report["mechanical"]["friction_rib_bare_interference_mm"] == pytest.approx(-0.20)


def test_direct_mechanical_api_rejects_nonfinite_measurement() -> None:
    config = SimpleNamespace(measured_diameter_mm=float("nan"), raw={}, fit=None)
    try:
        _mechanical_values(config)
    except ModelError as exc:
        assert "finite" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("non-finite direct API measurement was accepted")


def test_direct_mechanical_api_rejects_boolean_as_a_measurement() -> None:
    config = SimpleNamespace(measured_diameter_mm=True, raw={}, fit=None)
    try:
        _mechanical_values(config)
    except ModelError as exc:
        assert "finite number" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("boolean direct API measurement was accepted")


def test_direct_mechanical_api_rejects_nonfinite_rib_parameter() -> None:
    config = SimpleNamespace(
        measured_diameter_mm=95.0,
        nozzle_mm=0.2,
        raw={},
        fit=SimpleNamespace(friction_rib_width_mm=float("nan")),
    )
    try:
        _mechanical_values(config)
    except ModelError as exc:
        assert "friction_rib_width_mm" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("non-finite direct API rib parameter was accepted")


def test_direct_mechanical_api_rejects_overflowing_derived_height() -> None:
    config = SimpleNamespace(
        measured_diameter_mm=95.0,
        nozzle_mm=0.2,
        raw={},
        fit=SimpleNamespace(bottom_thickness_mm=1e308, side_height_mm=1e308),
    )
    try:
        _mechanical_values(config)
    except ModelError as exc:
        assert "derived cavity/outer diameter" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("overflowing derived height was accepted")


def test_friction_rib_cannot_consume_compressed_foam_gap(tmp_path: Path) -> None:
    config_path = _job(tmp_path)
    text = config_path.read_text(encoding="utf-8").replace(
        "face_diameter_mm = 52.0",
        "face_diameter_mm = 95.0\nmeasured_diameter_mm = 95.0",
    )
    text += """
[fit]
foam_liner_status = "foam"
liner_thickness_mm = 1.5
compression_fraction = 0.20
friction_rib_protrusion_mm = 1.20
"""
    config_path.write_text(text, encoding="utf-8")
    try:
        load_config(config_path)
    except ConfigError as exc:
        assert "compressed foam radial gap" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("rib intrusion consuming the foam gap was accepted")


def test_model_uses_arbitrary_palette_labels(tmp_path: Path) -> None:
    """No maker/coating name or colour label may be a model-stage default."""
    config_path = _job(tmp_path)
    text = config_path.read_text(encoding="utf-8")
    text = text.replace(
        "face_diameter_mm = 52.0",
        "face_diameter_mm = 95.0\nmeasured_diameter_mm = 95.0",
    )
    text = text.replace("[palette.red.detect]", "[palette.accent_mark.detect]")
    text = text.replace("[palette.red]", "[palette.accent_mark]")
    text += """
[fit]
foam_liner_status = "foam"
liner_thickness_mm = 1.5
"""
    config_path.write_text(text, encoding="utf-8")
    source = Image.open(tmp_path / "master.png").convert("RGB")
    ImageDraw.Draw(source).rectangle((10, 10, 18, 18), fill=(190, 31, 35))
    source.save(tmp_path / "master.png")
    config = load_config(config_path)
    process_report = process(config)
    model = generate_model(config, process_report)
    scad = model.scad_path.read_text(encoding="utf-8")

    assert "relief_2_accent_mark" in scad
    assert "relief_2_red" not in scad
    assert "text(" not in scad


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
