from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from lens_cap_pipeline.cli import main
from lens_cap_pipeline.config import load_config


def test_init_creates_a_portable_config(tmp_path: Path) -> None:
    source = tmp_path / "master.png"
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    ImageDraw.Draw(image).ellipse((4, 4, 59, 59), fill=(17, 18, 17, 255))
    image.save(source)
    config = tmp_path / "job.toml"
    assert main(
        [
            "init",
            str(config),
            "--source",
            str(source),
            "--face-diameter",
            "52",
            "--job-slug",
            "demo",
        ]
    ) == 0
    assert config.is_file()
    assert 'job_slug = "demo"' in config.read_text(encoding="utf-8")
    assert main(["process", str(config)]) == 0
    assert main(["validate", str(config)]) == 0


def test_init_derives_face_from_measured_diameter(tmp_path: Path) -> None:
    source = tmp_path / "master.png"
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    ImageDraw.Draw(image).ellipse((4, 4, 59, 59), fill=(17, 18, 17, 255))
    image.save(source)
    config_path = tmp_path / "jobs" / "derived.toml"
    assert main(
        [
            "init",
            str(config_path),
            "--source",
            str(source),
            "--measured-diameter",
            "95",
            "--foam-thickness",
            "1.5",
        ]
    ) == 0
    config = load_config(config_path)
    assert config.measured_diameter_mm == 95.0
    assert config.face_diameter_mm == 95.0
    assert config.fit.foam_liner_status == "foam"
    assert config.fit.liner_thickness_mm == 1.5
    generated = config_path.read_text(encoding="utf-8")
    assert "friction_ribs_enabled = true" in generated
    assert "friction_rib_protrusion_mm = 0.1" in generated
    assert "friction_ribs_explicit = false" in generated
    # The generated TOML intentionally omits a duplicate face field when it is
    # derived from the measured mating diameter.
    assert "face_diameter_mm =" not in config_path.read_text(encoding="utf-8")


def test_init_defaults_to_inner_friction_ribs_and_records_default(tmp_path: Path) -> None:
    source = tmp_path / "master.png"
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    ImageDraw.Draw(image).ellipse((4, 4, 59, 59), fill=(17, 18, 17, 255))
    image.save(source)
    config_path = tmp_path / "jobs" / "ribs-default.toml"
    assert main(
        [
            "init",
            str(config_path),
            "--source",
            str(source),
            "--measured-diameter",
            "95",
        ]
    ) == 0
    config = load_config(config_path)
    assert config.fit.friction_ribs_enabled is True
    assert config.fit.friction_ribs_explicit is False
    assert config.fit.friction_rib_count == 12
    assert config.fit.friction_rib_protrusion_mm == 0.10
    assert config.fit.foam_liner_status == "none"
    assert config.fit.compression_fraction == 0.0
    assert config.fit.compression_is_assumption is False
    assert config.fit.retention_strategy == "bare_wall_plus_neutral_ribs"


def test_init_can_select_wide_tapered_profile(tmp_path: Path) -> None:
    source = tmp_path / "master.png"
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    ImageDraw.Draw(image).ellipse((4, 4, 59, 59), fill=(17, 18, 17, 255))
    image.save(source)
    config_path = tmp_path / "jobs" / "wide.toml"
    assert main(
        [
            "init",
            str(config_path),
            "--source",
            str(source),
            "--measured-diameter",
            "95",
            "--foam-thickness",
            "1.5",
            "--friction-rib-profile",
            "wide_tapered",
        ]
    ) == 0
    config = load_config(config_path)
    assert config.fit.friction_rib_profile == "wide_tapered"
    assert config.fit.friction_rib_count == 6
    assert config.fit.friction_rib_protrusion_mm == 0.30
    assert config.fit.friction_rib_width_mm == pytest.approx(6.7998027658)
    assert config.fit.friction_rib_height_mm == 12.5
    generated = config_path.read_text(encoding="utf-8")
    assert 'friction_rib_profile = "wide_tapered"' in generated


def test_init_can_select_profile_without_foam_option(tmp_path: Path) -> None:
    """Profile selection must work for a bare fitted cap as well as foam."""
    source = tmp_path / "master.png"
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    ImageDraw.Draw(image).ellipse((4, 4, 59, 59), fill=(17, 18, 17, 255))
    image.save(source)
    config_path = tmp_path / "jobs" / "wide-bare.toml"
    assert main(
        [
            "init",
            str(config_path),
            "--source",
            str(source),
            "--measured-diameter",
            "95",
            "--friction-rib-profile",
            "wide_tapered",
        ]
    ) == 0
    config = load_config(config_path)
    assert config.fit.friction_rib_profile == "wide_tapered"
    # Bare cavity = measured diameter + 0.40 mm clearance.
    assert config.fit.friction_rib_width_mm == pytest.approx(6.6601764256)
    assert config.fit.friction_rib_count == 6


