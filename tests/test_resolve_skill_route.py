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
            "Design a circular image for the Sigma 28-70mm F2.8 lens",
            ["lens-cap-imagegen"],
        ),
        (
            "Design a circular front pattern for Zeiss Planar 50mm F1.4 and export a 3MF",
            ["lens-cap-imagegen", "lens-cap-production"],
        ),
        (
            "为八羽怪58F2设计圆形镜头徽章并输出3MF",
            ["lens-cap-imagegen", "lens-cap-production"],
        ),
        (
            "给康泰时 Planar 50mm F1.4 做圆形镜头正面",
            ["lens-cap-imagegen"],
        ),
        (
            "Sigma 28-70mm F2.8 circular lens badge",
            ["lens-cap-imagegen"],
        ),
        (
            "Helios 44-2 58mm F2 3MF",
            ["lens-cap-imagegen", "lens-cap-production"],
        ),
        (
            "为康泰时50 1.4 CY做圆形镜头图稿",
            ["lens-cap-imagegen"],
        ),
        (
            "用适马2870设计圆形图像",
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


def test_approved_artwork_skips_only_the_creative_stage() -> None:
    prompt = "Export a printable 3MF front for the approved Helios-44-2 58mm F2 lens relief"
    report = resolver.resolve_request(prompt, approved_artwork=True)
    assert report["status"] == "matched"
    assert report["route"] == ["lens-cap-production"]


@pytest.mark.parametrize(
    "prompt",
    [
        "Create STL for Zeiss Planar 50mm F1.4 lens",
        "Design a SCAD for Zeiss Planar 50mm F1.4 lens",
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
        "Design a circular image for a lens 50mm F1.4",
        "为一颗 50mm F1.4 镜头设计圆形图像",
        "Design an optical relief map for the Zeiss Planar 50mm F1.4 lens",
        "Repair the Sigma 28-70mm F2.8 lens and make a product photo",
        "为 Zeiss 50mm F1.4 镜片设计圆形产品照片",
        "可以给我做一个镜头盖吗",
        "给它做个圆形镜头盖",
        "我要镜头盖",
        "只做镜头盖，不要设计",
        "请给我一个圆形镜头盖",
        "为任意镜头生成可打印模型",
        "Please design a circular image for 50mm F1.4 lens",
        "can you make a lens cap 50mm F1.4",
    ],
)
def test_manifest_route_rejects_unnamed_or_non_cap_near_misses(prompt: str) -> None:
    report = resolver.resolve_request(prompt)
    assert report["status"] == "not_matched"
    assert report["route"] == []


def test_explicit_cap_surface_overrides_optical_word_nearby() -> None:
    report = resolver.resolve_request(
        "For the Zeiss Planar 50mm F1.4 lens, design an optical-inspired circular lens-cap front"
    )
    assert report["status"] == "matched"
    assert report["route"] == ["lens-cap-imagegen"]


def test_named_lens_override_covers_attachment_or_catalog_identity() -> None:
    report = resolver.resolve_request(
        "Make the attached identified lens into a circular front badge",
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
