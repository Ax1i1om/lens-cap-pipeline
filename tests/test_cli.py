from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from lens_cap_pipeline.cli import main
from lens_cap_pipeline.config import load_config


def _approve_design_brief(path: Path) -> dict:
    """Replace every human-review placeholder needed by the strict gate."""

    brief = json.loads(path.read_text(encoding="utf-8"))
    brief["generation"]["approved"] = True
    brief["generation"]["approval_note"] = (
        "Human reviewer confirmed the exact text, hierarchy, circular composition, and source scope."
    )
    brief["design_review"].update(
        {
            "reviewed_candidate_sha256": brief["generation"]["candidate_sha256"],
            "hero_anchor_index": 0,
            "hero_anchor_id": "anchor-1",
            "anchor_system_consequences": [
                {
                    "anchor_id": "anchor-1",
                    "system": "typography_or_counterform",
                    "effect": "The system grid controls the focal-length counterform and spacing.",
                },
                {
                    "anchor_id": "anchor-1",
                    "system": "container_or_perimeter",
                    "effect": "The same grid resolves into the circular perimeter rhythm.",
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
                "The sourced system grid organizes the full circular field, locks into "
                "the focal-length counterform, and continues through the perimeter rhythm."
            ),
            "finish_target_note": (
                "The approved finish floor requires deliberate negative space, systematic "
                "weights and alignments, and no provisional or filler regions."
            ),
            "quality_reference_checks": [],
            "reviewer_note": (
                "At full resolution, the sourced system geometry controls type, field, and "
                "perimeter; its visual grammar is resolved, the printable reduction keeps "
                "that topology, and a neighbouring lens would require structural redesign."
            ),
        }
    )
    brief["anchors"][0].update(
        {
            "anchor_id": "anchor-1",
            "evidence_state": "verified_from_cited_source",
            "summary": "The cited manufacturer catalog identifies this lens and system.",
            "anchor_context": "Manufacturer-system evidence only; no film association is claimed.",
            "anchor_visual_motif": "Original modular geometry derived from the documented system context.",
            "recognition_cue": "The ordered model text and modular system grid remain recognizable.",
            "motif_commitment": "structural",
            "qualifier": "Manufacturer catalog evidence; visual geometry is original.",
        }
    )
    brief["provenance"].update(
        {
            "artwork_license": "Human-approved original artwork for this job.",
            "brand_mark_license": "Text identification only; no copied logo artwork.",
            "film_or_history_permissions": "No film material used; cited catalog facts only.",
            "notes": "Third-party marks and sources remain separate from the code licence.",
        }
    )
    path.write_text(json.dumps(brief, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return brief


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


def test_handoff_init_writes_hash_and_reviewable_circle_scaffold(tmp_path: Path, capsys) -> None:
    source = tmp_path / "master.png"
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    ImageDraw.Draw(image).ellipse((4, 4, 59, 59), fill=(17, 18, 17, 255))
    image.save(source)
    config_path = tmp_path / "job.toml"
    assert main(
        [
            "init",
            str(config_path),
            "--source",
            str(source),
            "--measured-diameter",
            "95",
            "--lens-identity",
            "Example Prime 50mm F1.4",
            "--display-text",
            "50",
            "F1.4",
        ]
    ) == 0
    assert main(
        [
            "handoff-init",
            str(config_path),
            "--brand",
            "Example",
            "--model",
            "Prime 50mm F1.4",
            "--focal-length",
            "50",
            "--maximum-aperture",
            "F1.4",
            "--provider",
            "test-image-provider",
            "--anchor-source",
            "https://example.test/catalog",
        ]
    ) == 0
    brief = json.loads((tmp_path / "design-brief.json").read_text(encoding="utf-8"))
    assert brief["schema_version"] == 2
    assert brief["generation"]["approved"] is False
    assert brief["design_review"] == {
        "reviewed_candidate_sha256": None,
        "hero_anchor_index": None,
        "hero_anchor_id": None,
        "anchor_system_consequences": [],
        "full_resolution_reviewed": False,
        "text_off_anchor_recognizable": False,
        "identity_swap_requires_redesign": False,
        "anchor_drives_primary_composition": False,
        "composition_resolved": False,
        "visual_grammar_consistent": False,
        "finish_target_met": False,
        "production_reduction_preserves_authorship": False,
        "structural_thesis": None,
        "finish_target_note": None,
        "quality_reference_checks": [],
        "reviewer_note": None,
    }
    assert len(brief["generation"]["candidate_sha256"]) == 64
    assert brief["circle_suggestion"]["method"] == "alpha_bbox_suggestion_review_required"
    assert brief["physical_fit"]["measured_diameter_mm"] == 95.0
    assert brief["anchors"][0]["anchor_id"] == "anchor-1"
    assert brief["production_target"] == "printable_front"


def test_handoff_init_keeps_custom_brief_path_in_follow_up_commands(tmp_path: Path, capsys) -> None:
    source = tmp_path / "master.png"
    image = Image.new("RGB", (64, 64), (17, 18, 17))
    image.save(source)
    config_path = tmp_path / "jobs" / "demo" / "job.toml"
    assert main(
        [
            "init",
            str(config_path),
            "--source",
            str(source),
            "--measured-diameter",
            "95",
            "--lens-identity",
            "Example Prime 50mm F1.4",
            "--display-text",
            "50",
            "F1.4",
        ]
    ) == 0
    capsys.readouterr()
    brief_path = tmp_path / "handoffs" / "reviewed-brief.json"
    assert main(
        [
            "handoff-init",
            str(config_path),
            "--brief",
            str(brief_path),
            "--brand",
            "Example",
            "--model",
            "Prime",
            "--focal-length",
            "50",
            "--maximum-aperture",
            "F1.4",
            "--provider",
            "test-image-provider",
        ]
    ) == 0
    report = json.loads(capsys.readouterr().out)
    assert brief_path.is_file()
    assert all(str(brief_path) in step for step in report["next"][-2:])
    assert "--brief" in report["next"][-1]


def test_handoff_init_marks_face_only_relief_as_printable_front(
    tmp_path: Path, capsys
) -> None:
    source = tmp_path / "master.png"
    Image.new("RGB", (64, 64), (17, 18, 17)).save(source)
    config_path = tmp_path / "job.toml"
    assert main(
        [
            "init",
            str(config_path),
            "--source",
            str(source),
            "--face-diameter",
            "95",
            "--lens-identity",
            "Example Prime 50mm F1.4",
            "--display-text",
            "50",
            "F1.4",
        ]
    ) == 0
    assert main(
        [
            "handoff-init",
            str(config_path),
            "--brand",
            "Example",
            "--model",
            "Prime",
            "--focal-length",
            "50",
            "--maximum-aperture",
            "F1.4",
            "--provider",
            "test-image-provider",
        ]
    ) == 0
    brief = json.loads((tmp_path / "design-brief.json").read_text(encoding="utf-8"))
    assert brief["production_target"] == "printable_front"
    assert brief["physical_fit"]["face_target_mm"] == 95.0
    assert brief["physical_fit"]["measured_diameter_mm"] is None


def test_handoff_check_rejects_unapproved_scaffold(tmp_path: Path, capsys) -> None:
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
            "--measured-diameter",
            "95",
            "--lens-identity",
            "Example Prime 50mm F1.4",
            "--display-text",
            "50",
            "F1.4",
        ]
    ) == 0
    assert main(
        [
            "handoff-init",
            str(config_path),
            "--brand",
            "Example",
            "--model",
            "Prime",
            "--focal-length",
            "50",
            "--maximum-aperture",
            "F1.4",
            "--provider",
            "test-image-provider",
        ]
    ) == 0
    assert main(["handoff-check", str(config_path), "--json"]) == 2
    assert "approved" in capsys.readouterr().err


def test_init_to_approved_handoff_round_trips_identity_and_closed_text(
    tmp_path: Path, capsys
) -> None:
    """A fresh CLI user can reach the strict handoff gate without hidden edits."""

    source = tmp_path / "jobs" / "mamiya" / "art" / "master.png"
    source.parent.mkdir(parents=True)
    image = Image.new("RGBA", (96, 96), (0, 0, 0, 0))
    ImageDraw.Draw(image).ellipse((6, 6, 89, 89), fill=(17, 18, 17, 255))
    image.save(source)
    config_path = tmp_path / "jobs" / "mamiya" / "job.toml"
    display_text = ["80", "F1.9", "MAMIYA", "SEKOR C", "645 SYSTEM"]

    assert main(
        [
            "init",
            str(config_path),
            "--source",
            str(source),
            "--measured-diameter",
            "85",
            "--adapter-nominal-ring",
            "80",
            "--adapter-radial-wall",
            "2.5",
            "--foam-thickness",
            "1.5",
            "--job-slug",
            "mamiya-sekor-c-80-f1-9-cap",
            "--lens-identity",
            "Mamiya-Sekor C 80mm F1.9",
            "--display-text",
            *display_text,
        ]
    ) == 0
    config = load_config(config_path)
    assert config.metadata["lens_identity"] == "Mamiya-Sekor C 80mm F1.9"
    assert config.metadata["display_text"] == display_text

    capsys.readouterr()
    assert main(
        [
            "handoff-init",
            str(config_path),
            "--brand",
            "Mamiya",
            "--model",
            "Mamiya-Sekor C 80mm F1.9",
            "--focal-length",
            "80",
            "--maximum-aperture",
            "F1.9",
            "--provider",
            "test-image-provider",
            "--anchor-source",
            "https://example.test/mamiya-m645-catalog",
        ]
    ) == 0
    handoff_report = json.loads(capsys.readouterr().out)
    assert any("evidence_state" in item for item in handoff_report["next"])
    assert any("licence" in item.casefold() for item in handoff_report["next"])
    assert any("display_text" in item for item in handoff_report["next"])

    brief_path = tmp_path / "jobs" / "mamiya" / "design-brief.json"
    brief = json.loads(brief_path.read_text(encoding="utf-8"))
    assert brief["display_text"] == display_text
    assert brief["allowed_text"] == display_text
    assert brief["job_binding"]["metadata_lens_identity"] == "Mamiya-Sekor C 80mm F1.9"
    _approve_design_brief(brief_path)

    assert main(["handoff-check", str(config_path), "--json"]) == 0
    check = json.loads(capsys.readouterr().out)
    assert check["status"] == "passed"
    assert check["display_text"] == display_text
    assert check["job_identity_binding_checked"] is True
    assert check["job_circle_binding_checked"] is True
    assert check["job_palette_binding_checked"] is True
    assert check["job_artwork_process_binding_checked"] is True
    assert "adapter_derived_mating_diameter_mm" in check["physical_fit_checked"]


def test_handoff_init_rejects_display_identity_disagreement(tmp_path: Path, capsys) -> None:
    source = tmp_path / "master.png"
    Image.new("RGB", (64, 64), (17, 18, 17)).save(source)
    config_path = tmp_path / "job.toml"
    assert main(
        [
            "init",
            str(config_path),
            "--source",
            str(source),
            "--measured-diameter",
            "52",
            "--lens-identity",
            "Zeiss Planar 50mm F1.4",
            "--display-text",
            "50",
            "F1.4",
            "PLANAR",
        ]
    ) == 0
    capsys.readouterr()
    assert main(
        [
            "handoff-init",
            str(config_path),
            "--brand",
            "Zeiss",
            "--model",
            "Planar 50mm F1.4",
            "--focal-length",
            "58",
            "--maximum-aperture",
            "F1.4",
            "--provider",
            "test-image-provider",
        ]
    ) == 2
    assert "metadata.display_text[0]" in capsys.readouterr().err
    assert not (tmp_path / "design-brief.json").exists()


@pytest.mark.parametrize(
    "aperture_args",
    [
        ["--maximum-aperture", "F3.5-5.6"],
        [
            "--maximum-aperture",
            "F3.5",
            "--maximum-aperture-display",
            "F3.5–5.6",
        ],
    ],
)
def test_variable_aperture_handoff_preserves_full_second_read(
    tmp_path: Path, capsys, aperture_args: list[str]
) -> None:
    source = tmp_path / "master.png"
    Image.new("RGB", (64, 64), (17, 18, 17)).save(source)
    config_path = tmp_path / "job.toml"
    assert main(
        [
            "init",
            str(config_path),
            "--source",
            str(source),
            "--measured-diameter",
            "67",
            "--lens-identity",
            "Sigma Zoom 28-70mm F3.5-5.6",
            "--display-text",
            "28–70",
            "F3.5–5.6",
            "SIGMA ZOOM",
        ]
    ) == 0
    assert main(
        [
            "handoff-init",
            str(config_path),
            "--brand",
            "Sigma",
            "--model",
            "Zoom 28-70mm F3.5-5.6",
            "--focal-length",
            "28",
            "--focal-length-display",
            "28–70mm",
            *aperture_args,
            "--provider",
            "test-image-provider",
            "--anchor-source",
            "https://example.test/sigma-catalog",
        ]
    ) == 0

    brief_path = tmp_path / "design-brief.json"
    brief = json.loads(brief_path.read_text(encoding="utf-8"))
    assert brief["lens_identity"]["maximum_aperture"] == "F3.5"
    assert brief["lens_identity"]["maximum_aperture_display"] == "F3.5-5.6"
    assert brief["display_text"][1] == "F3.5–5.6"
    _approve_design_brief(brief_path)
    capsys.readouterr()

    assert main(["handoff-check", str(config_path), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["identity"]["maximum_aperture"] == "F3.5"
    assert report["identity"]["maximum_aperture_display"] == "F3.5-5.6"


def test_variable_aperture_full_range_cannot_collapse_after_approval(
    tmp_path: Path, capsys
) -> None:
    source = tmp_path / "master.png"
    Image.new("RGB", (64, 64), (17, 18, 17)).save(source)
    config_path = tmp_path / "job.toml"
    assert main(
        [
            "init",
            str(config_path),
            "--source",
            str(source),
            "--measured-diameter",
            "67",
            "--lens-identity",
            "Sigma Zoom 28-70mm F3.5-5.6",
            "--display-text",
            "28-70",
            "F3.5-5.6",
        ]
    ) == 0
    assert main(
        [
            "handoff-init",
            str(config_path),
            "--brand",
            "Sigma",
            "--model",
            "Zoom 28-70mm F3.5-5.6",
            "--focal-length",
            "28",
            "--focal-length-display",
            "28-70",
            "--maximum-aperture",
            "F3.5-5.6",
            "--provider",
            "test-image-provider",
            "--anchor-source",
            "https://example.test/sigma-catalog",
        ]
    ) == 0
    brief_path = tmp_path / "design-brief.json"
    approved = _approve_design_brief(brief_path)
    approved["display_text"][1] = "F3.5"
    approved["allowed_text"][1] = "F3.5"
    brief_path.write_text(json.dumps(approved, indent=2) + "\n", encoding="utf-8")
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            'display_text = ["28-70", "F3.5-5.6"]',
            'display_text = ["28-70", "F3.5"]',
        ),
        encoding="utf-8",
    )
    capsys.readouterr()

    assert main(["handoff-check", str(config_path), "--json"]) == 2
    assert "maximum_aperture_display" in capsys.readouterr().err


def test_handoff_init_rejects_t_stop_until_schema_support_exists(
    tmp_path: Path, capsys
) -> None:
    source = tmp_path / "master.png"
    Image.new("RGB", (64, 64), (17, 18, 17)).save(source)
    config_path = tmp_path / "job.toml"
    assert main(
        [
            "init",
            str(config_path),
            "--source",
            str(source),
            "--measured-diameter",
            "52",
            "--lens-identity",
            "Example Cine 50mm T2.8",
            "--display-text",
            "50",
            "T2.8",
        ]
    ) == 0
    capsys.readouterr()

    assert main(
        [
            "handoff-init",
            str(config_path),
            "--brand",
            "Example",
            "--model",
            "Cine 50mm T2.8",
            "--focal-length",
            "50",
            "--maximum-aperture",
            "T2.8",
            "--provider",
            "test-image-provider",
        ]
    ) == 2
    assert "F-number" in capsys.readouterr().err


def test_handoff_init_requires_provider(tmp_path: Path, capsys) -> None:
    """The shortest documented handoff cannot silently create a null provider."""

    source = tmp_path / "master.png"
    Image.new("RGB", (64, 64), (17, 18, 17)).save(source)
    config_path = tmp_path / "job.toml"
    assert main(
        [
            "init",
            str(config_path),
            "--source",
            str(source),
            "--measured-diameter",
            "52",
            "--lens-identity",
            "Planar 50mm F1.4",
            "--display-text",
            "50",
            "F1.4",
            "PLANAR",
        ]
    ) == 0

    with pytest.raises(SystemExit) as exc_info:
        main(
            [
                "handoff-init",
                str(config_path),
                "--brand",
                "Zeiss",
                "--model",
                "Planar 50mm F1.4",
                "--focal-length",
                "50",
                "--maximum-aperture",
                "F1.4",
            ]
        )

    assert exc_info.value.code == 2
    assert "--provider" in capsys.readouterr().err
    assert not (tmp_path / "design-brief.json").exists()


def test_handoff_check_rejects_secondary_text_drift(tmp_path: Path, capsys) -> None:
    source = tmp_path / "master.png"
    Image.new("RGB", (64, 64), (17, 18, 17)).save(source)
    config_path = tmp_path / "job.toml"
    assert main(
        [
            "init",
            str(config_path),
            "--source",
            str(source),
            "--measured-diameter",
            "52",
            "--lens-identity",
            "Zeiss Planar 50mm F1.4",
            "--display-text",
            "50",
            "F1.4",
            "PLANAR",
        ]
    ) == 0
    assert main(
        [
            "handoff-init",
            str(config_path),
            "--brand",
            "Zeiss",
            "--model",
            "Planar 50mm F1.4",
            "--focal-length",
            "50",
            "--maximum-aperture",
            "F1.4",
            "--provider",
            "test-image-provider",
            "--anchor-source",
            "https://example.test/catalog",
        ]
    ) == 0
    brief_path = tmp_path / "design-brief.json"
    brief = _approve_design_brief(brief_path)
    brief["display_text"][-1] = "DISTAGON"
    brief["allowed_text"][-1] = "DISTAGON"
    brief_path.write_text(json.dumps(brief, indent=2) + "\n", encoding="utf-8")
    capsys.readouterr()

    assert main(["handoff-check", str(config_path), "--json"]) == 2
    assert "complete closed text set" in capsys.readouterr().err


def test_handoff_check_rejects_synchronized_sigma_identity_drift(
    tmp_path: Path, capsys
) -> None:
    """Changing both weak binding strings must not detach a Helios brief."""

    source = tmp_path / "master.png"
    Image.new("RGB", (64, 64), (17, 18, 17)).save(source)
    config_path = tmp_path / "job.toml"
    assert main(
        [
            "init",
            str(config_path),
            "--source",
            str(source),
            "--measured-diameter",
            "95",
            "--lens-identity",
            "Helios / Zenit Helios-44-2 58mm F2",
            "--display-text",
            "58",
            "F2",
            "HELIOS 44-2",
            "REHOUSED CINEMA",
            "M42",
        ]
    ) == 0
    assert main(
        [
            "handoff-init",
            str(config_path),
            "--brand",
            "Helios / Zenit",
            "--model",
            "Helios-44-2",
            "--focal-length",
            "58",
            "--maximum-aperture",
            "F2",
            "--provider",
            "test-image-provider",
            "--anchor-source",
            "https://www.zenitcamera.com/mans/zenit-e/zenit-e-eng.html",
        ]
    ) == 0
    brief_path = tmp_path / "design-brief.json"
    brief = _approve_design_brief(brief_path)

    wrong_identity = "Sigma 28-70mm F2.8 DG DN Contemporary"
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            'lens_identity = "Helios / Zenit Helios-44-2 58mm F2"',
            f'lens_identity = "{wrong_identity}"',
        ),
        encoding="utf-8",
    )
    brief["job_binding"]["metadata_lens_identity"] = wrong_identity
    brief_path.write_text(json.dumps(brief, indent=2) + "\n", encoding="utf-8")
    capsys.readouterr()

    assert main(["handoff-check", str(config_path), "--json"]) == 2
    error = capsys.readouterr().err
    assert "metadata.lens_identity" in error
    assert "lens_identity.brand/model/focal length/maximum aperture" in error


