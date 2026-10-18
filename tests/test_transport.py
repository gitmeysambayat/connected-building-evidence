"""The integration test uses real MQTT sockets, authentication and PUBACKs."""
import asyncio
import json
import socket
import threading
from pathlib import Path

import pytest
from paho.mqtt import client as mqtt

from twin import data
from twin.transport import HistoricalTransport


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def catalogue(tmp_path):
    data.initialise()
    t = HistoricalTransport(port=free_port(), runtime=tmp_path)
    t.initialise()
    return t


def test_source_identity_and_idempotency(catalogue):
    t = catalogue
    channel = "Room1_Temperature"
    event = t.payload(channel, t.source[channel]["rows"][0])
    topic = "osh/flat/historical/" + channel
    assert t.ingest(topic, json.dumps(event).encode())["status"] == "accepted"
    assert t.ingest(topic, json.dumps(event).encode())["status"] == "duplicate"
    assert t.status()["persistent_unique_events"] == 1
    row = t.events(1)["events"][0]
    assert row["observed_at"] < 1500000000  # Actual historical source time.
    assert row["received_at"].startswith("2026-") or row["received_at"][:4] > "2026"
    assert row["model_sha256"] == data.summary()["model_sha256"]
    assert "Toilet_ThermostatTemperature" not in t.source


@pytest.mark.parametrize("change", [
    {"value": 999}, {"value": float("nan")}, {"observed_at": True}, {"source_line": True},
    {"source_line": 9999999}, {"model_sha256": "another-building"}, {"room_guid": "another-space"},
    {"unit": "K"}, {"origin": "physical_current_sensor"}, {"source_sha256": "changed"},
    {"channel_id": "Toilet_ThermostatTemperature"}, {"event_id": "invented"},
])
def test_source_mismatch_rejected(catalogue, change):
    channel = "Room1_Temperature"
    event = catalogue.payload(channel, catalogue.source[channel]["rows"][0])
    event.update(change)
    assert catalogue.ingest("osh/flat/historical/" + channel, json.dumps(event).encode())["status"] == "rejected"
    assert catalogue.status()["persistent_unique_events"] == 0


@pytest.mark.parametrize("raw", [b"\xff\xfe", b"not-json", b"[]", b"null", b"{}", b"a" * 4097])
def test_malformed_is_quarantined(catalogue, raw):
    assert catalogue.ingest("osh/flat/historical/Room1_Temperature", raw)["status"] == "rejected"
    assert catalogue.events(1)["deliveries"][0]["status"] == "rejected"


def test_real_mqtt_authentication_delivery_duplicate_and_restart(tmp_path):
    async def scenario():
        data.initialise()
        t = HistoricalTransport(port=free_port(), runtime=tmp_path)
        await t.start()
        try:
            assert t.subscribed.is_set()
            failed_connect = threading.Event()
            outcome = []
            wrong = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="invalid-credentials")
            wrong.username_pw_set("twin", "wrong-password")
            def connected(client, userdata, flags, reason, properties):
                outcome.append(reason.is_failure)
                failed_connect.set()
            wrong.on_connect = connected
            wrong.connect_async("127.0.0.1", t.port)
            wrong.loop_start()
            try:
                assert await asyncio.to_thread(failed_connect.wait, 5)
                assert outcome == [True]
            finally:
                wrong.disconnect()
                await asyncio.to_thread(wrong.loop_stop)

            async def wait_for(predicate, timeout=10):
                for _ in range(int(timeout * 20)):
                    if predicate():
                        return
                    await asyncio.sleep(.05)
                raise AssertionError(t.status())

            t.begin_replay(100, 4, "Room1_Temperature")
            await wait_for(lambda: t.state["accepted"] == 4 and t.state["replay"] == "completed")
            assert t.state["published"] == 4
            # A second run is at-least-once transport with an idempotent sink.
            t.begin_replay(100, 4, "Room1_Temperature")
            await wait_for(lambda: t.state["duplicates"] == 4 and t.state["replay"] == "completed")
            assert t.status()["persistent_unique_events"] == 4
            await t.restart_broker()
            assert t.state["subscriber"] == "connected"
            t.begin_replay(100, 5, "Room1_Temperature")
            await wait_for(lambda: t.state["accepted"] == 5 and t.state["replay"] == "completed")
            assert t.state["broker_restarts"] == 1
            assert t.status()["persistent_unique_events"] == 5
        finally:
            await t.close()
    asyncio.run(scenario())
