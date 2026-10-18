"""Local test readings exercise software only, never a hosted physical claim."""
from __future__ import annotations

import asyncio
import csv
import hashlib
from io import StringIO
import json
import sqlite3
import uuid

from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest

from twin import main, settings
from twin.live_sensor import INGEST_PATH, LiveSensor, SensorIngressLimits, parse_reading, utc_timestamp
from twin.public_demo import PublicDemo

TOKEN = 'unit-test-only-' + 'x' * 40
DEVICE = 'test-environmental-station'
ORIGIN = 'https://building.example'


class Clock:
    wall = 1792314000.0
    now = 100.0

    def advance(self, seconds):
        self.wall += seconds
        self.now += seconds


@pytest.fixture
def station(tmp_path, monkeypatch):
    clock = Clock()
    service = LiveSensor(path=tmp_path / 'live.sqlite3', token=TOKEN, device_id=DEVICE,
                         clock=lambda: clock.now, wall_clock=lambda: clock.wall)
    monkeypatch.setattr(main, 'live_sensor', service)
    monkeypatch.setattr(main, 'public_demo', PublicDemo())
    client = TestClient(main.app, base_url=ORIGIN)
    yield client, service, clock
    client.close()


def payload(clock, **changes):
    return {'schema_version': 1, 'device_id': DEVICE,
            'boot_id': '9efbe8d3-0bbf-4367-8c16-50ead0c636b6', 'sequence': 0,
            'observed_at': utc_timestamp(clock.wall), 'temperature_c': 21.5,
            'relative_humidity_pct': 45.5, 'interval_seconds': 60, **changes}


def post(client, body, **kwargs):
    return client.post(INGEST_PATH, json=body, headers={'Authorization': 'Bearer ' + TOKEN}, **kwargs)


def test_disabled_has_no_readings_or_database_and_denies_before_body_read(tmp_path, monkeypatch):
    service = LiveSensor(path=tmp_path / 'not-created.sqlite3')
    monkeypatch.setattr(main, 'live_sensor', service)
    # No lifespan: the new endpoint is isolated from source/MQTT startup.
    client = TestClient(main.app, base_url=ORIGIN)
    try:
        response = client.get('/api/live-sensor/status')
        assert response.json()['state'] == 'disabled'
        assert response.json()['latest'] is None
        assert response.json()['freshness'] == 'awaiting_reading'
        assert client.get(INGEST_PATH).json()['readings'] == []
        rejected = client.post(INGEST_PATH, content=b'{' * 100000)
        assert rejected.status_code == 503
        assert rejected.headers['retry-after'] == '60'
        assert not service.path.exists()
    finally:
        client.close()


@pytest.mark.parametrize('token,device', [('short', DEVICE), (TOKEN, ''), ('', DEVICE), (TOKEN, 'invalid id')])
def test_partial_or_weak_configuration_fails_closed_without_echoing_secrets(tmp_path, token, device):
    with pytest.raises(ValueError) as error:
        LiveSensor(path=tmp_path / 'live.sqlite3', token=token, device_id=device)
    assert TOKEN not in str(error.value)


def test_store_cannot_replace_pinned_database_or_source(tmp_path):
    for path in (settings.DB_PATH, settings.SOURCE_DIR / 'new.sqlite3'):
        with pytest.raises(ValueError, match='separate'):
            LiveSensor(path=path, token=TOKEN, device_id=DEVICE)


@pytest.mark.parametrize('auth', [None, 'Bearer wrong', 'Basic ' + TOKEN, 'Bearer ' + TOKEN + 'x'])
def test_authentication_is_required_before_parsing(station, auth):
    client, service, clock = station
    headers = {} if auth is None else {'Authorization': auth}
    result = client.post(INGEST_PATH, content=b'invalid-json', headers=headers)
    assert result.status_code == 401
    assert TOKEN not in result.text and DEVICE not in result.text
    assert not service.path.exists()


def test_valid_receipt_stable_duplicate_and_no_public_identifiers(station):
    client, service, clock = station
    reading = payload(clock)
    accepted = post(client, reading)
    assert accepted.status_code == 201
    receipt = accepted.json()['receipt']
    assert accepted.json()['state'] == 'accepted'
    uuid.UUID(receipt['receipt_id'])
    assert receipt['observed_at'] == reading['observed_at']
    assert receipt['received_at'] == utc_timestamp(clock.wall)
    clock.advance(1801)
    duplicate = post(client, reading)
    assert duplicate.status_code == 200
    assert duplicate.json() == {'state': 'duplicate', 'receipt': receipt}
    assert client.get('/api/live-sensor/status').json()['count'] == 1
    documents = client.get('/api/live-sensor/status').text + client.get(INGEST_PATH).text + duplicate.text
    for private in (TOKEN, DEVICE, reading['boot_id'], str(service.path), 'event_key', 'payload_sha256'):
        assert private not in documents
    assert client.get('/api/live-sensor/status').headers['cache-control'] == 'no-store'
    assert client.get(INGEST_PATH).headers['cache-control'] == 'no-store'