def test_handoff_check_binds_all_body_geometry_dimensions(
    tmp_path: Path, capsys
) -> None:
    source = tmp_path / "master.png"
    Image.new("RGB", (64, 64), (17, 18, 17)).save(source)
    config_path = tmp_path / "job.toml"
    assert main(
        [
            "init",
            str(config_path),
            "--source",
            str(source),
            "--measured-diameter",
            "52",
            "--lens-identity",
            "Zeiss Planar 50mm F1.4",
            "--display-text",
            "50",
            "F1.4",
            "PLANAR",
        ]
    ) == 0
    assert main(
        [
            "handoff-init",
            str(config_path),
            "--brand",
            "Zeiss",
            "--model",
            "Planar 50mm F1.4",
            "--focal-length",
            "50",
            "--maximum-aperture",
            "F1.4",
            "--provider",
            "test-image-provider",
            "--anchor-source",
            "https://example.test/catalog",
        ]
    ) == 0
    brief_path = tmp_path / "design-brief.json"
    approved = _approve_design_brief(brief_path)
    capsys.readouterr()
    assert main(["handoff-check", str(config_path), "--json"]) == 0
    capsys.readouterr()

    mutations = {
        "wall_thickness_mm": approved["physical_fit"]["wall_thickness_mm"] + 0.5,
        "bottom_thickness_mm": approved["physical_fit"]["bottom_thickness_mm"] + 0.5,
        "side_height_mm": approved["physical_fit"]["side_height_mm"] + 0.5,
        "bare_clearance_mm": approved["physical_fit"]["bare_clearance_mm"] + 0.5,
        "nozzle_mm": approved["physical_fit"]["nozzle_mm"] + 0.1,
        "friction_rib_count": approved["physical_fit"]["friction_rib_count"] + 1,
        "liner_material": "foam",
        "compression_is_assumption": not approved["physical_fit"][
            "compression_is_assumption"
        ],
        "retention_strategy": "tampered_strategy",
        "friction_rib_profile_derived": not approved["physical_fit"][
            "friction_rib_profile_derived"
        ],
    }
    reference = approved["physical_fit"]["friction_rib_profile_reference_cavity_mm"]
    mutations["friction_rib_profile_reference_cavity_mm"] = (
        99.0 if reference is None else reference + 0.5
    )
    for field, value in mutations.items():
        mutated = json.loads(json.dumps(approved))
        mutated["physical_fit"][field] = value
        brief_path.write_text(json.dumps(mutated, indent=2) + "\n", encoding="utf-8")
        assert main(["handoff-check", str(config_path), "--json"]) == 2
        assert f"physical_fit.{field}" in capsys.readouterr().err


