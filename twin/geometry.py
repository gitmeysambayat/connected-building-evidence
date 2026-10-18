"""Read-only IFC tessellation and spatial metadata for the connected building.

IfcOpenShell performs its geometric calculations in metres.  Keeping
``convert-back-units`` false and applying world placements once prevents the
common error of multiplying tessellated vertices by the source unit scale a
second time.  The source IFC is never changed.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
from threading import RLock
from typing import Any

import ifcopenshell
import ifcopenshell.geom
import ifcopenshell.util.element
import ifcopenshell.util.unit


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = "20261018.v1"
SOURCE_COMMIT = "34acb1fb7a24005ddf29f7f232081713c2f78291"
SOURCE_REPOSITORY = "https://github.com/TechnicalBuildingSystems/OpenSmartHomeData"
PUBLISHED_IFC_SHA256 = "42ffba5ac38db6def62df2c1ea000dce1d2139dc0100e16e1ede8ee0562fef03"
GEOMETRY_DOCUMENTATION = "https://docs.ifcopenshell.org/ifcopenshell/geometry_settings.html"
_LOCK = RLock()
_MEMORY_KEY: tuple[str, str, str] | None = None
_MEMORY_PAYLOAD: dict[str, Any] | None = None


def _source_path() -> Path:
    return Path(os.environ.get("TWIN_IFC_PATH", PROJECT_ROOT / "data" / "source" / "04_Flat.ifc"))


def _cache_path() -> Path:
    return Path(os.environ.get("TWIN_GEOMETRY_PATH", PROJECT_ROOT / "data" / "derived" / "20261018_v1_geometry.json"))


def _sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _bounds(vertices: list[float]) -> dict[str, list[float]] | None:
    if not vertices:
        return None
    low = [min(vertices[axis::3]) for axis in range(3)]
    high = [max(vertices[axis::3]) for axis in range(3)]
    return {"min": low, "max": high}


def _combine_bounds(meshes: list[dict[str, Any]]) -> dict[str, list[float]] | None:
    bounds = [item["bounds"] for item in meshes if item["bounds"] is not None]
    if not bounds:
        return None
    return {
        "min": [min(item["min"][axis] for item in bounds) for axis in range(3)],
        "max": [max(item["max"][axis] for item in bounds) for axis in range(3)],
    }


def _identity(entity: Any) -> dict[str, Any]:
    return {
        "guid": entity.GlobalId,
        "step_id": entity.id(),
        "ifc_class": entity.is_a(),
        "name": getattr(entity, "Name", None),
        "long_name": getattr(entity, "LongName", None),
    }


def _parent(entity: Any) -> Any | None:
    """Return the IFC containment/decomposition parent, without guessing."""
    containers = getattr(entity, "ContainedInStructure", ())
    if containers:
        return containers[0].RelatingStructure
    for relation in getattr(entity, "Decomposes", ()):
        if relation.is_a("IfcRelAggregates") or relation.is_a("IfcRelNests"):
            return relation.RelatingObject
    return None


def _spatial_metadata(entity: Any, unit_scale: float) -> dict[str, Any]:
    parent = _parent(entity)
    cursor = parent
    seen = set()
    storey = None
    building = None
    ancestry = []
    while cursor is not None and cursor.id() not in seen:
        seen.add(cursor.id())
        ancestry.append(_identity(cursor))
        if cursor.is_a("IfcBuildingStorey") and storey is None:
            storey = _identity(cursor)
            elevation = getattr(cursor, "Elevation", None)
            storey["elevation_m"] = float(elevation) * unit_scale if elevation is not None else None
        if cursor.is_a("IfcBuilding"):
            building = _identity(cursor)
        cursor = _parent(cursor)
    return {
        "spatial_parent": _identity(parent) if parent is not None else None,
        "storey": storey,
        "building": building,
        "spatial_ancestry": ancestry,
    }


def _source_units(model: Any, unit_scale: float) -> dict[str, Any]:
    unit = ifcopenshell.util.unit.get_project_unit(model, "LENGTHUNIT")
    return {
        "geometry": "m",
        "source_length": {
            "name": getattr(unit, "Name", None),
            "prefix": getattr(unit, "Prefix", None),
            "ifc_class": unit.is_a() if unit is not None else None,
            "unit_scale_to_m": unit_scale,
        },
        "conversion": "IfcOpenShell returns metres; no further vertex scaling is applied.",
    }


def _validate_mesh(vertices: list[float], faces: list[int]) -> None:
    if not vertices or len(vertices) % 3:
        raise ValueError("Mesh has no complete XYZ vertices.")
    if not faces or len(faces) % 3:
        raise ValueError("Mesh has no complete triangle indices.")
    if not all(math.isfinite(value) for value in vertices):
        raise ValueError("Mesh contains non-finite coordinates.")
    vertex_count = len(vertices) // 3
    if any(index < 0 or index >= vertex_count for index in faces):
        raise ValueError("Mesh triangle refers to an unavailable vertex.")


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(payload, stream, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            stream.write("\n")
        temporary.replace(path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def build_geometry(source_path: str | Path | None = None, output_path: str | Path | None = None) -> dict[str, Any]:
    """Tessellate all represented spaces and physical elements into a JSON cache.

    IFC openings are voids, already subtracted from their host geometry.  They
    remain in source counts and the omission audit, rather than being drawn as
    solid blocks that would incorrectly fill doors and windows.
    """
    source = Path(source_path) if source_path is not None else _source_path()
    destination = Path(output_path) if output_path is not None else _cache_path()
    source_hash = _sha256(source)
    model = ifcopenshell.open(str(source))
    unit_scale = float(ifcopenshell.util.unit.calculate_unit_scale(model))
    settings = ifcopenshell.geom.settings()
    settings.set("use-world-coords", True)
    settings.set("convert-back-units", False)
    settings.set("weld-vertices", True)

    spaces = model.by_type("IfcSpace")
    elements = model.by_type("IfcElement")
    products = sorted(spaces + elements, key=lambda product: product.id())
    meshes = []
    errors = []
    omitted = []
    for product in products:
        if product.is_a("IfcOpeningElement"):
            omitted.append({**_identity(product), "reason": "opening_void", "detail": "Void is subtracted from its host; it is not a solid physical element."})
            continue
        if not getattr(product, "Representation", None):
            omitted.append({**_identity(product), "reason": "no_representation"})
            continue
        try:
            shape = ifcopenshell.geom.create_shape(settings, product)
            vertices = [float(value) for value in shape.geometry.verts]
            faces = [int(value) for value in shape.geometry.faces]
            _validate_mesh(vertices, faces)
            item = {
                **_identity(product),
                "kind": "space" if product.is_a("IfcSpace") else "element",
                "vertices": vertices,
                "faces": faces,
                "bounds": _bounds(vertices),
                **_spatial_metadata(product, unit_scale),
                "properties": ifcopenshell.util.element.get_psets(product),
            }
            meshes.append(item)
        except Exception as exc:
            errors.append({**_identity(product), "stage": "tessellation", "error": f"{type(exc).__name__}: {exc}"})

    source_matches = source_hash == PUBLISHED_IFC_SHA256
    source_info = {
        "file": source.name,
        "ifc_sha256": source_hash,
        "bytes": source.stat().st_size,
        "schema": model.schema,
        "published_source_verified": source_matches,
        "repository_url": SOURCE_REPOSITORY if source_matches else None,
        "commit": SOURCE_COMMIT if source_matches else None,
        "file_url": f"{SOURCE_REPOSITORY}/blob/{SOURCE_COMMIT}/04_Flat.ifc" if source_matches else None,
        "licence": "CC BY-SA 4.0" if source_matches else "Not established by this geometry exporter.",
    }
    space_mesh_count = sum(item["kind"] == "space" for item in meshes)
    element_mesh_count = len(meshes) - space_mesh_count
    payload = {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "generator": {
            "library": "IfcOpenShell",
            "version": ifcopenshell.version,
            "settings": {"use-world-coords": True, "convert-back-units": False, "weld-vertices": True},
            "documentation_url": GEOMETRY_DOCUMENTATION,
        },
        "source": source_info,
        "source_ifc_sha256": source_hash,
        "units": _source_units(model, unit_scale),
        "coordinate_system": {"reference": "IFC world coordinates after ObjectPlacement", "up_axis": "Z", "length_unit": "m", "display_recentring_applied": False},
        "meshes": meshes,
        "bounds": _combine_bounds(meshes),
        "spatial": {
            "buildings": [_identity(item) for item in model.by_type("IfcBuilding")],
            "storeys": [{**_identity(item), "elevation_m": float(item.Elevation) * unit_scale if item.Elevation is not None else None} for item in model.by_type("IfcBuildingStorey")],
            "spaces": [{**_identity(item), **_spatial_metadata(item, unit_scale)} for item in spaces],
        },
        "counts": {
            "spaces_in_source": len(spaces),
            "elements_in_source": len(elements),
            "space_meshes": space_mesh_count,
            "element_meshes": element_mesh_count,
            "meshes": len(meshes),
            "vertices": sum(len(item["vertices"]) // 3 for item in meshes),
            "triangles": sum(len(item["faces"]) // 3 for item in meshes),
            "source_element_classes": dict(sorted(Counter(item.is_a() for item in elements).items())),
            "mesh_classes": dict(sorted(Counter(item["ifc_class"] for item in meshes).items())),
            "omitted": len(omitted),
            "errors": len(errors),
        },
        "errors": errors,
        "omitted": omitted,
        "limitations": [
            "Space volumes are IFC geometry, not sensor installation positions.",
            "Only represented physical elements and spaces are tessellated; IFC voids are listed in the omission audit.",
            "The model is published reference material. Exporting its mesh does not verify the real building's present condition.",
        ],
    }
    if _sha256(source) != source_hash:
        raise RuntimeError("Source IFC changed during geometry generation; cache was not written.")
    _write_json_atomic(destination, payload)
    return payload


def get_geometry() -> dict[str, Any]:
    """Return a cached export only when its SHA-256 still matches the source."""
    global _MEMORY_KEY, _MEMORY_PAYLOAD
    source = _source_path()
    destination = _cache_path()
    source_hash = _sha256(source)
    key = (str(source.resolve()), source_hash, str(destination.resolve()))
    with _LOCK:
        if _MEMORY_KEY == key and _MEMORY_PAYLOAD is not None:
            return _MEMORY_PAYLOAD
        payload = None
        if destination.is_file():
            try:
                candidate = json.loads(destination.read_text(encoding="utf-8"))
                if candidate.get("schema_version") == SCHEMA_VERSION and candidate.get("source_ifc_sha256") == source_hash:
                    payload = candidate
            except (OSError, ValueError, TypeError, AttributeError):
                pass
        if payload is None:
            payload = build_geometry(source, destination)
        _MEMORY_KEY = key
        _MEMORY_PAYLOAD = payload
        return payload


def summary() -> dict[str, Any]:
    """Return model provenance, spatial metadata and conversion assurance."""
    payload = get_geometry()
    return {key: value for key, value in payload.items() if key != "meshes"}


def element(guid: str) -> dict[str, Any] | None:
    """Return a represented IFC object by GUID; unknown GUIDs return None."""
    return next((item for item in get_geometry()["meshes"] if item["guid"] == guid), None)
