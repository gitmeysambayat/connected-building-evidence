"""Regressions identified by independent review of the real-source workflow."""

from __future__ import annotations

import json
import sqlite3

import pytest

from twin import data, settings, transport
from twin.transport import HistoricalTransport


@pytest.fixture
def catalogue(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DB_PATH", tmp_path / "source.sqlite3")
    data.initialise()
    receiver = HistoricalTransport(runtime=tmp_path / "receipts")
    receiver.initialise()
    return receiver


def test_identical_weather_files_keep_distinct_channel_provenance(catalogue):
    first_id = "Kitchen_Virtual_OutdoorTemperature"
    second_id = "Room1_Virtual_OutdoorTemperature"
    assert catalogue.source[first_id]["sha256"] == catalogue.source[second_id]["sha256"]
    first = catalogue.payload(first_id, catalogue.source[first_id]["rows"][0])
    second = catalogue.payload(second_id, catalogue.source[second_id]["rows"][0])
    assert first["observed_at"] == second["observed_at"]
    assert first["value"] == second["value"]
    assert first["room_guid"] != second["room_guid"]
    assert first["event_id"] != second["event_id"]
    first_topic = "osh/flat/historical/" + first_id
    second_topic = "osh/flat/historical/" + second_id
    assert catalogue.ingest(first_topic, json.dumps(first).encode())["status"] == "accepted"
    assert catalogue.ingest(second_topic, json.dumps(second).encode())["status"] == "accepted"
    assert catalogue.status()["persistent_unique_events"] == 2
    receipts = catalogue.events()["events"]
    assert {row["channel_id"] for row in receipts} == {first_id, second_id}
    for receipt in receipts:
        source = catalogue.source[receipt["channel_id"]]
        assert receipt["unit"] == source["unit"] == "°C"
        assert receipt["source_sha256"] == source["sha256"]
        assert receipt["room_guid"] == source["room_guid"]
        assert receipt["origin"] == "published_historical_replay"
    # Only a repeated delivery of the same logical channel event is duplicate.
    assert catalogue.ingest(second_topic, json.dumps(second).encode())["status"] == "duplicate"
    assert catalogue.status()["persistent_unique_events"] == 2


def test_transport_methods_close_every_sqlite_connection(catalogue, monkeypatch):
    opened = []
    original_connect = sqlite3.connect

    def tracked_connect(*args, **kwargs):
        connection = original_connect(*args, **kwargs)
        opened.append(connection)
        return connection

    monkeypatch.setattr(transport.sqlite3, "connect", tracked_connect)
    try:
        catalogue.initialise()
        channel_id = "Room1_Temperature"
        event = catalogue.payload(channel_id, catalogue.source[channel_id]["rows"][0])
        assert catalogue.ingest("osh/flat/historical/" + channel_id, json.dumps(event).encode())["status"] == "accepted"
        catalogue.status()
        catalogue.events()
        assert opened
        for connection in opened:
            with pytest.raises(sqlite3.ProgrammingError, match="closed"):
                connection.execute("SELECT 1")
    finally:
        # This also keeps a failing regression test from leaking Windows file
        # handles into the following tests or temporary-directory cleanup.
        for connection in opened:
            connection.close()


def test_nonempty_receipts_without_current_identity_version_are_not_silently_reused(catalogue):
    channel_id = "Room1_Temperature"
    event = catalogue.payload(channel_id, catalogue.source[channel_id]["rows"][0])
    assert catalogue.ingest("osh/flat/historical/" + channel_id, json.dumps(event).encode())["status"] == "accepted"
    with catalogue.db() as connection:
        connection.execute("DELETE FROM transport_metadata WHERE key='schema_version'")
    with pytest.raises(ValueError, match="older development transport identity"):
        catalogue.initialise()
    with catalogue.db() as connection:
        assert connection.execute("SELECT COUNT(*) FROM accepted").fetchone()[0] == 1
