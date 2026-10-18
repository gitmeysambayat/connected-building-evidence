"""Execute real Linux-container checks under 512 MiB RAM and 0.1 CPU limits.

Run on a Docker host after building the image. This script uses only Python's
standard library. Reports contain no session cookies or host filesystem paths.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import http.cookiejar
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.error
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]
MODEL = "42ffba5ac38db6def62df2c1ea000dce1d2139dc0100e16e1ede8ee0562fef03"


def command(*args: str) -> str:
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout.strip()


class BrowserSession:
    def __init__(self, base: str):
        self.base = base
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def request(self, path: str, body=None, expected=200):
        headers = {"Origin": self.base}
        payload = None
        if body is not None:
            payload = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.base + path, data=payload, headers=headers)
        try:
            response = self.opener.open(request, timeout=45)
        except urllib.error.HTTPError as error:
            response = error
        content = response.read()
        assert response.status == expected, (path, response.status, content[:400].decode(errors="replace"))
        return json.loads(content)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default="connected-building-cloud:ci")
    parser.add_argument("--output", type=Path, default=Path("reports/20261018_v1_Cloud_Container_Verification.json"))
    parser.add_argument("--port", type=int, default=18080)
    args = parser.parse_args()
    assert command("docker", "info", "--format", "{{.OSType}}") == "linux", "Linux containers are required."
    base = f"http://127.0.0.1:{args.port}"
    containers = []
    results = {"generated_at_utc": datetime.now(timezone.utc).isoformat(),
               "source_revision": command("git", "rev-parse", "HEAD"),
               "scope": "Executed Linux Docker checks. This is not a Render deployment or browser rendering check.",
               "execution_environment": "GitHub Actions" if os.environ.get("GITHUB_ACTIONS") == "true" else "Other Docker host",
               "limits": {"memory_bytes": 512 * 1024 * 1024, "cpus": 0.1,
                          "one_uvicorn_worker": True, "mqtt_disabled": False},
               "checks": [], "containers": [], "success": False}
    active = None

    def start_container():
        cid = command("docker", "run", "--detach", "--name", "connected-building-smoke-" + uuid.uuid4().hex[:12],
                      "--label", "connected-building-ci=capacity-verification", "--memory", "512m", "--memory-swap", "512m",
                      "--cpus", "0.1", "--pids-limit", "256", "--security-opt", "no-new-privileges:true",
                      "--publish", f"127.0.0.1:{args.port}:8080", "--env", "TWIN_PUBLIC_DEMO=1",
                      "--env", "TWIN_PUBLIC_ORIGIN=" + base, "--env", "TWIN_DEPLOYMENT_ENVIRONMENT=ci-linux-container",
                      "--env", "TWIN_STATE_PERSISTENCE=ephemeral", args.image)
        containers.append(cid)
        return cid

    def ready(cid):
        start = time.monotonic()
        last_error = "not contacted"
        while time.monotonic() - start < 180:
            status = json.loads(command("docker", "inspect", cid))[0]["State"]
            if not status["Running"]:
                raise AssertionError("Container stopped before becoming healthy: " + str(status))
            try:
                health = BrowserSession(base).request("/health")
                if health["status"] == "ok" and health["mqtt"] == "running":
                    assert health["source_model_sha256"] == MODEL
                    return round(time.monotonic() - start, 3)
            except Exception as error:
                last_error = str(error)
            time.sleep(2)
        raise AssertionError("Container did not become healthy within 180 s: " + last_error)

    def inspect_capacity(cid, label):
        info = json.loads(command("docker", "inspect", cid))[0]
        peak = int(command("docker", "exec", cid, "cat", "/sys/fs/cgroup/memory.peak"))
        events = dict(line.split() for line in command("docker", "exec", cid, "cat", "/sys/fs/cgroup/memory.events").splitlines())
        record = {"phase": label, "memory_peak_bytes": peak, "oom_killed": info["State"]["OOMKilled"],
                  "oom_events": int(events.get("oom", 0)), "oom_kill_events": int(events.get("oom_kill", 0)),
                  "configured_memory_bytes": info["HostConfig"]["Memory"],
                  "configured_nano_cpus": info["HostConfig"]["NanoCpus"], "running": info["State"]["Running"]}
        results["containers"].append(record)
        assert record["configured_memory_bytes"] == 512 * 1024 * 1024
        assert record["configured_nano_cpus"] == 100000000
        assert record["running"] and not record["oom_killed"] and record["oom_kill_events"] == 0
        assert peak <= 512 * 1024 * 1024

    def wait_replay(client, expected_count, duplicates=False):
        start = time.monotonic()
        while time.monotonic() - start < 45:
            status = client.request("/api/replay/status")
            if status["replay"] == "failed":
                raise AssertionError(status)
            run = status["current_run"]
            key = "duplicates" if duplicates else "accepted"
            if status["replay"] == "completed" and run[key] == expected_count:
                assert run["published"] == expected_count and run["rejected"] == 0
                return status
            time.sleep(1)
        raise AssertionError("MQTT replay did not complete with the expected verified receipts.")

    try:
        active = start_container()
        first_start = ready(active)
        client = BrowserSession(base)
        summary = client.request("/api/summary")
        assert (summary["spaces"], summary["channels"], summary["observations"]) == (9, 37, 285954)
        assert summary["verified_channels"] == 36 and summary["model_sha256"] == MODEL
        assert summary["deployment"]["state_persistence"] == "ephemeral"
        assert summary["transport"]["persistent_unique_events"] == 0
        results["checks"].append({"name": "fresh_prepared_boot_and_source_counts", "passed": True, "ready_seconds": first_start})
        station = client.request("/api/live-sensor/status")
        assert station["enabled"] is False and station["state"] == "disabled"
        assert station["latest"] is None and station["count"] == 0
        assert station["model_assignment"] == "unassigned"
        assert client.request("/api/live-sensor/readings")["readings"] == []
        client.request("/api/live-sensor/readings", {}, expected=503)
        assert client.request("/api/live-sensor/status")["count"] == 0
        results["checks"].append({"name": "sensor_station_empty_by_default_and_unconfigured_ingestion_rejected", "passed": True})
        graph = client.request("/api/graph/query?template=room_alignment")
        assert len(graph["rows"]) == 9
        assert len(client.request("/api/graph/query?template=verified_channels")["rows"]) == 36
        geometry = client.request("/api/geometry")
        assert geometry["source_ifc_sha256"] == MODEL and geometry["counts"]["space_meshes"] == 9
        forecast = client.request("/api/analytics")
        assert len(forecast["rooms"]) == 6 and all(r["recommended_predictor"] == "persistence" for r in forecast["rooms"])
        results["checks"].append({"name": "live_oxigraph_sparql_ifc_and_frozen_forecast", "passed": True})
        series = client.request("/api/series/Room1_Temperature?limit=3")
        gql = client.request("/graphql", {"query": '{channel(id:"Room1_Temperature"){id roomGuid modelSha256 readings(limit:3){epoch value sourceLine}}}'})
        assert "errors" not in gql
        channel = gql["data"]["channel"]
        assert channel["modelSha256"] == MODEL and channel["roomGuid"] == series["channel"]["room_guid"]
        assert channel["readings"] == [{"epoch": r["epoch"], "value": r["value"], "sourceLine": r["source_line"]} for r in series["readings"]]
        results["checks"].append({"name": "rest_graphql_same_source_rows", "passed": True})
        replay = {"speed": 100, "limit": 20, "channel_id": "Room1_Temperature"}
        client.request("/api/replay/start", replay, expected=403)
        session = client.request("/api/session")
        assert session["public_demo"] and session["session_ready"]
        client.request("/api/replay/start", replay)
        status = wait_replay(client, 20)
        stranger = BrowserSession(base)
        stranger.request("/api/session")
        stranger.request("/api/replay/stop", {}, expected=403)
        client.request("/api/replay/restart-broker", {})
        cooldown = client.request("/api/replay/status")["public_demo"]["cooldowns"]["start"]
        if cooldown:
            time.sleep(cooldown + 0.2)
        client.request("/api/replay/start", replay)
        status = wait_replay(client, 20, duplicates=True)
        assert status["persistent_unique_events"] == 20 and status["subscriber"] == "connected"
        events = client.request("/api/replay/events?limit=20")["events"]
        with (ROOT / "data/source/Measurements/Room1_Temperature.csv").open(encoding="utf-8") as stream:
            source_rows = list(csv.reader(stream, delimiter="\t"))
        for event in events:
            row = source_rows[event["source_line"] - 1]
            assert (event["observed_at"], event["value"]) == (int(row[0]), float(row[1]))
            assert event["model_sha256"] == MODEL and event["room_guid"] == series["channel"]["room_guid"]
        results["checks"].append({"name": "real_authenticated_mqtt_20_receipts_20_duplicates_and_subscriber_reconnection", "passed": True,
                                  "visitor_cookie_required": True, "other_visitor_control_rejected": True})
        inspect_capacity(active, "after_cold_start_and_mqtt")
        command("docker", "restart", "--time", "20", active)
        restart_start = ready(active)
        assert BrowserSession(base).request("/api/replay/status")["persistent_unique_events"] == 20
        assert len(BrowserSession(base).request("/api/graph/query?template=room_alignment")["rows"]) == 9
        results["checks"].append({"name": "same_container_restart_preserves_writable_state", "passed": True, "ready_seconds": restart_start})
        inspect_capacity(active, "after_same_container_restart")
        command("docker", "stop", "--time", "20", active)
        active = start_container()
        replacement_start = ready(active)
        fresh = BrowserSession(base).request("/api/summary")
        assert fresh["observations"] == 285954 and fresh["transport"]["persistent_unique_events"] == 0
        results["checks"].append({"name": "replacement_container_rebuilds_baseline_and_resets_ephemeral_receipts", "passed": True,
                                  "ready_seconds": replacement_start})
        inspect_capacity(active, "after_replacement_cold_start")
        results["image_bytes"] = int(command("docker", "image", "inspect", args.image, "--format", "{{.Size}}"))
        results["success"] = True
    except Exception as error:
        results["failure"] = type(error).__name__ + ": " + str(error)
        raise
    finally:
        container_logs = []
        results["final_container_states"] = []
        for index, cid in enumerate(containers, 1):
            try:
                info = json.loads(command("docker", "inspect", cid))[0]
                state = info["State"]
                results["final_container_states"].append({
                    "container_number": index, "running": state["Running"],
                    "oom_killed": state["OOMKilled"], "exit_code": state["ExitCode"],
                    "configured_memory_bytes": info["HostConfig"]["Memory"],
                    "configured_nano_cpus": info["HostConfig"]["NanoCpus"]})
                if state["Running"] and not results["success"]:
                    inspect_capacity(cid, "failure_diagnostics_" + str(index))
            except Exception as error:
                results.setdefault("diagnostic_errors", []).append(type(error).__name__ + ": " + str(error))
            log = subprocess.run(["docker", "logs", "--tail", "120", cid], capture_output=True, text=True)
            container_logs.append("Container " + str(index) + "\n" + log.stdout + log.stderr)
        results["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(results, indent=2), encoding="utf-8")
        args.output.with_name("20261018_v1_Cloud_Container_Logs.txt").write_text("\n".join(container_logs), encoding="utf-8")
        print(json.dumps(results, indent=2), flush=True)
        print("\n".join(container_logs), flush=True)
        for cid in containers:
            subprocess.run(["docker", "rm", "--force", cid], check=False, capture_output=True)


if __name__ == "__main__":
    main()
