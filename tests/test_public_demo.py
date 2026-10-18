"""Public control boundaries use actual HTTP cookies, source rows and MQTT.

The HTTP lease tests mock only the broker's completion timing. Retention tests
validate genuine pinned source payloads; the deadline test uses real TCP MQTT.
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import socket
import time
from pathlib import Path

from fastapi.testclient import TestClient
import pytest
from starlette.websockets import WebSocketDisconnect

from twin import data, main, settings
from twin.public_demo import COOKIE, PublicDemo
from twin.transport import HistoricalTransport

ORIGIN = 'https://building.example'


class Clock:
    def __init__(self):
        self.now = 100.0
        self.wall = 1700000000.0
    def advance(self, seconds):
        self.now += seconds
        self.wall += seconds


class ControlledTransport:
    def __init__(self):
        self.state = {'broker': 'running', 'replay': 'idle'}
        self.worker = None
        self.starts = []
        self.stops = 0
        self.restarts = 0
    def status(self):
        return {**self.state, 'persistent_unique_events': 0, 'persistent_delivery_count': 0}
    def begin_replay(self, speed, limit, channel_id, **kwargs):
        self.starts.append((speed, limit, channel_id, kwargs))
        self.state['replay'] = 'running'
        return self.status()
    def stop(self):
        self.stops += 1
        self.state['replay'] = 'stopped'
        return self.status()
    async def restart_broker(self):
        self.restarts += 1
        return self.status()


@pytest.fixture
def sandbox(monkeypatch):
    clock, transport = Clock(), ControlledTransport()
    policy = PublicDemo(enabled=True, origin=ORIGIN, environment='render', persistence='ephemeral',
                        secret=b'unit-test-signing-secret', clock=lambda: clock.now, wall_clock=lambda: clock.wall)
    monkeypatch.setattr(main, 'public_demo', policy)
    monkeypatch.setattr(main, 'transport', transport)
    # No lifespan is started here: source/startup integrity has its own tests.
    client = TestClient(main.app, base_url=ORIGIN)
    yield client, policy, transport, clock
    client.close()


def initialise(client):
    result = client.get('/api/session')
    assert result.status_code == 200
    assert result.json()['session_ready'] is True
    return result


def post(client, path='start', body=None, origin=ORIGIN):
    headers = {} if origin is None else {'Origin': origin}
    return client.post('/api/replay/' + path, json=body if body is not None else {'speed': 20, 'limit': 300}, headers=headers)


def test_session_cookie_is_signed_http_only_and_metadata_has_no_token_or_owner_identifier(sandbox):
    client, policy, transport, clock = sandbox
    result = initialise(client)
    cookie = result.headers['set-cookie'].lower()
    assert 'httponly' in cookie and 'secure' in cookie and 'samesite=lax' in cookie
    assert result.headers['cache-control'] == 'no-store'
    body = result.json()
    assert body['public_demo'] is True
    assert body['capabilities']['max_events'] == 300
    assert body['capabilities']['max_duration_seconds'] == 30
    assert not any(word in json.dumps(body).lower() for word in ('secret', 'token', 'signature', 'session_id', 'owner_id'))
    assert not client.get('/api/session').headers.get('set-cookie')
    deployment = policy.deployment()
    assert deployment['profile'] == 'public_demo' and deployment['environment'] == 'render'
    assert deployment['public_url'] == ORIGIN and deployment['state_persistence'] == 'ephemeral'
    assert 'cloud_deployment_verified' not in deployment


@pytest.mark.parametrize('origin', [None, 'null', 'https://attacker.example', 'http://building.example'])
def test_public_mutations_require_matching_origin(sandbox, origin):
    client, _, transport, _ = sandbox
    initialise(client)
    assert post(client, origin=origin).status_code == 403
    assert not transport.starts


def test_missing_forged_and_expired_cookies_cannot_control_replay(sandbox):
    client, _, transport, clock = sandbox
    assert post(client).status_code == 403
    initialise(client)
    actual = client.cookies.get(COOKIE)
    client.cookies.clear()
    client.cookies.set(COOKIE, actual[:-1] + ('0' if actual[-1] != '0' else '1'))
    assert post(client).status_code == 403
    client.cookies.clear()
    initialise(client)
    clock.advance(3601)
    assert post(client).status_code == 403
    assert not transport.starts


@pytest.mark.parametrize('body', [
    {'speed': 20, 'limit': 301}, {'speed': 1, 'limit': 31}, {'speed': 101, 'limit': 20},
    {'speed': 20, 'limit': True}, {'speed': '20', 'limit': 20}, {'speed': 20, 'limit': '20'},
    {'speed': 20, 'limit': 20, 'invented': True},
])
def test_public_replay_rejects_excessive_duration_counts_and_coerced_input(sandbox, body):
    client, _, transport, _ = sandbox
    initialise(client)
    assert post(client, body=body).status_code == 422
    assert not transport.starts


def test_valid_default_replay_acquires_lease_and_passes_execution_budget(sandbox):
    client, _, transport, _ = sandbox
    initialise(client)
    result = post(client)
    assert result.status_code == 200
    metadata = result.json()['public_demo']
    assert metadata['lease']['owned_by_you'] is True
    assert metadata['lease']['replay_running'] is True
    assert metadata['controls']['can_stop'] is True
    assert transport.starts == [(20.0, 300, None, {'max_duration_seconds': 30})]
    status = client.get('/api/replay/status').json()['public_demo']
    assert status['lease']['owned_by_you'] is True
    assert post(client, 'stop', {}).status_code == 200
    assert transport.stops == 1


def test_another_browser_cannot_stop_restart_or_replace_the_lease(sandbox):
    owner, _, transport, _ = sandbox
    initialise(owner)
    other = TestClient(main.app, base_url=ORIGIN)
    initialise(other)
    assert post(owner).status_code == 200
    for path in ('start', 'stop', 'restart-broker'):
        assert post(other, path).status_code == 403
    assert len(transport.starts) == 1 and transport.stops == 0 and transport.restarts == 0
    metadata = other.get('/api/replay/status').json()['public_demo']
    assert metadata['lease']['active'] is True and metadata['lease']['owned_by_you'] is False
    assert not any(metadata['controls'].values())
    other.close()


def test_expired_idle_lease_can_be_acquired_but_active_replay_cannot_be_stolen(sandbox):
    owner, _, transport, clock = sandbox
    initialise(owner)
    other = TestClient(main.app, base_url=ORIGIN)
    initialise(other)
    assert post(owner).status_code == 200
    clock.advance(46)
    assert post(other).status_code == 403
    assert len(transport.starts) == 1
    transport.state['replay'] = 'completed'
    assert post(other).status_code == 200
    assert post(owner, 'stop', {}).status_code == 403
    assert other.get('/api/replay/status').json()['public_demo']['lease']['owned_by_you'] is True
    other.close()


def test_start_and_broker_restart_cooldowns_bound_repeated_public_controls(sandbox):
    client, _, transport, clock = sandbox
    initialise(client)
    assert post(client).status_code == 200
    transport.state['replay'] = 'completed'
    blocked = post(client)
    assert blocked.status_code == 429 and int(blocked.headers['retry-after']) > 0
    clock.advance(20)
    assert post(client).status_code == 200
    assert post(client, 'restart-broker', {}).status_code == 409  # running
    transport.state['replay'] = 'completed'
    assert post(client, 'restart-broker', {}).status_code == 200
    assert post(client, 'restart-broker', {}).status_code == 429
    clock.advance(61)
    assert post(client, 'restart-broker', {}).status_code == 200
    assert transport.restarts == 2


def test_simultaneous_start_requests_only_start_one_shared_replay(sandbox):
    client, _, transport, _ = sandbox
    other = TestClient(main.app, base_url=ORIGIN)
    initialise(client); initialise(other)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda visitor: post(visitor).status_code, (client, other)))
    assert sorted(results) == [200, 403]
    assert len(transport.starts) == 1
    other.close()


def test_public_request_body_read_budget_and_websocket_bounds(sandbox):
    client, _, _, clock = sandbox
    oversized = client.post('/graphql', content=b'x' * 65537, headers={'Content-Type': 'application/json'})
    assert oversized.status_code == 413
    assert client.post('/graphql', json={'query': '{ __typename }'}).status_code == 200
    with pytest.raises(WebSocketDisconnect) as closed:
        with client.websocket_connect('/graphql', subprotocols=['graphql-transport-ws']):
            pass
    assert closed.value.code == 1008
    responses = [client.get('/api/session') for _ in range(122)]
    assert any(result.status_code == 429 for result in responses)
    clock.advance(61)
    assert client.get('/api/session').status_code == 200


def test_public_graphql_rejects_cycles_and_fragment_expansion_before_depth_validation(sandbox):
    client, _, _, _ = sandbox
    cycle = 'query { ...A } fragment A on QueryRoot { ...B } fragment B on QueryRoot { ...A }'
    result = client.post('/graphql', json={'query': cycle}).json()
    assert 'cyclic' in ' '.join(error['message'] for error in result['errors']).lower()
    expansion = 'query { ...F0 } ' + ' '.join(f'fragment F{i} on QueryRoot {{ ...F{i+1} ...F{i+1} }}' for i in range(13)) + ' fragment F13 on QueryRoot { __typename }'
    result = client.post('/graphql', json={'query': expansion}).json()
    assert 'expanded-selection budget' in ' '.join(error['message'] for error in result['errors'])


def source_transport(tmp_path, monkeypatch, retention=None, public_mode=False):
    audit = data.source_audit()
    room = next(item for item in audit['room_mapping'] if item['room'] == 'Room1')
    monkeypatch.setattr(data, 'channels', lambda **kwargs: [{'id': 'Room1_Temperature', 'room_guid': room['ifc_guid'], 'unit': '°C'}])
    transport = HistoricalTransport(runtime=tmp_path, delivery_retention=retention, public_mode=public_mode)
    transport.initialise()
    return transport


def test_public_delivery_retention_preserves_finite_event_deduplication_and_lifetime_count(tmp_path, monkeypatch):
    transport = source_transport(tmp_path, monkeypatch, retention=3)
    payload = transport.payload('Room1_Temperature', transport.source['Room1_Temperature']['rows'][0])
    topic = 'osh/flat/historical/Room1_Temperature'
    assert transport.ingest(topic, json.dumps(payload).encode())['status'] == 'accepted'
    for _ in range(7):
        assert transport.ingest(topic, json.dumps(payload).encode())['status'] == 'duplicate'
    assert transport.ingest(topic, b'{}')['status'] == 'rejected'
    status = transport.status()
    assert status['persistent_unique_events'] == 1
    assert status['persistent_delivery_count'] == 3
    assert status['total_delivery_count'] == 9
    assert status['delivery_history']['pruned_rows'] == 6
    assert len(transport.events()['deliveries']) == 3
    # The first delivery has been pruned, while the accepted event still deduplicates.
    assert transport.ingest(topic, json.dumps(payload).encode())['status'] == 'duplicate'
    assert transport.status()['total_delivery_count'] == 10
    assert transport.status()['persistent_delivery_count'] == 3


def test_default_local_profile_keeps_unauthenticated_local_controls_and_full_history(sandbox, tmp_path, monkeypatch):
    client, policy, fake, _ = sandbox
    policy.enabled = False
    assert client.get('/api/session').json()['public_demo'] is False
    assert post(client, body={'speed': 100, 'limit': 2000}, origin=None).status_code == 200
    assert fake.starts[-1] == (100.0, 2000, None, {})
    assert policy.deployment()['cloud_deployment_verified'] is False
    transport = source_transport(tmp_path, monkeypatch)
    payload = transport.payload('Room1_Temperature', transport.source['Room1_Temperature']['rows'][0])
    for _ in range(8):
        transport.ingest('osh/flat/historical/Room1_Temperature', json.dumps(payload).encode())
    assert transport.status()['persistent_delivery_count'] == 8
    assert transport.status()['delivery_history']['retention_enabled'] is False


def test_real_mqtt_public_execution_budget_stops_a_long_replay(tmp_path, monkeypatch):
    transport = source_transport(tmp_path, monkeypatch, retention=3, public_mode=True)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        transport.port = sock.getsockname()[1]
    async def exercise():
        await transport.start()
        try:
            began = time.monotonic()
            transport.begin_replay(1, 20, 'Room1_Temperature', max_duration_seconds=1)
            await asyncio.to_thread(transport.worker.join, 5)
            assert not transport.worker.is_alive()
            assert time.monotonic() - began < 5
            assert transport.state['published'] < 20
            assert transport.state['replay'] == 'failed'
            assert 'execution budget' in transport.state['last_error']
        finally:
            await transport.close()
    asyncio.run(exercise())


def test_signed_sessions_behind_one_proxy_have_separate_browser_read_budgets(sandbox):
    first, _, _, _ = sandbox
    second = TestClient(main.app, base_url=ORIGIN)
    initialise(first); initialise(second)
    # Both TestClients use the same socket client address. Exhaust one signed
    # browser's budget while remaining below the shared global request budget.
    results = [first.get('/api/session') for _ in range(121)]
    assert any(result.status_code == 429 for result in results)
    assert second.get('/api/session').status_code == 200
    assert first.get('/api/session').status_code == 429
    second.close()
