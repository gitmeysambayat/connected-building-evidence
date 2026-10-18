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
import heapq
import json
import logging
import math
import os
from pathlib import Path
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timezone

from amqtt.broker import Broker as AMQTTBroker
from paho.mqtt import client as mqtt
from pwdlib import PasswordHash

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = Path(os.environ.get("TWIN_RUNTIME_DIR", ROOT / "data" / "runtime"))
TOPIC = "osh/flat/historical/+"
LOGGER = logging.getLogger("uvicorn.error")


class Broker(AMQTTBroker):
    """Own TCP connections even before a peer sends its MQTT CONNECT packet."""

    def __init__(self, *args, **kwargs):
        self.connection_writers = set()
        self.connection_tasks = set()
        self.cleanup_complete = False
        super().__init__(*args, **kwargs)

    async def stream_connected(self, reader, writer, listener_name):
        task = asyncio.current_task()
        self.connection_writers.add(writer)
        self.connection_tasks.add(task)
        try:
            await super().stream_connected(reader, writer, listener_name)
        finally:
            writer.close()
            self.connection_writers.discard(writer)
            self.connection_tasks.discard(task)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class HistoricalTransport:
    restart_timeout = 30.0
    broker_shutdown_timeout = 6.0
    broker_start_timeout = 8.0
    cleanup_timeout = 2.0
    client_stop_timeout = 4.0

    def __init__(self, port: int | None = None, runtime: Path | None = None, *, delivery_retention: int | None = None, public_mode: bool = False):
        self.port = port or int(os.environ.get("TWIN_MQTT_PORT", "18883"))
        if delivery_retention is not None and (type(delivery_retention) is not int or delivery_retention < 1):
            raise ValueError("Delivery retention must be a positive integer.")
        self.delivery_retention = delivery_retention
        self.public_mode = public_mode
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
        self._operation_deadline = None
        self._phase_tasks = set()

    @staticmethod
    def _broker_resources(broker):
        """Capture owned resources before amqtt can pop a stalled session.

        The pinned amqtt 0.12.1 shutdown removes a session before awaiting its
        handler. Pre-CONNECT TCP clients are not in that session dictionary.
        Their broker-owned connection tasks still retain the stream writers.
        """
        handlers = [entry[1] for entry in broker.sessions.values() if entry[1]]
        servers = [server.instance for server in broker._servers.values() if server.instance]
        tasks = set(broker.connection_tasks)
        writers = set(broker.connection_writers)
        for handler in handlers:
            adapter = getattr(handler, "writer", None)
            writer = getattr(adapter, "_writer", None)
            if writer is not None:
                writers.add(writer)
        for task in (broker._broadcast_task, broker._session_monitor_task,
                     *broker.plugins_manager._fired_events):
            if task is not None:
                tasks.add(task)
        return {"handlers": handlers, "servers": servers, "tasks": tasks, "writers": writers}

    def _phase_timeout(self, timeout):
        if self._operation_deadline is None:
            return timeout
        return max(.01, min(timeout, self._operation_deadline - time.monotonic()))

    async def _bounded(self, awaitable, timeout, phase):
        """Bound the wait itself, including cancellation-resistant library code."""
        timeout = self._phase_timeout(timeout)
        task = asyncio.ensure_future(awaitable)
        self._phase_tasks.add(task)
        def finished(future):
            self._phase_tasks.discard(future)
            if not future.cancelled():
                future.exception()
        task.add_done_callback(finished)
        try:
            done, _ = await asyncio.wait({task}, timeout=timeout)
            if done:
                return task.result()
            raise TimeoutError(f"MQTT {phase} exceeded {timeout:g} seconds.")
        finally:
            if not task.done():
                task.cancel()
                await asyncio.wait({task}, timeout=self._phase_timeout(self.cleanup_timeout))
            if task.done() and not task.cancelled():
                task.exception()  # Consume a late exception after a timeout.

    async def _stop_subscriber(self):
        client = self.subscriber
        if client is None:
            return
        # loop_stop() joins without a timeout in Paho 2.1.0. Use its termination
        # flag with a bounded join, rather than leaving an unbounded pool job.
        client._thread_terminate = True
        client.disconnect()
        thread = client._thread
        if thread is not None:
            await asyncio.to_thread(thread.join, self._phase_timeout(self.client_stop_timeout))
            if thread.is_alive():
                client._sock_close()
                await asyncio.to_thread(thread.join, self._phase_timeout(self.cleanup_timeout))
            if thread.is_alive():
                raise TimeoutError("MQTT subscriber network thread did not stop.")
        self.subscriber = None
        self.connected.clear()
        self.subscribed.clear()
        self.state["subscriber"] = "disconnected"

    async def _shutdown_broker(self, broker, resources):
        if broker.cleanup_complete:
            return False
        # Close listeners before teardown so local port probes and reconnects
        # cannot create a new untracked connection while sessions are removed.
        for server in resources["servers"]:
            server.close()
        resources["writers"].update(broker.connection_writers)
        resources["tasks"].update(broker.connection_tasks)
        for writer in resources["writers"]:
            writer.close()
            writer.transport.abort()
        forced = False
        shutdown = asyncio.create_task(broker.shutdown())
        resources["tasks"].add(shutdown)
        try:
            await self._bounded(shutdown, self.broker_shutdown_timeout, "broker shutdown")
        except TimeoutError:
            forced = True
            LOGGER.warning("MQTT broker shutdown timed out; closing captured broker resources")
        finally:
            # A session may already have been popped by amqtt. The snapshot
            # retains its reader and socket so cancellation cannot orphan it.
            resources["tasks"].update(broker.connection_tasks)
            resources["tasks"].update(self._phase_tasks)
            resources["writers"].update(broker.connection_writers)
            for writer in resources["writers"]:
                writer.close()
                writer.transport.abort()
            for handler in resources["handlers"]:
                keepalive = getattr(handler, "_keepalive_task", None)
                if keepalive is not None:
                    keepalive.cancel()
                reader = getattr(handler, "_reader_task", None)
                if reader is not None:
                    resources["tasks"].add(reader)
            resources["tasks"].update(broker.plugins_manager._fired_events)
            for task in resources["tasks"]:
                if not task.done():
                    task.cancel()
            if resources["tasks"]:
                done, pending = await asyncio.wait(resources["tasks"], timeout=self._phase_timeout(self.cleanup_timeout))
                for task in done:
                    if not task.cancelled():
                        task.exception()
                if pending:
                    raise RuntimeError("MQTT broker cleanup left unfinished owned tasks; replacement was not started.")
            for server in resources["servers"]:
                await self._bounded(server.wait_closed(), self.cleanup_timeout, "listener close")
            broker.cleanup_complete = True
        return forced

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
            con.execute("INSERT OR IGNORE INTO transport_metadata(key,value) SELECT 'total_deliveries',CAST(COUNT(*) AS TEXT) FROM deliveries")
            if self.delivery_retention is not None:
                con.execute("DELETE FROM deliveries WHERE id <= (SELECT MAX(id) FROM deliveries) - ?", (self.delivery_retention,))
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

    def _delivery_recorded(self, con):
        con.execute("UPDATE transport_metadata SET value=CAST(value AS INTEGER)+1 WHERE key='total_deliveries'")
        if self.delivery_retention is not None:
            con.execute("DELETE FROM deliveries WHERE id <= (SELECT MAX(id) FROM deliveries) - ?", (self.delivery_retention,))

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
                self._delivery_recorded(con)
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
                self._delivery_recorded(con)
            with self.lock:
                self.state["rejected"] += 1
            return {"status": "rejected", "reason": reason, "received_at": received}

    async def _start_subscriber(self, timeout):
        self.connected.clear()
        self.subscribed.clear()
        self.subscriber = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="osh-verified-subscriber")
        self.subscriber.username_pw_set("twin", self.password)
        self.subscriber.reconnect_delay_set(1, 8)

        def on_connect(client, userdata, flags, reason_code, properties):
            if client is self.subscriber and reason_code == 0:
                self.connected.set()
                self.state["subscriber"] = "connected"
                client.subscribe(TOPIC, qos=1)

        def on_subscribe(client, userdata, mid, reasons, properties):
            if client is self.subscriber and all(not r.is_failure for r in reasons):
                self.subscribed.set()

        def on_disconnect(client, userdata, flags, reason_code, properties):
            if client is not self.subscriber:
                return
            self.connected.clear()
            self.subscribed.clear()
            self.state["subscriber"] = "disconnected"

        self.subscriber.on_connect = on_connect
        self.subscriber.on_subscribe = on_subscribe
        self.subscriber.on_disconnect = on_disconnect
        self.subscriber.on_message = lambda client, userdata, msg: self.ingest(msg.topic, msg.payload)
        self.subscriber.connect_async("127.0.0.1", self.port, 30)
        self.subscriber.loop_start()
        if not await asyncio.to_thread(self.subscribed.wait, self._phase_timeout(timeout)):
            raise RuntimeError("MQTT subscription did not become ready")

    async def start(self):
        self.initialise()
        password_file = self.runtime / ".mqtt_passwords"
        password_file.write_text("twin:" + PasswordHash.recommended().hash(self.password) + "\n", encoding="utf-8")
        self.config = {"listeners": {"default": {"type": "tcp", "bind": f"127.0.0.1:{self.port}", "max_connections": 8}},
                       "plugins": {"amqtt.plugins.authentication.FileAuthPlugin": {"password_file": str(password_file)}}}
        self.broker = Broker(self.config)
        self.state["broker"] = "starting"
        try:
            await self._bounded(self.broker.start(), self.broker_start_timeout, "broker start")
            await self._start_subscriber(30 if self.public_mode else 8)
            self.state["broker"] = "running"
        except BaseException as exc:
            self.state["last_error"] = str(exc)
            await self.close()
            self.state["broker"] = "failed"
            raise

    def begin_replay(self, speed: float = 20, limit: int = 300, channel_id: str | None = None, *, max_duration_seconds: float | None = None) -> dict:
        with self.start_lock:
            return self._begin_replay(speed, limit, channel_id, max_duration_seconds=max_duration_seconds)

    def _begin_replay(self, speed: float, limit: int, channel_id: str | None, *, max_duration_seconds: float | None = None) -> dict:
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
        if max_duration_seconds is not None and (not math.isfinite(max_duration_seconds) or max_duration_seconds <= 0):
            raise ValueError("Maximum replay duration must be positive and finite.")
        # Keep a bounded heap, rather than materialising every source tuple for
        # a small public replay. Ordering is identical to the complete sort.
        records = ((row[0], channel, row) for channel, source in self.source.items()
                   if channel_id is None or channel == channel_id for row in source["rows"])
        selected = heapq.nsmallest(limit, records, key=lambda item: (item[0], item[1], item[2][2]))
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
            deadline = time.monotonic() + max_duration_seconds if max_duration_seconds is not None else None
            def remaining(default):
                if deadline is None:
                    return default
                left = deadline - time.monotonic()
                if left <= 0:
                    raise TimeoutError("Replay reached its configured execution budget.")
                return min(default, left)
            try:
                if self.public_mode:
                    client.connect_timeout = remaining(5)
                client.connect("127.0.0.1", self.port, 30)
                client.loop_start()
                if not self.publish_connected.wait(remaining(30 if self.public_mode else 8)):
                    raise RuntimeError("publisher failed to connect")
                for _, channel, row in selected:
                    if self.stop_event.is_set():
                        break
                    remaining(5)
                    info = client.publish(f"osh/flat/historical/{channel}", json.dumps(self.payload(channel, row)), qos=1, retain=False)
                    info.wait_for_publish(timeout=remaining(5))
                    if not info.is_published():
                        raise RuntimeError("MQTT PUBACK timeout")
                    self.state["published"] += 1
                    if self.stop_event.wait(remaining(1 / speed)):
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
        """Replace the real broker and confirm a fresh authenticated SUBACK.

        Stop the client loop and close every owned TCP stream, including raw
        port probes, before amqtt waits for server closure on Python 3.12.
        """
        if self.worker and self.worker.is_alive():
            raise ValueError("Stop the replay before restarting the broker.")
        if self.broker is None or self.config is None:
            raise ValueError("Broker has not been started.")
        if self.state["broker"] == "restarting":
            raise ValueError("A broker restart is already running.")
        self._operation_deadline = time.monotonic() + self.restart_timeout
        resources = self._broker_resources(self.broker)
        self.state.update(broker="restarting", restart_phase="stop_subscriber", last_error=None)
        # Do not accept another health/port probe while the subscriber stops.
        for server in resources["servers"]:
            server.close()
        try:
            LOGGER.info("MQTT restart phase=stop_subscriber")
            await self._stop_subscriber()
            self.state["restart_phase"] = "shutdown_broker"
            LOGGER.info("MQTT restart phase=shutdown_broker")
            forced = await self._shutdown_broker(self.broker, resources)
            self.broker = Broker(self.config)
            self.state["restart_phase"] = "start_broker"
            LOGGER.info("MQTT restart phase=start_broker")
            await self._bounded(self.broker.start(), self.broker_start_timeout, "broker start")
            self.state["restart_phase"] = "subscribe"
            LOGGER.info("MQTT restart phase=subscribe")
            await self._start_subscriber(15 if self.public_mode else 8)
            self.state.update(broker="running", restart_phase="completed", broker_shutdown_forced=forced,
                              broker_restarts=self.state.get("broker_restarts", 0) + 1)
            LOGGER.info("MQTT restart completed; fresh subscriber acknowledged; forced_cleanup=%s", forced)
            return self.status()
        except BaseException as exc:
            failed_phase = self.state.get("restart_phase")
            self.state.update(broker="failed", restart_phase="failed",
                              last_error=f"Broker restart failed during {failed_phase}: {exc}")
            LOGGER.exception("MQTT restart failed during %s", failed_phase)
            # Reserve a small bounded cleanup window even after the run budget.
            self._operation_deadline = time.monotonic() + 5
            try:
                latest = self._broker_resources(self.broker)
                for key in ("tasks", "writers"):
                    resources[key].update(latest[key])
                for key in ("handlers", "servers"):
                    resources[key].extend(latest[key])
                await self._stop_subscriber()
                await self._shutdown_broker(self.broker, resources)
            except BaseException as cleanup_error:
                LOGGER.error("MQTT failure cleanup: %s", cleanup_error)
            raise
        finally:
            self._operation_deadline = None

    def status(self) -> dict:
        with self.db() as con:
            total = con.execute("SELECT COUNT(*) FROM accepted").fetchone()[0]
            delivery_count = con.execute("SELECT COUNT(*) FROM deliveries").fetchone()[0]
            total_row = con.execute("SELECT value FROM transport_metadata WHERE key='total_deliveries'").fetchone()
            total_deliveries = int(total_row[0]) if total_row is not None else delivery_count
        return {**self.state, "schema_version": 2, "host": "127.0.0.1", "port": self.port, "protocol": "MQTT 3.1.1", "qos": 1,
                "authentication": "per-process random password, Argon2 stored hash", "tls": False,
                "scope": "loopback demonstration", "persistent_unique_events": total, "persistent_delivery_count": delivery_count,
                "total_delivery_count": total_deliveries,
                "delivery_history": {"retention_enabled": self.delivery_retention is not None, "max_rows": self.delivery_retention,
                                     "retained_rows": delivery_count, "pruned_rows": max(0, total_deliveries-delivery_count),
                                     "scope": "Newest retained delivery rows; accepted-event identities remain deduplicated until runtime reset." if self.delivery_retention else "All delivery rows retained in this runtime database."},
                "counter_scope": {"published": "current replay run", "accepted": "current process", "duplicates": "current process", "rejected": "current process", "persistent_unique_events": "all runs retained in this runtime database", "persistent_delivery_count": "currently retained delivery rows", "total_delivery_count": "all deliveries counted since this runtime store was initialised"},
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
        resources = self._broker_resources(self.broker) if self.broker else None
        try:
            await self._stop_subscriber()
        finally:
            if self.broker:
                await self._shutdown_broker(self.broker, resources)
            self.state["broker"] = "stopped"
            self.state["subscriber"] = "disconnected"
