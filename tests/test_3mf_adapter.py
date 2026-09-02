"""Portable checks for the optional 3MF adapter."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ADAPTER_PATH = ROOT / "tools" / "3mf_adapter" / "three_mf_adapter.py"
SPEC = importlib.util.spec_from_file_location("lenscap_3mf_adapter_test", ADAPTER_PATH)
assert SPEC and SPEC.loader
adapter = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = adapter
SPEC.loader.exec_module(adapter)


def test_standard_core_roundtrip_is_closed_and_xml_valid(tmp_path: Path) -> None:
    mesh = adapter._mesh_from_triangles(adapter._cube_triangles(12.0))
    output = tmp_path / "cube.3mf"
    report = adapter.write_standard_3mf(mesh, output, title="test cube")
    verified = adapter.verify_3mf(output)
    assert report["closed"] is True
    assert verified["has_embedded_gcode"] is False
    assert verified["model"]["triangles"] == 12
    assert verified["model"]["build_items"] == 1


def test_native_openscad_fixture_without_model_relationship_is_valid() -> None:
    path = ROOT / "tools" / "3mf_adapter" / "fixtures" / "fixture-cube-openscad.3mf"
    if not path.is_file():
        pytest.skip("native OpenSCAD fixture is optional in a source-only checkout")
    report = adapter.verify_3mf(path)
    assert report["model"]["parts"] >= 1
    assert report["model"]["triangles"] > 0


def test_parser_accepts_timeout_after_external_subcommand() -> None:
    args = adapter.parser().parse_args(["openscad", "in.scad", "out.3mf", "--timeout", "17"])
    assert args.timeout == 17


def test_explicit_missing_executable_does_not_fall_back_to_candidates(tmp_path: Path) -> None:
    missing = tmp_path / "missing-openscad"
    assert adapter._resolve_executable(str(missing), (sys.executable,)) is None


def test_portable_path_redacts_external_absolute_paths() -> None:
    assert adapter._portable_path(Path("/Users/example/private/model.stl")).startswith("<external>/")
