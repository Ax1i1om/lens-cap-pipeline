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