def test_handoff_check_rejects_circle_palette_and_artwork_process_job_drift(
    tmp_path: Path, capsys
) -> None:
    source = tmp_path / "master.png"
    Image.new("RGB", (64, 64), (17, 18, 17)).save(source)
    config_path = tmp_path / "job.toml"
    assert main(
        [
            "init",
            str(config_path),
            "--source",
            str(source),
            "--measured-diameter",
            "52",
            "--lens-identity",
            "Zeiss Planar 50mm F1.4",
            "--display-text",
            "50",
            "F1.4",
            "PLANAR",
        ]
    ) == 0
    assert main(
        [
            "handoff-init",
            str(config_path),
            "--brand",
            "Zeiss",
            "--model",
            "Planar 50mm F1.4",
            "--focal-length",
            "50",
            "--maximum-aperture",
            "F1.4",
            "--provider",
            "test-image-provider",
            "--anchor-source",
            "https://example.test/catalog",
        ]
    ) == 0
    brief_path = tmp_path / "design-brief.json"
    approved = _approve_design_brief(brief_path)
    assert approved["job_binding"]["circle"]["allow_outside"] is False
    assert approved["job_binding"]["palette"]["gray"]["height_mm"] == 0.4
    assert approved["job_binding"]["artwork_process"] == {
        "grid_size": 520,
        "safe_border_mm": 0.4,
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
    }
    original_config = config_path.read_text(encoding="utf-8")
    capsys.readouterr()
    assert main(["handoff-check", str(config_path), "--json"]) == 0
    capsys.readouterr()

    mutations = (
        ("allow_outside = false", "allow_outside = true", "job_binding.circle"),
        ("height_mm = 0.4", "height_mm = 0.9", "job_binding.palette"),
        ("grid_size = 520", "grid_size = 512", "job_binding.artwork_process"),
        ("safe_border_mm = 0.4", "safe_border_mm = 0.9", "job_binding.artwork_process"),
        ('name = "median"', 'name = "none"', "job_binding.artwork_process"),
        (
            "[cleanup]\nenabled = true",
            "[cleanup]\nenabled = false",
            "job_binding.artwork_process",
        ),
        (
            'assembly_mode = "auto"',
            'assembly_mode = "integrated_part"',
            "job_binding.artwork_process",
        ),
    )
    for old, new, expected_error in mutations:
        assert old in original_config
        config_path.write_text(original_config.replace(old, new, 1), encoding="utf-8")
        assert main(["handoff-check", str(config_path), "--json"]) == 2
        assert expected_error in capsys.readouterr().err
    config_path.write_text(original_config, encoding="utf-8")