def test_same_identity_different_content_conflicts_without_replacement(station):
    client, service, clock = station
    reading = payload(clock)
    assert post(client, reading).status_code == 201
    conflict = post(client, {**reading, 'temperature_c': 22})
    assert conflict.status_code == 409
    assert client.get('/api/live-sensor/status').json()['latest']['temperature_c'] == 21.5
    assert client.get('/api/live-sensor/status').json()['count'] == 1


@pytest.mark.parametrize('changes', [
    {'temperature_c': '22'}, {'temperature_c': True}, {'temperature_c': -40.01}, {'temperature_c': 85.01},
    {'relative_humidity_pct': False}, {'relative_humidity_pct': -0.1}, {'relative_humidity_pct': 100.1},
    {'sequence': True}, {'sequence': '0'}, {'sequence': -1}, {'sequence': 2147483648},
    {'boot_id': 'missing-uuid'}, {'observed_at': '2026-10-18T09:00:00'},
    {'observed_at': 1792314000}, {'observed_at': '2026-10-18'},
    {'schema_version': 2}, {'schema_version': True}, {'interval_seconds': 1},
    {'device_id': 'not a device'}, {'private_location': 'test-only-value'},
])
def test_strict_payload_validation(station, changes):
    client, service, clock = station
    result = post(client, payload(clock, **changes))
    assert result.status_code == 422
    assert DEVICE not in result.text
    assert service.status()['count'] == 0


@pytest.mark.parametrize('raw', [b'{"temperature_c":NaN}', b'{"temperature_c":Infinity}',
                                 b'{"device_id":"a","device_id":"b"}', b'[]', b'null',
                                 b'\xff', b'[' * 1001 + b']' * 1001])
def test_malformed_duplicate_key_nonfinite_and_nested_json_rejected(station, raw):
    client, service, clock = station
    result = client.post(INGEST_PATH, content=raw,
                         headers={'Authorization': 'Bearer ' + TOKEN, 'Content-Type': 'application/json'})
    assert result.status_code == 422


def test_device_allowlist_is_separate_from_valid_sender_credential(station):
    client, service, clock = station
    result = post(client, payload(clock, device_id='unknown-station'))
    assert result.status_code == 403
    assert service.status()['count'] == 0


@pytest.mark.parametrize('offset,status_code', [(-901, 422), (-900, 201), (120, 201), (121, 422)])
def test_timestamp_acceptance_window(station, offset, status_code):
    client, service, clock = station
    assert post(client, payload(clock, observed_at=utc_timestamp(clock.wall + offset))).status_code == status_code


def test_freshness_uses_observation_and_latest_observation_survives_delayed_report(station):
    client, service, clock = station
    awaiting = client.get('/api/live-sensor/status').json()
    assert awaiting['enabled'] is True and awaiting['state'] == 'awaiting_reading'
    assert awaiting['count'] == 0 and awaiting['latest'] is None
    assert post(client, payload(clock)).status_code == 201
    clock.advance(10)
    assert post(client, payload(clock, sequence=1, observed_at=utc_timestamp(clock.wall - 500))).status_code == 201
    result = client.get('/api/live-sensor/status').json()
    assert result['age_seconds'] == 10 and result['freshness'] == 'fresh'
    assert result['server_time'] == utc_timestamp(clock.wall)
    assert result['provenance']['physical_origin_independently_verified'] is False
    assert result['provenance']['historical_source_dataset'] is False
    assert result['model_assignment'] == 'unassigned'
    clock.advance(171)
    assert client.get('/api/live-sensor/status').json()['freshness'] == 'stale'
    rows = client.get(INGEST_PATH).json()['readings']
    assert [r['observed_at'] for r in rows] == sorted(r['observed_at'] for r in rows)


def test_cooldown_retry_is_not_inserted_until_success_and_duplicate_bypasses_new_sample_cooldown(station):
    client, service, clock = station
    reading = payload(clock)
    assert post(client, reading).status_code == 201
    assert post(client, reading).status_code == 200
    next_reading = payload(clock, sequence=1)
    limited = post(client, next_reading)
    assert limited.status_code == 429 and limited.headers['retry-after'] == '5'
    assert service.status()['count'] == 1
    clock.advance(5)
    assert post(client, next_reading).status_code == 201
    assert service.status()['count'] == 2


def test_transient_storage_failure_can_retry_same_identity(station, monkeypatch):
    client, service, clock = station
    reading = payload(clock)
    original = service._connect
    def unavailable():
        raise sqlite3.OperationalError('private test database path')
    monkeypatch.setattr(service, '_connect', unavailable)
    failed = post(client, reading)
    assert failed.status_code == 503 and failed.headers['retry-after'] == '30'
    assert 'private test database path' not in failed.text
    monkeypatch.setattr(service, '_connect', original)
    assert post(client, reading).status_code == 201
    assert post(client, reading).status_code == 200
    assert service.status()['count'] == 1


