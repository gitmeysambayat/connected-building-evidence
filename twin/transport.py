"""Real local MQTT transport carrying explicitly labelled historical records.

The publisher and subscriber are separate Paho clients over TCP. The subscriber
checks each message against the pinned CSV before accepting it. Source event
time and present-day receipt time are kept separately. Nothing controls a BMS.
"""
from __future__ import annotations

import asyncio
import csv
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timezone

from amqtt.broker import Broker
from paho.mqtt import client as mqtt
from pwdlib import PasswordHash

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = Path(os.environ.get("TWIN_RUNTIME_DIR", ROOT / "data" / "runtime"))
TOPIC = "osh/flat/historical/+"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class HistoricalTransport:
    def __init__(self, port: int | None = None, runtime: Path | None = None):
        self.port = port or int(os.environ.get("TWIN_MQTT_PORT", "18883"))
        self.runtime = runtime or RUNTIME
        self.broker = None
        self.subscriber = None
        self.publisher = None
        self.connected = threading.Event()
        self.subscribed = threading.Event()
        self.publish_connected = threading.Event()
        self.stop_event = threading.Event()
        self.worker = None
        self.lock = threading.Lock()
        self.start_lock = threading.Lock()
        self.state = {"broker": "stopped", "subscriber": "disconnected", "replay": "idle", "published": 0,
                      "accepted": 0, "duplicates": 0, "rejected": 0, "last_error": None, "last_received_at": None,
                      "label": "Historical measured data replay over local MQTT, not current physical sensors"}
        self.run_baseline = {"accepted": 0, "duplicates": 0, "rejected": 0}
        self.source = {}
        self.model_sha = ""
        self.password = secrets.token_urlsafe(32)
        self.config = None

    @contextmanager
    def db(self):
        self.runtime.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self.runtime / "transport.sqlite", timeout=15)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA busy_timeout=15000")
        try:
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def initialise(self):
        with self.db() as con:
            con.executescript("""
            CREATE TABLE IF NOT EXISTS accepted(event_id TEXT PRIMARY KEY,channel_id TEXT NOT NULL,
                observed_at INTEGER NOT NULL,value REAL NOT NULL,source_line INTEGER NOT NULL,
                received_at TEXT NOT NULL,model_sha256 TEXT NOT NULL,payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS deliveries(id INTEGER PRIMARY KEY,received_at TEXT NOT NULL,
                channel_id TEXT,status TEXT NOT NULL,reason TEXT NOT NULL,event_id TEXT);
            CREATE INDEX IF NOT EXISTS accepted_time ON accepted(observed_at);
            CREATE TABLE IF NOT EXISTS transport_metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            """)
            version = con.execute("SELECT value FROM transport_metadata WHERE key='schema_version'").fetchone()
            if version is None:
                if con.execute("SELECT COUNT(*) FROM accepted").fetchone()[0]:
                    raise ValueError("An older development transport identity is present. Choose a new TWIN_RUNTIME_DIR; published source files are unaffected.")
                con.execute("INSERT INTO transport_metadata VALUES('schema_version','2')")
            elif version[0] != '2':
                raise ValueError("Unsupported transport schema version.")
        self.model_sha = hashlib.sha256((ROOT / "data/source/04_Flat.ifc").read_bytes()).hexdigest()
        # This catalogue uses the same verified mapping decision as REST.
        from twin import data
        channel_catalogue = {c["id"]: c for c in data.channels(include_unverified=False)}
        for path in sorted((ROOT / "data/source/Measurements").glob("*.csv")):
            if path.stem not in channel_catalogue:
                continue
            rows = []
            with path.open(encoding="utf-8", newline="") as handle:
                for index, row in enumerate(csv.reader(handle, delimiter="\t"), 1):
                    if not row:
                        continue
                    rows.append((int(row[0]), float(row[1]), index))
            self.source[path.stem] = {"rows": rows, "by_line": {r[2]: r for r in rows},
                                      "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                                      "room_guid": channel_catalogue[path.stem]["room_guid"],
                                      "unit": channel_catalogue[path.stem]["unit"]}

    def payload(self, channel_id: str, row: tuple) -> dict:
        stamp, value, line = row
        source = self.source[channel_id]
        # Identical weather files are retained as publisher channel aliases.
        # A repeat delivery on one channel is a duplicate; a different channel
        # with identical bytes is still a distinct provenance association.
        event_id = hashlib.sha256(f"{self.model_sha}:{channel_id}:{source['sha256']}:{line}".encode()).hexdigest()
        return {"event_id": event_id, "channel_id": channel_id, "observed_at": stamp, "value": value,
                "source_line": line, "source_sha256": source["sha256"], "model_sha256": self.model_sha,
                "room_guid": source["room_guid"], "unit": source["unit"], "origin": "published_historical_replay"}

    def ingest(self, topic: str, raw: bytes) -> dict:
        received = utc_now()
        event = {}
        try:
            if len(raw) > 4096:
                raise ValueError("message exceeds 4096 bytes")
            event = json.loads(raw)
            if not isinstance(event, dict):
                raise ValueError("payload must be an object")
            channel = event.get("channel_id")
            if channel not in self.source:
                raise ValueError("unknown or unverified source channel")
            if topic != f"osh/flat/historical/{channel}":
                raise ValueError("topic and payload channel differ")
            if type(event.get("source_line")) is not int:
                raise ValueError("source_line must be an integer")
            row = self.source[channel]["by_line"].get(event["source_line"])
            if row is None:
                raise ValueError("source row does not exist")
            expected = self.payload(channel, row)
            if type(event.get("observed_at")) is not int or type(event.get("value")) not in (int, float):
                raise ValueError("invalid timestamp or value type")
            if not math.isfinite(event["value"]):
                raise ValueError("value must be finite")
            for key, value in expected.items():
                if event.get(key) != value:
                    raise ValueError(f"source mismatch: {key}")
            with self.db() as con:
                cursor = con.execute("INSERT OR IGNORE INTO accepted VALUES(?,?,?,?,?,?,?,?)",
                    (event["event_id"], channel, event["observed_at"], event["value"], event["source_line"], received,
                     self.model_sha, json.dumps(event, allow_nan=False)))
                status = "accepted" if cursor.rowcount else "duplicate"
                con.execute("INSERT INTO deliveries(received_at,channel_id,status,reason,event_id) VALUES(?,?,?,?,?)",
                            (received, channel, status, "pinned CSV row verified", event["event_id"]))
            with self.lock:
                self.state["accepted" if status == "accepted" else "duplicates"] += 1
                self.state["last_received_at"] = received
            return {"status": status, "event_id": event["event_id"], "received_at": received}
        except (ValueError, TypeError, KeyError, UnicodeError, json.JSONDecodeError) as exc:
            channel = event.get("channel_id") if isinstance(event, dict) else None
            # Do not persist untrusted raw payloads or secrets.
            reason = str(exc)[:240]
            with self.db() as con:
                con.execute("INSERT INTO deliveries(received_at,channel_id,status,reason) VALUES(?,?,?,?)",
                            (received, str(channel)[:100] if channel else None, "rejected", reason))
            with self.lock:
                self.state["rejected"] += 1
            return {"status": "rejected", "reason": reason, "received_at": received}

    async def start(self):
        self.initialise()
        password_file = self.runtime / ".mqtt_passwords"
        password_file.write_text("twin:" + PasswordHash.recommended().hash(self.password) + "\n", encoding="utf-8")
        config = {"listeners": {"default": {"type": "tcp", "bind": f"127.0.0.1:{self.port}", "max_connections": 8}},
                  "plugins": {"amqtt.plugins.authentication.FileAuthPlugin": {"password_file": str(password_file)}}}
        self.config = config
        self.broker = Broker(config)
        try:
            await self.broker.start()
            self.state["broker"] = "running"
            self.subscriber = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="osh-verified-subscriber")
            self.subscriber.username_pw_set("twin", self.password)
            self.subscriber.reconnect_delay_set(1, 8)

            def on_connect(client, userdata, flags, reason_code, properties):
                if reason_code == 0:
                    self.connected.set()
                    self.state["subscriber"] = "connected"
                    client.subscribe(TOPIC, qos=1)

            def on_subscribe(client, userdata, mid, reasons, properties):
                if all(not r.is_failure for r in reasons):
                    self.subscribed.set()

            def on_disconnect(client, userdata, flags, reason_code, properties):
                self.connected.clear()
                self.subscribed.clear()
                self.state["subscriber"] = "disconnected"

            self.subscriber.on_connect = on_connect
            self.subscriber.on_subscribe = on_subscribe
            self.subscriber.on_disconnect = on_disconnect
            self.subscriber.on_message = lambda client, userdata, msg: self.ingest(msg.topic, msg.payload)
            self.subscriber.connect_async("127.0.0.1", self.port, 30)
            self.subscriber.loop_start()
            if not await asyncio.to_thread(self.subscribed.wait, 8):
                raise RuntimeError("MQTT subscription did not become ready")
        except Exception as exc:
            self.state["last_error"] = str(exc)
            self.state["broker"] = "failed"
            await self.close()
            raise

    def begin_replay(self, speed: float = 20, limit: int = 300, channel_id: str | None = None) -> dict:
        with self.start_lock:
            return self._begin_replay(speed, limit, channel_id)

    def _begin_replay(self, speed: float, limit: int, channel_id: str | None) -> dict:
        if self.state["broker"] != "running" or not self.subscribed.is_set():
            raise ValueError("MQTT broker/subscriber is not ready")
        if self.worker and self.worker.is_alive() and self.state["replay"] in {"completed", "stopped", "failed"}:
            self.worker.join(timeout=.25)
        if self.worker and self.worker.is_alive():
            raise ValueError("a replay is already running")
        if not 1 <= speed <= 100 or not 1 <= limit <= 2000:
            raise ValueError("speed must be 1-100 events/s and limit 1-2000")
        if channel_id and channel_id not in self.source:
            raise ValueError("unknown or unverified channel")
        records = [(row[0], channel, row) for channel, source in self.source.items()
                   if channel_id is None or channel == channel_id for row in source["rows"]]
        records.sort(key=lambda item: (item[0], item[1], item[2][2]))
        selected = records[:limit]
        self.stop_event.clear()
        self.publish_connected.clear()
        self.run_baseline = {key: self.state[key] for key in ("accepted", "duplicates", "rejected")}
        self.state.update(replay="running", published=0, requested_limit=limit, events_per_second=speed, last_error=None,
                          replay_started_at=utc_now())

        def run():
            client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="osh-historical-publisher")
            self.publisher = client
            client.username_pw_set("twin", self.password)
            client.on_connect = lambda c, u, f, reason, p: self.publish_connected.set() if reason == 0 else None
            finish_state = "completed"
            try:
                client.connect("127.0.0.1", self.port, 30)
                client.loop_start()
                if not self.publish_connected.wait(8):
                    raise RuntimeError("publisher failed to connect")
                for _, channel, row in selected:
                    if self.stop_event.is_set():
                        break
                    info = client.publish(f"osh/flat/historical/{channel}", json.dumps(self.payload(channel, row)), qos=1, retain=False)
                    info.wait_for_publish(timeout=5)
                    if not info.is_published():
                        raise RuntimeError("MQTT PUBACK timeout")
                    self.state["published"] += 1
                    if self.stop_event.wait(1 / speed):
                        break
                finish_state = "stopped" if self.stop_event.is_set() else "completed"
            except Exception as exc:
                self.state["last_error"] = str(exc)
                finish_state = "failed"
            finally:
                client.disconnect()
                client.loop_stop()
                self.state["replay_finished_at"] = utc_now()
                self.state["replay"] = finish_state

        self.worker = threading.Thread(target=run, daemon=True, name="historical-mqtt-publisher")
        self.worker.start()
        return self.status()

    def stop(self) -> dict:
        self.stop_event.set()
        return self.status()

    async def restart_broker(self) -> dict:
        """Demonstrate reconnection while idle, without claiming durable sessions."""
        if self.worker and self.worker.is_alive():
            raise ValueError("Stop the replay before restarting the broker.")
        if self.broker is None or self.config is None:
            raise ValueError("Broker has not been started.")
        self.state["broker"] = "restarting"
        await self.broker.shutdown()
        self.connected.clear()
        self.subscribed.clear()
        self.broker = Broker(self.config)
        await self.broker.start()
        self.state["broker"] = "running"
        if not await asyncio.to_thread(self.subscribed.wait, 15):
            self.state["last_error"] = "Subscriber did not reconnect after broker restart"
            raise RuntimeError(self.state["last_error"])
        self.state["broker_restarts"] = self.state.get("broker_restarts", 0) + 1
        return self.status()

    def status(self) -> dict:
        with self.db() as con:
            total = con.execute("SELECT COUNT(*) FROM accepted").fetchone()[0]
            delivery_count = con.execute("SELECT COUNT(*) FROM deliveries").fetchone()[0]
        return {**self.state, "schema_version": 2, "host": "127.0.0.1", "port": self.port, "protocol": "MQTT 3.1.1", "qos": 1,
                "authentication": "per-process random password, Argon2 stored hash", "tls": False,
                "scope": "loopback demonstration", "persistent_unique_events": total, "persistent_delivery_count": delivery_count,
                "counter_scope": {"published": "current replay run", "accepted": "current process", "duplicates": "current process", "rejected": "current process", "persistent_unique_events": "all runs retained in this runtime database"},
                "current_run": {"published": self.state["published"], **{key: self.state[key] - self.run_baseline[key] for key in self.run_baseline}},
                "model_sha256": self.model_sha, "source_channels": len(self.source),
                "source_time_basis": "UTC from verified Unix timestamps", "replay_clock": "bounded event rate; source timestamps preserved"}

    def events(self, limit: int = 50) -> dict:
        with self.db() as con:
            rows = [dict(r) for r in con.execute("SELECT * FROM accepted ORDER BY received_at DESC LIMIT ?", (max(1, min(limit, 500)),))]
            deliveries = [dict(r) for r in con.execute("SELECT * FROM deliveries ORDER BY id DESC LIMIT ?", (max(1, min(limit, 500)),))]
        for row in rows:
            verified_payload = json.loads(row.pop("payload"))
            row.update({key: verified_payload[key] for key in ("unit", "room_guid", "source_sha256", "origin")})
        return {"events": rows, "deliveries": deliveries, "label": self.state["label"]}

    async def close(self):
        self.stop_event.set()
        if self.worker and self.worker.is_alive():
            await asyncio.to_thread(self.worker.join, 7)
        if self.subscriber:
            self.subscriber.disconnect()
            await asyncio.to_thread(self.subscriber.loop_stop)
        if self.broker:
            await self.broker.shutdown()
        self.state["broker"] = "stopped"
        self.state["subscriber"] = "disconnected"
