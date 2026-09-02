from __future__ import annotations

import hashlib
import importlib
import json
from pathlib import Path

from test_pipeline import _job

import lens_cap_pipeline.external as external_module
from lens_cap_pipeline.config import load_config
from lens_cap_pipeline.external import write_bambu_handoff
from lens_cap_pipeline.model import generate_model
from lens_cap_pipeline.process import process

validate_module = importlib.import_module("lens_cap_pipeline.validate")


def _fitted_job(tmp_path: Path) -> Path:
    path = _job(tmp_path)
    text = path.read_text(encoding="utf-8")
    text = text.replace(
        "face_diameter_mm = 52.0",
        "face_diameter_mm = 95.0\nmeasured_diameter_mm = 95.0",
    )
    text += '\n[fit]\nfoam_liner_status = "foam"\nliner_thickness_mm = 1.5\n'
    path.write_text(text, encoding="utf-8")
    return path


def test_external_resolver_keeps_bare_command_on_path_and_path_typo_is_fail_closed(
    tmp_path: Path, monkeypatch
) -> None:
    calls: list[str] = []

    def fake_which(value: str) -> str | None:
        calls.append(value)
        return "/host/bin/openscad" if value == "openscad" else None

    monkeypatch.setattr(external_module.shutil, "which", fake_which)
    assert external_module._resolve_tool("openscad", ("fallback",), base_dir=tmp_path) == "/host/bin/openscad"
    assert calls == ["openscad"]

    calls.clear()
    assert external_module._resolve_tool("tools/missing-openscad", ("fallback",), base_dir=tmp_path) is None
    # A bad explicit path must not fall through to the fallback names.
    assert calls == []


def test_external_resolver_accepts_one_shot_name_iterables(monkeypatch) -> None:
    """Desktop fallback detection must not consume a generator twice."""

    monkeypatch.setattr(
        external_module.shutil,
        "which",
        lambda value: "/host/bin/openscad" if value == "openscad" else None,
    )
    names = (name for name in ("openscad",))
    assert external_module._resolve_tool(None, names) == "/host/bin/openscad"


def test_validation_tool_resolver_uses_path_for_bare_name(tmp_path: Path, monkeypatch) -> None:
    calls: list[str] = []

    def fake_which(value: str) -> str | None:
        calls.append(value)
        return "/host/bin/openscad" if value == "openscad" else None

    monkeypatch.setattr(validate_module.shutil, "which", fake_which)
    assert validate_module._tool("openscad", ("fallback",), base_dir=tmp_path) == "/host/bin/openscad"
    assert calls == ["openscad"]
    calls.clear()
    assert validate_module._tool("tools/missing-openscad", ("fallback",), base_dir=tmp_path) is None
    assert calls == []


def test_bambu_handoff_filters_stale_meshes_and_exposes_exclusive_sets(tmp_path: Path) -> None:
    config = load_config(_fitted_job(tmp_path))
    process_report = process(config)
    model = generate_model(config, process_report)
    mesh_dir = model.model_dir / "mesh"
    mesh_dir.mkdir(parents=True, exist_ok=True)
    for selector in ("assembly", "base", "fit_ring"):
        (mesh_dir / f"{config.job_slug}-{selector}.stl").write_bytes(b"synthetic-stl")
    stale = mesh_dir / f"{config.job_slug}-old-selector.stl"
    stale.write_bytes(b"stale")

    handoff = write_bambu_handoff(config, model)
    payload = json.loads(handoff.report_path.read_text(encoding="utf-8"))
    # Manual/legacy STL files are listed for inspection, but without a
    # matching OpenSCAD report they are not claimed as current production
    # inputs.
    assert payload["status"] == "unverifiable"
    assert payload["mesh_provenance"]["status"] == "unverifiable"
    assert payload["declared_selectors"] == model.report["render_part_selectors"]
    assert {item["selector"] for item in payload["assembly_stls"]} == {"assembly"}
    assert {item["selector"] for item in payload["coupon_stls"]} == {"fit_ring"}
    assert {item["selector"] for item in payload["component_stls"]} == {"base"}
    assert payload["print_sets"]["integrated_monochrome"]["inputs"] == payload["assembly_stls"]
    assert payload["print_sets"]["multicolor_components"]["inputs"] == payload["component_stls"]
    assert payload["do_not_import_print_sets_together"] is True
    assert payload["ignored_stl_inputs"][0]["selector"] == "old-selector"
    assert payload["ignored_stl_inputs"][0]["path"].endswith("old-selector.stl")


