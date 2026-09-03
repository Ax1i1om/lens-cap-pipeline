"""Executable checks for the manifest-driven lens-cap routing shim."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import resolve_skill_route as resolver  # noqa: E402


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        (
            "Design circular lens-front artwork for the Sigma 28-70mm F2.8 lens",
            ["lens-cap-imagegen"],
        ),
        (
            "Design a circular lens-cap front pattern for Zeiss Planar 50mm F1.4 and export a 3MF",
            ["lens-cap-imagegen", "lens-cap-production"],
        ),
        (
            "为八羽怪58F2设计圆形镜头盖正面徽章并输出3MF",
            ["lens-cap-imagegen", "lens-cap-production"],
        ),
        (
            "给康泰时 Planar 50mm F1.4 做圆形镜头盖正面",
            ["lens-cap-imagegen"],
        ),
        (
            "Sigma 28-70mm F2.8 circular lens-cap badge",
            ["lens-cap-imagegen"],
        ),
        (
            "Helios 44-2 58mm F2 lens-cap 3MF",
            ["lens-cap-imagegen", "lens-cap-production"],
        ),
        (
            "为康泰时50 1.4 CY做圆形镜头正面图稿",
            ["lens-cap-imagegen"],
        ),
        (
            "用适马2870设计镜头盖圆形图像",
            ["lens-cap-imagegen"],
        ),
    ],
)
def test_manifest_route_matches_realistic_named_lens_requests(
    prompt: str, expected: list[str]
) -> None:
    report = resolver.resolve_request(prompt)
    assert report["status"] == "matched"
    assert report["route"] == expected
    assert report["generic_parallel_skills"] == []


@pytest.mark.parametrize(
    "prompt",
    [
        "Canon 50mm F1.4 lens-cap front image",
        "Canon FD 35-105 front-cap artwork",
        "Canon FD 35-105 lens-cap badge",
        "佳能50 1.4镜头盖前口图像",
        "佳能50 1.4镜头盖正面设计",
        "佳能50 1.4镜头盖铭牌",
        "给东德蔡司135F3.5 JENA DDR做一个镜头盖正面圆形图",
        "请做成可打印的镜头盖模型：Mamiya 645 80mm F1.9",
    ],
)
def test_natural_surface_aliases_route_named_lenses(prompt: str) -> None:
    report = resolver.resolve_request(prompt)
    assert report["status"] == "matched"
    assert report["route"]


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        (
            "Design a circular image for the Sigma 28-70mm F2.8 lens",
            ["lens-cap-imagegen"],
        ),
        (
            "Sigma 28-70mm F2.8 circular lens badge",
            ["lens-cap-imagegen"],
        ),
        (
            "为适马 28-70mm F2.8 镜头设计圆形图像",
            ["lens-cap-imagegen"],
        ),
    ],
)
def test_core_natural_ownership_phrases_remain_routable(
    prompt: str, expected: list[str]
) -> None:
    report = resolver.resolve_request(prompt)
    assert report["status"] == "matched"
    assert report["route"] == expected
    assert report["matched_terms"]["non_cap_deliverable"] == []


@pytest.mark.parametrize(
    "prompt",
    [
        "Helios 44-2 58mm F2 3MF",
        "Create STL for Zeiss Planar 50mm F1.4 lens",
        "请做成可打印的模型：Mamiya 645 80mm F1.9",
    ],
)
def test_bare_format_shorthand_requires_established_cap_context(prompt: str) -> None:
    clean_report = resolver.resolve_request(prompt)
    assert clean_report["status"] == "not_matched"
    assert clean_report["production_intent"] is False

    contextual_report = resolver.resolve_request(prompt, lens_cap_context=True)
    assert contextual_report["status"] == "matched"
    assert contextual_report["route"] == ["lens-cap-imagegen", "lens-cap-production"]
    assert contextual_report["production_intent"] is True


@pytest.mark.parametrize(
    "prompt",
    [
        "Design a circular image for a generic 50mm F1.4 lens",
        "Design a circular image for a black and white 50mm F1.4 lens",
        "Design a circular image for an unknown 50mm F1.4 lens",
        "Design a circular image in Bauhaus style for a 50mm F1.4 lens",
        "为一颗漂亮的50mm F1.4镜头设计圆形图像",
        "Design a bold 50mm F1.4 circular image",
        "Design a bold 28-70mm F3.5-5.6 circular image",
        "为复古风格 50mm F1.4 设计圆形图像",
    ],
)
def test_style_or_generic_latin_words_are_not_lens_identity(prompt: str) -> None:
    report = resolver.resolve_request(prompt)
    assert report["status"] == "not_matched"
    assert report["named_lens_detected"] is False
    assert "named_lens_required" in report["reasons"]


@pytest.mark.parametrize("generic_word", ["REHOUSE", "rehoused", "CINE", "cinema"])
def test_generic_cinema_terms_do_not_impersonate_a_lens_identity(
    generic_word: str,
) -> None:
    report = resolver.resolve_request(
        "Please design a cap for a common "
        f"{generic_word} vintage lens and deliver a printable 3MF; "
        "the measured mating diameter is 95mm"
    )
    assert report["status"] == "matched"
    assert report["route"] == ["lens-cap-imagegen", "lens-cap-production"]
    assert report["named_lens_detected"] is False
    assert "lens_identity_intake_required" in report["reasons"]


@pytest.mark.parametrize(
    "prompt",
    [
        "Design a circular image for Schneider 50mm F2.8 lens",
        "Design a circular lens badge for Rodenstock 80mm F4 lens",
        "Design a circular image for Acme Xenon-50 50mm F2 lens",
        "为潘太康50mm F1.8设计圆形图像",
    ],
)
def test_proper_or_catalog_identity_is_not_limited_to_brand_whitelist(prompt: str) -> None:
    report = resolver.resolve_request(prompt)
    assert report["status"] == "matched"
    assert report["named_lens_detected"] is True


@pytest.mark.parametrize(
    "prompt",
    [
        "试试康泰时28F2",
        "再用 Sigma 28-70 测试",
        "Try 八羽怪",
        "再试试八羽怪",
        "Try 八枚玉",
        "再试试八枚玉",
    ],
)
def test_terse_lens_continuation_routes_without_repeating_surface_noun(prompt: str) -> None:
    clean_report = resolver.resolve_request(prompt)
    assert clean_report["status"] == "not_matched"
    assert clean_report["lens_cap_context"] is False

    report = resolver.resolve_request(prompt, lens_cap_context=True)
    assert report["status"] == "matched"
    assert report["lens_cap_context"] is True
    assert report["matched_terms"]["implicit_surface"]


@pytest.mark.parametrize(
    "prompt",
    ["Try Zeiss Planar", "Try Helios", "Test Canon FD", "Try Olympus Zuiko", "Try Contax Planar"],
)
def test_contextual_latin_family_names_route_and_request_normalization(prompt: str) -> None:
    assert resolver.resolve_request(prompt)["status"] == "not_matched"
    report = resolver.resolve_request(prompt, lens_cap_context=True)
    assert report["status"] == "matched"
    assert report["route"] == ["lens-cap-imagegen"]
    assert report["identity_needs_normalization"] is True


@pytest.mark.parametrize("prompt", ["试试漂亮风格", "Try a generic lens", "再试试普通镜头"])
def test_context_does_not_turn_unnamed_descriptions_into_lens_identity(prompt: str) -> None:
    report = resolver.resolve_request(prompt, lens_cap_context=True)
    assert report["status"] == "not_matched"
    assert report["named_lens_detected"] is False


@pytest.mark.parametrize(
    "prompt",
    [
        "Design a bold 50mm F1.4 lens cap",
        "Design an elegant 50mm F1.4 lens-cap",
        "Create a minimalist 50mm F1.4 camera lens cap",
        "Design an unknown brand 50mm F1.4 lens cap",
        "设计复古风格的50mm F1.4镜头盖",
        "为一颗漂亮的50mm F1.4镜头设计圆形镜头盖",
        "生成黑白极简50mm F1.4镜头盖图案",
        "设计未知品牌50mm F1.4镜头盖",
    ],
)
def test_explicit_cap_routes_but_style_words_do_not_fake_identity(prompt: str) -> None:
    report = resolver.resolve_request(prompt)
    assert report["status"] == "matched"
    assert report["route"] == ["lens-cap-imagegen"]
    assert report["named_lens_detected"] is False
    assert "lens_identity_intake_required" in report["reasons"]


@pytest.mark.parametrize(
    "prompt",
    [
        "Test chromatic aberration on the Zeiss Planar 50mm F1.4 lens",
        "Test distortion on the Sigma 28-70mm F2.8 lens",
        "Try bokeh with the Helios-44-2 58mm F2 lens",
        "测试蔡司 Planar 50mm F1.4 镜头的色差",
        "测试适马 28-70mm F2.8 镜头的畸变",
        "试试 Helios-44-2 58mm F2 镜头的焦外",
    ],
)
def test_optical_tests_do_not_borrow_established_cap_context(prompt: str) -> None:
    report = resolver.resolve_request(prompt, lens_cap_context=True)
    assert report["status"] == "not_matched"
    assert report["route"] == []
    assert report["matched_terms"]["exclusions"]


def test_cli_requires_explicit_context_flag_for_terse_continuation(capsys) -> None:
    assert resolver.main(["试试康泰时28F2", "--json"]) == 1
    capsys.readouterr()
    assert resolver.main(["试试康泰时28F2", "--lens-cap-context", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["route"] == ["lens-cap-imagegen"]


def test_approved_artwork_skips_only_the_creative_stage() -> None:
    prompt = "Export a printable 3MF lens-cap front for the approved Helios-44-2 58mm F2"
    report = resolver.resolve_request(prompt, approved_artwork=True)
    assert report["status"] == "matched"
    assert report["route"] == ["lens-cap-production"]


@pytest.mark.parametrize(
    "prompt",
    [
        "Create an STL lens-cap model for Zeiss Planar 50mm F1.4",
        "Design a SCAD front-cap model for Zeiss Planar 50mm F1.4",
        "Build a CAD lens cap for Zeiss Planar 50mm F1.4",
        "Print a lens cap for Zeiss Planar 50mm F1.4",
        "Zeiss Planar 50mm F1.4 lens cap fit",
    ],
)
def test_production_shorthand_routes_to_both_stages(prompt: str) -> None:
    report = resolver.resolve_request(prompt)
    assert report["status"] == "matched"
    assert report["route"] == ["lens-cap-imagegen", "lens-cap-production"]


@pytest.mark.parametrize(
    "prompt",
    [
        "Inspect Helios-44-2 58mm F2 3MF support",
        "Explain STL export for the Zeiss Planar 50mm F1.4 lens",
        "检查 Helios-44-2 58mm F2 镜头是否支持 3MF",
        "解释蔡司 Planar 50mm F1.4 镜头的 STL 导出流程",
    ],
)
def test_format_inspection_or_explanation_is_not_a_production_request(prompt: str) -> None:
    report = resolver.resolve_request(prompt)
    assert report["status"] == "not_matched"
    assert report["route"] == []
    assert report["production_intent"] is False
    assert report["matched_terms"]["non_production_action"]


@pytest.mark.parametrize(
    "prompt",
    [
        "Create a 3MF model of the Sigma 28-70mm F2.8 lens body",
        "Export the Zeiss Planar 50mm F1.4 focusing ring as STL",
        "Generate CAD for the Helios-44-2 58mm F2 barrel and focus gear",
        "Make a printable lens hood and mount for Mamiya 645 80mm F1.9",
    ],
)
def test_lens_component_formats_never_become_cap_production(prompt: str) -> None:
    for lens_cap_context in (False, True):
        report = resolver.resolve_request(prompt, lens_cap_context=lens_cap_context)
        assert report["status"] == "not_matched"
        assert report["route"] == []
        assert report["production_intent"] is False
        assert report["matched_terms"]["lens_component_deliverable"]


@pytest.mark.parametrize(
    "prompt",
    [
        "Create STL for GoPro Hero 12 cage",
        "Make an STL for Arca Swiss 60mm plate",
        "Test ISO100 label",
        "Generate CAD for a SmallRig camera grip",
        "Create a 3MF travel case",
    ],
)
def test_sticky_context_does_not_capture_a_new_non_lens_deliverable(prompt: str) -> None:
    report = resolver.resolve_request(prompt, lens_cap_context=True)
    assert report["status"] == "not_matched"
    assert report["route"] == []
    assert report["production_intent"] is False


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        (
            "Make a cap for Sigma 28-70mm F2.8",
            ["lens-cap-imagegen", "lens-cap-production"],
        ),
        (
            "Design a cap for my Zeiss Planar 50mm F1.4",
            ["lens-cap-imagegen"],
        ),
        (
            "为适马 28-70mm F2.8 设计镜头帽",
            ["lens-cap-imagegen"],
        ),
    ],
)
def test_cap_for_named_lens_ownership_routes(prompt: str, expected: list[str]) -> None:
    report = resolver.resolve_request(prompt)
    assert report["status"] == "matched"
    assert report["route"] == expected
    assert report["matched_terms"]["cap_object"]


@pytest.mark.parametrize(
    "prompt",
    [
        "Create Helios-44-2 58mm F2 lens cap artwork in a screen-print style",
        "Create Helios-44-2 58mm F2 lens cap artwork in a blueprint style",
        "Create Helios-44-2 58mm F2 lens cap artwork with a graffiti motif",
        "Create Helios-44-2 58mm F2 lens cap artwork about a decade of cinema",
        "Create Helios-44-2 58mm F2 lens cap artwork in a woodcut print aesthetic",
        "Create Helios-44-2 58mm F2 lens cap artwork and make the type fit the circle",
        "Invoke ImageGen for Helios-44-2 58mm F2 lens cap artwork",
        "Run another Helios-44-2 58mm F2 lens-cap concept",
        "Create Helios-44-2 58mm F2 lens cap artwork with a slice-of-life cinema motif",
        "Design circular lens-cap artwork for Zeiss Planar 50mm F1.4 in screen-print style",
        "Design circular lens-cap artwork for Zeiss Planar 50mm F1.4 in blueprint style",
    ],
)
def test_artwork_style_substrings_do_not_trigger_production(prompt: str) -> None:
    report = resolver.resolve_request(prompt)
    assert report["status"] == "matched"
    assert report["route"] == ["lens-cap-imagegen"]
    assert report["production_intent"] is False
    assert report["matched_terms"]["production"] == []


@pytest.mark.parametrize(
    "prompt",
    [
        "为蔡司 Planar 50mm F1.4 设计圆形镜头盖图案，模型名称用小字",
        "为蔡司 Planar 50mm F1.4 设计圆形镜头盖图案，让构图适配极简版画风格",
        "为蔡司 Planar 50mm F1.4 设计圆形镜头盖图案，参考登月舱模型的几何",
    ],
)
def test_chinese_artwork_model_or_style_words_do_not_trigger_production(prompt: str) -> None:
    report = resolver.resolve_request(prompt)
    assert report["status"] == "matched"
    assert report["route"] == ["lens-cap-imagegen"]
    assert report["production_intent"] is False


@pytest.mark.parametrize(
    "prompt",
    [
        "Write a cover article reviewing the Sigma 28-70mm F2.8 lens",
        "Create a lens review badge for the Zeiss Planar 50mm F1.4",
        "Use the Helios-44-2 58mm F2 lens to shoot a printable poster",
        "Export this photo shot with the Mamiya 80mm F1.9 lens as 3MF metadata",
        "写一篇适马 28-70mm F2.8 镜头评测封面文章",
        "给蔡司 Planar 50mm F1.4 镜头评测做徽章",
        "用 Helios-44-2 58mm F2 镜头拍一张可打印海报",
        "把用 Mamiya 80mm F1.9 镜头拍的照片导出为 3MF metadata",
    ],
)
def test_non_cap_objects_do_not_inherit_broad_surface_or_format_aliases(prompt: str) -> None:
    report = resolver.resolve_request(prompt)
    assert report["status"] == "not_matched"
    assert report["route"] == []
    assert report["production_intent"] is False
    assert report["matched_terms"]["cap_object"] == []


@pytest.mark.parametrize(
    "prompt",
    [
        "Create a protective cover for the Sigma 28-70mm F2.8 lens manual",
        "Make a printable warranty card for the Zeiss Planar 50mm F1.4 lens",
        "Export 3MF metadata for photos from the Helios-44-2 58mm F2 lens",
        "Design an award badge for the Mamiya 80mm F1.9 lens",
        "Make a dust cover for the Sigma 28-70mm F2.8 lens pouch",
        "Write a user manual for the Zeiss Planar 50mm F1.4 lens cover",
        "Photos from the Helios-44-2 58mm F2 lens: export 3MF metadata",
        "为适马 28-70mm F2.8 镜头说明书制作保护封面",
        "为蔡司 Planar 50mm F1.4 镜头制作可打印保修卡",
        "把 Helios-44-2 58mm F2 镜头照片导出为 3MF metadata",
        "为 Mamiya 80mm F1.9 镜头设计奖项徽章",
        "为适马 28-70mm F2.8 镜头包制作防尘罩",
        "为蔡司 Planar 50mm F1.4 镜头罩写用户手册",
        "Helios-44-2 58mm F2 镜头的照片：导出 3MF 元数据",
    ],
)
def test_broad_aliases_never_create_a_cap_object(prompt: str) -> None:
    report = resolver.resolve_request(prompt)
    assert report["status"] == "not_matched"
    assert report["route"] == []
    assert report["production_intent"] is False
    assert report["matched_terms"]["cap_object"] == []


@pytest.mark.parametrize(
    "prompt",
    [
        "Make a physical front cap for the Sigma 28-70mm F2.8 lens",
        "Design the front face of a lens cap for the Zeiss Planar 50mm F1.4",
        "为 Helios-44-2 58mm F2 镜头做镜头前盖",
        "为 Mamiya 80mm F1.9 制作实体盖正面并输出 3MF",
    ],
)
def test_explicit_physical_cap_objects_still_route(prompt: str) -> None:
    report = resolver.resolve_request(prompt)
    assert report["status"] == "matched"
    assert report["route"]
    assert report["matched_terms"]["cap_object"]


@pytest.mark.parametrize(
    "prompt",
    [
        "Design a circular image for a lens 50mm F1.4",
        "为一颗 50mm F1.4 镜头设计圆形图像",
        "Design an optical relief map for the Zeiss Planar 50mm F1.4 lens",
        "Repair the Sigma 28-70mm F2.8 lens and make a product photo",
        "为 Zeiss 50mm F1.4 镜片设计圆形产品照片",
        "为任意镜头生成可打印模型",
        "为某品牌的某颗镜头制作圆形正面并完成3MF",
        "Please design a circular image for 50mm F1.4 lens",
        "Canon FD 35-105 cover",
        "Canon FD 35-105 badge",
    ],
)
def test_manifest_route_rejects_unnamed_or_non_cap_near_misses(prompt: str) -> None:
    report = resolver.resolve_request(prompt)
    assert report["status"] == "not_matched"
    assert report["route"] == []


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        ("可以给我做一个镜头盖吗", ["lens-cap-imagegen"]),
        ("给它做个圆形镜头帽", ["lens-cap-imagegen"]),
        ("我要镜头闷盖", ["lens-cap-imagegen"]),
        ("Design a lens cap", ["lens-cap-imagegen"]),
        ("Design a lens-cap", ["lens-cap-imagegen"]),
        (
            "为某镜头设计镜头盖并得到可打印3MF",
            ["lens-cap-imagegen", "lens-cap-production"],
        ),
        (
            "Design a printable lens cap for a certain lens",
            ["lens-cap-imagegen", "lens-cap-production"],
        ),
        (
            "设计一个未知品牌50mm F1.4镜头盖浮雕",
            ["lens-cap-imagegen", "lens-cap-production"],
        ),
        (
            "Make a 3MF cap for an unspecified camera lens",
            ["lens-cap-imagegen", "lens-cap-production"],
        ),
    ],
)
def test_explicit_cap_object_routes_before_identity_intake(
    prompt: str, expected: list[str]
) -> None:
    report = resolver.resolve_request(prompt)
    assert report["status"] == "matched"
    assert report["route"] == expected
    assert report["named_lens_detected"] is False
    assert report["matched_terms"]["cap_object"]
    assert "lens_identity_intake_required" in report["reasons"]


def test_explicit_cap_surface_overrides_optical_word_nearby() -> None:
    report = resolver.resolve_request(
        "For the Zeiss Planar 50mm F1.4 lens, design an optical-inspired circular lens-cap front"
    )
    assert report["status"] == "matched"
    assert report["route"] == ["lens-cap-imagegen"]


def test_negated_lens_cap_does_not_override_lens_barrel_deliverable() -> None:
    for lens_cap_context in (False, True):
        report = resolver.resolve_request(
            "Do not design a lens cap; design the lens barrel for Sigma 28-70 F2.8",
            lens_cap_context=lens_cap_context,
        )
        assert report["status"] == "not_matched"
        assert report["route"] == []
        assert report["matched_terms"]["negated_cap_object"]


def test_not_a_lens_cap_request_does_not_override_bokeh_explanation() -> None:
    for lens_cap_context in (False, True):
        report = resolver.resolve_request(
            "This is not a lens cap request; explain bokeh of Helios 44-2",
            lens_cap_context=lens_cap_context,
        )
        assert report["status"] == "not_matched"
        assert report["route"] == []
        assert report["matched_terms"]["negated_cap_object"]


def test_chinese_negated_cap_does_not_override_focus_gear_deliverable() -> None:
    for lens_cap_context in (False, True):
        report = resolver.resolve_request(
            "不要设计镜头盖，请设计适马2870 F2.8的对焦齿圈",
            lens_cap_context=lens_cap_context,
        )
        assert report["status"] == "not_matched"
        assert report["route"] == []
        assert report["matched_terms"]["negated_cap_object"]


def test_chinese_not_a_cap_request_does_not_override_lens_hood() -> None:
    for lens_cap_context in (False, True):
        report = resolver.resolve_request(
            "不是镜头盖，是佳能FD 50 F1.4遮光罩",
            lens_cap_context=lens_cap_context,
        )
        assert report["status"] == "not_matched"
        assert report["route"] == []
        assert report["matched_terms"]["negated_cap_object"]


def test_auditing_lens_cap_skill_is_not_a_cap_design_deliverable() -> None:
    for lens_cap_context in (False, True):
        report = resolver.resolve_request(
            "Please audit our lens cap design skill",
            lens_cap_context=lens_cap_context,
        )
        assert report["status"] == "not_matched"
        assert report["route"] == []
        assert report["matched_terms"]["meta_or_read_only_task"]


def test_updating_lens_cap_skill_docs_is_not_a_cap_design_deliverable() -> None:
    for lens_cap_context in (False, True):
        report = resolver.resolve_request(
            "Update the lens cap design skill documentation",
            lens_cap_context=lens_cap_context,
        )
        assert report["status"] == "not_matched"
        assert report["route"] == []
        assert report["matched_terms"]["meta_or_read_only_task"]


def test_why_question_about_prior_cap_design_is_read_only() -> None:
    for lens_cap_context in (False, True):
        report = resolver.resolve_request(
            "Why did you design a lens cap?",
            lens_cap_context=lens_cap_context,
        )
        assert report["status"] == "not_matched"
        assert report["route"] == []
        assert report["matched_terms"]["meta_or_read_only_task"]


def test_later_affirmative_cap_deliverable_overrides_an_earlier_negated_one() -> None:
    report = resolver.resolve_request(
        "Do not design a lens cap for Canon; instead design a lens cap for Sigma 28-70mm F2.8"
    )
    assert report["status"] == "matched"
    assert report["route"] == ["lens-cap-imagegen"]


def test_meta_clause_does_not_veto_a_later_explicit_cap_deliverable() -> None:
    report = resolver.resolve_request(
        "Audit our lens cap design skill, then design a lens cap for Sigma 28-70mm F2.8"
    )
    assert report["status"] == "matched"
    assert report["route"] == ["lens-cap-imagegen"]


def test_action_conjunction_keeps_later_affirmative_cap_deliverable() -> None:
    report = resolver.resolve_request(
        "Test the lens cap skill trigger and design a lens cap for Sigma 28-70mm F2.8"
    )
    assert report["status"] == "matched"
    assert report["route"] == ["lens-cap-imagegen"]


@pytest.mark.parametrize(
    "prompt",
    [
        "Please review the Sigma 28-70mm F2.8 lens cap design",
        "How was this lens cap designed?",
        "Lens cap design skill documentation: audit it",
        "请审计我们的镜头盖设计SKILL",
        "更新镜头盖设计SKILL文档",
        "为什么你设计了镜头盖？",
    ],
)
def test_read_only_or_meta_cap_tasks_do_not_route(prompt: str) -> None:
    report = resolver.resolve_request(prompt, lens_cap_context=True)
    assert report["status"] == "not_matched"
    assert report["route"] == []
    assert report["matched_terms"]["meta_or_read_only_task"]


@pytest.mark.parametrize(
    "prompt",
    [
        "Anything except a lens cap for Canon 50 F1.4",
        "Without a lens cap, make the barrel",
        "镜头盖不要，做适马2870的对焦环",
        "我不想要镜头盖，要遮光罩",
        "请跳过镜头盖，设计镜身",
    ],
)
def test_additional_preposed_or_postposed_cap_negations_do_not_route(
    prompt: str,
) -> None:
    for lens_cap_context in (False, True):
        report = resolver.resolve_request(prompt, lens_cap_context=lens_cap_context)
        assert report["status"] == "not_matched"
        assert report["route"] == []
        assert report["matched_terms"]["negated_cap_object"]


@pytest.mark.parametrize(
    "prompt",
    [
        "Design anything but a lens cap for Sigma 28-70 F2.8",
        "Design something other than a lens cap for Sigma 28-70 F2.8",
        "除镜头盖外，为适马28-70 F2.8做任何设计",
    ],
)
def test_exceptive_cap_negations_do_not_route_in_clean_or_sticky_context(prompt: str) -> None:
    for lens_cap_context in (False, True):
        report = resolver.resolve_request(prompt, lens_cap_context=lens_cap_context)
        assert report["status"] == "not_matched"
        assert report["route"] == []
        assert report["matched_terms"]["negated_cap_object"]


@pytest.mark.parametrize(
    "prompt",
    [
        "Debug the lens cap pipeline",
        "Test lens cap skill trigger",
        "Does this skill support lens cap design?",
        "What is a lens cap design skill?",
        "Compare lens cap design tools",
        "Tell me about lens cap design",
        "Open the lens cap design task",
        "Archive the lens cap design task",
    ],
)
def test_cap_capability_and_task_management_requests_do_not_route(prompt: str) -> None:
    for lens_cap_context in (False, True):
        report = resolver.resolve_request(prompt, lens_cap_context=lens_cap_context)
        assert report["status"] == "not_matched"
        assert report["route"] == []
        assert report["matched_terms"]["meta_or_read_only_task"]


@pytest.mark.parametrize(
    "prompt",
    [
        "Show the lens cap design documentation for Sigma 28-70 F2.8",
        "Is the Sigma 28-70 F2.8 lens cap design task done?",
        "Run tests for Sigma 28-70 F2.8 lens cap generation",
        "Fix the lens cap generator for Sigma 28-70 F2.8",
        "适马28-70 F2.8镜头盖设计支持哪些格式",
    ],
)
def test_additional_meta_and_status_requests_do_not_route(prompt: str) -> None:
    for lens_cap_context in (False, True):
        report = resolver.resolve_request(prompt, lens_cap_context=lens_cap_context)
        assert report["status"] == "not_matched"
        assert report["route"] == []
        assert report["matched_terms"]["meta_or_read_only_task"]


def test_sticky_context_does_not_turn_lens_hood_3mf_into_cap_production() -> None:
    report = resolver.resolve_request(
        "把适马28-70 F2.8做成遮光罩3MF", lens_cap_context=True
    )
    assert report["status"] == "not_matched"
    assert report["route"] == []
    assert report["matched_terms"]["non_cap_deliverable"]


@pytest.mark.parametrize(
    "prompt",
    [
        "在已有镜头盖任务里，改做对焦环。",
        "把镜头盖改做对焦环。",
        "We already have a lens-cap task; instead make a focusing ring.",
    ],
)
def test_prior_cap_context_does_not_capture_a_replacement_lens_component(
    prompt: str,
) -> None:
    for lens_cap_context in (False, True):
        report = resolver.resolve_request(prompt, lens_cap_context=lens_cap_context)
        assert report["status"] == "not_matched"
        assert report["route"] == []
        assert report["matched_terms"]["lens_component_deliverable"]


def test_focusing_ring_may_be_an_inspiration_for_an_explicit_cap_design() -> None:
    report = resolver.resolve_request("设计带对焦环刻度语言的适马28-70 F2.8镜头盖正面")
    assert report["status"] == "matched"
    assert report["route"] == ["lens-cap-imagegen"]


@pytest.mark.parametrize(
    "prompt",
    [
        (
            "只做只读审计，不修改文件。模拟用户自然请求：『帮我设计一枚适马 "
            "28-70mm F2.8 镜头盖，最后给我可打印 3MF。』不生成或改文件。"
        ),
        "测试下面提示词是否触发：『为康泰时 50mm F1.4 设计镜头盖并输出3MF』",
        (
            "Perform a read-only audit. Simulate this user request: "
            "\"Design a Sigma 28-70mm F2.8 lens cap and export 3MF.\" "
            "Do not generate any files."
        ),
    ],
)
def test_quoted_or_simulated_cap_requests_are_meta_data_not_deliverables(
    prompt: str,
) -> None:
    report = resolver.resolve_request(prompt)
    assert report["status"] == "not_matched"
    assert report["route"] == []
    assert report["production_intent"] is False
    assert report["matched_terms"]["meta_wrapper"]
    assert "quoted_affirmative_deliverable_ignored" in report["reasons"]


def test_named_lens_override_covers_attachment_or_catalog_identity() -> None:
    report = resolver.resolve_request(
        "Make the attached identified lens into a circular lens-cap front badge",
        named_lens=True,
    )
    assert report["status"] == "matched"
    assert report["route"] == ["lens-cap-imagegen"]


def test_cli_returns_machine_readable_not_matched_result(capsys) -> None:
    status = resolver.main(["Design a generic round logo", "--json"])
    assert status == 1
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "not_matched"
    assert "named_lens_required" in report["reasons"]
