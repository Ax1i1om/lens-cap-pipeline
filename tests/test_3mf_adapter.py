"""Portable checks for the optional 3MF adapter."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import xml.etree.ElementTree as ET
import zipfile
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
    verified = adapter.verify_3mf(output, require_closed=True)
    assert report["closed"] is True
    assert verified["has_embedded_gcode"] is False
    assert verified["model"]["triangles"] == 12
    assert verified["model"]["build_items"] == 1


def test_openscad_sanitizer_canonicalizes_timestamp_uuid_and_zip_metadata(
    tmp_path: Path,
) -> None:
    mesh = adapter._mesh_from_triangles(adapter._cube_triangles(12.0))
    source = tmp_path / "source.3mf"
    adapter.write_standard_3mf(mesh, source, title="canonical cube")
    production_ns = "http://schemas.microsoft.com/3dmanufacturing/production/2015/06"

    def volatile_copy(name: str, timestamp: str, uuid_value: str, year: int) -> Path:
        target = tmp_path / name
        with zipfile.ZipFile(source) as old, zipfile.ZipFile(target, "w") as new:
            for entry in old.infolist():
                payload = old.read(entry.filename)
                if entry.filename == adapter.CORE_MODEL:
                    root = ET.fromstring(payload)
                    namespace = root.tag.rsplit("}", 1)[0] + "}"
                    metadata = ET.Element(
                        f"{namespace}metadata",
                        {"name": "CreationDate", "preserve": "1"},
                    )
                    metadata.text = timestamp
                    root.insert(0, metadata)
                    for node in (
                        _first_local(root, "object"),
                        _first_local(root, "build"),
                        _first_local(root, "item"),
                    ):
                        node.set(f"{{{production_ns}}}UUID", uuid_value)
                    payload = ET.tostring(root, encoding="utf-8", xml_declaration=True)
                info = zipfile.ZipInfo(entry.filename, date_time=(year, 1, 2, 3, 4, 6))
                info.compress_type = zipfile.ZIP_STORED
                new.writestr(info, payload)
        return target

    first = volatile_copy(
        "first.3mf",
        "2026-09-03T09:32:56Z",
        "11111111-1111-4111-8111-111111111111",
        2025,
    )
    second = volatile_copy(
        "second.3mf",
        "2030-01-02T03:04:05Z",
        "22222222-2222-4222-8222-222222222222",
        2030,
    )
    first_report = adapter._sanitize_openscad_core(first)
    second_report = adapter._sanitize_openscad_core(second)

    assert first.read_bytes() == second.read_bytes()
    assert first_report["removed_volatile_metadata"] == 1
    assert first_report["canonicalized_uuid_attributes"] == 3
    assert second_report["canonicalized_uuid_attributes"] == 3
    adapter.verify_3mf(first, require_closed=True, require_single_volume=True)


def test_native_openscad_fixture_without_model_relationship_is_valid() -> None:
    path = ROOT / "tools" / "3mf_adapter" / "fixtures" / "fixture-cube-openscad.3mf"
    if not path.is_file():
        pytest.skip("native OpenSCAD fixture is optional in a source-only checkout")
    report = adapter.verify_3mf(path)
    assert report["model"]["parts"] >= 1
    assert report["model"]["triangles"] > 0


def _mutated_cube(tmp_path: Path, name: str, mutate) -> Path:
    mesh = adapter._mesh_from_triangles(adapter._cube_triangles(12.0))
    source = tmp_path / "source.3mf"
    adapter.write_standard_3mf(mesh, source, title="mutated cube")
    target = tmp_path / name
    with zipfile.ZipFile(source) as old, zipfile.ZipFile(target, "w") as new:
        for info in old.infolist():
            payload = old.read(info.filename)
            if info.filename == adapter.CORE_MODEL:
                root = ET.fromstring(payload)
                mutate(root)
                payload = ET.tostring(root, encoding="utf-8", xml_declaration=True)
            new.writestr(info, payload)
    return target


def _first_local(root: ET.Element, name: str) -> ET.Element:
    return next(node for node in root.iter() if node.tag.rsplit("}", 1)[-1] == name)


def test_verifier_rejects_unreferenced_vertices(tmp_path: Path) -> None:
    def mutate(root: ET.Element) -> None:
        vertices = _first_local(root, "vertices")
        template = _first_local(root, "vertex")
        ET.SubElement(vertices, template.tag, {"x": "6", "y": "6", "z": "6"})

    package = _mutated_cube(tmp_path, "unused-vertex.3mf", mutate)
    with pytest.raises(ValueError, match="unreferenced vertices"):
        adapter.verify_3mf(package, require_closed=True)


def test_verifier_rejects_open_or_repeated_index_meshes(tmp_path: Path) -> None:
    def remove_triangle(root: ET.Element) -> None:
        triangles = _first_local(root, "triangles")
        triangles.remove(_first_local(root, "triangle"))

    opened = _mutated_cube(tmp_path, "open.3mf", remove_triangle)
    with pytest.raises(ValueError, match="not edge-closed/manifold"):
        adapter.verify_3mf(opened, require_closed=True)

    def repeat_index(root: ET.Element) -> None:
        triangle = _first_local(root, "triangle")
        triangle.set("v2", triangle.get("v1", "0"))

    repeated = _mutated_cube(tmp_path, "repeated.3mf", repeat_index)
    with pytest.raises(ValueError, match="repeated-index triangle"):
        adapter.verify_3mf(repeated)


def test_verifier_rejects_non_millimeter_model_units(tmp_path: Path) -> None:
    def mutate(root: ET.Element) -> None:
        root.set("unit", "inch")

    package = _mutated_cube(tmp_path, "inch.3mf", mutate)
    with pytest.raises(ValueError, match="unit='millimeter'"):
        adapter.verify_3mf(package)


def test_verifier_rejects_negative_or_out_of_range_material_indices(
    tmp_path: Path,
) -> None:
    def add_material(root: ET.Element) -> None:
        resources = _first_local(root, "resources")
        obj = _first_local(root, "object")
        namespace = obj.tag.rsplit("}", 1)[0] + "}"
        group = ET.Element(f"{namespace}basematerials", {"id": "9"})
        ET.SubElement(group, f"{namespace}base", {"name": "black", "displaycolor": "#000000"})
        resources.insert(0, group)
        obj.set("pid", "9")
        obj.set("pindex", "0")
        triangle = _first_local(root, "triangle")
        triangle.set("pid", "9")
        triangle.set("p1", "-1")

    negative = _mutated_cube(tmp_path, "negative-material.3mf", add_material)
    with pytest.raises(ValueError, match="malformed or negative"):
        adapter.verify_3mf(negative)

    def out_of_range(root: ET.Element) -> None:
        add_material(root)
        _first_local(root, "triangle").set("p1", "1")

    oversized = _mutated_cube(tmp_path, "oversized-material.3mf", out_of_range)
    with pytest.raises(ValueError, match="out of range"):
        adapter.verify_3mf(oversized)


def test_verifier_rejects_flipped_face_orientation(tmp_path: Path) -> None:
    def flip(root: ET.Element) -> None:
        triangle = _first_local(root, "triangle")
        old_v2 = triangle.attrib["v2"]
        triangle.set("v2", triangle.attrib["v3"])
        triangle.set("v3", old_v2)

    package = _mutated_cube(tmp_path, "flipped-face.3mf", flip)
    with pytest.raises(ValueError, match="inconsistently oriented"):
        adapter.verify_3mf(package, require_closed=True)


def test_verifier_rejects_closed_zero_volume_component(tmp_path: Path) -> None:
    # A tetrahedral topology can be edge-closed while all four vertices are
    # coplanar.  It is not a printable solid and must fail the strict gate.
    vertices = (
        (0.0, 0.0, 0.0),
        (2.0, 0.0, 0.0),
        (2.0, 2.0, 0.0),
        (0.0, 2.0, 0.0),
    )
    triangles = ((0, 2, 1), (0, 1, 3), (1, 2, 3), (2, 0, 3))
    package = tmp_path / "flat-closed.3mf"
    adapter.write_standard_3mf(adapter.Mesh(vertices, triangles), package)
    with pytest.raises(ValueError, match="zero-volume components"):
        adapter.verify_3mf(package, require_closed=True)


def test_single_volume_gate_rejects_two_cubes_touching_only_at_a_point(
    tmp_path: Path,
) -> None:
    first = adapter._cube_triangles(1.0)
    second = tuple(
        tuple((x + 1.0, y + 1.0, z + 1.0) for x, y, z in triangle)
        for triangle in adapter._cube_triangles(1.0)
    )
    mesh = adapter._mesh_from_triangles((*first, *second))
    package = tmp_path / "point-touch.3mf"
    adapter.write_standard_3mf(mesh, package)
    closed = adapter.verify_3mf(package, require_closed=True)
    assert closed["model"]["volume_components"] == 2
    with pytest.raises(ValueError, match="exactly one positive-volume component"):
        adapter.verify_3mf(package, require_single_volume=True)


def test_sliced_verifier_rejects_arbitrary_gcode_with_forged_md5(
    tmp_path: Path,
) -> None:
    source = (
        ROOT
        / "examples/fixtures/helios-44-2-rehouse-imagegen-v2/artifacts"
        / "helios-44-2-rehouse-imagegen-v2-95mm-bambu-slice.3mf"
    )
    if not source.is_file():
        pytest.skip("retained sliced fixture is optional in a source-only checkout")
    target = tmp_path / "forged-slice.3mf"
    with zipfile.ZipFile(source) as old, zipfile.ZipFile(target, "w") as new:
        for info in old.infolist():
            payload = old.read(info.filename)
            if info.filename.endswith(".gcode"):
                payload = b"; not a sliced lens cap\nG1 X0 Y0\n"
            elif info.filename.endswith(".gcode.md5"):
                payload = b"0" * 32
            new.writestr(info, payload)
    with pytest.raises(ValueError, match="G-code MD5 mismatch"):
        adapter.verify_3mf(target, require_slice=True)


def test_sliced_verifier_rejects_293_byte_semantic_gcode_forgery(
    tmp_path: Path,
) -> None:
    source = (
        ROOT
        / "examples/fixtures/helios-44-2-rehouse-imagegen-v2/artifacts"
        / "helios-44-2-rehouse-imagegen-v2-95mm-bambu-slice.3mf"
    )
    if not source.is_file():
        pytest.skip("retained sliced fixture is optional in a source-only checkout")
    fake_gcode = (
        "; HEADER_BLOCK_START\n"
        "; total layer number: 83\n"
        "; max_z_height: 16.6\n"
        "; HEADER_BLOCK_END\n"
        "; CONFIG_BLOCK_START\n"
        "; CONFIG_BLOCK_END\n"
        "; EXECUTABLE_BLOCK_START\n"
        + "G1 X1 Y1 E1\n" * 10
        + "; EXECUTABLE_BLOCK_END"
    ).encode("utf-8")
    assert len(fake_gcode) == 293
    fake_digest = hashlib.md5(fake_gcode, usedforsecurity=False).hexdigest().encode("ascii")
    target = tmp_path / "semantic-forgery.3mf"
    with zipfile.ZipFile(source) as old, zipfile.ZipFile(target, "w") as new:
        for info in old.infolist():
            payload = old.read(info.filename)
            if info.filename.endswith(".gcode"):
                payload = fake_gcode
            elif info.filename.endswith(".gcode.md5"):
                payload = fake_digest
            new.writestr(info, payload)
    with pytest.raises(ValueError, match="G-code layer structure"):
        adapter.verify_3mf(target, require_slice=True)


def test_retained_bambu_slice_passes_semantic_gcode_audit() -> None:
    source = (
        ROOT
        / "examples/fixtures/helios-44-2-rehouse-imagegen-v2/artifacts"
        / "helios-44-2-rehouse-imagegen-v2-95mm-bambu-slice.3mf"
    )
    if not source.is_file():
        pytest.skip("retained sliced fixture is optional in a source-only checkout")
    report = adapter.verify_3mf(source, require_slice=True)
    audit = report["slice_audit"]
    assert audit["status"] == "passed"
    semantic = audit["gcode_semantics"][0]
    assert semantic["declared_layer_count"] == semantic["observed_extrusion_layer_count"]
    assert semantic["positive_xy_extrusion_moves"] > semantic["declared_layer_count"]
    assert min(audit["extrusion_xy_size_mm"]) > 0
    assert audit["slice_info_layer_counts"] == [semantic["declared_layer_count"]]


def test_current_bambu_cube_accepts_feature_level_layer_height_comments() -> None:
    source = (
        ROOT
        / "tools/3mf_adapter/fixtures/fixture-cube-bambu-sliced.3mf"
    )
    if not source.is_file():
        pytest.skip("Bambu cube fixture is optional in a source-only checkout")
    report = adapter.verify_3mf(source, require_slice=True)
    semantic = report["slice_audit"]["gcode_semantics"][0]
    assert semantic["declared_layer_count"] == 200
    assert semantic["observed_extrusion_layer_count"] == 200
    assert semantic["minimum_layer_height_mm"] == pytest.approx(0.1, abs=2e-6)
    assert semantic["maximum_layer_height_mm"] == pytest.approx(0.1, abs=2e-6)


def test_strict_verifier_rejects_empty_build_unbuilt_mesh_and_transform(
    tmp_path: Path,
) -> None:
    def empty_build(root: ET.Element) -> None:
        build = _first_local(root, "build")
        build.clear()

    empty = _mutated_cube(tmp_path, "empty-build.3mf", empty_build)
    with pytest.raises(ValueError, match="empty build"):
        adapter.verify_3mf(empty, require_closed=True)

    def add_unbuilt_mesh(root: ET.Element) -> None:
        resources = _first_local(root, "resources")
        original = _first_local(root, "object")
        clone = ET.fromstring(ET.tostring(original))
        clone.set("id", "99")
        resources.append(clone)

    unbuilt = _mutated_cube(tmp_path, "unbuilt.3mf", add_unbuilt_mesh)
    with pytest.raises(ValueError, match="unbuilt or duplicate mesh"):
        adapter.verify_3mf(unbuilt, require_closed=True)

    def transform(root: ET.Element) -> None:
        _first_local(root, "item").set(
            "transform", "1 0 0 0 1 0 0 0 1 10 0 0"
        )

    moved = _mutated_cube(tmp_path, "moved.3mf", transform)
    with pytest.raises(ValueError, match="build transforms"):
        adapter.verify_3mf(moved, require_closed=True)


def test_parser_accepts_timeout_after_external_subcommand() -> None:
    args = adapter.parser().parse_args(["openscad", "in.scad", "out.3mf", "--timeout", "17"])
    assert args.timeout == 17


def test_explicit_missing_executable_does_not_fall_back_to_candidates(tmp_path: Path) -> None:
    missing = tmp_path / "missing-openscad"
    assert adapter._resolve_executable(str(missing), (sys.executable,)) is None


def test_portable_path_redacts_external_absolute_paths() -> None:
    assert adapter._portable_path(Path("/Users/example/private/model.stl")).startswith("<external>/")


def test_bambu_profile_inheritance_is_materialized_and_hashed(tmp_path: Path) -> None:
    parent = tmp_path / "parent.json"
    child = tmp_path / "child.json"
    parent.write_text(
        json.dumps(
            {
                "name": "base-process",
                "layer_height": "0.1",
                "line_width": "0.22",
                "wall_loops": "4",
            }
        ),
        encoding="utf-8",
    )
    child.write_text(
        json.dumps(
            {
                "name": "A1M 0.2 process",
                "inherits": "base-process",
                "initial_layer_print_height": "0.1",
            }
        ),
        encoding="utf-8",
    )
    resolved = adapter._resolve_profile_inheritance(child)
    assert resolved["effective"]["layer_height"] == "0.1"
    assert resolved["effective"]["line_width"] == "0.22"
    assert resolved["effective"]["initial_layer_print_height"] == "0.1"
    assert [item["name"] for item in resolved["chain"]] == [
        "base-process",
        "A1M 0.2 process",
    ]
    assert all(len(item["sha256"]) == 64 for item in resolved["chain"])
