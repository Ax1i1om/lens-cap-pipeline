"""Portable checks for the optional 3MF adapter."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
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


def _insert_collinear_cube_facet(root: ET.Element, *, near_offset: float = 0.0, edge_fraction: float = .5) -> None:
    """Add a conforming zero-area edge bridge without moving the cube surface."""
    vertices = _first_local(root, "vertices")
    triangles = _first_local(root, "triangles")
    original = list(triangles)[0]
    a, b, c = (int(original.get(key, "-1")) for key in ("v1", "v2", "v3"))
    av = [float(list(vertices)[a].get(axis, "nan")) for axis in "xyz"]
    bv = [float(list(vertices)[b].get(axis, "nan")) for axis in "xyz"]
    midpoint = [left + edge_fraction * (right - left) for left, right in zip(av, bv, strict=True)]
    perpendicular_axis = next(axis for axis in range(3) if av[axis] == bv[axis])
    midpoint[perpendicular_axis] += near_offset
    middle = len(vertices)
    ET.SubElement(vertices, list(vertices)[0].tag, dict(zip("xyz", map(repr, midpoint), strict=True)))
    triangles.remove(original)
    for face in ((a, middle, c), (middle, b, c), (a, b, middle)):
        ET.SubElement(triangles, original.tag, {"v1": str(face[0]), "v2": str(face[1]), "v3": str(face[2])})


def test_openscad_collinear_repair_preserves_vertices_area_volume_and_material(
    tmp_path: Path,
) -> None:
    def mutate(root: ET.Element) -> None:
        _insert_collinear_cube_facet(root)
        namespace = root.tag.rsplit("}", 1)[0] + "}"
        resources = _first_local(root, "resources")
        material = ET.Element(namespace + "basematerials", {"id": "9"})
        ET.SubElement(material, namespace + "base", {"name": "test gray", "displaycolor": "#888888"})
        resources.insert(0, material)
        for triangle in _first_local(root, "triangles"):
            triangle.set("pid", "9")
            triangle.set("p1", "0")
            triangle.set("p2", "0")
            triangle.set("p3", "0")

    path = _mutated_cube(tmp_path, "collinear.3mf", mutate)

    def geometry() -> tuple[list[dict[str, str]], float, float]:
        with zipfile.ZipFile(path) as archive:
            root = ET.fromstring(archive.read(adapter.CORE_MODEL))
        nodes = list(_first_local(root, "vertices"))
        coordinates = [tuple(float(node.get(axis, "nan")) for axis in "xyz") for node in nodes]
        faces = [tuple(int(node.get(key, "-1")) for key in ("v1", "v2", "v3")) for node in _first_local(root, "triangles")]
        vectors = [adapter._triangle_cross_and_volume(coordinates, face) for face in faces]
        return [dict(node.attrib) for node in nodes], sum(sum(x*x for x in cross) ** .5 / 2 for cross, _ in vectors), sum(volume for _, volume in vectors)

    before = geometry()
    report = adapter._sanitize_openscad_core(path)
    assert geometry() == before
    assert report["retriangulated_collinear_triangles"] == 1
    assert report["nudged_collinear_triangles"] == 0
    assert report["maximum_nudge_mm"] == 0
    assert report["vertex_coordinates_preserved"] is True
    checked = adapter.verify_3mf(path, require_closed=True, require_single_volume=True)
    assert checked["model"]["triangles"] == 14
    assert checked["model"]["zero_area_triangles"] == 0
    with zipfile.ZipFile(path) as archive:
        root = ET.fromstring(archive.read(adapter.CORE_MODEL))
    assert all({key: node.get(key) for key in ("pid", "p1", "p2", "p3")} == {"pid": "9", "p1": "0", "p2": "0", "p3": "0"} for node in _first_local(root, "triangles"))


def test_openscad_collinear_repair_refuses_near_collinear_surface_change(tmp_path: Path) -> None:
    path = _mutated_cube(tmp_path, "near-collinear.3mf", lambda root: _insert_collinear_cube_facet(root, near_offset=1e-12))
    before = path.read_bytes()
    with pytest.raises(ValueError, match="merely near-collinear"):
        adapter._sanitize_openscad_core(path)
    assert path.read_bytes() == before


def test_openscad_collinear_repair_refuses_ambiguous_long_edge(tmp_path: Path) -> None:
    def mutate(root: ET.Element) -> None:
        _insert_collinear_cube_facet(root)
        triangles = _first_local(root, "triangles")
        # Every original cube triangle may be duplicated here: the guard must
        # reject ambiguous long-edge adjacency, not choose an arbitrary side.
        for node in list(triangles)[:-3]:
            triangles.append(ET.Element(node.tag, dict(node.attrib)))
    path = _mutated_cube(tmp_path, "ambiguous-collinear.3mf", mutate)
    before = path.read_bytes()
    with pytest.raises(ValueError, match="unambiguous long-edge"):
        adapter._sanitize_openscad_core(path)
    assert path.read_bytes() == before


def test_openscad_repairs_only_existing_zero_length_edges_without_nudging(tmp_path: Path) -> None:
    path = _mutated_cube(tmp_path, "zero-length-edge.3mf", lambda root: _insert_collinear_cube_facet(root, edge_fraction=0))
    report = adapter._sanitize_openscad_core(path)
    assert report["collapsed_zero_length_edges"] == 1
    assert report["dropped_collapsed_triangles"] == 2
    assert report["retriangulated_collinear_triangles"] == 0
    assert report["maximum_nudge_mm"] == 0
    checked = adapter.verify_3mf(path, require_closed=True, require_single_volume=True)
    assert checked["model"]["triangles"] == 12
    assert checked["model"]["zero_area_triangles"] == 0
    assert checked["model"]["absolute_volume_mm3"] == 12 ** 3
    with zipfile.ZipFile(path) as archive:
        root = ET.fromstring(archive.read(adapter.CORE_MODEL))
    # The only removed vertex was the exact endpoint duplicate. Every kept
    # vertex is still an original cube corner, not an epsilon displacement.
    assert len(_first_local(root, "vertices")) == 8
    assert all(float(node.get(axis, "nan")) in {0.0, 12.0} for node in _first_local(root, "vertices") for axis in "xyz")


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


def _cube_gcode_with_config(updates: dict[str, str]) -> bytes:
    source = ROOT / "tools/3mf_adapter/fixtures/fixture-cube-bambu-sliced.3mf"
    if not source.is_file():
        pytest.skip("Bambu cube fixture is optional in a source-only checkout")
    with zipfile.ZipFile(source) as archive:
        name = next(name for name in archive.namelist() if name.endswith(".gcode"))
        text = archive.read(name).decode("utf-8")
    for key, value in updates.items():
        text, count = re.subn(rf"(?m)^; {re.escape(key)} = .*?$", lambda _: f"; {key} = {value}", text)
        assert count == 1
    return text.encode("utf-8")


def test_gcode_accepts_all_h2c_nozzles_and_five_material_vectors() -> None:
    result = adapter._audit_bambu_gcode(
        _cube_gcode_with_config({
            "nozzle_diameter": "0.2,0.2",
            "filament_diameter": "1.75,1.75,1.75,1.75,1.75",
            "filament_type": "PLA;PLA;PLA;PLA;PLA",
            "filament_settings_id": '"Basic";"Basic";"Basic";"Basic";"Metal"',
        }), name="vendor-list-regression.gcode", nozzle_diameters=[0.2, 0.2],
    )
    assert result["config"]["nozzle_diameters_mm"] == [0.2, 0.2]
    assert result["config"]["filament_diameters_mm"] == [1.75] * 5
    assert result["config"]["profile_values"]["filament_settings_id"] == ["Basic"] * 4 + ["Metal"]


@pytest.mark.parametrize("value", ["", "0.2,", "0.2,,0.2", "0.2,nan", "0.2,inf", "0.2,-0.2", "0.2,0", "0.2;0.2", "0.2,nozzle"])
def test_gcode_numeric_vectors_reject_malformed_or_nonpositive_entries(value: str) -> None:
    with pytest.raises(ValueError):
        adapter._gcode_positive_numeric_list(value, key="nozzle_diameter", name="invalid.gcode")


def test_gcode_rejects_second_nozzle_mismatch() -> None:
    with pytest.raises(ValueError, match="nozzle disagrees"):
        adapter._audit_bambu_gcode(
            _cube_gcode_with_config({"nozzle_diameter": "0.2,0.4"}),
            name="wrong-second-nozzle.gcode", nozzle_diameters=[0.2, 0.2],
        )


def test_gcode_excludes_h2c_startup_purge_above_first_layer() -> None:
    payload = _cube_gcode_with_config({})
    startup = b"G90\nM83\nG1 X0 Y0 Z5.8\nG1 X1 Y0 E0.1\n"
    payload = payload.replace(b"; CHANGE_LAYER", startup + b"; CHANGE_LAYER", 1)
    result = adapter._audit_bambu_gcode(payload, name="startup-purge.gcode", nozzle_diameters=[0.2])
    assert result["observed_extrusion_layer_count"] == 200
    assert result["startup_xy_extrusion_moves_excluded_from_layers"] >= 1


def test_gcode_still_rejects_backward_extrusion_inside_print_layers() -> None:
    payload = _cube_gcode_with_config({})
    before, marker, remainder = payload.partition(b"; CHANGE_LAYER")
    second = remainder.index(b"; CHANGE_LAYER")
    bad_path = b"G90\nM83\nG1 X0 Y0 Z5.8\nG1 X1 Y0 E0.1\n"
    payload = before + marker + remainder[:second] + bad_path + remainder[second:]
    with pytest.raises(ValueError, match="move backwards in Z"):
        adapter._audit_bambu_gcode(payload, name="backward-model-layer.gcode", nozzle_diameters=[0.2])


@pytest.mark.parametrize("key,project_value,gcode_value", [
    ("nozzle_diameter", ["0.2", "0.2"], "0.2"),
    ("filament_diameter", ["1.75", "1.75"], "1.75,2.85"),
    ("filament_settings_id", ["Basic", "Metal"], '"Metal";"Basic"'),
])
def test_slice_binding_checks_every_ordered_vector_entry(
    tmp_path: Path, key: str, project_value: list[str], gcode_value: str,
) -> None:
    source = ROOT / "tools/3mf_adapter/fixtures/fixture-cube-bambu-sliced.3mf"
    payload = _cube_gcode_with_config({key: gcode_value})
    digest = hashlib.md5(payload, usedforsecurity=False).hexdigest().encode("ascii")
    target = tmp_path / "wrong-vector.3mf"
    with zipfile.ZipFile(source) as old, zipfile.ZipFile(target, "w") as new:
        for info in old.infolist():
            data = old.read(info.filename)
            if info.filename.endswith(".gcode"):
                data = payload
            elif info.filename.endswith(".gcode.md5"):
                data = digest
            elif info.filename == "Metadata/project_settings.config":
                config = json.loads(data)
                config[key] = project_value
                data = json.dumps(config).encode("utf-8")
            new.writestr(info, data)
    with pytest.raises(ValueError, match="ordered project settings"):
        adapter.verify_3mf(target, require_slice=True)


def test_assembled_bounds_resolve_nested_rotation_translation_and_build(tmp_path: Path) -> None:
    mesh = adapter._mesh_from_triangles(adapter._cube_triangles(20.0))
    root = ET.fromstring(adapter._xml_model(mesh, "transformed cube"))
    ns = "{" + adapter.CORE_NS + "}"
    resources = root.find(ns + "resources")
    assert resources is not None
    assembly = ET.SubElement(resources, ns + "object", {"id": "2", "type": "model"})
    components = ET.SubElement(assembly, ns + "components")
    ET.SubElement(components, ns + "component", {
        "objectid": "1", "path": "/" + adapter.CORE_MODEL,
        "transform": "0 1 0 -1 0 0 0 0 1 4 5 3",
    })
    build = root.find(ns + "build")
    assert build is not None
    build[0].set("objectid", "2")
    build[0].set("transform", "1 0 0 0 1 0 0 0 1 0 0 7")
    source = tmp_path / "nested-transform.3mf"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr(adapter.CORE_MODEL, ET.tostring(root))
    with zipfile.ZipFile(source) as archive:
        result = adapter._assembled_model_bounds(archive)
    assert result["min_mm"] == pytest.approx([-16, 5, 10])
    assert result["max_mm"] == pytest.approx([4, 25, 30])
    assert result["size_mm"] == pytest.approx([20, 20, 20])


def test_sliced_height_binding_rejects_translated_model_above_toolpaths(tmp_path: Path) -> None:
    source = ROOT / "tools/3mf_adapter/fixtures/fixture-cube-bambu-sliced.3mf"
    if not source.is_file():
        pytest.skip("Bambu cube fixture is optional in a source-only checkout")
    target = tmp_path / "translated-above-gcode.3mf"
    with zipfile.ZipFile(source) as old, zipfile.ZipFile(target, "w") as new:
        for info in old.infolist():
            data = old.read(info.filename)
            if info.filename == adapter.CORE_MODEL:
                root = ET.fromstring(data)
                build = next(node for node in root if adapter._xml_local(node.tag) == "build")
                for item in build:
                    values = (item.get("transform") or "1 0 0 0 1 0 0 0 1 0 0 0").split()
                    values[11] = str(float(values[11]) + 1)
                    item.set("transform", " ".join(values))
                data = ET.tostring(root)
            new.writestr(info, data)
    with pytest.raises(ValueError, match="max height disagrees"):
        adapter.verify_3mf(target, require_slice=True)


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


def test_bambu_includes_override_parent_in_order_before_leaf(tmp_path: Path) -> None:
    profiles = {
        "base": {"name": "base", "machine_start_gcode": "GENERIC", "nozzle_diameter": ["0.4"]},
        "first": {"name": "first", "instantiation": "false", "machine_start_gcode": "H2C", "shared": "first"},
        "second": {"name": "second", "type": "template", "shared": "second", "own": "template"},
        "leaf": {"name": "H2C 0.2", "type": "machine", "inherits": "base", "include": ["first", "second"], "own": "leaf", "nozzle_diameter": ["0.2", "0.2"]},
    }
    for name, payload in profiles.items():
        (tmp_path / f"{name}.json").write_text(json.dumps(payload), encoding="utf-8")
    resolved = adapter._resolve_profile_inheritance(tmp_path / "leaf.json")
    assert resolved["resolver_version"] == 2
    assert resolved["effective"] == {
        "name": "H2C 0.2", "type": "machine", "machine_start_gcode": "H2C",
        "nozzle_diameter": ["0.2", "0.2"], "shared": "second", "own": "leaf",
    }
    assert [item["name"] for item in resolved["chain"]] == ["base", "first", "second", "H2C 0.2"]
    assert all(len(item["sha256"]) == 64 for item in resolved["chain"])


def test_bambu_include_string_nested_name_lookup_and_reuse(tmp_path: Path) -> None:
    (tmp_path / "fragment.json").write_text(json.dumps({"name": "shared template", "value": "shared"}), encoding="utf-8")
    (tmp_path / "nested.json").write_text(json.dumps({"name": "nested", "include": "shared template", "extra": "nested"}), encoding="utf-8")
    leaf = tmp_path / "leaf.json"
    leaf.write_text(json.dumps({"name": "leaf", "include": ["nested", "shared template"]}), encoding="utf-8")
    result = adapter._resolve_profile_inheritance(leaf)
    assert result["effective"] == {"name": "leaf", "value": "shared", "extra": "nested"}
    assert [item["name"] for item in result["chain"]] == ["shared template", "nested", "shared template", "leaf"]


@pytest.mark.parametrize("includes", [None, 3, {}, [1], [""]])
def test_bambu_rejects_malformed_include(tmp_path: Path, includes: object) -> None:
    leaf = tmp_path / "leaf.json"
    leaf.write_text(json.dumps({"include": includes}), encoding="utf-8")
    with pytest.raises(RuntimeError, match="invalid Bambu profile include"):
        adapter._resolve_profile_inheritance(leaf)


def test_bambu_rejects_missing_include_and_mixed_dependency_cycle(tmp_path: Path) -> None:
    leaf = tmp_path / "leaf.json"
    leaf.write_text(json.dumps({"name": "leaf", "include": ["missing"]}), encoding="utf-8")
    with pytest.raises(RuntimeError, match="cannot resolve Bambu profile include"):
        adapter._resolve_profile_inheritance(leaf)
    leaf.write_text(json.dumps({"name": "leaf", "include": ["fragment"]}), encoding="utf-8")
    (tmp_path / "fragment.json").write_text(json.dumps({"name": "fragment", "inherits": "leaf"}), encoding="utf-8")
    with pytest.raises(RuntimeError, match="inheritance cycle"):
        adapter._resolve_profile_inheritance(leaf)


def test_bambu_effective_profile_audit_rejects_generic_gcode_fallback(tmp_path: Path) -> None:
    machine = {
        "nozzle_diameter": ["0.2", "0.2"], "min_layer_height": ["0.04", "0.04"],
        "max_layer_height": ["0.14", "0.14"], "machine_start_gcode": "; machine: H2C\n",
    }
    project = tmp_path / "project.3mf"
    with zipfile.ZipFile(project, "w") as archive:
        archive.writestr("Metadata/project_settings.config", json.dumps(machine))
    resolved = {"machine": {"effective": machine}}
    assert adapter._audit_effective_bambu_profiles(project, resolved)["status"] == "passed"
    with zipfile.ZipFile(project, "w") as archive:
        archive.writestr("Metadata/project_settings.config", json.dumps({**machine, "machine_start_gcode": "GENERIC"}))
    with pytest.raises(RuntimeError, match="machine_start_gcode does not match"):
        adapter._audit_effective_bambu_profiles(project, resolved)


def test_bambu_mixed_filament_parser_and_slot_mapping(tmp_path: Path) -> None:
    metal = tmp_path / "Bambu PLA Metal.json"
    args = adapter.parser().parse_args([
        "bambu", "base.stl", "cap.3mf", "--filament-profile", "basic.json",
        "--slot-filament-profile", f"5={metal}",
    ])
    assert args.filament_profile == "basic.json"
    labels, paths = adapter._filament_slot_labels(args.slot_filament_profile, 5)
    assert labels == ["filament"] * 4 + ["filament_slot_5"]
    assert paths == {"filament_slot_5": metal}
    assert adapter._filament_slot_labels([], 5) == (["filament"] * 5, {})
    with pytest.raises(RuntimeError, match="duplicate"):
        adapter._filament_slot_labels([(5, metal), (5, metal)], 5)
    with pytest.raises(RuntimeError, match="outside"):
        adapter._filament_slot_labels([(6, metal)], 5)


@pytest.mark.parametrize("value", ["0=a.json", "-1=a.json", "1=", "1", "x=a.json"])
def test_bambu_rejects_invalid_filament_slot_option(value: str) -> None:
    with pytest.raises(SystemExit):
        adapter.parser().parse_args(["bambu", "base.stl", "cap.3mf", "--slot-filament-profile", value])


def test_bambu_audits_exact_filament_profile_per_slot(tmp_path: Path) -> None:
    basic = {"name": "Bambu PLA Basic", "filament_type": ["PLA"], "filament_diameter": ["1.75"]}
    metal = {**basic, "name": "Bambu PLA Metal"}
    resolved = {"filament": {"effective": basic}, "filament_slot_5": {"effective": metal}}
    labels = ["filament"] * 4 + ["filament_slot_5"]
    project = tmp_path / "mixed.3mf"
    settings = {
        "filament_settings_id": ["Bambu PLA Basic"] * 4 + ["Bambu PLA Metal"],
        "filament_type": ["PLA"] * 5, "filament_diameter": ["1.75"] * 5,
    }
    adapter._resize_bambu_flush_configuration(settings, 5)
    with zipfile.ZipFile(project, "w") as archive:
        archive.writestr("Metadata/project_settings.config", json.dumps(settings))
    assert adapter._audit_effective_bambu_profiles(project, resolved, labels)["status"] == "passed"
    # Both are PLA: the material identity audit must still catch a Metal slot
    # that silently fell back to Basic, and a swapped color/material order.
    for names in (["Bambu PLA Basic"] * 5, ["Bambu PLA Metal"] + ["Bambu PLA Basic"] * 4):
        with zipfile.ZipFile(project, "w") as archive:
            archive.writestr("Metadata/project_settings.config", json.dumps({**settings, "filament_settings_id": names}))
        with pytest.raises(RuntimeError, match="per-slot filament_settings_id"):
            adapter._audit_effective_bambu_profiles(project, resolved, labels)


def test_bambu_mixed_profile_command_uses_ordered_materialized_profiles(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    basic = tmp_path / "basic.json"
    metal = tmp_path / "metal.json"
    basic.write_text(json.dumps({"name": "Basic", "filament_type": ["PLA"], "filament_diameter": ["1.75"]}), encoding="utf-8")
    metal.write_text(json.dumps({"name": "Metal", "filament_type": ["PLA"], "filament_diameter": ["1.75"]}), encoding="utf-8")
    parts = [tmp_path / f"part-{index}.stl" for index in range(5)]
    for part in parts:
        part.write_bytes(b"test-only input")
    output = tmp_path / "export.3mf"
    args = adapter.parser().parse_args([
        "bambu", str(parts[0]), str(output), "--filament-profile", str(basic),
        "--slot-filament-profile", f"5={metal}",
        *[token for part in parts[1:] for token in ("--part", str(part))],
    ])
    observed = {}
    def fake_run(command: list[str], timeout: int) -> tuple[int, str, str]:
        loaded = command[command.index("--load-filaments") + 1].split(";")
        observed["names"] = [json.loads(Path(path).read_text())["name"] for path in loaded]
        assert command[command.index("--arrange") + 1] == "1"
        assert command[command.index("--orient") + 1] == "0"
        assert "--allow-rotations=0" in command
        destination = Path(command[command.index("--outputdir") + 1]) / command[command.index("--export-3mf") + 1]
        with zipfile.ZipFile(destination, "w") as archive:
            archive.writestr("Metadata/project_settings.config", json.dumps({
                "filament_settings_id": observed["names"], "filament_type": ["PLA"] * 5,
                "filament_diameter": ["1.75"] * 5,
            }))
        return 0, "", ""
    monkeypatch.setattr(adapter, "_resolve_executable", lambda *args: sys.executable)
    monkeypatch.setattr(adapter, "_run", fake_run)
    monkeypatch.setattr(adapter, "_tool_version", lambda _: "test")
    monkeypatch.setattr(adapter, "verify_3mf", lambda *args, **kwargs: {"status": "passed"})
    assert adapter.bambu_command(args) == 0
    assert observed["names"] == ["Basic"] * 4 + ["Metal"]
    manifest = json.loads(Path(f"{output}.manifest.json").read_text())
    assert manifest["adapter_version"] == 6
    assert set(manifest["profiles"]) == {"filament", "filament_slot_5"}
    assert manifest["filament_slot_profiles"][-1] == {"slot": 5, "profile_key": "filament_slot_5"}
    assert manifest["palette_patch"]["flush_configuration"]["matrix_length"] == 25
    assert manifest["effective_profile_audit"]["checked"]["flush_configuration"]["vector_length"] == 10


def test_bambu_palette_patch_resizes_four_to_five_without_changing_other_members(tmp_path: Path) -> None:
    project = tmp_path / "official-export.3mf"
    matrix = [0 if row == column else 280 for row in range(4) for column in range(4)]
    matrix[1] = "432.5"  # directional customized 1 -> 2 must retain its identity
    matrix[4] = 375  # reverse 2 -> 1 has a different value
    vector = [130, 150, 160, 170, 180, 190, 200, 210]
    settings = {"flush_volumes_matrix": matrix, "flush_volumes_vector": vector,
                "unrelated_process_value": "do not change"}
    geometry = b"unchanged official Bambu geometry and build transforms"
    with zipfile.ZipFile(project, "w") as archive:
        archive.writestr("Metadata/project_settings.config", json.dumps(settings))
        archive.writestr("3D/3dmodel.model", geometry)
    colors = ["#000000", "#888888", "#EEEEEE", "#CC2222", "#C0964B"]
    report = adapter._patch_bambu_project_colors(project, colors)
    with zipfile.ZipFile(project) as archive:
        actual = json.loads(archive.read("Metadata/project_settings.config"))
        assert archive.read("3D/3dmodel.model") == geometry
    assert actual["unrelated_process_value"] == "do not change"
    assert actual["filament_colour"] == colors
    assert actual["default_filament_colour"] == colors
    for row in range(5):
        for column in range(5):
            expected = str(matrix[row * 4 + column]) if row < 4 and column < 4 else ("0" if row == column else "280")
            assert actual["flush_volumes_matrix"][row * 5 + column] == expected
    assert actual["flush_volumes_vector"] == [str(value) for value in vector] + ["140", "140"]
    assert all(isinstance(value, str) for value in actual["flush_volumes_matrix"] + actual["flush_volumes_vector"])
    assert report["flush_configuration"]["preserved_matrix_values"] == 16
    assert report["flush_configuration"]["matrix_length"] == 25
    unchanged = json.loads(json.dumps(actual))
    adapter._resize_bambu_flush_configuration(actual, 5)
    assert actual == unchanged


@pytest.mark.parametrize("settings", [
    {"flush_volumes_matrix": [0, 280, 0]},
    {"flush_volumes_vector": [140]},
    {"flush_volumes_matrix": [float("nan")]},
    {"flush_volumes_vector": [-1, 140]},
    {"flush_volumes_vector": [True, 140]},
    {"flush_volumes_matrix": "0,280,280,0"},
])
def test_bambu_purge_resize_rejects_malformed_values(settings: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        adapter._resize_bambu_flush_configuration(settings, 5)


def test_bambu_purge_audit_rejects_four_slot_default_for_five_color_project() -> None:
    settings = {}
    adapter._resize_bambu_flush_configuration(settings, 4)
    with pytest.raises(ValueError, match="do not match 5 filament slots"):
        adapter._audit_bambu_flush_configuration(settings, 5)


def test_bambu_purge_audit_rejects_json_number_tokens() -> None:
    settings = {}
    adapter._resize_bambu_flush_configuration(settings, 5)
    settings["flush_volumes_matrix"][4] = 280
    with pytest.raises(ValueError, match="JSON string tokens"):
        adapter._audit_bambu_flush_configuration(settings, 5)
