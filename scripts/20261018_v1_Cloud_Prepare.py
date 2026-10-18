"""Compute real, hash-bound baseline stores before starting a small cloud instance."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--revision", default="unrecorded")
    args = parser.parse_args()
    target = args.output.resolve()
    if target.exists() and any(target.iterdir()):
        raise ValueError("Prepared-store destination must be empty.")
    target.mkdir(parents=True, exist_ok=True)
    os.environ.update({
        "CONNECTED_BUILDING_DATA_DIR": str(target),
        "CONNECTED_BUILDING_DB": str(target / "connected_building.sqlite3"),
        "CONNECTED_BUILDING_GRAPH": str(target / "semantic_graph"),
        "TWIN_GEOMETRY_PATH": str(target / "20261018_v1_geometry.json"),
        "TWIN_ANALYTICS_PATH": str(target / "20261018_v1_temperature_forecast.json"),
    })
    # Import only after the immutable output paths have been configured.
    from twin import analytics, data, geometry, semantic, settings

    measured = data.initialise(force=True)
    graph = semantic.initialise(force=True)
    mesh = geometry.build_geometry()
    forecast = analytics.fit(force=True)
    assert measured["spaces"] == 9 and measured["channels"] == 37
    assert measured["observations"] == 285954
    assert len(semantic.query("room_alignment")["rows"]) == 9
    assert len(semantic.query("verified_channels")["rows"]) == 36
    assert mesh["counts"]["space_meshes"] == 9 and not mesh["errors"]
    assert len(forecast["rooms"]) == 6
    assert all(row["recommended_predictor"] == "persistence" for row in forecast["rooms"])
    semantic.close()  # Copy no live RocksDB handle or unflushed graph.
    prepared = {
        "schema_version": "20261018.v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_revision": args.revision,
        "dataset_commit": settings.SOURCE_COMMIT,
        "source_manifest_sha256": digest(settings.SOURCE_DIR / settings.MANIFEST_NAME),
        "source_ifc_sha256": digest(settings.SOURCE_IFC),
        "source_files": {p.relative_to(settings.SOURCE_DIR).as_posix(): digest(p)
                         for p in sorted(settings.SOURCE_DIR.rglob("*")) if p.is_file()},
        "prepared_files": {p.relative_to(target).as_posix(): digest(p)
                           for p in sorted(target.rglob("*")) if p.is_file()},
        "verified": {"spaces": 9, "channels": 37, "observations": 285954,
                     "room_alignment_query_rows": 9, "verified_channel_query_rows": 36,
                     "geometry_space_meshes": 9, "forecast_rooms": 6,
                     "validation_gate": "Persistence selected in all six rooms"},
        "graph_engine": graph["engine"],
        "scope": "Actual computation when this build runs. This file alone is not an execution receipt for a hosting provider.",
    }
    (target / "20261018_v1_Prepared_Manifest.json").write_text(
        json.dumps(prepared, indent=2), encoding="utf-8")
    print(json.dumps({"status": "prepared", "verified": prepared["verified"],
                      "prepared_file_count": len(prepared["prepared_files"]),
                      "source_manifest_sha256": prepared["source_manifest_sha256"]}))


if __name__ == "__main__":
    main()
