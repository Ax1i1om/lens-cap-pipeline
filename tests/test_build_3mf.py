"""Unit checks for the public one-command 3MF bridge."""

from __future__ import annotations

import hashlib
import json
import math
import shutil
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

# ``scripts`` is a repository helper package rather than an installed runtime
# package.  Make this test work from both a checkout and an sdist unpacked into
# a temporary directory, where pytest may not put the repository root on
# ``sys.path`` automatically.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import scripts.build_3mf as bridge  # noqa: E402
from lens_cap_pipeline.brief import BriefError, validate_design_brief  # noqa: E402
from lens_cap_pipeline.config import load_config  # noqa: E402
from lens_cap_pipeline.process import process  # noqa: E402


def test_bridge_parser_defaults_to_native_only() -> None:
    args = bridge._parser().parse_args(["job.toml"])
    assert args.bambu == "never"
    assert args.force is False
    assert args.brief is None


def test_material_footprint_uses_post_vectorisation_area(tmp_path: Path) -> None:
    (tmp_path / "process-report.json").write_text(
        json.dumps(
            {
                "mask_stats": {
                    "ivory": {
                        "pixels": 999,
                        "svg_vectorization": {"filled_area_mm2": 12.375},
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    assert bridge._expected_palette_footprints(
        SimpleNamespace(output_dir=tmp_path)
    ) == {"ivory": 12.375}


def test_projection_tolerance_is_derived_from_bounded_vectorisation(
    tmp_path: Path,
) -> None:
    (tmp_path / "process-report.json").write_text(
        json.dumps(
            {
                "mask_stats": {
                    "ivory": {
                        "svg_vectorization": {
                            "simplification_tolerance_px": 0.5,
                            "maximum_deviation_mm": 0.1,
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    assert bridge._projection_tolerance(
        SimpleNamespace(output_dir=tmp_path, nozzle_mm=0.2)
    ) == {
        "tolerance_pixels": 2,
        "triangle_rasterization_pixels": 1,
        "vectorization_budget_pixels": 0.5,
        "vectorization_budget_mm": 0.1,
    }


@pytest.mark.parametrize(
    ("deviation_px", "deviation_mm"),
    ((0.5001, 0.1), (0.5, 0.2)),
)
def test_projection_tolerance_rejects_an_unbounded_vectorisation_budget(
    tmp_path: Path,
    deviation_px: float,
    deviation_mm: float,
) -> None:
    (tmp_path / "process-report.json").write_text(
        json.dumps(
            {
                "mask_stats": {
                    "ivory": {
                        "svg_vectorization": {
                            "simplification_tolerance_px": deviation_px,
                            "maximum_deviation_mm": deviation_mm,
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(bridge.ReleaseError, match="bounded contour budget"):
        bridge._projection_tolerance(
            SimpleNamespace(output_dir=tmp_path, nozzle_mm=0.2)
        )


def test_release_paths_reject_collisions_and_protected_intermediates(
    tmp_path: Path,
) -> None:
    config = load_config(
        ROOT / "examples/fixtures/helios-44-2-rehouse-imagegen-v3/jobs/95mm/job.toml"
    )
    native = tmp_path / "native.3mf"
    with pytest.raises(bridge.ReleaseError, match="must all be distinct"):
        bridge._validate_release_paths(
            config,
            native=native,
            bambu_output=native,
            report=tmp_path / "release.json",
            brief=ROOT
            / "examples/fixtures/helios-44-2-rehouse-imagegen-v3/design-brief.json",
        )
    with pytest.raises(bridge.ReleaseError, match="protected input/intermediate"):
        bridge._validate_release_paths(
            config,
            native=native,
            bambu_output=None,
            report=config.output_dir / "model/geometry-report.json",
            brief=ROOT
            / "examples/fixtures/helios-44-2-rehouse-imagegen-v3/design-brief.json",
        )
    with pytest.raises(bridge.ReleaseError, match="must end in .3mf"):
        bridge._validate_release_paths(
            config,
            native=tmp_path / "native.stl",
            bambu_output=None,
            report=tmp_path / "release.json",
            brief=ROOT
            / "examples/fixtures/helios-44-2-rehouse-imagegen-v3/design-brief.json",
        )


def test_retarget_manifest_records_final_not_staging_path(tmp_path: Path) -> None:
    staging = tmp_path / ".stage" / "cap.3mf"
    staging.parent.mkdir()
    manifest = Path(f"{staging}.manifest.json")
    manifest.write_text(
        json.dumps(
            {
                "output": str(staging),
                "verification": {"path": str(staging), "status": "passed"},
            }
        ),
        encoding="utf-8",
    )
    final = tmp_path / "release" / "cap.3mf"
    returned = bridge._retarget_adapter_manifest(staging, final)
    payload = json.loads(returned.read_text(encoding="utf-8"))
    assert payload["output"] == "<external>/cap.3mf"
    assert payload["verification"]["path"] == payload["output"]
    assert ".stage" not in payload["output"]


def _write_minimal_bambu_project(
    path: Path,
    *,
    gray_translation_x: float = 0.0,
    gray_metadata_x: float | None = None,
) -> list[dict[str, object]]:
    core_ns = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
    core = ET.Element(f"{{{core_ns}}}model", {"unit": "millimeter"})
    resources = ET.SubElement(core, f"{{{core_ns}}}resources")
    assembly = ET.SubElement(resources, f"{{{core_ns}}}object", {"id": "4"})
    components = ET.SubElement(assembly, f"{{{core_ns}}}components")
    names = ("base.stl", "gray.stl", "ivory.stl")
    colors = ("#111211", "#747471", "#F2E7D3")
    translations = (0.0, gray_translation_x, 0.0)
    for index, translation in enumerate(translations, 1):
        ET.SubElement(
            components,
            f"{{{core_ns}}}component",
            {
                "objectid": str(index),
                "path": "/3D/Objects/object_1.model",
                "transform": f"1 0 0 0 1 0 0 0 1 {translation} 0 0",
            },
        )
    build = ET.SubElement(core, f"{{{core_ns}}}build")
    ET.SubElement(
        build,
        f"{{{core_ns}}}item",
        {"objectid": "4", "printable": "1"},
    )

    objects_model = ET.Element(f"{{{core_ns}}}model", {"unit": "millimeter"})
    object_resources = ET.SubElement(objects_model, f"{{{core_ns}}}resources")
    expected: list[dict[str, object]] = []
    for index, (name, color, translation) in enumerate(
        zip(names, colors, translations, strict=True), 1
    ):
        obj = ET.SubElement(
            object_resources, f"{{{core_ns}}}object", {"id": str(index)}
        )
        mesh = ET.SubElement(obj, f"{{{core_ns}}}mesh")
        vertices = ET.SubElement(mesh, f"{{{core_ns}}}vertices")
        for x, y, z in ((-1, -1, 0), (1, -1, 0), (0, 1, 1)):
            ET.SubElement(
                vertices,
                f"{{{core_ns}}}vertex",
                {"x": str(x), "y": str(y), "z": str(z)},
            )
        triangles = ET.SubElement(mesh, f"{{{core_ns}}}triangles")
        ET.SubElement(
            triangles,
            f"{{{core_ns}}}triangle",
            {"v1": "0", "v2": "1", "v3": "2"},
        )
        expected.append(
            {
                "name": Path(name).stem,
                "filename": name,
                "extruder": index,
                "color": color,
                "role": "base" if index == 1 else "relief",
                "mesh": {
                    "triangles": 1,
                    "absolute_volume_mm3": 1.0 / 3.0,
                    "surface_fingerprint": bridge._surface_fingerprint(
                        [
                            (
                                (-1.0, -1.0, 0.0),
                                (1.0, -1.0, 0.0),
                                (0.0, 1.0, 1.0),
                            )
                        ],
                        translation=(translation, 0.0, 0.0),
                    ),
                    "bounds": {
                        "min_mm": [-1 + translation, -1.0, 0.0],
                        "max_mm": [1 + translation, 1.0, 1.0],
                        "size_mm": [2.0, 2.0, 1.0],
                    }
                },
            }
        )

    settings = ET.Element("config")
    settings_object = ET.SubElement(settings, "object", {"id": "4"})
    metadata_translations = (
        0.0,
        gray_translation_x if gray_metadata_x is None else gray_metadata_x,
        0.0,
    )
    for index, (name, translation) in enumerate(
        zip(names, metadata_translations, strict=True), 1
    ):
        part = ET.SubElement(
            settings_object,
            "part",
            {"id": str(index), "subtype": "normal_part"},
        )
        values = {
            "name": name,
            "extruder": str(index),
            "matrix": f"1 0 0 {translation} 0 1 0 0 0 0 1 0 0 0 0 1",
            "source_offset_x": str(translation),
            "source_offset_y": "0",
            "source_offset_z": "0",
        }
        for key, value in values.items():
            ET.SubElement(part, "metadata", {"key": key, "value": value})
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "3D/3dmodel.model",
            ET.tostring(core, encoding="utf-8", xml_declaration=True),
        )
        archive.writestr(
            "3D/Objects/object_1.model",
            ET.tostring(objects_model, encoding="utf-8", xml_declaration=True),
        )
        archive.writestr(
            "Metadata/model_settings.config",
            ET.tostring(settings, encoding="utf-8", xml_declaration=True),
        )
        archive.writestr(
            "Metadata/project_settings.config",
            json.dumps({"filament_colour": list(colors), "nozzle_diameter": ["0.2"]}),
        )
    return expected


def _rewrite_zip_member(path: Path, member: str, payload: bytes) -> None:
    temporary = path.with_suffix(".rewrite")
    with zipfile.ZipFile(path) as source, zipfile.ZipFile(temporary, "w") as output:
        for info in source.infolist():
            output.writestr(info, payload if info.filename == member else source.read(info.filename))
    temporary.replace(path)


def test_bambu_audit_binds_component_transforms_to_stl_world_bounds(
    tmp_path: Path,
) -> None:
    clean = tmp_path / "clean.3mf"
    expected = _write_minimal_bambu_project(clean)
    assert bridge._audit_bambu_project(clean, expected, nozzle_mm=0.2)["status"] == "passed"

    inconsistent = tmp_path / "inconsistent.3mf"
    expected = _write_minimal_bambu_project(
        inconsistent, gray_translation_x=1000.0, gray_metadata_x=0.0
    )
    with pytest.raises(bridge.ReleaseError, match="transforms disagree"):
        bridge._audit_bambu_project(inconsistent, expected, nozzle_mm=0.2)

    self_consistent_but_moved = tmp_path / "moved.3mf"
    expected = _write_minimal_bambu_project(
        self_consistent_but_moved, gray_translation_x=1000.0
    )
    # Replace the expected source bounds with the original audited STL bounds;
    # internally consistent metadata must not legitimize a moved relief.
    expected[1]["mesh"]["bounds"] = {
        "min_mm": [-1.0, -1.0, 0.0],
        "max_mm": [1.0, 1.0, 1.0],
        "size_mm": [2.0, 2.0, 1.0],
    }
    with pytest.raises(bridge.ReleaseError, match="world-space bounds drifted"):
        bridge._audit_bambu_project(
            self_consistent_but_moved, expected, nozzle_mm=0.2
        )


def test_bambu_audit_rejects_same_bounds_and_volume_with_wrong_surface(
    tmp_path: Path,
) -> None:
    project = tmp_path / "wrong-surface.3mf"
    expected = _write_minimal_bambu_project(project)
    with zipfile.ZipFile(project) as archive:
        root = ET.fromstring(archive.read("3D/Objects/object_1.model"))
    second = next(
        node
        for node in root.iter()
        if node.tag.rsplit("}", 1)[-1] == "object" and node.attrib.get("id") == "2"
    )
    vertices = [node for node in second.iter() if node.tag.rsplit("}", 1)[-1] == "vertex"]
    # Moving this apex in X preserves the object's AABB and signed volume.
    vertices[2].set("x", "0.5")
    _rewrite_zip_member(
        project,
        "3D/Objects/object_1.model",
        ET.tostring(root, encoding="utf-8", xml_declaration=True),
    )
    with pytest.raises(bridge.ReleaseError, match="surface geometry drifted"):
        bridge._audit_bambu_project(project, expected, nozzle_mm=0.2)


def test_bambu_audit_rejects_mixed_nozzle_list(tmp_path: Path) -> None:
    project = tmp_path / "mixed-nozzle.3mf"
    expected = _write_minimal_bambu_project(project)
    with zipfile.ZipFile(project) as archive:
        settings = json.loads(
            archive.read("Metadata/project_settings.config").decode("utf-8")
        )
    settings["nozzle_diameter"] = ["0.2", "0.8"]
    _rewrite_zip_member(
        project,
        "Metadata/project_settings.config",
        json.dumps(settings).encode("utf-8"),
    )
    with pytest.raises(bridge.ReleaseError, match="nozzle does not match"):
        bridge._audit_bambu_project(project, expected, nozzle_mm=0.2)


def test_bambu_audit_rejects_decoy_components_outside_the_build_root(
    tmp_path: Path,
) -> None:
    project = tmp_path / "decoy-build-root.3mf"
    expected = _write_minimal_bambu_project(project)
    with zipfile.ZipFile(project) as archive:
        root = ET.fromstring(archive.read("3D/3dmodel.model"))
    namespace = root.tag.split("}", 1)[0].lstrip("{")
    resources = next(
        node for node in root if node.tag.rsplit("}", 1)[-1] == "resources"
    )
    ET.SubElement(resources, f"{{{namespace}}}object", {"id": "5", "type": "model"})
    build_item = next(
        node for node in root.iter() if node.tag.rsplit("}", 1)[-1] == "item"
    )
    build_item.set("objectid", "5")
    _rewrite_zip_member(
        project,
        "3D/3dmodel.model",
        ET.tostring(root, encoding="utf-8", xml_declaration=True),
    )
    with pytest.raises(bridge.ReleaseError, match="sole audited assembly|reachable assembly"):
        bridge._audit_bambu_project(project, expected, nozzle_mm=0.2)


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        ("swapped_part_ids", "extruder assignment|name disagrees"),
        ("negative_part", "normal_part"),
        ("unprintable", "printable='1'"),
    ),
)
def test_bambu_audit_binds_part_identity_and_printability(
    tmp_path: Path, mutation: str, message: str
) -> None:
    project = tmp_path / f"{mutation}.3mf"
    expected = _write_minimal_bambu_project(project)
    if mutation in {"swapped_part_ids", "negative_part"}:
        with zipfile.ZipFile(project) as archive:
            root = ET.fromstring(archive.read("Metadata/model_settings.config"))
        parts = list(root.iter("part"))
        if mutation == "swapped_part_ids":
            parts[1].set("id", "3")
            parts[2].set("id", "2")
        else:
            parts[1].set("subtype", "negative_part")
        _rewrite_zip_member(
            project,
            "Metadata/model_settings.config",
            ET.tostring(root, encoding="utf-8", xml_declaration=True),
        )
    else:
        with zipfile.ZipFile(project) as archive:
            root = ET.fromstring(archive.read("3D/3dmodel.model"))
        item = next(
            node for node in root.iter() if node.tag.rsplit("}", 1)[-1] == "item"
        )
        item.set("printable", "0")
        _rewrite_zip_member(
            project,
            "3D/3dmodel.model",
            ET.tostring(root, encoding="utf-8", xml_declaration=True),
        )
    with pytest.raises(bridge.ReleaseError, match=message):
        bridge._audit_bambu_project(project, expected, nozzle_mm=0.2)


def test_bambu_mesh_audit_rejects_topology_defects() -> None:
    verification = {
        "model": {
            "mesh_parts": 3,
            "vertices": 10,
            "triangles": 12,
            "boundary_edges": 0,
            "nonmanifold_edges": 1,
            "inconsistent_orientation_edges": 0,
            "zero_area_triangles": 0,
            "duplicate_triangles": 0,
            "zero_volume_components": 0,
            "volume_components": 3,
            "unreferenced_vertices": 0,
        }
    }
    with pytest.raises(bridge.ReleaseError, match="mesh is not solid"):
        bridge._audit_bambu_mesh_report(verification)


def test_post_build_source_binding_rejects_artwork_swap(tmp_path: Path) -> None:
    fixture = tmp_path / "fixture"
    shutil.copytree(
        ROOT / "examples/fixtures/helios-44-2-rehouse-imagegen-v3", fixture
    )
    config = load_config(fixture / "jobs/95mm/job.toml")
    approved_hash = hashlib.sha256(config.source_path.read_bytes()).hexdigest()
    config.output_dir.mkdir(parents=True, exist_ok=True)
    (config.output_dir / "process-report.json").write_text(
        json.dumps(
            {
                "status": "passed",
                "source": {"sha256": approved_hash},
            }
        ),
        encoding="utf-8",
    )
    brief_report = {"candidate_sha256": approved_hash}
    assert bridge._audit_build_source_binding(config, brief_report)["status"] == "passed"
    config.source_path.write_bytes(config.source_path.read_bytes() + b"changed")
    with pytest.raises(bridge.ReleaseError, match="hashes disagree"):
        bridge._audit_build_source_binding(config, brief_report)


def test_last_json_ignores_diagnostic_prefix() -> None:
    assert bridge._last_json("startup warning\n{" + '"status":"passed"}') == {"status": "passed"}


def test_configured_tool_hint_is_relative_to_job_and_cli_wins(tmp_path: Path) -> None:
    job_dir = tmp_path / "jobs" / "custom"
    tool = job_dir / "tools" / "openscad"
    tool.parent.mkdir(parents=True)
    tool.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    tool.chmod(0o755)
    configured = bridge._configured_tool_hint(None, "tools/openscad", job_dir)
    assert configured == str((job_dir / "tools/openscad").resolve())
    assert bridge._resolve_executable(configured, ()) == str(tool.resolve())
    # A CLI spelling is intentionally left relative to the caller's cwd; the
    # caller supplied it explicitly and the executable resolver treats it as
    # authoritative rather than falling back to an unrelated installation.
    assert bridge._configured_tool_hint("./openscad", "ignored", job_dir) == "./openscad"


def test_configured_tool_hint_keeps_bare_command_names_for_path(tmp_path: Path) -> None:
    job_dir = tmp_path / "jobs" / "custom"
    job_dir.mkdir(parents=True)
    assert bridge._configured_tool_hint(None, "openscad", job_dir) == "openscad"
    assert bridge._configured_tool_hint(None, "BambuStudio", job_dir) == "BambuStudio"
    # An explicit filename/extension carries path intent and is therefore
    # resolved beside the job, just like a value containing a separator.
    assert bridge._configured_tool_hint(None, "tools/OpenSCAD.exe", job_dir) == str(
        (job_dir / "tools/OpenSCAD.exe").resolve()
    )


def test_missing_openscad_is_an_explicit_unverifiable_result(tmp_path: Path, capsys) -> None:
    # The source image only needs to exist for config parsing; the bridge must
    # stop before writing process/model output when the required external
    # native-3MF tool is unavailable.
    source = tmp_path / "master.png"
    source.write_bytes(b"not-an-image")
    config = tmp_path / "job.toml"
    config.write_text(
        "\n".join(
            [
                    'job_slug = "bridge-test"',
                    'source_art = "master.png"',
                    'measured_diameter_mm = 52.0',
                    "grid_size = 64",
                    "[metadata]",
                    'lens_identity = "Example Prime 50mm F1.4"',
                        'display_text = ["50", "F1.4"]',
                    "[fit]",
                    'foam_liner_status = "none"',
                    "friction_ribs_enabled = true",
                    "friction_ribs_explicit = false",
                    "[circle]",
                "center_px = [1, 1]",
                "radius_px = 1",
                "[palette.black]",
                "index = 0",
                "rgb = [0, 0, 0]",
                'role = "base"',
                "height_mm = 0.0",
                "[palette.white]",
                "index = 1",
                "rgb = [255, 255, 255]",
                'role = "relief"',
                "height_mm = 0.4",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    candidate_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    (tmp_path / "design-brief.json").write_text(
        json.dumps(
                {
                    "schema_version": 2,
                    "binding_mode": "single_job",
                    "job_slug": "bridge-test",
                    "production_target": "printable_front",
                "lens_identity": {
                    "brand": "Example",
                    "model": "Prime 50",
                    "focal_length_mm": 50,
                    "maximum_aperture": "F1.4",
                },
                "display_text": ["50", "F1.4"],
                "allowed_text": ["50", "F1.4"],
                "approved_references": [],
                "anchors": [
                        {
                            "anchor_id": "example-registration-grid",
                            "claim_kind": "manufacturer_culture",
                            "subject_scope": "Example Prime lens system",
                            "identity_binding": {
                                "brand": "Example",
                                "scope": "brand",
                            },
                        "source": "https://example.test/catalog",
                        "evidence_state": "verified",
                        "source_role": "proof",
                        "summary": "The fictional maker catalog documents this example lens system.",
                        "render_role": "visual_metaphor",
                        "anchor_context": "A test-only manufacturer culture anchor for the release gate.",
                        "motif_commitment": "structural",
                        "anchor_visual_motif": "Broad catalog registration bars and a circular calibration field.",
                        "recognition_cue": "The registration geometry remains visible without secondary words.",
                    }
                ],
                "generation": {
                    "provider": "test provider",
                    "mode": "generate",
                    "candidate_path": "master.png",
                    "candidate_sha256": candidate_hash,
                    "reference_hashes": [],
                    "approved": True,
                    "approval_note": (
                        "A human reviewer approved this exact raster for the printable front."
                    ),
                },
                    "design_review": {
                        "reviewed_candidate_sha256": candidate_hash,
                        "hero_anchor_index": 0,
                        "hero_anchor_id": "example-registration-grid",
                        "anchor_system_consequences": [
                            {
                                "anchor_id": "example-registration-grid",
                                "system": "typography_or_counterform",
                                "effect": "Registration geometry controls the focal counterform.",
                            },
                            {
                                "anchor_id": "example-registration-grid",
                                "system": "container_or_perimeter",
                                "effect": "Registration geometry continues into the perimeter rhythm.",
                            },
                        ],
                        "full_resolution_reviewed": True,
                        "text_off_anchor_recognizable": True,
                        "identity_swap_requires_redesign": True,
                        "anchor_drives_primary_composition": True,
                        "composition_resolved": True,
                        "visual_grammar_consistent": True,
                        "finish_target_met": True,
                        "production_reduction_preserves_authorship": True,
                        "structural_thesis": (
                            "The catalog registration field controls the whole circle, locks "
                            "into the focal-length counterform, and reaches the perimeter."
                        ),
                        "finish_target_note": (
                            "The finish target requires resolved negative space, consistent "
                            "weights and alignments, and no arbitrary filler panels."
                        ),
                        "quality_reference_checks": [],
                        "reviewer_note": (
                            "At full resolution the test catalog field controls type and "
                            "perimeter, keeps its topology after printable reduction, and a "
                            "neighbouring identity requires a different primary structure."
                        ),
                    },
                    "physical_fit": {
                            "measured_diameter_mm": 52.0,
                            "face_target_mm": 52.0,
                        "foam_liner_status": "none",
                        "liner_material": None,
                        "liner_thickness_mm": None,
                        "compression_fraction": 0.0,
                        "compression_is_assumption": False,
                    "wall_thickness_mm": 2.4,
                    "bottom_thickness_mm": 2.0,
                    "side_height_mm": 14.0,
                    "bare_clearance_mm": 0.4,
                    "friction_ribs_enabled": True,
                        "friction_ribs_explicit": False,
                        "friction_rib_profile": "light_tapered",
                        "friction_rib_profile_derived": False,
                        "friction_rib_profile_reference_cavity_mm": None,
                    "friction_rib_count": 12,
                    "friction_rib_protrusion_mm": 0.1,
                    "friction_rib_width_mm": 1.2,
                    "friction_rib_height_mm": 8.0,
                        "friction_rib_start_mm": 1.0,
                            "retention_strategy": "auto",
                        "nozzle_mm": 0.2,
                        "adapter_nominal_ring_mm": None,
                        "adapter_radial_wall_mm": None,
                        "adapter_derived_mating_diameter_mm": None,
                    },
                    "job_binding": {
                        "metadata_lens_identity": "Example Prime 50mm F1.4",
                        "circle": {
                            "center_px": [1.0, 1.0],
                            "radius_px": 1.0,
                            "allow_outside": False,
                        },
                        "palette": {
                            "black": {
                                "index": 0,
                                "rgb": [0, 0, 0],
                                "role": "base",
                                "height_mm": 0.0,
                                "required": True,
                            },
                            "white": {
                                "index": 1,
                                "rgb": [255, 255, 255],
                                "role": "relief",
                                "height_mm": 0.4,
                                "required": False,
                            },
                        },
                        "artwork_process": {
                            "grid_size": 64,
                            "safe_border_mm": 0.0,
                            "prefilter": {"name": "median", "size": 5, "radius": 0.8},
                            "cleanup": {
                                "enabled": True,
                                "max_area_px": 8,
                                "max_dimension_px": 3,
                                "ring_px": 2,
                                "dominance": 0.6,
                                "apply_to": ["relief"],
                            },
                            "assembly_mode": "auto",
                        },
                    },
                "provenance": {
                    "artwork_license": "test fixture",
                    "brand_mark_license": "No brand mark is used in this fictional fixture",
                    "film_or_history_permissions": "No protected film or history imagery is used",
                    "notes": "Test-only reviewed provenance for the missing-tool boundary.",
                },
            }
        ),
        encoding="utf-8",
    )
    result = bridge.main([str(config), "--openscad", str(tmp_path / "missing-openscad"), "--json"])
    assert result == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "unverifiable"
    assert "OpenSCAD" in payload["reason"]
    assert not (tmp_path / "build").exists()


def test_bridge_rejects_a_bare_toml_png_before_tool_checks(tmp_path: Path, capsys) -> None:
    source = tmp_path / "master.png"
    source.write_bytes(b"not-an-image")
    config = tmp_path / "job.toml"
    config.write_text(
        "\n".join(
            [
                'job_slug = "no-brief"',
                'source_art = "master.png"',
                "measured_diameter_mm = 52.0",
                "grid_size = 64",
                "[circle]",
                "center_px = [1, 1]",
                "radius_px = 1",
                "[palette.black]",
                "index = 0",
                "rgb = [0, 0, 0]",
                'role = "base"',
                "height_mm = 0.0",
                "[palette.white]",
                "index = 1",
                "rgb = [255, 255, 255]",
                'role = "relief"',
                "height_mm = 0.4",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    result = bridge.main([str(config), "--openscad", str(tmp_path / "missing"), "--json"])
    assert result == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "failed"
    assert payload["failure_class"] == "validation_failed"
    assert "design brief gate failed" in payload["reason"]
    assert not (tmp_path / "build").exists()


def test_bridge_fails_closed_when_design_brief_is_missing(tmp_path: Path, capsys) -> None:
    source = tmp_path / "master.png"
    source.write_bytes(b"not-an-image")
    config = tmp_path / "job.toml"
    config.write_text(
        "\n".join(
            [
                'job_slug = "brief-gate-test"',
                'source_art = "master.png"',
                "measured_diameter_mm = 52.0",
                "grid_size = 64",
                "[circle]",
                "center_px = [1, 1]",
                "radius_px = 1",
                "[palette.black]",
                "index = 0",
                "rgb = [0, 0, 0]",
                'role = "base"',
                "height_mm = 0.0",
                "[palette.white]",
                "index = 1",
                "rgb = [255, 255, 255]",
                'role = "relief"',
                "height_mm = 0.4",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    openscad = tmp_path / "openscad"
    openscad.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    openscad.chmod(0o755)
    result = bridge.main([str(config), "--openscad", str(openscad), "--json"])
    assert result == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "failed"
    assert payload["failure_class"] == "validation_failed"
    assert "design brief gate failed" in payload["reason"]
    assert not (tmp_path / "build").exists()


def test_requested_bambu_is_preflighted_before_native_outputs(tmp_path: Path, capsys) -> None:
    fixture = tmp_path / "fixture"
    shutil.copytree(
        ROOT / "examples/fixtures/helios-44-2-rehouse-imagegen-v3",
        fixture,
        ignore=shutil.ignore_patterns("out"),
    )
    job = fixture / "jobs/95mm/job.toml"
    openscad = tmp_path / "openscad"
    openscad.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    openscad.chmod(0o755)
    result = bridge.main(
        [
            str(job),
            "--openscad",
            str(openscad),
            "--bambu",
            "export",
            "--bambu-path",
            str(tmp_path / "missing-bambu"),
            "--json",
        ]
    )
    assert result == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "unverifiable"
    assert "Bambu Studio" in payload["reason"]
    assert not (fixture / "jobs/95mm/out").exists()


def test_force_invalidates_explicit_old_report_before_parsing_bad_job(
    tmp_path: Path, capsys
) -> None:
    config = tmp_path / "broken-job.toml"
    config.write_text('job_slug = "unterminated\n', encoding="utf-8")
    report = tmp_path / "release.json"
    report.write_text('{"status":"passed","stale":true}\n', encoding="utf-8")

    result = bridge.main(
        [
            str(config),
            "--force",
            "--native-output",
            str(tmp_path / "cap.3mf"),
            "--report",
            str(report),
            "--json",
        ]
    )

    assert result == 1
    emitted = json.loads(capsys.readouterr().out)
    retained = json.loads(report.read_text(encoding="utf-8"))
    assert emitted["status"] == "failed"
    assert emitted["failure_class"] == "validation_failed"
    assert retained["status"] == "failed"
    assert retained["failure_class"] == "validation_failed"
    assert retained.get("stale") is None
    assert retained["job_sha256"] == hashlib.sha256(config.read_bytes()).hexdigest()


def test_manifest_declares_bridge() -> None:
    manifest = json.loads((Path(__file__).parents[1] / "skills" / "manifest.json").read_text(encoding="utf-8"))
    distribution = manifest["distribution"]
    assert distribution["production_entrypoint"] == "scripts/build_3mf.py"
    assert distribution["production_alias"] == "bin/lens-cap-3mf"


def test_design_brief_gate_binds_fixture_artwork_and_identity() -> None:
    config = load_config(
        ROOT / "examples/fixtures/helios-44-2-rehouse/jobs/95mm/job.toml"
    )
    report = validate_design_brief(config)
    assert report["status"] == "passed"
    assert report["identity"]["focal_length_mm"] == 58.0
    assert report["identity"]["maximum_aperture"] == "F2"
    assert len(report["candidate_sha256"]) == 64
    assert "foam_liner_status" in report["physical_fit_checked"]
    # The shared multi-diameter brief deliberately leaves these variant
    # values null; the active TOML remains authoritative for them.
    assert "measured_diameter_mm" not in report["physical_fit_checked"]


def test_design_brief_gate_rejects_declared_physical_drift(tmp_path: Path) -> None:
    fixture = tmp_path / "fixture"
    shutil.copytree(
        ROOT / "examples/fixtures/helios-44-2-rehouse-imagegen-v3",
        fixture,
    )
    brief_path = fixture / "design-brief.json"
    brief = json.loads(brief_path.read_text(encoding="utf-8"))
    brief["physical_fit"]["measured_diameter_mm"] = 94.0
    brief_path.write_text(json.dumps(brief, ensure_ascii=False), encoding="utf-8")
    config = load_config(fixture / "jobs/95mm/job.toml")
    with pytest.raises(BriefError, match="measured_diameter_mm disagrees"):
        validate_design_brief(config)


def test_design_brief_gate_rejects_unrecorded_physical_intake(tmp_path: Path) -> None:
    fixture = tmp_path / "fixture"
    shutil.copytree(
        ROOT / "examples/fixtures/helios-44-2-rehouse-imagegen-v3",
        fixture,
    )
    job_path = fixture / "jobs/95mm/job.toml"
    text = job_path.read_text(encoding="utf-8")
    for line in (
        'foam_liner_status = "none"\n',
        "friction_ribs_enabled = true\n",
        "friction_ribs_explicit = false\n",
    ):
        text = text.replace(line, "")
    job_path.write_text(text, encoding="utf-8")
    config = load_config(job_path)
    with pytest.raises(BriefError, match="recorded physical-intake fields"):
        validate_design_brief(config)


def test_design_brief_gate_binds_optional_job_identity(tmp_path: Path) -> None:
    fixture = tmp_path / "fixture"
    shutil.copytree(
        ROOT / "examples/fixtures/helios-44-2-rehouse-imagegen-v3",
        fixture,
    )
    brief_path = fixture / "design-brief.json"
    brief = json.loads(brief_path.read_text(encoding="utf-8"))
    brief["job_binding"] = {"metadata_lens_identity": "Different lens"}
    brief_path.write_text(json.dumps(brief, ensure_ascii=False), encoding="utf-8")
    config = load_config(fixture / "jobs/95mm/job.toml")
    with pytest.raises(BriefError, match="metadata.lens_identity disagrees"):
        validate_design_brief(config)


def test_design_brief_gate_rejects_missing_or_unapproved_brief(tmp_path: Path) -> None:
    config = load_config(
        ROOT / "examples/fixtures/helios-44-2-rehouse/jobs/95mm/job.toml"
    )
    missing = tmp_path / "missing.json"
    try:
        validate_design_brief(config, missing)
    except BriefError as exc:
        assert "not found" in str(exc)
    else:  # pragma: no cover - assertion gives a clearer failure than pytest.raises here
        raise AssertionError("missing brief unexpectedly passed")


def test_retained_native_3mf_contains_all_integrated_rib_positions(tmp_path: Path) -> None:
    package = (
        ROOT
        / "examples/fixtures/helios-44-2-rehouse-imagegen-v3/artifacts"
        / "helios-44-2-rehouse-imagegen-v3-95mm-native.3mf"
    )
    mechanical = {
        "friction_ribs_enabled": True,
        "friction_rib_count": 12,
        "cavity_diameter_mm": 95.4,
        "friction_rib_protrusion_mm": 0.1,
        "friction_rib_start_mm": 1.0,
        "friction_rib_height_mm": 8.0,
        "friction_rib_tip_angle_deg": 0.792771,
        "nozzle_mm": 0.2,
    }
    report = bridge._audit_integrated_ribs(package, mechanical)
    assert report["status"] == "passed"
    assert report["detected_start_positions"] == 12
    assert report["detected_end_positions"] == 12
    assert report["scope"] == (
        "referenced_connected_full_width_tip_faces_full_height_axial_contact_columns_"
        "and_start_interior_end_wall_cross_sections_physical_fit_requires_coupon"
    )
    assert len(report["interior_cross_section_z_mm"]) == 3
    assert all(
        hits == 15
        for position in report["cross_section_sample_hits"]
        for hits in position["interior"]
    )
    assert report["integrated_vertical_edge_positions"] == 12
    assert report["minimum_full_height_tip_face_width_mm"] >= 0.59
    assert all(
        item["continuous_columns"] == 15
        for item in report["axial_continuity_sample_hits"]
    )
    assert report["unused_vertices"] == 0

    wrong_count = {**mechanical, "friction_rib_count": 13}
    with pytest.raises(bridge.ReleaseError, match="friction-rib mesh audit failed"):
        bridge._audit_integrated_ribs(package, wrong_count)

    config = load_config(
        ROOT / "examples/fixtures/helios-44-2-rehouse-imagegen-v3/jobs/95mm/job.toml"
    )
    materials = bridge._audit_material_assignments(package, config.palette)
    assert materials["status"] == "passed"
    assert materials["unexpected_used_colors"] == []
    assert set(materials["colors"]) == {"black", "gray", "ivory"}
    assert all(item["triangle_count"] > 0 for item in materials["colors"].values())

    optional_palette = tuple(
        type(entry)(
            entry.name,
            entry.index,
            entry.rgb,
            entry.role,
            entry.height_mm,
            False if entry.name == "ivory" else entry.required,
            entry.detector,
        )
        for entry in config.palette
    )
    with pytest.raises(bridge.ReleaseError, match="inactive zero-pixel"):
        bridge._audit_material_assignments(
            package,
            optional_palette,
            expected_footprint_mm2={"black": 100.0, "gray": 20.0, "ivory": 0.0},
        )

    # The OpenSCAD table carries an unused default yellow. Turning even one
    # triangle to that material must fail the closed current-job palette gate.
    tampered = tmp_path / "unexpected-color.3mf"
    with zipfile.ZipFile(package) as source, zipfile.ZipFile(tampered, "w") as target:
        for info in source.infolist():
            content = source.read(info.filename)
            if info.filename == "3D/3dmodel.model":
                content = content.replace(b'p1="1"', b'p1="0"', 1)
            target.writestr(info, content)
    with pytest.raises(bridge.ReleaseError, match="outside the active job palette"):
        bridge._audit_material_assignments(tampered, config.palette)

    mixed = tmp_path / "single-mixed-proof.3mf"
    with zipfile.ZipFile(package) as source, zipfile.ZipFile(mixed, "w") as target:
        for info in source.infolist():
            content = source.read(info.filename)
            if info.filename == "3D/3dmodel.model":
                root = ET.fromstring(content)
                triangle = next(
                    node
                    for node in root.iter()
                    if node.tag.rsplit("}", 1)[-1] == "triangle"
                )
                triangle.set("p2", "2")
                triangle.set("p3", "3")
                content = ET.tostring(root, encoding="utf-8", xml_declaration=True)
            target.writestr(info, content)
    with pytest.raises(bridge.ReleaseError, match="mixed-property triangle"):
        bridge._audit_material_assignments(mixed, config.palette)

    negative = tmp_path / "negative-material-index.3mf"
    with zipfile.ZipFile(package) as source, zipfile.ZipFile(negative, "w") as target:
        for info in source.infolist():
            content = source.read(info.filename)
            if info.filename == "3D/3dmodel.model":
                root = ET.fromstring(content)
                triangle = next(
                    node
                    for node in root.iter()
                    if node.tag.rsplit("}", 1)[-1] == "triangle"
                    and "p1" in node.attrib
                )
                triangle.set("p1", "-1")
                content = ET.tostring(root, encoding="utf-8", xml_declaration=True)
            target.writestr(info, content)
    with pytest.raises(bridge.ReleaseError, match="malformed or negative"):
        bridge._audit_material_assignments(negative, config.palette)


def test_material_spatial_audit_rejects_relief_color_on_body_bottom(
    tmp_path: Path,
) -> None:
    fixture = tmp_path / "fixture"
    shutil.copytree(
        ROOT / "examples/fixtures/helios-44-2-rehouse-imagegen-v3",
        fixture,
        ignore=shutil.ignore_patterns("out", "artifacts"),
    )
    config = load_config(fixture / "jobs/95mm/job.toml")
    process(config)
    package = (
        ROOT
        / "examples/fixtures/helios-44-2-rehouse-imagegen-v3/artifacts"
        / "helios-44-2-rehouse-imagegen-v3-95mm-native.3mf"
    )
    tampered = tmp_path / "wrong-bottom-color.3mf"
    with zipfile.ZipFile(package) as source, zipfile.ZipFile(tampered, "w") as target:
        for info in source.infolist():
            content = source.read(info.filename)
            if info.filename == "3D/3dmodel.model":
                root = ET.fromstring(content)
                gray_group = None
                gray_index = None
                for group in root.iter():
                    if group.tag.rsplit("}", 1)[-1] != "basematerials":
                        continue
                    colors = [
                        str(node.attrib.get("displaycolor", "")).upper()
                        for node in group
                        if node.tag.rsplit("}", 1)[-1] == "base"
                    ]
                    if "#747471FF" in colors:
                        gray_group = group.attrib["id"]
                        gray_index = colors.index("#747471FF")
                        break
                assert gray_group is not None and gray_index is not None
                changed = False
                for obj in root.iter():
                    if obj.tag.rsplit("}", 1)[-1] != "object":
                        continue
                    vertices = [
                        (
                            float(node.attrib["x"]),
                            float(node.attrib["y"]),
                            float(node.attrib["z"]),
                        )
                        for node in obj.iter()
                        if node.tag.rsplit("}", 1)[-1] == "vertex"
                    ]
                    for triangle in obj.iter():
                        if triangle.tag.rsplit("}", 1)[-1] != "triangle":
                            continue
                        indices = [int(triangle.attrib[name]) for name in ("v1", "v2", "v3")]
                        if all(abs(vertices[index][2]) <= 1e-9 for index in indices):
                            triangle.set("pid", gray_group)
                            for field in ("p1", "p2", "p3"):
                                triangle.set(field, str(gray_index))
                            changed = True
                            break
                    if changed:
                        break
                assert changed
                content = ET.tostring(root, encoding="utf-8", xml_declaration=True)
            target.writestr(info, content)

    release = json.loads(
        (
            ROOT
            / "examples/fixtures/helios-44-2-rehouse-imagegen-v3/artifacts"
            / "helios-44-2-rehouse-imagegen-v3-95mm-3mf-release.json"
        ).read_text(encoding="utf-8")
    )
    top_z = {
        name: details["expected_top_z_mm"]
        for name, details in release["material_assignment_audit"][
            "spatial_mask_binding"
        ].items()
    }
    with pytest.raises(bridge.ReleaseError, match="selector envelope"):
        bridge._audit_material_assignments(
            tampered,
            config.palette,
            expected_footprint_mm2=bridge._expected_palette_footprints(config),
            expected_masks={
                name: config.output_dir / "masks" / f"{name}.png" for name in top_z
            },
            expected_top_z_mm=top_z,
            canvas_size_mm=config.face_diameter_mm,
        )


def test_rib_audit_rejects_unreferenced_coordinate_markers(tmp_path: Path) -> None:
    source_path = (
        ROOT
        / "examples/fixtures/helios-44-2-rehouse-imagegen-v3/artifacts"
        / "helios-44-2-rehouse-imagegen-v3-95mm-native.3mf"
    )
    tampered = tmp_path / "marker-spoof.3mf"
    with zipfile.ZipFile(source_path) as source, zipfile.ZipFile(tampered, "w") as target:
        for info in source.infolist():
            payload = source.read(info.filename)
            if info.filename == "3D/3dmodel.model":
                root = ET.fromstring(payload)
                vertices_node = next(
                    node
                    for node in root.iter()
                    if node.tag.rsplit("}", 1)[-1] == "vertices"
                )
                vertex_tag = next(iter(vertices_node)).tag
                for vertex in vertices_node:
                    x = float(vertex.attrib["x"])
                    y = float(vertex.attrib["y"])
                    z = float(vertex.attrib["z"])
                    radius = math.hypot(x, y)
                    if abs(radius - 47.6) <= 0.03 and (
                        abs(z - 1.0) <= 0.03 or abs(z - 9.0) <= 0.03
                    ):
                        scale = 47.7 / radius
                        vertex.set("x", f"{x * scale:.9f}")
                        vertex.set("y", f"{y * scale:.9f}")
                for position in range(12):
                    angle = math.radians(position * 30.0)
                    for z in (1.0, 9.0):
                        ET.SubElement(
                            vertices_node,
                            vertex_tag,
                            {
                                "x": f"{47.6 * math.cos(angle):.9f}",
                                "y": f"{47.6 * math.sin(angle):.9f}",
                                "z": f"{z:.9f}",
                            },
                        )
                payload = ET.tostring(root, encoding="utf-8", xml_declaration=True)
            target.writestr(info, payload)

    mechanical = {
        "friction_ribs_enabled": True,
        "friction_rib_count": 12,
        "cavity_diameter_mm": 95.4,
        "friction_rib_protrusion_mm": 0.1,
        "friction_rib_start_mm": 1.0,
        "friction_rib_height_mm": 8.0,
        "friction_rib_tip_angle_deg": 0.792771,
        "nozzle_mm": 0.2,
    }
    with pytest.raises(bridge.ReleaseError, match="mesh audit failed"):
        bridge._audit_integrated_ribs(tampered, mechanical)


def _write_hollow_frame_rib_spoof(path: Path) -> None:
    """Write ribs that are filled only at both ends, not through their height.

    This deliberately satisfies the historical endpoint/vertical-edge checks:
    each nominal rib has a complete wedge at z=1 and z=9, broad tip and wall
    edges, and two full-height side rails.  Its middle is hollow, so it is not
    a printable contact rib and must fail the interior slice proof.
    """

    namespace = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
    model = ET.Element(f"{{{namespace}}}model", {"unit": "millimeter"})
    resources = ET.SubElement(model, f"{{{namespace}}}resources")
    obj = ET.SubElement(resources, f"{{{namespace}}}object", {"id": "1", "type": "model"})
    mesh = ET.SubElement(obj, f"{{{namespace}}}mesh")
    vertices_node = ET.SubElement(mesh, f"{{{namespace}}}vertices")
    triangles_node = ET.SubElement(mesh, f"{{{namespace}}}triangles")
    vertices: list[tuple[float, float, float]] = []
    triangles: list[tuple[int, int, int]] = []

    def add_vertex(radius: float, angle_deg: float, z: float) -> int:
        angle = math.radians(angle_deg)
        vertices.append((radius * math.cos(angle), radius * math.sin(angle), z))
        return len(vertices) - 1

    start_center = add_vertex(0.0, 0.0, 1.0)
    end_center = add_vertex(0.0, 0.0, 9.0)
    tip_radius = 47.6
    wall_radius = 47.7
    half_angle = 0.792771 / 2.0
    for position in range(3):
        centre_angle = position * 120.0
        start_tip_left = add_vertex(tip_radius, centre_angle - half_angle, 1.0)
        start_tip_right = add_vertex(tip_radius, centre_angle + half_angle, 1.0)
        start_wall_left = add_vertex(wall_radius, centre_angle - half_angle, 1.0)
        start_wall_right = add_vertex(wall_radius, centre_angle + half_angle, 1.0)
        end_tip_left = add_vertex(tip_radius, centre_angle - half_angle, 9.0)
        end_tip_right = add_vertex(tip_radius, centre_angle + half_angle, 9.0)
        end_wall_left = add_vertex(wall_radius, centre_angle - half_angle, 9.0)
        end_wall_right = add_vertex(wall_radius, centre_angle + half_angle, 9.0)
        # Filled nominal contact patches at both ends.
        triangles.extend(
            (
                (start_tip_left, start_wall_left, start_wall_right),
                (start_tip_left, start_wall_right, start_tip_right),
                (end_tip_left, end_wall_right, end_wall_left),
                (end_tip_left, end_tip_right, end_wall_right),
                # Endpoint-only spokes make every rib one connected component.
                (start_center, start_wall_right, start_wall_left),
                (end_center, end_wall_left, end_wall_right),
                # Full-height boundary rails retain direct tip edges and wall
                # adjacency, but leave the middle of the wedge unfilled.
                (start_tip_left, end_tip_left, end_wall_left),
                (start_tip_left, end_wall_left, start_wall_left),
                (start_tip_right, start_wall_right, end_wall_right),
                (start_tip_right, end_wall_right, end_tip_right),
            )
        )

    for x, y, z in vertices:
        ET.SubElement(
            vertices_node,
            f"{{{namespace}}}vertex",
            {"x": f"{x:.9f}", "y": f"{y:.9f}", "z": f"{z:.9f}"},
        )
    for left, middle, right in triangles:
        ET.SubElement(
            triangles_node,
            f"{{{namespace}}}triangle",
            {"v1": str(left), "v2": str(middle), "v3": str(right)},
        )
    build = ET.SubElement(model, f"{{{namespace}}}build")
    ET.SubElement(build, f"{{{namespace}}}item", {"objectid": "1"})
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "3D/3dmodel.model",
            ET.tostring(model, encoding="utf-8", xml_declaration=True),
        )


def test_rib_audit_rejects_endpoint_only_hollow_frame(tmp_path: Path) -> None:
    package = tmp_path / "endpoint-only-frame.3mf"
    _write_hollow_frame_rib_spoof(package)
    mechanical = {
        "friction_ribs_enabled": True,
        "friction_rib_count": 3,
        "cavity_diameter_mm": 95.4,
        "friction_rib_protrusion_mm": 0.1,
        "friction_rib_start_mm": 1.0,
        "friction_rib_height_mm": 8.0,
        "friction_rib_tip_angle_deg": 0.792771,
        "nozzle_mm": 0.2,
    }
    with pytest.raises(bridge.ReleaseError, match="detected 0"):
        bridge._audit_integrated_ribs(package, mechanical)
