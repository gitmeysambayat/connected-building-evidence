"""Copy a verified baseline into writable state and run one application worker."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def checked_child(parent: Path, name: str) -> Path:
    child = (parent / name).resolve()
    if not child.is_relative_to(parent.resolve()):
        raise ValueError("Prepared manifest contains an invalid relative path.")
    return child


def main() -> None:
    prepared = Path(os.environ.get("TWIN_PREPARED_DIR", "/opt/twin-prepared")).resolve()
    state = Path(os.environ.get("CONNECTED_BUILDING_DATA_DIR", "/state")).resolve()
    manifest_path = prepared / "20261018_v1_Prepared_Manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_hash = digest(manifest_path)
    for name, expected in manifest["source_files"].items():
        if digest(checked_child(ROOT / "data" / "source", name)) != expected:
            raise ValueError("Pinned source differs from the prepared baseline: " + name)
    for name, expected in manifest["prepared_files"].items():
        if digest(checked_child(prepared, name)) != expected:
            raise ValueError("Prepared store hash differs: " + name)
    state.mkdir(parents=True, exist_ok=True)
    installed = state / "20261018_v1_Installed_Baseline.json"
    restored = not installed.exists()
    if installed.exists():
        previous = json.loads(installed.read_text(encoding="utf-8"))
        if previous.get("prepared_manifest_sha256") != manifest_hash:
            raise ValueError("Existing state belongs to another baseline; use a fresh state directory.")
    else:
        if (state / "transport").exists():
            raise ValueError("Refusing to replace unrecognised existing transport state.")
        for name in manifest["prepared_files"]:
            source, destination = checked_child(prepared, name), checked_child(state, name)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            if digest(destination) != manifest["prepared_files"][name]:
                raise ValueError("Prepared-store copy failed verification: " + name)
        installed.write_text(json.dumps({"prepared_manifest_sha256": manifest_hash}), encoding="utf-8")
    boot = {
        "boot_id": uuid.uuid4().hex,
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_revision": manifest["source_revision"],
        "prepared_manifest_sha256": manifest_hash,
        "source_ifc_sha256": manifest["source_ifc_sha256"],
        "restored_baseline": restored,
        "state_persistence": os.environ.get("TWIN_STATE_PERSISTENCE", "ephemeral"),
        "scope": "Configured container storage policy. The hosting provider's execution is verified separately.",
    }
    record = Path(os.environ.get("TWIN_CLOUD_BOOT_RECORD", state / "20261018_v1_Cloud_Boot.json"))
    record.write_text(json.dumps(boot, indent=2), encoding="utf-8")
    print(json.dumps({"event": "verified_baseline_ready", **boot}), flush=True)
    port = int(os.environ.get("PORT", "8080"))
    if not 1024 <= port <= 65535:
        raise ValueError("HTTP port must be between 1024 and 65535.")
    os.execv(sys.executable, [sys.executable, "-m", "uvicorn", "twin.main:app",
                            "--host", "0.0.0.0", "--port", str(port), "--workers", "1"])


if __name__ == "__main__":
    main()
