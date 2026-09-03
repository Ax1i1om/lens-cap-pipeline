"""Fast unit checks for the clean-room integration rehearsal."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import scripts.build_3mf as bridge  # noqa: E402
import scripts.smoke_rehouse as smoke  # noqa: E402
from lens_cap_pipeline.brief import validate_design_brief  # noqa: E402

FIXTURE = ROOT / "examples" / "fixtures" / "helios-44-2-rehouse"
MAMIYA_FIXTURE = ROOT / "examples" / "fixtures" / "mamiya-sekor-c-80-f1-9-rehouse"
IMAGEGEN_V2_FIXTURE = ROOT / "examples" / "fixtures" / "helios-44-2-rehouse-imagegen-v2"
IMAGEGEN_V3_FIXTURE = ROOT / "examples" / "fixtures" / "helios-44-2-rehouse-imagegen-v3"


def test_explicit_bambu_profiles_are_portable_and_all_or_nothing(tmp_path: Path) -> None:
    profiles = {
        name: tmp_path / f"{name}.json"
        for name in ("machine", "process", "filament")
    }
    for path in profiles.values():
        path.write_text("{}", encoding="utf-8")

    assert smoke._explicit_bambu_profiles(
        profiles["machine"], profiles["process"], profiles["filament"]
    ) == {name: path.resolve() for name, path in profiles.items()}
    with pytest.raises(smoke.SmokeError, match="all-or-nothing"):
        smoke._explicit_bambu_profiles(profiles["machine"], None, None)
    with pytest.raises(smoke.SmokeError, match="does not exist"):
        smoke._explicit_bambu_profiles(
            profiles["machine"], profiles["process"], tmp_path / "missing.json"
        )


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


def test_brief_accepts_complete_variable_aperture_display(tmp_path: Path) -> None:
    fixture = tmp_path / "variable-aperture-fixture"
    smoke._copy_fixture(FIXTURE, fixture)
    brief_path = fixture / "design-brief.json"
    brief = json.loads(brief_path.read_text(encoding="utf-8"))
    brief["lens_identity"]["maximum_aperture"] = "F3.5"
    brief["lens_identity"]["maximum_aperture_display"] = "F3.5–5.6"
    brief["display_text"][1] = "F3.5–5.6"
    brief["allowed_text"][1] = "F3.5–5.6"
    prompt_path = fixture / "prompt.txt"
    prompt_path.write_text(
        prompt_path.read_text(encoding="utf-8") + "\nF3.5–5.6\n",
        encoding="utf-8",
    )
    brief["generation"]["prompt_sha256"] = hashlib.sha256(
        prompt_path.read_bytes()
    ).hexdigest()
    brief_path.write_text(json.dumps(brief, ensure_ascii=False, indent=2), encoding="utf-8")

    report = smoke._validate_brief(fixture)
    assert report["display_text"][1] == "F3.5–5.6"


def test_brief_rejects_truncated_variable_aperture_second_read(tmp_path: Path) -> None:
    fixture = tmp_path / "truncated-aperture-fixture"
    smoke._copy_fixture(FIXTURE, fixture)
    brief_path = fixture / "design-brief.json"
    brief = json.loads(brief_path.read_text(encoding="utf-8"))
    brief["lens_identity"]["maximum_aperture"] = "F3.5"
    brief["lens_identity"]["maximum_aperture_display"] = "F3.5–5.6"
    brief["display_text"][1] = "F3.5"
    brief["allowed_text"][1] = "F3.5"
    brief_path.write_text(json.dumps(brief, ensure_ascii=False, indent=2), encoding="utf-8")

    try:
        smoke._validate_brief(fixture)
    except smoke.SmokeError as exc:
        assert "complete maximum aperture" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("a truncated variable-aperture second read must fail")


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


def test_current_imagegen_rehouse_fixture_and_retained_3mf_are_self_contained(tmp_path: Path) -> None:
    report = smoke._validate_brief(IMAGEGEN_V3_FIXTURE)
    assert report["status"] == "passed"
    assert report["display_text"][:2] == ["58", "F2"]
    clean_fixture = tmp_path / "fixture-v3"
    jobs = smoke._copy_fixture(IMAGEGEN_V3_FIXTURE, clean_fixture)
    assert [path.as_posix() for path in jobs] == ["jobs/95mm/job.toml"]
    assert not (clean_fixture / "artifacts").exists()

    job_path = IMAGEGEN_V3_FIXTURE / "jobs/95mm/job.toml"
    # Recreate process/model evidence from source-only fixture inputs.  The
    # sdist intentionally prunes jobs/*/out, so this test must not obtain a
    # green result from checkout-local generated masks or geometry reports.
    config, _ = smoke._build_job(
        clean_fixture / "jobs/95mm/job.toml",
        require_external=False,
        export_openscad=False,
    )
    current_brief = validate_design_brief(config)
    package = IMAGEGEN_V3_FIXTURE / "artifacts/helios-44-2-rehouse-imagegen-v3-95mm-native.3mf"
    sidecar = package.with_name(package.name + ".manifest.json")
    assert package.is_file() and package.stat().st_size > 1024
    manifest = json.loads(sidecar.read_text(encoding="utf-8"))
    assert manifest["verification"]["sha256"] == hashlib.sha256(package.read_bytes()).hexdigest()
    assert manifest["verification"]["model"]["parts"] == 1
    assert manifest["verification"]["model"]["objects"] == 1
    release = IMAGEGEN_V3_FIXTURE / "artifacts/helios-44-2-rehouse-imagegen-v3-95mm-3mf-release.json"
    assert release.is_file()
    release_report = json.loads(release.read_text(encoding="utf-8"))
    assert release_report["status"] == "passed"
    assert release_report["job_sha256"] == hashlib.sha256(job_path.read_bytes()).hexdigest()
    assert release_report["design_brief"]["status"] == "passed"
    assert release_report["design_brief"]["sha256"] == current_brief["sha256"]
    assert release_report["design_brief"]["candidate_sha256"] == current_brief["candidate_sha256"]
    assert release_report["design_brief"]["job_identity_binding_checked"] is True
    assert release_report["source_binding_audit"]["current_source_sha256"] == current_brief[
        "candidate_sha256"
    ]

    native_verification = smoke._adapter_verify(
        package,
        require_closed=True,
        require_single_volume=True,
    )
    assert release_report["native_3mf"]["sha256"] == native_verification["sha256"]
    assert release_report["native_3mf"]["bytes"] == native_verification["bytes"]
    geometry = json.loads(
        (config.output_dir / "model/geometry-report.json").read_text(encoding="utf-8")
    )
    current_rib_audit = bridge._audit_integrated_ribs(package, geometry["mechanical"])
    assert current_rib_audit["status"] == "passed"
    assert current_rib_audit["detected_start_positions"] == 12
    assert current_rib_audit["detected_end_positions"] == 12
    assert current_rib_audit["minimum_full_height_tip_face_width_mm"] > 0
    assert release_report["friction_rib_mesh_audit"] == current_rib_audit

    expected_top = bridge._expected_relief_top_z(geometry)
    material_audit = bridge._audit_material_assignments(
        package,
        config.palette,
        expected_footprint_mm2=bridge._expected_palette_footprints(config),
        expected_masks={
            name: config.output_dir / "masks" / f"{name}.png" for name in expected_top
        },
        expected_top_z_mm=expected_top,
        canvas_size_mm=config.face_diameter_mm,
    )
    assert material_audit["status"] == "passed"
    assert material_audit["unassigned_triangle_count"] == 0
    assert material_audit["unexpected_used_colors"] == []
    assert all(entry["triangle_count"] > 0 for entry in material_audit["colors"].values())
    assert release_report["material_assignment_audit"] == material_audit
    current_bounds = bridge._audit_native_bounds(native_verification, geometry)
    assert release_report["native_bounds_audit"] == current_bounds
    assert release_report["mechanical"]["friction_ribs_enabled"] is True

    bambu = IMAGEGEN_V3_FIXTURE / "artifacts/helios-44-2-rehouse-imagegen-v3-95mm-bambu-project.3mf"
    bambu_verification = smoke._adapter_verify(bambu)
    bambu_manifest = json.loads(
        Path(f"{bambu}.manifest.json").read_text(encoding="utf-8")
    )
    assert bambu_manifest["verification"]["sha256"] == hashlib.sha256(
        bambu.read_bytes()
    ).hexdigest()
    bambu_release = release_report["bambu_3mf"]
    assert bambu_release["mode"] == "export"
    assert bambu_release["sha256"] == bambu_verification["sha256"]
    assert bambu_release["bytes"] == bambu_verification["bytes"]
    prepared_by_selector = {
        item["selector"]: item for item in bambu_release["closed_part_preparation"]
    }
    palette_by_name = {str(item.name): item for item in config.palette}
    expected_parts = []
    for extruder, selector in enumerate(prepared_by_selector, start=1):
        retained_part = prepared_by_selector[selector]
        palette = palette_by_name[retained_part["name"]]
        expected_parts.append(
            {
                "name": retained_part["name"],
                "role": retained_part["role"],
                "selector": selector,
                "filename": retained_part["filename"],
                "extruder": extruder,
                "color": "#%02X%02X%02X" % tuple(int(value) for value in palette.rgb),
                "mesh": retained_part["mesh"],
            }
        )
    current_bambu_audit = bridge._audit_bambu_project(
        bambu,
        expected_parts,
        nozzle_mm=config.nozzle_mm,
    )
    assert current_bambu_audit == bambu_release["multipart_audit"]
    current_bambu_mesh = bridge._audit_bambu_mesh_report(bambu_verification)
    assert current_bambu_mesh == bambu_release["mesh_audit"]
    current_profile_audit = smoke._profile_manifest_audit(
        Path(f"{bambu}.manifest.json")
    )
    assert current_profile_audit["status"] == "passed"
    assert current_profile_audit["inputs"] == bambu_release["profile_inputs"]


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


def test_multi_lens_multi_diameter_releases_bind_current_sources_and_native_packages() -> None:
    """Keep the retained cross-brand/fit matrix reproducible, not decorative."""

    matrix = (
        (IMAGEGEN_V2_FIXTURE, ("77mm", "82mm", "95mm")),
        (MAMIYA_FIXTURE, ("77mm", "85mm", "95mm")),
    )
    for fixture, diameters in matrix:
        for diameter in diameters:
            job = fixture / f"jobs/{diameter}/job.toml"
            config = smoke.load_config(job)
            brief = validate_design_brief(config)
            artifacts = fixture / "artifacts"
            native = artifacts / f"{config.job_slug}-native.3mf"
            release_path = artifacts / f"{config.job_slug}-3mf-release.json"
            release = json.loads(release_path.read_text(encoding="utf-8"))

            assert release["status"] == "passed"
            assert release["job_sha256"] == hashlib.sha256(job.read_bytes()).hexdigest()
            assert release["design_brief"]["sha256"] == brief["sha256"]
            assert release["design_brief"]["candidate_sha256"] == brief[
                "candidate_sha256"
            ]
            assert release["source_binding_audit"]["status"] == "passed"
            assert release["source_binding_audit"]["current_source_sha256"] == brief[
                "candidate_sha256"
            ]
            assert release["native_3mf"]["sha256"] == hashlib.sha256(
                native.read_bytes()
            ).hexdigest()
            assert release["native_3mf"]["model"]["parts"] == 1
            assert release["native_3mf"]["model"]["objects"] == 1
            rib_audit = release["friction_rib_mesh_audit"]
            assert rib_audit["status"] == "passed"
            assert rib_audit["detected_start_positions"] == config.fit.friction_rib_count
            assert rib_audit["detected_end_positions"] == config.fit.friction_rib_count
            assert release["material_assignment_audit"]["status"] == "passed"
            assert release["native_bounds_audit"]["status"] == "passed"


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


def _fake_canonical_payload(
    native: Path,
    *,
    bambu: Path | None = None,
    profiles: dict[str, Path] | None = None,
) -> dict[str, object]:
    native_digest = hashlib.sha256(native.read_bytes()).hexdigest()
    payload: dict[str, object] = {
        "schema_version": 1,
        "status": "passed",
        "runner": "scripts/build_3mf.py",
        "native_3mf": {
            "sha256": native_digest,
            "bytes": native.stat().st_size,
        },
        "design_brief": {"status": "passed", "sha256": "b" * 64},
        "source_binding_audit": {"status": "passed"},
        "projection": {"status": "passed"},
        "native_bounds_audit": {"status": "passed"},
        "material_assignment_audit": {"status": "passed"},
        "friction_rib_mesh_audit": {"status": "passed"},
        "retention_status": "geometry_present_fit_unverified",
    }
    if bambu is not None:
        assert profiles is not None
        digest = hashlib.sha256(bambu.read_bytes()).hexdigest()
        profile_inputs = {
            label: {
                "path": f"<{label}>.json",
                "sha256": hashlib.sha256(profiles[label].read_bytes()).hexdigest(),
                "post_run_sha256": hashlib.sha256(
                    profiles[label].read_bytes()
                ).hexdigest(),
            }
            for label in ("machine", "process", "filament")
        }
        payload["bambu_3mf"] = {
            "mode": "export",
            "sha256": digest,
            "bytes": bambu.stat().st_size,
            "profile_inputs": profile_inputs,
            "multipart_audit": {"status": "passed", "part_count": 3},
            "mesh_audit": {"status": "passed"},
        }
    else:
        payload["bambu_3mf"] = None
    return payload


def _write_fake_bambu_manifest(
    path: Path,
    profiles_paths: dict[str, Path],
) -> None:
    labels = ("machine", "process", "filament")
    profiles = {
        label: {
            "path": f"<{label}>.json",
            "sha256": hashlib.sha256(profiles_paths[label].read_bytes()).hexdigest(),
            "post_run_sha256": hashlib.sha256(
                profiles_paths[label].read_bytes()
            ).hexdigest(),
        }
        for label in labels
    }
    path.write_text(
        json.dumps(
            {
                "adapter": "bambu-studio-3mf",
                "multipart": True,
                "profiles": profiles,
                "profile_resolution": {
                    label: {
                        "chain": [
                            {
                                "path": f"<{label}>.json",
                                "sha256": profiles[label]["sha256"],
                            }
                        ]
                    }
                    for label in labels
                },
                "effective_profile_audit": {
                    "status": "passed",
                    "checked": {label: {"field": "value"} for label in labels},
                },
            }
        ),
        encoding="utf-8",
    )


def test_canonical_bridge_is_the_only_native_and_multipart_bambu_producer(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config = SimpleNamespace(
        job_slug="test-lens-95mm",
        config_path=tmp_path / "job.toml",
    )
    config.config_path.write_text("[job]\n", encoding="utf-8")
    profiles = {
        label: tmp_path / f"{label}.json"
        for label in ("machine", "process", "filament")
    }
    for path in profiles.values():
        path.write_text("{}", encoding="utf-8")
    commands: list[list[str]] = []

    def fake_run(command, *, cwd=smoke.ROOT, timeout=900):
        del cwd, timeout
        argv = [str(item) for item in command]
        commands.append(argv)
        native = Path(argv[argv.index("--native-output") + 1])
        bambu = Path(argv[argv.index("--slice-output") + 1])
        report = Path(argv[argv.index("--report") + 1])
        native.parent.mkdir(parents=True, exist_ok=True)
        native.write_bytes(b"canonical-native")
        bambu.write_bytes(b"canonical-multipart-bambu")
        _write_fake_bambu_manifest(Path(f"{bambu}.manifest.json"), profiles)
        payload = _fake_canonical_payload(native, bambu=bambu, profiles=profiles)
        report.write_text(json.dumps(payload), encoding="utf-8")
        return {
            "argv": argv,
            "returncode": 0,
            "stdout": json.dumps(payload),
            "stderr": "",
        }

    verify_calls: list[tuple[Path, bool, bool, bool]] = []

    def fake_verify(
        path: Path,
        *,
        require_slice: bool = False,
        require_closed: bool = False,
        require_single_volume: bool = False,
    ):
        verify_calls.append(
            (path, require_slice, require_closed, require_single_volume)
        )
        return {
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "bytes": path.stat().st_size,
            "model": {"parts": 1, "objects": 1},
            "has_embedded_gcode": False,
        }

    monkeypatch.setattr(smoke, "_run", fake_run)
    monkeypatch.setattr(smoke, "_adapter_verify", fake_verify)
    result = smoke._canonical_bridge(
        config,
        tmp_path / "artifacts",
        openscad="/opt/OpenSCAD",
        require_external=False,
        bambu_mode="export",
        bambu="/opt/BambuStudio",
        profiles=profiles,
    )

    assert result["status"] == "passed"
    assert result["native_3mf"]["status"] == "passed"
    assert result["bambu_3mf"]["status"] == "passed"
    assert result["bambu_3mf"]["multipart_audit"]["status"] == "passed"
    assert result["bambu_3mf"]["profile_audit"]["effective"]["status"] == "passed"
    assert len(commands) == 1
    command = commands[0]
    assert command[1] == str(smoke.BRIDGE)
    assert str(smoke.ADAPTER) not in command
    assert not any(value.endswith("-assembly.stl") for value in command)
    assert command[command.index("--bambu") + 1] == "export"
    assert all(
        flag in command
        for flag in ("--machine-profile", "--process-profile", "--filament-profile")
    )
    assert verify_calls[0][2:] == (True, True)
    assert verify_calls[1][1:] == (False, False, False)


def test_canonical_bridge_propagates_external_unverifiable_and_validation_failed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config = SimpleNamespace(job_slug="test", config_path=tmp_path / "job.toml")
    config.config_path.write_text("[job]\n", encoding="utf-8")

    def result(status: str, failure_class: str):
        monkeypatch.setattr(
            smoke,
            "_run",
            lambda *_args, **_kwargs: {
                "returncode": 1,
                "stdout": json.dumps(
                    {
                        "status": status,
                        "failure_class": failure_class,
                        "reason": "deliberate gate result",
                    }
                ),
                "stderr": "",
            },
        )
        return smoke._canonical_bridge(
            config,
            tmp_path / status,
            openscad="/opt/OpenSCAD",
            require_external=False,
            bambu_mode="export",
            bambu=None,
            profiles=None,
        )

    unavailable = result("unverifiable", "external_dependency_unavailable")
    assert unavailable["status"] == "unverifiable"
    assert unavailable["mode"] == "export"
    failed = result("failed", "validation_failed")
    assert failed["status"] == "failed"
    assert failed["failure_class"] == "validation_failed"


def test_release_status_never_hides_requested_bambu_or_native_failure() -> None:
    assert smoke._aggregate_release_status(["passed", "passed"]) == "passed"
    assert smoke._aggregate_release_status(["passed", "unverifiable"]) == "unverifiable"
    assert smoke._aggregate_release_status(["passed", "failed"]) == "failed"
    assert smoke._aggregate_release_status([]) == "failed"


def test_legacy_direct_native_and_flattened_bambu_shortcuts_are_removed() -> None:
    assert not hasattr(smoke, "_native_3mf")
    assert not hasattr(smoke, "_bambu_run")


def test_bambu_profile_audit_rejects_flattened_or_incomplete_manifest(tmp_path: Path) -> None:
    manifest = tmp_path / "bad.manifest.json"
    manifest.write_text(
        json.dumps({"adapter": "bambu-studio-3mf", "multipart": False}),
        encoding="utf-8",
    )
    try:
        smoke._profile_manifest_audit(manifest)
    except smoke.SmokeError as exc:
        assert "multipart" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("a flattened Bambu package must not pass the smoke gate")
