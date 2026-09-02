"""Fast unit checks for the clean-room integration rehearsal."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import scripts.smoke_rehouse as smoke  # noqa: E402

FIXTURE = ROOT / "examples" / "fixtures" / "helios-44-2-rehouse"
MAMIYA_FIXTURE = ROOT / "examples" / "fixtures" / "mamiya-sekor-c-80-f1-9-rehouse"


def test_fixture_brief_and_prompt_are_self_contained() -> None:
    report = smoke._validate_brief(FIXTURE)
    assert report["status"] == "passed"
    assert report["display_text"][:2] == ["58", "F2"]
    assert report["artwork"] == "art/master.png"


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
