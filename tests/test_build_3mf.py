"""Unit checks for the public one-command 3MF bridge."""

from __future__ import annotations

import json
import sys
from pathlib import Path

# ``scripts`` is a repository helper package rather than an installed runtime
# package.  Make this test work from both a checkout and an sdist unpacked into
# a temporary directory, where pytest may not put the repository root on
# ``sys.path`` automatically.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import scripts.build_3mf as bridge  # noqa: E402


def test_bridge_parser_defaults_to_native_only() -> None:
    args = bridge._parser().parse_args(["job.toml"])
    assert args.bambu == "never"
    assert args.force is False


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
                'face_diameter_mm = 52.0',
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
    result = bridge.main([str(config), "--openscad", str(tmp_path / "missing-openscad"), "--json"])
    assert result == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "unverifiable"
    assert "OpenSCAD" in payload["reason"]
    assert not (tmp_path / "build").exists()


def test_manifest_declares_bridge() -> None:
    manifest = json.loads((Path(__file__).parents[1] / "skills" / "manifest.json").read_text(encoding="utf-8"))
    distribution = manifest["distribution"]
    assert distribution["production_entrypoint"] == "scripts/build_3mf.py"
    assert distribution["production_alias"] == "bin/lens-cap-3mf"