def test_init_can_explicitly_disable_inner_friction_ribs(tmp_path: Path) -> None:
    source = tmp_path / "master.png"
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    ImageDraw.Draw(image).ellipse((4, 4, 59, 59), fill=(17, 18, 17, 255))
    image.save(source)
    config_path = tmp_path / "jobs" / "ribs-off.toml"
    assert main(
        [
            "init",
            str(config_path),
            "--source",
            str(source),
            "--measured-diameter",
            "95",
            "--no-friction-ribs",
        ]
    ) == 0
    config = load_config(config_path)
    assert config.fit.friction_ribs_enabled is False
    assert config.fit.friction_ribs_explicit is True
    assert "friction_ribs_enabled = false" in config_path.read_text(encoding="utf-8")


def test_init_creates_the_declared_output_directory(tmp_path: Path) -> None:
    source = tmp_path / "master.png"
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    ImageDraw.Draw(image).ellipse((4, 4, 59, 59), fill=(17, 18, 17, 255))
    image.save(source)
    config_path = tmp_path / "jobs" / "custom.toml"
    assert main(
        [
            "init",
            str(config_path),
            "--source",
            str(source),
            "--face-diameter",
            "52",
            "--output-dir",
            "artifacts",
        ]
    ) == 0
    assert (config_path.parent / "artifacts").is_dir()
    assert not (config_path.parent / "build").exists()


def test_init_keeps_missing_relative_source_relative_to_task_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty cwd must not leak into a nested task's starter config.

    ``init`` intentionally permits a starter artwork path that will be filled
    in after the command.  When that path is absent in both the cwd and task
    directory, its serialized spelling must still be resolved from the task
    config directory rather than from the caller's cwd.
    """

    monkeypatch.chdir(tmp_path)
    config_path = tmp_path / "jobs" / "empty" / "job.toml"
    assert main(
        [
            "init",
            str(config_path),
            "--source",
            "art/master.png",
            "--face-diameter",
            "52",
        ]
    ) == 0
    generated = config_path.read_text(encoding="utf-8")
    assert 'source_art = "art/master.png"' in generated
    assert (config_path.parent / "art").is_dir()
    assert not (tmp_path / "art").exists()


def test_init_creates_placeholder_for_a_custom_relative_source_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    config_path = tmp_path / "jobs" / "custom" / "job.toml"
    assert main(
        [
            "init",
            str(config_path),
            "--source",
            "reference/master.png",
            "--face-diameter",
            "52",
        ]
    ) == 0
    assert 'source_art = "reference/master.png"' in config_path.read_text(encoding="utf-8")
    assert (config_path.parent / "reference").is_dir()
    assert not (config_path.parent / "art").exists()


def test_init_rejects_an_empty_source_argument(tmp_path: Path) -> None:
    config_path = tmp_path / "job.toml"
    assert main(["init", str(config_path), "--source", "", "--face-diameter", "52"]) == 2
    assert not config_path.exists()


def test_init_rejects_foam_without_a_mating_measurement(tmp_path: Path) -> None:
    source = tmp_path / "master.png"
    image = Image.new("RGB", (64, 64), (17, 18, 17))
    image.save(source)
    config_path = tmp_path / "job.toml"
    assert main(
        [
            "init",
            str(config_path),
            "--source",
            str(source),
            "--face-diameter",
            "52",
            "--foam-thickness",
            "1.5",
        ]
    ) == 2
    assert not config_path.exists()


def test_model_refuses_to_reuse_an_incomplete_process_transaction(tmp_path: Path) -> None:
    source = tmp_path / "master.png"
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    ImageDraw.Draw(image).ellipse((4, 4, 59, 59), fill=(17, 18, 17, 255))
    image.save(source)
    config = tmp_path / "job.toml"
    assert main(
        [
            "init",
            str(config),
            "--source",
            str(source),
            "--measured-diameter",
            "95",
            "--foam-thickness",
            "1.5",
        ]
    ) == 0
    # Fill the required circle and leave the generated starter palette intact.
    text = config.read_text(encoding="utf-8")
    text = text.replace("# center_px = [627, 624] # required for opaque square art", "center_px = [32, 32]")
    text = text.replace("# radius_px = 619", "radius_px = 28")
    config.write_text(text, encoding="utf-8")
    assert main(["process", str(config)]) == 0
    (tmp_path / "build" / ".process-in-progress").write_text("interrupted\n", encoding="utf-8")
    assert main(["model", str(config)]) == 2