def test_handoff_check_rejects_empty_anchor_mapping_and_provenance(
    tmp_path: Path, capsys
) -> None:
    source = tmp_path / "master.png"
    Image.new("RGB", (64, 64), (17, 18, 17)).save(source)
    config_path = tmp_path / "job.toml"
    assert main(
        [
            "init",
            str(config_path),
            "--source",
            str(source),
            "--measured-diameter",
            "52",
            "--lens-identity",
            "Zeiss Planar 50mm F1.4",
            "--display-text",
            "50",
            "F1.4",
            "PLANAR",
        ]
    ) == 0
    assert main(
        [
            "handoff-init",
            str(config_path),
            "--brand",
            "Zeiss",
            "--model",
            "Planar 50mm F1.4",
            "--focal-length",
            "50",
            "--maximum-aperture",
            "F1.4",
            "--provider",
            "test-image-provider",
            "--anchor-source",
            "https://example.test/catalog",
        ]
    ) == 0
    brief_path = tmp_path / "design-brief.json"
    scaffold = json.loads(brief_path.read_text(encoding="utf-8"))
    assert (
        "not applicable — no third-party mark rendered"
        in scaffold["provenance"]["brand_mark_license"]
    )
    approved = _approve_design_brief(brief_path)
    capsys.readouterr()

    semantic_not_applicable = json.loads(json.dumps(approved))
    semantic_not_applicable["provenance"]["brand_mark_license"] = (
        "not applicable — no third-party mark rendered"
    )
    brief_path.write_text(
        json.dumps(semantic_not_applicable, indent=2) + "\n", encoding="utf-8"
    )
    assert main(["handoff-check", str(config_path), "--json"]) == 0
    capsys.readouterr()

    archive_bound = json.loads(json.dumps(approved))
    archive_bound["anchors"][0]["source"] = "archive:maker-catalog/volume-7/page-12"
    brief_path.write_text(json.dumps(archive_bound, indent=2) + "\n", encoding="utf-8")
    assert main(["handoff-check", str(config_path), "--json"]) == 0
    capsys.readouterr()

    invalid_source = json.loads(json.dumps(approved))
    invalid_source["anchors"][0]["source"] = "manufacturer catalog"
    brief_path.write_text(json.dumps(invalid_source, indent=2) + "\n", encoding="utf-8")
    assert main(["handoff-check", str(config_path), "--json"]) == 2
    assert "http(s) URL or an explicit archive identifier" in capsys.readouterr().err

    for evidence_state in ("not verified", "not sourced", "unverified"):
        invalid_evidence = json.loads(json.dumps(approved))
        invalid_evidence["anchors"][0]["evidence_state"] = evidence_state
        brief_path.write_text(
            json.dumps(invalid_evidence, indent=2) + "\n", encoding="utf-8"
        )
        assert main(["handoff-check", str(config_path), "--json"]) == 2
        assert "positive sourced/verified status" in capsys.readouterr().err

    negative_claim = json.loads(json.dumps(approved))
    negative_claim["anchors"][0]["claim_kind"] = "not_manufacturer_culture"
    brief_path.write_text(json.dumps(negative_claim, indent=2) + "\n", encoding="utf-8")
    assert main(["handoff-check", str(config_path), "--json"]) == 2
    assert "canonical positive claim kinds" in capsys.readouterr().err

    unrelated_brand = json.loads(json.dumps(approved))
    unrelated_brand["anchors"][0]["subject_scope"] = "Canon FD 35-105 lens system"
    unrelated_brand["anchors"][0]["identity_binding"]["brand"] = "Canon"
    brief_path.write_text(
        json.dumps(unrelated_brand, indent=2) + "\n", encoding="utf-8"
    )
    assert main(["handoff-check", str(config_path), "--json"]) == 2
    assert "identity_binding.brand disagrees" in capsys.readouterr().err

    unrelated_subject = json.loads(json.dumps(approved))
    unrelated_subject["anchors"][0]["subject_scope"] = "Canon FD system"
    brief_path.write_text(
        json.dumps(unrelated_subject, indent=2) + "\n", encoding="utf-8"
    )
    assert main(["handoff-check", str(config_path), "--json"]) == 2
    assert "must name this lens brand/model" in capsys.readouterr().err

    anchor_fields = (
        "claim_kind",
        "subject_scope",
        "evidence_state",
        "source_role",
        "source",
        "summary",
        "render_role",
        "anchor_context",
        "motif_commitment",
        "anchor_visual_motif",
        "recognition_cue",
        "qualifier",
    )
    for field in anchor_fields:
        mutated = json.loads(json.dumps(approved))
        mutated["anchors"][0][field] = "x"
        brief_path.write_text(json.dumps(mutated, indent=2) + "\n", encoding="utf-8")
        assert main(["handoff-check", str(config_path), "--json"]) == 2
        assert f"anchors[0].{field}" in capsys.readouterr().err

    for field in (
        "artwork_license",
        "brand_mark_license",
        "film_or_history_permissions",
        "notes",
    ):
        mutated = json.loads(json.dumps(approved))
        mutated["provenance"][field] = "x"
        brief_path.write_text(json.dumps(mutated, indent=2) + "\n", encoding="utf-8")
        assert main(["handoff-check", str(config_path), "--json"]) == 2
        assert f"provenance.{field}" in capsys.readouterr().err

    for field in (
        "full_resolution_reviewed",
        "text_off_anchor_recognizable",
        "identity_swap_requires_redesign",
        "anchor_drives_primary_composition",
        "composition_resolved",
        "visual_grammar_consistent",
        "finish_target_met",
        "production_reduction_preserves_authorship",
    ):
        mutated = json.loads(json.dumps(approved))
        mutated["design_review"][field] = False
        brief_path.write_text(json.dumps(mutated, indent=2) + "\n", encoding="utf-8")
        assert main(["handoff-check", str(config_path), "--json"]) == 2
        assert f"design_review.{field}" in capsys.readouterr().err

    for field in ("structural_thesis", "finish_target_note", "reviewer_note"):
        shallow_review = json.loads(json.dumps(approved))
        shallow_review["design_review"][field] = "generic"
        brief_path.write_text(
            json.dumps(shallow_review, indent=2) + "\n", encoding="utf-8"
        )
        assert main(["handoff-check", str(config_path), "--json"]) == 2
        assert f"design_review.{field}" in capsys.readouterr().err

    missing_approval_evidence = json.loads(json.dumps(approved))
    missing_approval_evidence["generation"]["approval_note"] = None
    brief_path.write_text(
        json.dumps(missing_approval_evidence, indent=2) + "\n", encoding="utf-8"
    )
    assert main(["handoff-check", str(config_path), "--json"]) == 2
    assert "generation.approval_note" in capsys.readouterr().err

    invalid_hero = json.loads(json.dumps(approved))
    invalid_hero["design_review"]["hero_anchor_index"] = 99
    brief_path.write_text(json.dumps(invalid_hero, indent=2) + "\n", encoding="utf-8")
    assert main(["handoff-check", str(config_path), "--json"]) == 2
    assert "hero_anchor_index is outside anchors" in capsys.readouterr().err

    nonstructural_hero = json.loads(json.dumps(approved))
    nonstructural_hero["anchors"][0]["motif_commitment"] = "supporting"
    brief_path.write_text(
        json.dumps(nonstructural_hero, indent=2) + "\n", encoding="utf-8"
    )
    assert main(["handoff-check", str(config_path), "--json"]) == 2
    assert "motif_commitment is structural" in capsys.readouterr().err

    repeated_consequence = json.loads(json.dumps(approved))
    repeated_consequence["design_review"]["anchor_system_consequences"][1][
        "system"
    ] = "typography_or_counterform"
    brief_path.write_text(
        json.dumps(repeated_consequence, indent=2) + "\n", encoding="utf-8"
    )
    assert main(["handoff-check", str(config_path), "--json"]) == 2
    assert "must use distinct systems" in capsys.readouterr().err

    mismatched_hero_id = json.loads(json.dumps(approved))
    mismatched_hero_id["design_review"]["hero_anchor_id"] = "another-anchor"
    brief_path.write_text(
        json.dumps(mismatched_hero_id, indent=2) + "\n", encoding="utf-8"
    )
    assert main(["handoff-check", str(config_path), "--json"]) == 2
    assert "hero_anchor_id must match" in capsys.readouterr().err

    mismatched_consequence = json.loads(json.dumps(approved))
    mismatched_consequence["design_review"]["anchor_system_consequences"][0][
        "anchor_id"
    ] = "another-anchor"
    brief_path.write_text(
        json.dumps(mismatched_consequence, indent=2) + "\n", encoding="utf-8"
    )
    assert main(["handoff-check", str(config_path), "--json"]) == 2
    assert "must equal design_review.hero_anchor_id" in capsys.readouterr().err

    duplicate_anchor_id = json.loads(json.dumps(approved))
    duplicate_anchor_id["anchors"].append(
        json.loads(json.dumps(duplicate_anchor_id["anchors"][0]))
    )
    brief_path.write_text(
        json.dumps(duplicate_anchor_id, indent=2) + "\n", encoding="utf-8"
    )
    assert main(["handoff-check", str(config_path), "--json"]) == 2
    assert "duplicate anchor_id" in capsys.readouterr().err

    for filler in (
        "a" * 40,
        "1234" * 10,
        "ok " * 20,
        "asdf qwer zxcv " * 4,
        "anchor system consequence " * 4,
    ):
        mechanical_review = json.loads(json.dumps(approved))
        mechanical_review["design_review"]["reviewer_note"] = filler
        brief_path.write_text(
            json.dumps(mechanical_review, indent=2) + "\n", encoding="utf-8"
        )
        assert main(["handoff-check", str(config_path), "--json"]) == 2
        assert "reviewer_note" in capsys.readouterr().err

    natural_replacement_note = json.loads(json.dumps(approved))
    natural_replacement_note["design_review"]["reviewer_note"] = (
        "Replacement geometry preserves the approved topology while the sourced "
        "grid still controls type, field rhythm, and the circular perimeter."
    )
    brief_path.write_text(
        json.dumps(natural_replacement_note, indent=2) + "\n", encoding="utf-8"
    )
    assert main(["handoff-check", str(config_path), "--json"]) == 0
    capsys.readouterr()

    chinese_review_note = json.loads(json.dumps(approved))
    chinese_review_note["design_review"]["reviewer_note"] = (
        "同一套校准网格切入焦段数字的反形，并继续收束到外圆周；替换近邻镜头时必须重做比例与节奏。"
    )
    brief_path.write_text(
        json.dumps(chinese_review_note, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    assert main(["handoff-check", str(config_path), "--json"]) == 0
    capsys.readouterr()

    reference_path = tmp_path / "quality-reference.png"
    Image.new("RGB", (32, 32), (242, 231, 211)).save(reference_path)
    reference_hash = hashlib.sha256(reference_path.read_bytes()).hexdigest()
    with_quality_reference = json.loads(json.dumps(approved))
    with_quality_reference["approved_references"] = [
        {
            "id": "finish-benchmark",
            "roles": ["quality_reference", "style_reference"],
            "path_or_url": "user-supplied comparison",
            "snapshot_path": reference_path.name,
            "snapshot_sha256": reference_hash,
            "transferable_traits": [
                "Integrated focal typography and structural geometry",
                "Systematic edge weights and resolved negative space",
            ],
        }
    ]
    with_quality_reference["generation"]["reference_hashes"] = [reference_hash]
    brief_path.write_text(
        json.dumps(with_quality_reference, indent=2) + "\n", encoding="utf-8"
    )
    assert main(["handoff-check", str(config_path), "--json"]) == 2
    assert "must cover every quality_reference" in capsys.readouterr().err

    with_quality_reference["design_review"]["quality_reference_checks"] = [
        {
            "reference_id": "finish-benchmark",
            "met": True,
            "comparison_note": (
                "The candidate matches the benchmark's integration, edge discipline, "
                "and deliberate negative-space relationships without copying its motif."
            ),
        }
    ]
    brief_path.write_text(
        json.dumps(with_quality_reference, indent=2) + "\n", encoding="utf-8"
    )
    assert main(["handoff-check", str(config_path), "--json"]) == 0
    quality_report = json.loads(capsys.readouterr().out)
    assert quality_report["quality_reference_count"] == 1

    packed_reference_roles = json.loads(json.dumps(with_quality_reference))
    packed_reference_roles["approved_references"][0].pop("roles")
    packed_reference_roles["approved_references"][0]["role"] = (
        "quality_reference,style_reference"
    )
    brief_path.write_text(
        json.dumps(packed_reference_roles, indent=2) + "\n", encoding="utf-8"
    )
    assert main(["handoff-check", str(config_path), "--json"]) == 2
    assert ".role is ambiguous" in capsys.readouterr().err

    duplicate_reference_roles = json.loads(json.dumps(with_quality_reference))
    duplicate_reference_roles["approved_references"][0]["roles"] = [
        "quality_reference",
        "quality_reference",
    ]
    brief_path.write_text(
        json.dumps(duplicate_reference_roles, indent=2) + "\n", encoding="utf-8"
    )
    assert main(["handoff-check", str(config_path), "--json"]) == 2
    assert "roles must not contain duplicates" in capsys.readouterr().err

    stale_quality_reference = json.loads(json.dumps(with_quality_reference))
    stale_quality_reference["approved_references"][0]["snapshot_sha256"] = "f" * 64
    stale_quality_reference["generation"]["reference_hashes"] = ["f" * 64]
    brief_path.write_text(
        json.dumps(stale_quality_reference, indent=2) + "\n", encoding="utf-8"
    )
    assert main(["handoff-check", str(config_path), "--json"]) == 2
    assert "snapshot_sha256 does not match" in capsys.readouterr().err

    stale_review = json.loads(json.dumps(approved))
    stale_review["design_review"]["reviewed_candidate_sha256"] = "0" * 64
    brief_path.write_text(json.dumps(stale_review, indent=2) + "\n", encoding="utf-8")
    assert main(["handoff-check", str(config_path), "--json"]) == 2
    assert "reviewed_candidate_sha256 does not match" in capsys.readouterr().err

    legacy_review = json.loads(json.dumps(approved))
    legacy_review["schema_version"] = 1
    brief_path.write_text(json.dumps(legacy_review, indent=2) + "\n", encoding="utf-8")
    assert main(["handoff-check", str(config_path), "--json"]) == 2
    schema_error = capsys.readouterr().err
    assert "schema_version must be 2" in schema_error
    assert "legacy v1 requires a new reviewed v2 brief" in schema_error

    for invalid_version in (2.0, True, None):
        invalid_schema = json.loads(json.dumps(approved))
        invalid_schema["schema_version"] = invalid_version
        brief_path.write_text(
            json.dumps(invalid_schema, indent=2) + "\n", encoding="utf-8"
        )
        assert main(["handoff-check", str(config_path), "--json"]) == 2
        assert "schema_version must be the integer 2" in capsys.readouterr().err

    future_schema = json.loads(json.dumps(approved))
    future_schema["schema_version"] = 3
    brief_path.write_text(json.dumps(future_schema, indent=2) + "\n", encoding="utf-8")
    assert main(["handoff-check", str(config_path), "--json"]) == 2
    assert "unsupported design brief schema_version 3" in capsys.readouterr().err

    for token_only in ("NONE", "not applicable"):
        mutated = json.loads(json.dumps(approved))
        mutated["provenance"]["brand_mark_license"] = token_only
        brief_path.write_text(json.dumps(mutated, indent=2) + "\n", encoding="utf-8")
        assert main(["handoff-check", str(config_path), "--json"]) == 2
        assert "provenance.brand_mark_license" in capsys.readouterr().err


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


def test_init_persists_and_checks_optional_adapter_envelope(tmp_path: Path) -> None:
    source = tmp_path / "master.png"
    Image.new("RGB", (64, 64), (17, 18, 17)).save(source)
    config_path = tmp_path / "jobs" / "adapter" / "job.toml"
    assert main(
        [
            "init",
            str(config_path),
            "--source",
            str(source),
            "--measured-diameter",
            "85",
            "--adapter-nominal-ring",
            "80",
            "--adapter-radial-wall",
            "2.5",
        ]
    ) == 0
    config = load_config(config_path)
    assert config.metadata["adapter_nominal_ring_mm"] == 80.0
    assert config.metadata["adapter_radial_wall_mm"] == 2.5
    assert config.metadata["adapter_derived_mating_diameter_mm"] == 85.0
    generated = config_path.read_text(encoding="utf-8")
    assert "[metadata]" in generated
    assert "adapter_nominal_ring_mm = 80.0" in generated


def test_init_rejects_mismatched_adapter_envelope(tmp_path: Path) -> None:
    source = tmp_path / "master.png"
    Image.new("RGB", (64, 64), (17, 18, 17)).save(source)
    config_path = tmp_path / "job.toml"
    assert main(
        [
            "init",
            str(config_path),
            "--source",
            str(source),
            "--measured-diameter",
            "85",
            "--adapter-nominal-ring",
            "77",
            "--adapter-radial-wall",
            "2.5",
        ]
    ) == 2
    assert not config_path.exists()


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


def test_profile_defaults_recompute_when_measured_diameter_changes(tmp_path: Path) -> None:
    """Init's visible profile numbers must not freeze a later diameter edit."""
    source = tmp_path / "master.png"
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    ImageDraw.Draw(image).ellipse((4, 4, 59, 59), fill=(17, 18, 17, 255))
    image.save(source)
    config_path = tmp_path / "jobs" / "profile-recompute.toml"
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
    original = load_config(config_path)
    assert original.fit.friction_rib_profile_derived is True
    assert original.fit.friction_rib_profile_reference_cavity_mm == pytest.approx(95.4)
    edited = config_path.read_text(encoding="utf-8").replace(
        "measured_diameter_mm = 95.0", "measured_diameter_mm = 85.0"
    )
    config_path.write_text(edited, encoding="utf-8")
    changed = load_config(config_path)
    assert changed.fit.friction_rib_profile_derived is True
    assert changed.fit.friction_rib_width_mm == pytest.approx(
        5.9620447248, rel=1e-5
    )


def test_profile_numeric_edit_becomes_explicit_override(tmp_path: Path) -> None:
    source = tmp_path / "master.png"
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    ImageDraw.Draw(image).ellipse((4, 4, 59, 59), fill=(17, 18, 17, 255))
    image.save(source)
    config_path = tmp_path / "jobs" / "profile-explicit.toml"
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
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            "friction_rib_protrusion_mm = 0.1", "friction_rib_protrusion_mm = 0.25"
        ),
        encoding="utf-8",
    )
    config = load_config(config_path)
    assert config.fit.friction_rib_profile_derived is False
    assert config.fit.friction_rib_protrusion_mm == pytest.approx(0.25)


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


def test_init_normalizes_missing_cwd_qualified_source_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cwd-qualified source remains portable before the file exists."""

    monkeypatch.chdir(tmp_path)
    config_path = tmp_path / "jobs" / "qualified" / "job.toml"
    assert main(
        [
            "init",
            str(config_path),
            "--source",
            "jobs/qualified/art/master.png",
            "--face-diameter",
            "52",
        ]
    ) == 0
    generated = config_path.read_text(encoding="utf-8")
    assert 'source_art = "art/master.png"' in generated
    source = config_path.parent / "art" / "master.png"
    image = Image.new("RGB", (64, 64), (17, 18, 17))
    image.save(source)
    assert load_config(config_path).source_path == source.resolve()


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
