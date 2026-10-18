"""Run the full test suite and bind its receipt to an unchanged file snapshot."""
from __future__ import annotations
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]


def snapshot():
    paths = [p for folder in ("twin", "tests", "scripts", "static", "data/source", "data/derived", "docs", ".github")
             for p in (ROOT / folder).rglob("*") if p.is_file() and "__pycache__" not in p.parts]
    paths.extend(p for p in ROOT.iterdir() if p.is_file())
    return {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(set(paths))}


def main():
    reports = ROOT / "reports"
    reports.mkdir(exist_ok=True)
    junit = reports / "20261018_v1_Test_Results.xml"
    command = [sys.executable, "-m", "pytest", "--junitxml", str(junit), "-q"]
    before = snapshot()
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    hashes = snapshot()
    changed = sorted(key for key in before.keys() | hashes.keys() if before.get(key) != hashes.get(key))
    def redact(value):
        for path, label in ((ROOT, "<project>"), (Path(sys.prefix), "<python-environment>"), (Path.home(), "<user-home>")):
            value = value.replace(str(path), label).replace(path.as_posix(), label)
        return value
    safe_output = redact(result.stdout + result.stderr)
    (reports / "20261018_v1_Test_Output.txt").write_text(safe_output, encoding="utf-8")
    root = ET.parse(junit).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    for suite in suites:
        suite.attrib.pop("hostname", None)
    for node in root.iter():
        if node.text:
            node.text = redact(node.text)
        if node.tail:
            node.tail = redact(node.tail)
        for key in node.attrib:
            node.attrib[key] = redact(node.attrib[key])
    ET.ElementTree(root).write(junit, encoding="utf-8", xml_declaration=True)
    counts = {key: sum(int(s.get(key, "0")) for s in suites) for key in ("tests", "failures", "errors", "skipped")}
    verified_exit = result.returncode or (2 if changed else 0)
    output = {"generated_at_utc": datetime.now(timezone.utc).isoformat(), "command": "python -m pytest --junitxml reports/20261018_v1_Test_Results.xml -q",
              "python": sys.version.split()[0], "exit_code": verified_exit, "pytest_exit_code": result.returncode,
              "files_unchanged_during_tests": not changed, "files_changed_during_tests": changed, **counts,
              "scope": ("GitHub Actions execution. Container execution is separate and not implied."
                        if os.environ.get("GITHUB_ACTIONS") == "true"
                        else "Local execution. Hosted CI and container execution are separate and not implied."),
              "redactions": "Local project/environment paths in text output and machine hostname in JUnit are removed from the portable report.",
              "source_manifest_sha256": hashlib.sha256((ROOT / "data/source/20261018_v1_Manifest.json").read_bytes()).hexdigest(),
              "file_sha256": hashes}
    (reports / "20261018_v1_Test_Results.json").write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in output.items() if key != "file_sha256"}, indent=2))
    return verified_exit


if __name__ == "__main__":
    raise SystemExit(main())
