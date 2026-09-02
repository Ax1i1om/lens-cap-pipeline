from __future__ import annotations

from pathlib import Path

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
    # The generated TOML intentionally omits a duplicate face field when it is
    # derived from the measured mating diameter.
    assert "face_diameter_mm =" not in config_path.read_text(encoding="utf-8")


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
