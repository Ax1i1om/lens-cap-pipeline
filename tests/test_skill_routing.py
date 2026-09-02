"""Regression checks for the repository's lens-cap Skill routing contract."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL_DIRS = ("lens-cap-imagegen", "lens-cap-production")


def test_lens_cap_skills_declare_primary_triggers_and_exclusion_rule() -> None:
    for directory in SKILL_DIRS:
        english = (ROOT / "skills" / directory / "SKILL.md").read_text(encoding="utf-8")
        chinese = (ROOT / "skills" / directory / "SKILL.zh-CN.md").read_text(encoding="utf-8")
        for document in (english, chinese):
            assert "triggers:" in document
            assert "primary" in document.lower()
            assert "parallel" in document.lower() or "并行" in document
            assert "generic" in document.lower() or "通用" in document


def test_skill_frontmatter_uses_only_supported_top_level_keys() -> None:
    """Keep strict host Skill validators from rejecting routing metadata."""

    supported = {"name", "description", "license", "allowed-tools", "metadata"}
    for directory in SKILL_DIRS:
        document = (ROOT / "skills" / directory / "SKILL.md").read_text(encoding="utf-8")
        assert document.startswith("---\n")
        _, frontmatter, _ = document.split("---\n", 2)
        keys = {
            line.split(":", 1)[0].strip()
            for line in frontmatter.splitlines()
            if line and not line.startswith((" ", "\t", "#")) and ":" in line
        }
        assert keys <= supported
        assert "triggers:" in frontmatter


def test_cross_context_trigger_and_intake_contract_is_present() -> None:
    imagegen = (ROOT / "skills/lens-cap-imagegen/SKILL.md").read_text(encoding="utf-8")
    production = (ROOT / "skills/lens-cap-production/SKILL.md").read_text(encoding="utf-8")
    imagegen_zh = (ROOT / "skills/lens-cap-imagegen/SKILL.zh-CN.md").read_text(encoding="utf-8")
    production_zh = (ROOT / "skills/lens-cap-production/SKILL.zh-CN.md").read_text(encoding="utf-8")
    # Keep common host wording covered so a trigger list cannot regress to one
    # punctuation/language variant after a localized edit.
    for phrase in ("lens cap design", "lens-cap artwork", "lens cap artwork", "lens medallion"):
        assert phrase in imagegen
    for phrase in (
        "printable lens cap",
        "lens cap model",
        "lens cap STL",
        "lens cap 3MF",
        "lens cap production",
    ):
        assert phrase in production
    for document in (imagegen, production):
        lowered = document.lower()
        assert "focal length" in lowered
        assert "maximum aperture" in lowered
        assert "actual" in lowered and "diameter" in lowered
        assert "foam" in lowered and "friction" in lowered and "default" in lowered
        assert "only" in lowered and "design skill" in lowered
    assert "焦段是最大、第一阅读层级" in imagegen_zh
    assert "最大光圈（F 值）是第二大阅读层级" in imagegen_zh
    assert "镜头实际卡合的圆柱外径" in production_zh
    assert "最大光圈（F 值）／F值／F-stop／F-number 是第二层级" in production_zh
    assert "默认开启" in production_zh and "摩擦凸条" in production_zh
    assert "不要并行调用任何其他设计 Skill" in production_zh


def test_skill_manifest_declares_portable_sync_entrypoint() -> None:
    manifest = (ROOT / "skills/manifest.json").read_text(encoding="utf-8")
    for token in (
        '"project_version"',
        '"entrypoint": "scripts/install_skills.py"',
        '"default_mode": "dry-run"',
        '"apply_flag": "--apply"',
        '"drift_exit_code": 1',
        '"codex": "$CODEX_HOME/skills"',
        '"claude": "$CLAUDE_HOME/skills"',
    ):
        assert token in manifest


def test_manifest_records_artwork_hierarchy_and_fitted_cap_intake() -> None:
    import json

    data = json.loads((ROOT / "skills/manifest.json").read_text(encoding="utf-8"))
    policy = data["routing_policy"]["lens_cap_intent"]
    assert policy["artwork_hierarchy"] == {
        "first_read": "focal_length",
        "second_read": "maximum_aperture",
        "second_read_aliases": ["f_stop", "f_number", "f_value"],
        "preserve_after_approval": True,
    }
    intake = policy["production_intake"]
    assert intake["friction_ribs_default_enabled"] is True
    assert intake["smooth_wall_requires_explicit_opt_out"] is True
    assert intake["grouped_questions"] == [
        "mating_outside_diameter_mm",
        "foam_liner_plan_and_uncompressed_thickness_mm",
        "friction_rib_preference",
    ]


def test_host_metadata_keeps_the_same_exclusive_hierarchy_and_intake() -> None:
    imagegen_agent = (ROOT / "skills/lens-cap-imagegen/agents/openai.yaml").read_text(encoding="utf-8").lower()
    production_agent = (ROOT / "skills/lens-cap-production/agents/openai.yaml").read_text(encoding="utf-8").lower()
    for document in (imagegen_agent, production_agent):
        assert "focal length" in document
        assert "maximum aperture" in document
        assert "f-stop" in document and "f-number" in document
        assert "friction" in document and "enabled by default" in document
        assert "do not" in document and "design" in document
    assert "mating diameter" in imagegen_agent
    assert "foam" in imagegen_agent and "uncompressed" in imagegen_agent
    assert "mating diameter" in production_agent
    assert "foam" in production_agent and "uncompressed" in production_agent


def test_skill_agent_metadata_routes_lens_cap_requests() -> None:
    imagegen_agent = (ROOT / "skills/lens-cap-imagegen/agents/openai.yaml").read_text(encoding="utf-8")
    production_agent = (ROOT / "skills/lens-cap-production/agents/openai.yaml").read_text(encoding="utf-8")
    for document in (imagegen_agent, production_agent):
        assert "default_prompt:" in document
        assert "lens-cap" in document
        assert "only" in document.lower()
        assert "generic" in document.lower()


def test_portable_resolver_and_manifest_agree_on_two_primary_skills() -> None:
    resolver = (ROOT / "skills/RESOLVER.md").read_text(encoding="utf-8")
    manifest = (ROOT / "skills/manifest.json").read_text(encoding="utf-8")
    for skill_name in SKILL_DIRS:
        assert f"`${skill_name}`" in resolver
        assert f'"{skill_name}"' in manifest
    assert "exclusive_primary_design_route" in manifest
    assert "never parallel creative redesign" in resolver
    assert "focal length is the" in resolver.lower()
    assert "maximum aperture" in resolver.lower()
    assert "mating/front" in resolver
    assert "friction-rib" in resolver