def test_row_and_age_retention_and_source_ifc_untouched(station):
    client, service, clock = station
    source = settings.SOURCE_IFC
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    service.max_rows = 3
    for sequence in range(5):
        assert post(client, payload(clock, sequence=sequence)).status_code == 201
        clock.advance(61)
    rows = service.readings(10)
    assert rows['count'] == 3 and len(rows['readings']) == 3
    clock.advance(7 * 86400 + 1)
    assert service.status()['count'] == 0
    assert service.status()['state'] == 'awaiting_reading'
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before


def test_device_budget_does_not_depend_on_browser_budget(station, monkeypatch):
    client, service, clock = station
    public = PublicDemo(enabled=True, origin=ORIGIN, clock=lambda: clock.now)
    monkeypatch.setattr(main, 'public_demo', public)
    public.http_requests.extend([clock.now] * 240)
    assert client.get('/api/live-sensor/status').status_code == 429
    assert post(client, payload(clock)).status_code == 201
    # The exact POST still passes through its own auth and body/rate bounds.
    assert client.post(INGEST_PATH, json=payload(clock)).status_code == 401
    service.max_authenticated_per_minute = 1
    assert post(client, payload(clock)).status_code == 429


def test_authenticated_and_unauthenticated_budgets_are_bounded(station):
    client, service, clock = station
    service.max_requests_per_minute = 2
    assert client.post(INGEST_PATH, content=b'').status_code == 401
    assert client.post(INGEST_PATH, content=b'').status_code == 401
    limited = post(client, payload(clock))
    assert limited.status_code == 429 and limited.headers['retry-after'] == '60'
    clock.advance(61)
    assert post(client, payload(clock)).status_code == 201


@pytest.mark.parametrize('mode', ['length', 'chunked', 'content-type', 'compressed', 'insecure', 'slow', 'huge-length', 'duplicate-auth'])
def test_preparse_bounds_also_apply_in_local_mode(station, mode):
    _, service, _ = station
    consumed, reached, sent = [], [], []
    headers = [(b'authorization', ('Bearer ' + TOKEN).encode()), (b'content-type', b'application/json'), (b'host', b'building.example')]
    scope = {'type': 'http', 'method': 'POST', 'path': INGEST_PATH, 'scheme': 'https', 'headers': headers, 'client': ('203.0.113.1', 1)}
    messages = [{'type': 'http.request', 'body': b'{' * 1500, 'more_body': True},
                {'type': 'http.request', 'body': b'}' * 1000, 'more_body': False}]
    expected = 413
    if mode == 'length':
        headers.append((b'content-length', b'2049'))
    elif mode == 'huge-length':
        headers.append((b'content-length', b'9' * 5000))
    elif mode == 'content-type':
        headers[1] = (b'content-type', b'text/plain')
        expected = 415
    elif mode == 'compressed':
        headers.append((b'content-encoding', b'gzip'))
        expected = 415
    elif mode == 'insecure':
        scope['scheme'] = 'http'
        headers[-1] = (b'host', b'localhost')
        expected = 400  # Spoofing Host alone does not permit insecure transport.
    elif mode == 'slow':
        service.body_timeout_seconds = 0.01
        expected = 408
    elif mode == 'duplicate-auth':
        headers.append(headers[0])
        expected = 401
    async def receive():
        if mode == 'slow':
            await asyncio.sleep(1)
        consumed.append(True)
        return messages.pop(0)
    async def send(message):
        sent.append(message)
    async def endpoint(scope, receive, send):
        reached.append(True)
    asyncio.run(SensorIngressLimits(endpoint, lambda: service)(scope, receive, send))
    assert sent[0]['status'] == expected
    assert not reached
    if mode not in ('chunked',):
        assert not consumed


def test_get_limit_is_bounded(station):
    client, _, _ = station
    assert client.get(INGEST_PATH + '?limit=0').status_code == 422
    assert client.get(INGEST_PATH + '?limit=361').status_code == 422
    assert client.get(INGEST_PATH + '?limit=60').status_code == 200


def test_csv_export_contains_only_safe_retained_rows(station):
    client, service, clock = station
    empty = client.get('/api/live-sensor/export.csv')
    assert list(csv.DictReader(StringIO(empty.text))) == []
    assert post(client, payload(clock)).status_code == 201
    exported = client.get('/api/live-sensor/export.csv')
    assert exported.status_code == 200 and exported.headers['cache-control'] == 'no-store'
    assert 'attachment;' in exported.headers['content-disposition']
    rows = list(csv.DictReader(StringIO(exported.text)))
    assert len(rows) == 1 and rows[0]['temperature_c'] == '21.5'
    assert rows[0]['model_assignment'] == 'unassigned'
    assert rows[0]['source'] == 'authenticated_device_report'
    assert not any(value in exported.text for value in (TOKEN, DEVICE, payload(clock)['boot_id']))
