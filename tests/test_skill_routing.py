"""Regression checks for the repository's lens-cap Skill routing contract."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL_DIRS = ("lens-cap-imagegen", "lens-cap-production")


def _frontmatter_triggers(path: Path) -> set[str]:
    document = path.read_text(encoding="utf-8")
    _, frontmatter, _ = document.split("---\n", 2)
    in_triggers = False
    triggers: set[str] = set()
    for line in frontmatter.splitlines():
        stripped = line.strip()
        if stripped == "triggers:":
            in_triggers = True
            continue
        if not in_triggers or not stripped.startswith("- "):
            continue
        triggers.add(stripped[2:].strip().strip('"').casefold())
    return triggers


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
    for phrase in (
        "lens cap design",
        "lens-cap artwork",
        "lens cap artwork",
        "lens-cap medallion",
        "lens-cap front graphic",
        "lens-cap front badge",
        "circular lens-cap badge",
        "circular lens-front artwork",
        "front face of a lens cap",
        "cap for a named lens",
    ):
        assert phrase in imagegen
    for phrase in (
        "printable lens cap",
        "lens cap model",
        "lens cap STL",
        "lens cap 3MF",
        "lens cap production",
        "lens-cap front relief",
        "lens-cap badge model",
        "circular lens-cap badge model",
        "lens-cap front 3mf",
        "circular lens-cap relief",
        "front-cap model",
        "make a cap for a named lens",
    ):
        assert phrase.lower() in production.lower()
    for document in (imagegen, production):
        lowered = document.lower()
        assert "focal length" in lowered
        assert "maximum aperture" in lowered
        assert "actual" in lowered and "diameter" in lowered
        assert "foam" in lowered and "friction" in lowered and "default" in lowered
        assert "only" in lowered and "design skill" in lowered
        assert "once per job" in lowered or "每个任务最多询问一次" in document
    assert "焦段是最强、第一阅读层级" in imagegen_zh
    assert "最大光圈（F 值）是第二强阅读层级" in imagegen_zh
    assert "镜头实际卡合的圆柱外径" in production_zh
    assert "最大光圈（F 值）／F值／F-stop／F-number 是第二层级" in production_zh
    assert "默认开启" in production_zh and "摩擦凸条" in production_zh
    assert "不要并行调用任何其他设计 Skill" in production_zh


def test_pure_artwork_triggers_do_not_overlap_production_metadata() -> None:
    """Static host metadata must not race imagegen for an image-only request."""

    imagegen_paths = (
        ROOT / "skills/lens-cap-imagegen/SKILL.md",
        ROOT / "skills/lens-cap-imagegen/SKILL.zh-CN.md",
    )
    production_paths = (
        ROOT / "skills/lens-cap-production/SKILL.md",
        ROOT / "skills/lens-cap-production/SKILL.zh-CN.md",
    )
    imagegen_triggers = set().union(*(_frontmatter_triggers(path) for path in imagegen_paths))
    production_triggers = set().union(
        *(_frontmatter_triggers(path) for path in production_paths)
    )
    cap_owned_artwork = {
        "circular lens-front artwork",
        "circular lens front artwork",
        "圆形镜头正面图稿",
    }
    broad_non_cap_aliases = {
        "lens badge",
        "circular lens image",
        "circular lens artwork",
        "lens cover artwork",
        "镜头徽章",
        "圆形镜头图像",
        "镜头罩图稿",
    }
    assert cap_owned_artwork <= imagegen_triggers
    assert cap_owned_artwork.isdisjoint(production_triggers)
    assert broad_non_cap_aliases.isdisjoint(imagegen_triggers | production_triggers)

    production_agent = (
        ROOT / "skills/lens-cap-production/agents/openai.yaml"
    ).read_text(encoding="utf-8")
    flattened = " ".join(production_agent.split()).casefold()
    assert "explicit circular lens-front artwork only to $lens-cap-imagegen" in flattened
    assert "format owned by metadata/photos" in flattened
    assert "circular image or artwork" not in flattened


def test_skill_manifest_declares_portable_sync_entrypoint() -> None:
    manifest = (ROOT / "skills/manifest.json").read_text(encoding="utf-8")
    for token in (
        '"project_version"',
        '"entrypoint": "scripts/install_skills.py"',
        '"routing_entrypoint": "scripts/resolve_skill_route.py"',
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


def test_manifest_named_lens_surface_semantic_route_is_scoped() -> None:
    import json

    data = json.loads((ROOT / "skills/manifest.json").read_text(encoding="utf-8"))
    semantic = data["routing_policy"]["lens_cap_intent"]["semantic_match"]
    assert semantic["requires_named_lens"] is False
    assert semantic["requires_named_lens_when_cap_object_is_omitted"] is True
    assert semantic["explicit_cap_object_starts_identity_intake"] is True
    assert semantic["bare_production_format_requires_lens_cap_context"] is True
    assert {
        "lens cap",
        "front cap",
        "physical cap",
        "cap artwork",
        "cap badge",
        "cap relief",
        "cap model",
        "lens cap 3mf",
        "circular lens-front artwork",
        "cap for a named lens",
        "镜头盖",
        "镜头帽",
        "镜头闷盖",
        "镜头帽",
        "镜头前盖",
        "前盖正面",
        "实体盖",
        "镜头盖图稿",
        "镜头盖模型",
        "圆形镜头正面图稿",
    } <= set(semantic["surface_terms"])
    assert {
        "cover",
        "badge",
        "printable",
        "lens cover",
        "镜头罩",
    }.isdisjoint(semantic["surface_terms"])
    assert {
        "cover article",
        "lens review",
        "warranty card",
        "printable poster",
        "metadata",
        "lens pouch",
        "封面文章",
        "镜头评测",
        "保修卡",
        "可打印海报",
        "元数据",
        "镜头包",
        "lens body",
        "lens barrel",
        "focusing ring",
        "focus gear",
        "lens mount",
        "cage",
        "plate",
        "label",
        "grip",
    } <= set(semantic["non_cap_deliverable_terms"])
    assert {"design", "generate", "设计", "生成"} <= set(semantic["verbs"])
    assert "optical design" in semantic["exclude_without_surface_intent"]
    assert "optical" in semantic["exclude_without_surface_intent"]
    assert "product photo" in semantic["exclude_without_surface_intent"]
    assert "产品照片" in semantic["exclude_without_surface_intent"]
    assert {
        "chromatic aberration",
        "distortion",
        "bokeh",
        "色差",
        "畸变",
        "焦外",
    } <= set(semantic["exclude_without_surface_intent"])
    assert {
        "inspect a file format",
        "explain a file export",
        "format support question",
        "检查文件格式支持",
        "解释文件导出",
    } <= set(semantic["non_production_action_terms"])
    assert "circular lens-front artwork" in semantic["explicit_cap_surface_terms"]
    assert "front face of a lens cap" in semantic["explicit_cap_surface_terms"]
    assert "前盖正面" in semantic["explicit_cap_surface_terms"]


def test_named_lens_surface_route_positive_and_negative_matrix() -> None:
    """Exercise the documented host-neutral semantic rule, not just its prose."""

    import json

    data = json.loads((ROOT / "skills/manifest.json").read_text(encoding="utf-8"))
    semantic = data["routing_policy"]["lens_cap_intent"]["semantic_match"]
    surfaces = tuple(str(item).lower() for item in semantic["surface_terms"])
    verbs = tuple(str(item).lower() for item in semantic["verbs"])
    exclusions = tuple(str(item).lower() for item in semantic["exclude_without_surface_intent"])
    non_cap = tuple(str(item).lower() for item in semantic["non_cap_deliverable_terms"])
    explicit_cap = tuple(
        str(item).lower() for item in semantic["explicit_cap_surface_terms"]
    )

    def matches(prompt: str, *, named_lens: bool = True) -> bool:
        text = prompt.casefold()
        has_explicit_cap = any(term in text for term in explicit_cap)
        if not named_lens and not has_explicit_cap:
            return False
        if not any(verb in text for verb in verbs) and not has_explicit_cap:
            return False
        if any(term in text for term in non_cap):
            return False
        has_surface = any(term.casefold() in text for term in surfaces)
        if not has_surface:
            return False
        # An optical-design/repair exclusion wins unless the user also states
        # an explicit cap/relief surface deliverable.
        if any(term in text for term in exclusions) and not has_explicit_cap:
            return False
        return True

    assert matches("Design lens-cap front artwork for Zeiss Planar 50mm F1.4")
    assert matches("用康泰时 50mm F1.4 设计镜头盖正面图案")
    assert matches("Sigma 28-70mm F2.8 做镜头盖浮雕")
    assert matches("Design circular lens-front artwork for the Sigma 28-70mm F2.8")
    assert matches("Make a printable lens cap for the Sigma 28-70mm F2.8")
    assert matches("为适马 28-70mm F2.8 生成可打印镜头盖")
    assert matches("Design a lens cap", named_lens=False)
    assert matches("设计镜头帽", named_lens=False)
    assert not matches("Design a circular image", named_lens=False)
    assert not matches("Design an optical diagram for a Zeiss Planar 50mm F1.4")
    assert not matches("Design an optical relief map for a Zeiss Planar 50mm F1.4")
    assert not matches("Design an optical circular image for a Zeiss Planar 50mm F1.4")
    assert not matches("为 Zeiss 50mm 镜片设计圆形产品照片")
    assert not matches("为 Zeiss 50mm F1.4 设计圆形图像产品照片")
    assert not matches("为 Zeiss 50mm F1.4 设计圆形艺术图产品海报")
    assert not matches("为圆形镜片产品照片设计一张海报")
    assert not matches("Design an award badge for a Zeiss Planar 50mm F1.4 lens")
    assert not matches("Export 3MF metadata for a Zeiss Planar 50mm F1.4 lens photo")
    assert not matches("Make a printable poster with a Zeiss Planar 50mm F1.4 lens")
    assert not matches("设计圆形图像", named_lens=False)
    assert not matches("Repair a Zeiss 50mm F1.4 lens")


def test_resolver_documents_catalog_reload_and_named_lens_surface_route() -> None:
    resolver = (ROOT / "skills/RESOLVER.md").read_text(encoding="utf-8")
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    for document in (resolver, agents):
        lowered = document.lower()
        assert "named camera lens" in lowered
        assert "front" in lowered and "3mf" in lowered
    assert "new Codex task" in resolver


def test_host_metadata_keeps_the_same_exclusive_hierarchy_and_intake() -> None:
    imagegen_agent = (ROOT / "skills/lens-cap-imagegen/agents/openai.yaml").read_text(encoding="utf-8").lower()
    production_agent = (ROOT / "skills/lens-cap-production/agents/openai.yaml").read_text(encoding="utf-8").lower()
    for document in (imagegen_agent, production_agent):
        assert "focal length" in document
        assert "maximum aperture" in document
        assert "f-stop" in document and "f-number" in document
        assert "friction" in document and "enabled by default" in document
        assert "do not" in document and "design" in document
        assert "circular" in document and "optical design" in document
        assert "approval" in document and "hash" in document
    assert "mating diameter" in imagegen_agent
    assert "foam" in imagegen_agent and "uncompressed" in imagegen_agent
    assert "mating diameter" in production_agent
    assert "foam" in production_agent and "uncompressed" in production_agent


def test_fitted_intake_is_a_persisted_gate_before_first_production_command() -> None:
    """Keep the clean-room handoff order explicit in every host-facing copy."""

    imagegen = (ROOT / "skills/lens-cap-imagegen/SKILL.md").read_text(encoding="utf-8").lower()
    production = (ROOT / "skills/lens-cap-production/SKILL.md").read_text(encoding="utf-8").lower()
    imagegen_zh = (ROOT / "skills/lens-cap-imagegen/SKILL.zh-CN.md").read_text(encoding="utf-8")
    production_zh = (ROOT / "skills/lens-cap-production/SKILL.zh-CN.md").read_text(encoding="utf-8")
    resolver = (ROOT / "skills/RESOLVER.md").read_text(encoding="utf-8").lower()
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8").lower()
    imagegen_agent = (ROOT / "skills/lens-cap-imagegen/agents/openai.yaml").read_text(encoding="utf-8").lower()
    production_agent = (ROOT / "skills/lens-cap-production/agents/openai.yaml").read_text(encoding="utf-8").lower()
    # Markdown/YAML wrapping is presentation-only; compare normalized prose so
    # a harmless line-wrap edit cannot disable this acceptance gate.
    imagegen_flat = " ".join(imagegen.split())
    production_flat = " ".join(production.split())
    resolver_flat = " ".join(resolver.split())
    agents_flat = " ".join(agents.split())
    imagegen_agent_flat = " ".join(imagegen_agent.split())
    production_agent_flat = " ".join(production_agent.split())

    # ImageGen may make concept art first, but its handoff cannot enter the
    # production route until the grouped physical answers are persisted.
    assert "first production gate" in imagegen_flat
    assert "concept art may be generated before it, but do not invoke" in imagegen_flat
    assert "before the first geometry/build/export/3mf command" in imagegen_agent_flat
    assert "before the first fitted geometry/build/export/3mf command" in production_agent_flat
    assert "persist all three answers before entering production" in production_agent_flat

    # The production Skill and the host-level resolver repeat the same gate so
    # a clean context cannot interpret the intake as an optional late check.
    assert "hard gate before the first geometry" in production_flat
    assert "answers are persisted" in production_flat
    assert "首次 geometry、build、export 或 3mf 命令" in " ".join(production_zh.lower().split())
    assert "进入生产的第一道门禁" in imagegen_zh
    assert "before the first geometry/build/export/3mf command" in resolver_flat
    assert "before the first geometry/build/export/3mf command" in agents_flat

    for document in (imagegen_flat, production_flat, resolver_flat, agents_flat):
        assert "friction" in document and "default" in document
    for document in (imagegen_flat, production_flat):
        assert "do not ask for a second relief" in document
        assert "do not ask" in document


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
