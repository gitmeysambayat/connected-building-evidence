"""Assure the source-model link, triangle data, placements and length units."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import ifcopenshell
import ifcopenshell.geom
import ifcopenshell.util.placement
import pytest

from twin import geometry


SOURCE = Path(__file__).resolve().parents[1] / "data" / "source" / "04_Flat.ifc"


@pytest.fixture(scope="module")
def exported(tmp_path_factory):
    return geometry.build_geometry(SOURCE, tmp_path_factory.mktemp("geometry") / "geometry.json")


def test_every_ifc_space_has_its_real_guid_and_spatial_parent(exported):
    model = ifcopenshell.open(str(SOURCE))
    source_spaces = {item.GlobalId: item for item in model.by_type("IfcSpace")}
    meshes = {item["guid"]: item for item in exported["meshes"] if item["kind"] == "space"}
    assert len(source_spaces) == 9
    assert set(meshes) == set(source_spaces)
    for guid, mesh in meshes.items():
        source = source_spaces[guid]
        assert mesh["step_id"] == source.id()
        assert mesh["long_name"] == source.LongName
        assert mesh["storey"]["guid"] in {item.GlobalId for item in model.by_type("IfcBuildingStorey")}
        assert mesh["spatial_parent"]["guid"] == source.Decomposes[0].RelatingObject.GlobalId


def test_meshes_are_finite_indexed_triangles_from_real_source_objects(exported):
    model = ifcopenshell.open(str(SOURCE))
    for mesh in exported["meshes"]:
        source = model.by_guid(mesh["guid"])
        assert source.id() == mesh["step_id"]
        assert source.is_a() == mesh["ifc_class"]
        vertices, faces = mesh["vertices"], mesh["faces"]
        assert vertices and len(vertices) % 3 == 0
        assert faces and len(faces) % 3 == 0
        assert all(math.isfinite(value) for value in vertices)
        assert all(isinstance(index, int) and 0 <= index < len(vertices) // 3 for index in faces)
        for axis in range(3):
            assert mesh["bounds"]["min"][axis] == min(vertices[axis::3])
            assert mesh["bounds"]["max"][axis] == max(vertices[axis::3])
    assert not exported["errors"]


def test_output_is_metres_and_applies_source_world_placement_once(exported):
    model = ifcopenshell.open(str(SOURCE))
    mesh = next(item for item in exported["meshes"] if item["kind"] == "space")
    product = model.by_guid(mesh["guid"])
    local_settings = ifcopenshell.geom.settings()
    local_settings.set("use-world-coords", False)
    local_settings.set("convert-back-units", False)
    local_shape = ifcopenshell.geom.create_shape(local_settings, product)
    local_vertices = list(local_shape.geometry.verts)
    # Source placement matrix uses millimetres; geometric vertices already use m.
    matrix = ifcopenshell.util.placement.get_local_placement(product.ObjectPlacement)
    expected_world = []
    for index in range(0, len(local_vertices), 3):
        xyz = local_vertices[index:index + 3]
        for axis in range(3):
            expected_world.append(sum(matrix[axis, dim] * xyz[dim] for dim in range(3)) + matrix[axis, 3] * 0.001)
    actual_xyz = sorted(tuple(round(value, 7) for value in mesh["vertices"][index:index + 3]) for index in range(0, len(mesh["vertices"]), 3))
    expected_xyz = sorted(tuple(round(value, 7) for value in expected_world[index:index + 3]) for index in range(0, len(expected_world), 3))
    assert actual_xyz == expected_xyz
    assert exported["units"]["geometry"] == "m"
    assert exported["units"]["source_length"]["prefix"] == "MILLI"
    assert exported["units"]["source_length"]["unit_scale_to_m"] == pytest.approx(0.001)
    extents = [high - low for low, high in zip(exported["bounds"]["min"], exported["bounds"]["max"])]
    # A measured source extrusion is 2520 mm, so the storey separation is 2.52 m.
    assert [item["elevation_m"] for item in exported["spatial"]["storeys"]] == [0.0, 2.52]
    assert max(extents) < 100.0
    assert max(extents) > 1.0


def test_source_provenance_and_omissions_are_complete(exported):
    digest = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    assert exported["source_ifc_sha256"] == digest == geometry.PUBLISHED_IFC_SHA256
    assert exported["source"]["published_source_verified"] is True
    assert exported["source"]["commit"] == geometry.SOURCE_COMMIT
    assert exported["counts"]["elements_in_source"] == 54
    assert len(exported["omitted"]) == 16
    assert all(item["reason"] == "opening_void" for item in exported["omitted"])
    assert exported["counts"]["meshes"] == 47
    assert exported["counts"]["space_meshes"] == 9
    assert exported["counts"]["element_meshes"] == 38
    assert exported["counts"]["errors"] == 0
    assert hashlib.sha256(SOURCE.read_bytes()).hexdigest() == digest


def test_cache_and_lookup_keep_the_same_ifc_source(exported, tmp_path, monkeypatch):
    cache = tmp_path / "geometry.json"
    # An unrelated cache cannot be reused simply because it exists.
    cache.write_text(json.dumps({"schema_version": geometry.SCHEMA_VERSION, "source_ifc_sha256": "unrelated", "meshes": []}), encoding="utf-8")
    monkeypatch.setenv("TWIN_IFC_PATH", str(SOURCE))
    monkeypatch.setenv("TWIN_GEOMETRY_PATH", str(cache))
    current = geometry.get_geometry()
    assert current["source_ifc_sha256"] == exported["source_ifc_sha256"]
    assert len(current["meshes"]) == 47
    guid = current["meshes"][0]["guid"]
    assert geometry.element(guid)["guid"] == guid
    assert geometry.element("not-an-ifc-guid") is None
    assert "meshes" not in geometry.summary()
    assert geometry.summary()["counts"]["spaces_in_source"] == 9


def test_invalid_triangle_and_non_finite_meshes_are_rejected():
    with pytest.raises(ValueError, match="unavailable vertex"):
        geometry._validate_mesh([0.0, 0.0, 0.0], [0, 1, 2])
    with pytest.raises(ValueError, match="non-finite"):
        geometry._validate_mesh([math.nan, 0.0, 0.0], [0, 0, 0])
