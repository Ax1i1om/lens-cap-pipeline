"""Checks for the provider-neutral clean-room interaction rehearsal."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCENARIO = ROOT / "examples/rehearsals/helios-44-2-rehouse-clean-room.json"
MAMIYA_SCENARIO = ROOT / "examples/rehearsals/mamiya-sekor-c-80-f1-9-rehouse-clean-room.json"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import rehearse_user_agent as rehearsal  # noqa: E402


def _scenario() -> dict[str, object]:
    return json.loads(SCENARIO.read_text(encoding="utf-8"))


def _mamiya_scenario() -> dict[str, object]:
    return json.loads(MAMIYA_SCENARIO.read_text(encoding="utf-8"))


def test_clean_room_interaction_validates_without_running_cad() -> None:
    report = rehearsal.validate_interaction(_scenario())
    assert report["status"] == "passed"
    assert report["interaction"]["route"] == ["lens-cap-imagegen", "lens-cap-production"]
    assert report["interaction"]["route_resolution"]["status"] == "matched"
    assert report["interaction"]["route_resolution"]["generic_parallel_skills"] == []
    assert report["interaction"]["intake"]["mating_outside_diameter_mm"] == 77.0
    assert report["persisted_job"]["friction_ribs_enabled"] is True


def test_second_lens_rehearsal_validates_foam_and_adapter_wall() -> None:
    report = rehearsal.validate_interaction(_mamiya_scenario())
    assert report["status"] == "passed"
    assert report["fixture"]["lens_identity"]["model"] == "Mamiya-Sekor C 80mm F1.9"
    assert report["interaction"]["intake"]["mating_outside_diameter_mm"] == 85.0
    assert report["interaction"]["intake"]["liner_thickness_mm"] == 1.5


def test_clean_room_rejects_generic_parallel_design_skill() -> None:
    scenario = _scenario()
    route = dict(scenario["route"])
    route["generic_parallel_skills"] = ["design-md"]
    scenario["route"] = route
    with pytest.raises(rehearsal.RehearsalError, match="parallel"):
        rehearsal.validate_interaction(scenario)


def test_clean_room_accepts_slash_aperture_spelling() -> None:
    scenario = _scenario()
    turns = [dict(turn) for turn in scenario["conversation"]["turns"]]
    turns[0]["text"] = turns[0]["text"].replace("F2", "F/2")
    conversation = dict(scenario["conversation"])
    conversation["turns"] = turns
    scenario["conversation"] = conversation
    assert rehearsal.validate_interaction(scenario)["status"] == "passed"


def test_clean_room_accepts_printable_model_wording_without_literal_cap() -> None:
    scenario = _scenario()
    turns = [dict(turn) for turn in scenario["conversation"]["turns"]]
    turns[0]["text"] = (
        "没有历史上下文：请为 IronGlass REHOUSE Helios-44-2 58mm F2 制作可打印模型，"
        "正面为圆形浮雕，58 和 F2 是主要信息，最后输出 3MF。"
    )
    conversation = dict(scenario["conversation"])
    conversation["turns"] = turns
    scenario["conversation"] = conversation
    assert rehearsal.validate_interaction(scenario)["status"] == "passed"


def test_clean_room_accepts_bare_koujing_wording_for_diameter() -> None:
    """Chinese users commonly say 口径 rather than 前口径/外径."""

    scenario = _scenario()
    turns = [dict(turn) for turn in scenario["conversation"]["turns"]]
    turns[1]["text"] = turns[1]["text"].replace("实际卡合圆柱外径（含转接圈壁）是多少？", "实际口径是多少？")
    conversation = dict(scenario["conversation"])
    conversation["turns"] = turns
    scenario["conversation"] = conversation
    assert rehearsal.validate_interaction(scenario)["status"] == "passed"


def test_clean_room_rejects_production_command_before_intake() -> None:
    scenario = _scenario()
    turns = [dict(turn) for turn in scenario["conversation"]["turns"]]
    turns[1]["text"] = (
        "我先运行 ./bin/lens-cap-3mf jobs/77mm/job.toml --force；然后再问实际外径、泡棉和凸条。"
    )
    conversation = dict(scenario["conversation"])
    conversation["turns"] = turns
    scenario["conversation"] = conversation
    with pytest.raises(rehearsal.RehearsalError, match="production command"):
        rehearsal.validate_interaction(scenario)


def test_clean_room_allows_conditional_future_bridge_mention_before_intake() -> None:
    scenario = _scenario()
    turns = [dict(turn) for turn in scenario["conversation"]["turns"]]
    turns[1]["text"] += (
        " 我会先收齐三项参数；批准图稿后再运行 ./bin/lens-cap-3mf jobs/77mm/job.toml。"
    )
    conversation = dict(scenario["conversation"])
    conversation["turns"] = turns
    scenario["conversation"] = conversation
    assert rehearsal.validate_interaction(scenario)["status"] == "passed"


def test_clean_room_rejects_claimed_completed_bridge_before_intake() -> None:
    scenario = _scenario()
    turns = [dict(turn) for turn in scenario["conversation"]["turns"]]
    turns[1]["text"] = (
        "已经运行 ./bin/lens-cap-3mf jobs/77mm/job.toml 并生成 3MF，之后再问口径、泡棉和凸条。"
    )
    conversation = dict(scenario["conversation"])
    conversation["turns"] = turns
    scenario["conversation"] = conversation
    with pytest.raises(rehearsal.RehearsalError, match="production command"):
        rehearsal.validate_interaction(scenario)


def test_clean_room_matches_optional_zoom_focal_display(tmp_path: Path) -> None:
    """A zoom range is matched as a complete token while prime matching stays intact."""

    fixture = tmp_path / "zoom-fixture"
    rehearsal.smoke._copy_fixture(ROOT / "examples/fixtures/helios-44-2-rehouse", fixture)
    brief_path = fixture / "design-brief.json"
    brief = json.loads(brief_path.read_text(encoding="utf-8"))
    brief["lens_identity"]["focal_length_mm"] = 28
    brief["lens_identity"]["focal_length_display"] = "28–70mm"
    brief_path.write_text(json.dumps(brief, ensure_ascii=False), encoding="utf-8")

    scenario = _scenario()
    scenario["fixture"] = {
        "path": str(fixture),
        "primary_job": "jobs/77mm/job.toml",
    }
    turns = [dict(turn) for turn in scenario["conversation"]["turns"]]
    turns[0]["text"] = turns[0]["text"].replace("58mm", "28–70mm").replace("焦段 58", "焦段 28–70")
    conversation = dict(scenario["conversation"])
    conversation["turns"] = turns
    scenario["conversation"] = conversation
    report = rehearsal.validate_interaction(scenario, scenario_base=tmp_path)
    assert report["status"] == "passed"
    assert report["interaction"]["focal_length_display"] == "28–70mm"
    assert rehearsal._contains_focal_display("Sigma 28-70mm F2.8", "28–70mm")
    assert not rehearsal._contains_focal_display("Sigma 28-700mm F2.8", "28–70mm")
    with pytest.raises(rehearsal.RehearsalError, match="focal_length_display"):
        rehearsal._contains_focal_display("Sigma 28-70mm F2.8", "28–70mm zoom")


def test_clean_room_rejects_same_specs_with_wrong_lens_identity() -> None:
    scenario = _scenario()
    turns = [dict(turn) for turn in scenario["conversation"]["turns"]]
    turns[0]["text"] = turns[0]["text"].replace("Helios-44-2", "Sigma 28-70mm")
    conversation = dict(scenario["conversation"])
    conversation["turns"] = turns
    scenario["conversation"] = conversation
    with pytest.raises(rehearsal.RehearsalError, match="name the fixture lens"):
        rehearsal.validate_interaction(scenario)


def test_clean_room_rejects_larger_aperture_substring() -> None:
    scenario = _scenario()
    turns = [dict(turn) for turn in scenario["conversation"]["turns"]]
    turns[0]["text"] = turns[0]["text"].replace("F2", "F2.8")
    conversation = dict(scenario["conversation"])
    conversation["turns"] = turns
    scenario["conversation"] = conversation
    with pytest.raises(rehearsal.RehearsalError, match="maximum aperture"):
        rehearsal.validate_interaction(scenario)


def test_clean_room_rejects_unpersisted_measurement() -> None:
    scenario = _scenario()
    intake = dict(scenario["intake"])
    answer = dict(intake["answer"])
    answer["mating_outside_diameter_mm"] = 95
    answer["adapter_nominal_ring_mm"] = 95
    answer["adapter_radial_wall_mm"] = 0
    intake["answer"] = answer
    scenario["intake"] = intake
    turns = [dict(turn) for turn in scenario["conversation"]["turns"]]
    turns[2]["text"] = turns[2]["text"].replace("77", "95")
    conversation = dict(scenario["conversation"])
    conversation["turns"] = turns
    scenario["conversation"] = conversation
    with pytest.raises(rehearsal.RehearsalError, match="measured_diameter"):
        rehearsal.validate_interaction(scenario)


def test_clean_room_rejects_bad_adapter_wall_arithmetic() -> None:
    scenario = _scenario()
    intake = dict(scenario["intake"])
    answer = dict(intake["answer"])
    answer["adapter_radial_wall_mm"] = 3.0
    intake["answer"] = answer
    scenario["intake"] = intake
    with pytest.raises(rehearsal.RehearsalError, match="nominal ring"):
        rehearsal.validate_interaction(scenario)


def test_clean_room_rejects_rib_explicitness_mismatch(tmp_path: Path) -> None:
    """The default/on distinction must survive into the persisted TOML."""

    source_fixture = ROOT / "examples/fixtures/helios-44-2-rehouse"
    fixture = tmp_path / "fixture"
    rehearsal.smoke._copy_fixture(source_fixture, fixture)
    job_path = fixture / "jobs/77mm/job.toml"
    job_text = job_path.read_text(encoding="utf-8").replace(
        "friction_ribs_explicit = false", "friction_ribs_explicit = true"
    )
    job_path.write_text(job_text, encoding="utf-8")
    scenario = _scenario()
    scenario["fixture"] = {"path": str(fixture), "primary_job": "jobs/77mm/job.toml"}
    with pytest.raises(rehearsal.RehearsalError, match="friction_ribs_explicit"):
        rehearsal.validate_interaction(scenario, scenario_base=tmp_path)


def test_clean_room_rejects_foam_polarity_mismatch() -> None:
    scenario = _scenario()
    intake = dict(scenario["intake"])
    answer = dict(intake["answer"])
    answer["foam_liner_status"] = "foam"
    answer["liner_thickness_mm"] = 1.5
    intake["answer"] = answer
    scenario["intake"] = intake
    with pytest.raises(rehearsal.RehearsalError, match="positive foam"):
        rehearsal.validate_interaction(scenario)


def test_clean_room_rejects_rib_polarity_mismatch() -> None:
    scenario = _scenario()
    intake = dict(scenario["intake"])
    answer = dict(intake["answer"])
    answer["friction_ribs_enabled"] = False
    answer["friction_ribs_explicit"] = True
    intake["answer"] = answer
    scenario["intake"] = intake
    with pytest.raises(rehearsal.RehearsalError, match="smooth-wall"):
        rehearsal.validate_interaction(scenario)


def test_run_rehearsal_uses_stable_job_suffix_for_isolated_copy(monkeypatch: pytest.MonkeyPatch) -> None:
    """The production runner rewrites absolute paths in its clean fixture."""

    class Completed:
        returncode = 0
        stdout = json.dumps(
            {
                "status": "passed",
                "runner": "scripts/smoke_rehouse.py",
                "jobs": [{"job": "/tmp/isolated/fixture/jobs/77mm/job.toml"}],
                "copied_artifacts": [],
                "fit_status": "unverifiable_until_coupon_measurement",
            }
        )
        stderr = ""

    monkeypatch.setattr(rehearsal.subprocess, "run", lambda *args, **kwargs: Completed())
    report = rehearsal.run_rehearsal(SCENARIO, bambu="never")
    assert report["status"] == "passed"
    assert report["production"]["status"] == "passed"


def test_external_scenario_file_is_allowed_with_absolute_fixture(tmp_path: Path) -> None:
    """Users may keep their transcript outside the checkout."""

    scenario = _scenario()
    scenario["fixture"] = {
        "path": str(ROOT / "examples/fixtures/helios-44-2-rehouse"),
        "primary_job": "jobs/77mm/job.toml",
    }
    external = tmp_path / "my-rehearsal.json"
    external.write_text(json.dumps(scenario), encoding="utf-8")
    report = rehearsal.validate_interaction(scenario, scenario_base=tmp_path)
    assert report["status"] == "passed"


def test_external_scenario_resolves_relative_fixture_beside_it(tmp_path: Path) -> None:
    source_fixture = ROOT / "examples/fixtures/helios-44-2-rehouse"
    local_fixture = tmp_path / "fixture"
    rehearsal.smoke._copy_fixture(source_fixture, local_fixture)
    scenario = _scenario()
    scenario["fixture"] = {"path": "fixture", "primary_job": "jobs/77mm/job.toml"}
    report = rehearsal.validate_interaction(scenario, scenario_base=tmp_path)
    assert Path(report["fixture"]["path"]) == local_fixture.resolve()
