"""Build a real IFC mesh cache without changing the source model."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from twin.geometry import build_geometry


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, help="IFC source; defaults to data/source/04_Flat.ifc")
    parser.add_argument("--output", type=Path, help="Destination cache JSON; defaults to data/derived/20261018_v1_geometry.json")
    options = parser.parse_args()
    payload = build_geometry(options.source, options.output)
    print(json.dumps({"source": payload["source"], "units": payload["units"], "bounds": payload["bounds"], "counts": payload["counts"], "errors": payload["errors"]}, indent=2, ensure_ascii=False))
    return int(bool(payload["errors"]) or payload["counts"]["space_meshes"] != payload["counts"]["spaces_in_source"])


if __name__ == "__main__":
    raise SystemExit(main())