def test_bambu_handoff_rejects_meshes_from_a_previous_model(tmp_path: Path) -> None:
    """A same-named STL must not survive a forced model regeneration."""
    config = load_config(_fitted_job(tmp_path))
    process_report = process(config)
    model = generate_model(config, process_report)
    mesh_dir = model.model_dir / "mesh"
    mesh_dir.mkdir(parents=True, exist_ok=True)
    assembly = mesh_dir / f"{config.job_slug}-assembly.stl"
    assembly.write_bytes(b"old-model-stl")
    # Simulate the report left by an earlier export: it deliberately points at
    # a different SCAD hash, so handoff must fail closed and list the stale
    # file instead of presenting it as a current printable input.
    (model.model_dir / "external-openscad-report.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "status": "passed",
                "model_sha256": "0" * 64,
                "parts": {
                    "assembly": {
                        "status": "passed",
                        "path": "mesh/" + assembly.name,
                        "sha256": "1" * 64,
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    handoff = write_bambu_handoff(config, model)
    assert handoff.report["status"] == "unverifiable"
    assert handoff.report["stl_inputs"] == []
    assert handoff.report["mesh_provenance"]["status"] == "failed"
    assert handoff.report["ignored_stl_inputs"][0]["reason"].startswith(
        "external-openscad-report belongs"
    )


def test_manual_stl_is_not_passed_even_when_bambu_is_available(
    tmp_path: Path, monkeypatch
) -> None:
    """A slicer being installed cannot certify an untracked manual mesh."""
    config = load_config(_fitted_job(tmp_path))
    process_report = process(config)
    model = generate_model(config, process_report)
    mesh_dir = model.model_dir / "mesh"
    mesh_dir.mkdir(parents=True, exist_ok=True)
    (mesh_dir / f"{config.job_slug}-assembly.stl").write_bytes(b"manual-stl")

    monkeypatch.setattr(external_module, "_resolve_tool", lambda *args, **kwargs: "/bin/echo")
    handoff = write_bambu_handoff(config, model)

    assert handoff.report["tool_status"] == "passed"
    assert handoff.report["status"] == "unverifiable"
    assert handoff.report["mesh_provenance"]["status"] == "unverifiable"


def test_verified_handoff_note_reflects_ready_stl_inputs(tmp_path: Path, monkeypatch) -> None:
    """A verified mesh with an available slicer gets the actionable note."""
    config = load_config(_fitted_job(tmp_path))
    process_report = process(config)
    model = generate_model(config, process_report)
    mesh_dir = model.model_dir / "mesh"
    mesh_dir.mkdir(parents=True, exist_ok=True)
    assembly = mesh_dir / f"{config.job_slug}-assembly.stl"
    assembly.write_bytes(b"verified-stl")
    model_hash = hashlib.sha256(model.scad_path.read_bytes()).hexdigest()
    assembly_hash = hashlib.sha256(assembly.read_bytes()).hexdigest()
    (model.model_dir / "external-openscad-report.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "status": "passed",
                "model_sha256": model_hash,
                "parts": {
                    "assembly": {
                        "status": "passed",
                        "path": "mesh/" + assembly.name,
                        "sha256": assembly_hash,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(external_module, "_resolve_tool", lambda *args, **kwargs: "/bin/echo")

    handoff = write_bambu_handoff(config, model)

    assert handoff.report["tool_status"] == "passed"
    assert handoff.report["status"] == "available"
    assert handoff.report["mesh_provenance"]["status"] == "passed"
    assert handoff.report["note"].startswith("STL inputs are ready")
