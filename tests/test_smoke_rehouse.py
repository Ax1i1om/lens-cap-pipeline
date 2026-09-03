"""Fast unit checks for the clean-room integration rehearsal."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import scripts.smoke_rehouse as smoke  # noqa: E402

FIXTURE = ROOT / "examples" / "fixtures" / "helios-44-2-rehouse"
MAMIYA_FIXTURE = ROOT / "examples" / "fixtures" / "mamiya-sekor-c-80-f1-9-rehouse"
IMAGEGEN_V2_FIXTURE = ROOT / "examples" / "fixtures" / "helios-44-2-rehouse-imagegen-v2"


def test_fixture_brief_and_prompt_are_self_contained() -> None:
    report = smoke._validate_brief(FIXTURE)
    assert report["status"] == "passed"
    assert report["display_text"][:2] == ["58", "F2"]
    assert report["focal_length_display"] == "58"
    assert report["artwork"] == "art/master.png"


def test_brief_accepts_optional_zoom_focal_length_display(tmp_path: Path) -> None:
    """A zoom may override only the human-facing focal token, not the numeric anchor."""

    fixture = tmp_path / "zoom-fixture"
    smoke._copy_fixture(FIXTURE, fixture)
    brief_path = fixture / "design-brief.json"
    brief = json.loads(brief_path.read_text(encoding="utf-8"))
    brief["lens_identity"]["focal_length_mm"] = 28
    brief["lens_identity"]["focal_length_display"] = "28–70mm"
    brief["display_text"][0] = "28–70mm"
    brief["allowed_text"][0] = "28–70mm"
    prompt_path = fixture / "prompt.txt"
    prompt_path.write_text(prompt_path.read_text(encoding="utf-8") + "\n28–70mm\n", encoding="utf-8")
    brief["generation"]["prompt_sha256"] = hashlib.sha256(prompt_path.read_bytes()).hexdigest()
    brief_path.write_text(json.dumps(brief, ensure_ascii=False, indent=2), encoding="utf-8")

    report = smoke._validate_brief(fixture)
    assert report["status"] == "passed"
    assert report["focal_length_mm"] == 28.0
    assert report["focal_length_display"] == "28–70mm"
    assert report["display_text"][0] == "28–70mm"


def test_brief_requires_approved_sourced_handoff(tmp_path: Path) -> None:
    fixture = tmp_path / "unapproved-fixture"
    smoke._copy_fixture(FIXTURE, fixture)
    brief_path = fixture / "design-brief.json"
    brief = json.loads(brief_path.read_text(encoding="utf-8"))
    brief["generation"]["approved"] = False
    brief_path.write_text(json.dumps(brief, ensure_ascii=False), encoding="utf-8")
    try:
        smoke._validate_brief(fixture)
    except smoke.SmokeError as exc:
        assert "approved" in str(exc)
    else:  # pragma: no cover - the assertion above is the intended branch
        raise AssertionError("an unapproved handoff must not pass the smoke gate")


def test_brief_requires_a_culture_or_rehouse_anchor(tmp_path: Path) -> None:
    fixture = tmp_path / "spec-only-fixture"
    smoke._copy_fixture(FIXTURE, fixture)
    brief_path = fixture / "design-brief.json"
    brief = json.loads(brief_path.read_text(encoding="utf-8"))
    brief["anchors"] = [brief["anchors"][0]]
    brief_path.write_text(json.dumps(brief, ensure_ascii=False), encoding="utf-8")
    try:
        smoke._validate_brief(fixture)
    except smoke.SmokeError as exc:
        assert "culture" in str(exc) or "rehouse" in str(exc)
    else:  # pragma: no cover - the assertion above is the intended branch
        raise AssertionError("a specification-only brief must not pass the smoke gate")


def test_zoom_display_must_match_numeric_anchor_and_increase(tmp_path: Path) -> None:
    fixture = tmp_path / "bad-zoom-fixture"
    smoke._copy_fixture(FIXTURE, fixture)
    brief_path = fixture / "design-brief.json"
    brief = json.loads(brief_path.read_text(encoding="utf-8"))
    brief["lens_identity"]["focal_length_display"] = "50–40mm"
    brief["display_text"][0] = "50–40mm"
    brief["allowed_text"][0] = "50–40mm"
    brief_path.write_text(json.dumps(brief, ensure_ascii=False), encoding="utf-8")
    try:
        smoke._validate_brief(fixture)
    except smoke.SmokeError as exc:
        assert "zoom range" in str(exc) or "focal_length_display" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("a descending zoom range must not pass the smoke gate")

    brief["lens_identity"]["focal_length_display"] = "59–70mm"
    brief["display_text"][0] = "59–70mm"
    brief["allowed_text"][0] = "59–70mm"
    brief_path.write_text(json.dumps(brief, ensure_ascii=False), encoding="utf-8")
    try:
        smoke._validate_brief(fixture)
    except smoke.SmokeError as exc:
        assert "start at focal_length_mm" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("the zoom start must match the numeric focal anchor")


def test_runner_accepts_a_second_rehouse_brand_without_helios_constants(tmp_path: Path) -> None:
    report = smoke._validate_brief(MAMIYA_FIXTURE)
    assert report["status"] == "passed"
    assert report["display_text"][:2] == ["80", "F1.9"]
    jobs = smoke._copy_fixture(MAMIYA_FIXTURE, tmp_path / "fixture")
    assert [path.as_posix() for path in jobs] == [
        "jobs/77mm/job.toml",
        "jobs/85mm/job.toml",
        "jobs/95mm/job.toml",
    ]


def test_fresh_imagegen_rehouse_fixture_is_self_contained(tmp_path: Path) -> None:
    report = smoke._validate_brief(IMAGEGEN_V2_FIXTURE)
    assert report["status"] == "passed"
    assert report["display_text"][:2] == ["58", "F2"]
    jobs = smoke._copy_fixture(IMAGEGEN_V2_FIXTURE, tmp_path / "fixture")
    assert [path.as_posix() for path in jobs] == [
        "jobs/77mm/job.toml",
        "jobs/82mm/job.toml",
        "jobs/95mm/job.toml",
    ]
    assert not (tmp_path / "fixture/artifacts").exists()


def test_fresh_imagegen_fixture_retains_verified_3mf_snapshots() -> None:
    artifacts = IMAGEGEN_V2_FIXTURE / "artifacts"
    expected = [
        "helios-44-2-rehouse-imagegen-v2-77mm-native.3mf",
        "helios-44-2-rehouse-imagegen-v2-82mm-native.3mf",
        "helios-44-2-rehouse-imagegen-v2-95mm-native.3mf",
        "helios-44-2-rehouse-imagegen-v2-95mm-bambu-slice.3mf",
        "helios-44-2-rehouse-imagegen-v2-77mm-assembly-standard.3mf",
        "helios-44-2-rehouse-imagegen-v2-82mm-assembly-standard.3mf",
        "helios-44-2-rehouse-imagegen-v2-95mm-assembly-standard.3mf",
    ]
    for name in expected:
        package = artifacts / name
        assert package.is_file() and package.stat().st_size > 1024
        manifest = artifacts / f"{name}.manifest.json"
        assert manifest.is_file()


def test_retained_imagegen_3mf_sidecars_match_packages() -> None:
    """Do not let a stale/renamed binary masquerade as a retained result."""

    artifacts = IMAGEGEN_V2_FIXTURE / "artifacts"
    for package in sorted(artifacts.glob("*.3mf")):
        sidecar = package.with_name(package.name + ".manifest.json")
        manifest = json.loads(sidecar.read_text(encoding="utf-8"))
        digest = hashlib.sha256(package.read_bytes()).hexdigest()
        verification = manifest["verification"]
        assert verification["sha256"] == digest
        if "bambu-slice" in package.name:
            # Bambu adds a settings/config object around the single mesh.
            assert verification["model"]["parts"] >= 1
            assert verification["model"]["objects"] >= 1
            assert verification["has_embedded_gcode"] is True
            assert verification["gcode_bytes"] > 0
        else:
            assert verification["model"]["parts"] == 1
            assert verification["model"]["objects"] == 1


def test_copy_fixture_excludes_previous_outputs(tmp_path: Path) -> None:
    destination = tmp_path / "fixture"
    smoke._copy_fixture(FIXTURE, destination)
    assert (destination / "art/master.png").is_file()
    assert (destination / "prompt.txt").is_file()
    assert (destination / "jobs/95mm/job.toml").is_file()
    assert not (destination / "jobs/95mm/out").exists()
    assert not (destination / "artifacts").exists()


def test_json_parser_handles_diagnostic_prefix() -> None:
    value = smoke._json_from_output("warning before report\n" + json.dumps({"status": "passed"}))
    assert value == {"status": "passed"}
