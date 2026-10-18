"""Real TCP/MQTT regressions for bounded broker replacement after port probes."""
import asyncio
import socket
import time

import pytest

from twin import data
from twin.public_demo import PublicDemo
from twin.transport import HistoricalTransport


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


async def replay(transport, count=20, *, duplicate=False):
    transport.begin_replay(100, count, 'Kitchen_Temperature', max_duration_seconds=10)
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        if transport.state['replay'] == 'failed':
            raise AssertionError(transport.status())
        if transport.state['replay'] == 'completed':
            run = transport.status()['current_run']
            if run['duplicates' if duplicate else 'accepted'] == count:
                assert run['published'] == count and run['rejected'] == 0
                return
        await asyncio.sleep(.02)
    raise AssertionError(transport.status())


def test_raw_closed_and_half_open_tcp_probes_do_not_hold_restart_listener_or_replay(tmp_path):
    async def scenario():
        data.initialise()
        transport = HistoricalTransport(port=free_port(), runtime=tmp_path, public_mode=True)
        await transport.start()
        writers = []
        try:
            await replay(transport)
            old_broker, old_subscriber = transport.broker, transport.subscriber
            # Match a port detector which closes without any MQTT CONNECT.
            _, probe = await asyncio.open_connection('127.0.0.1', transport.port)
            probe.close()
            await probe.wait_closed()
            # Also leave peers before CONNECT and half way through its header.
            for partial in (b'', b'\x10'):
                _, writer = await asyncio.open_connection('127.0.0.1', transport.port)
                writers.append(writer)
                writer.write(partial)
                await writer.drain()
            await asyncio.sleep(.02)
            resources = transport._broker_resources(old_broker)
            assert len(old_broker.connection_writers) >= 2
            began = time.monotonic()
            result = await asyncio.wait_for(transport.restart_broker(), timeout=12)
            assert time.monotonic() - began < 12
            assert result['broker'] == 'running' and result['subscriber'] == 'connected'
            assert result['restart_phase'] == 'completed'
            assert result['broker_restarts'] == 1
            assert transport.broker is not old_broker and transport.subscriber is not old_subscriber
            assert transport.subscribed.is_set()
            assert old_broker.cleanup_complete
            assert not old_broker.connection_tasks and not old_broker.connection_writers
            assert all(not server.is_serving() for server in resources['servers'])
            assert all(task.done() for task in resources['tasks'])
            assert not transport._phase_tasks
            await replay(transport, duplicate=True)
            assert transport.status()['persistent_unique_events'] == 20
            # Repeated replacement must release the same port and its old tasks.
            await asyncio.wait_for(transport.restart_broker(), timeout=12)
            assert transport.state['broker_restarts'] == 2
            await replay(transport, duplicate=True)
        finally:
            for writer in writers:
                writer.close()
            await transport.close()
    asyncio.run(scenario())


def test_stalled_shutdown_retains_popped_handler_references_and_replaces_real_broker(tmp_path, monkeypatch):
    async def scenario():
        data.initialise()
        transport = HistoricalTransport(port=free_port(), runtime=tmp_path, public_mode=True)
        await transport.start()
        old_broker = transport.broker
        resources = transport._broker_resources(old_broker)
        cancelled = asyncio.Event()
        async def stalled_shutdown():
            # amqtt pops sessions before awaiting their handler's stop. Reproduce
            # that loss of registry ownership while the shutdown never finishes.
            old_broker.sessions.clear()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        monkeypatch.setattr(old_broker, 'shutdown', stalled_shutdown)
        transport.broker_shutdown_timeout = .1
        try:
            result = await asyncio.wait_for(transport.restart_broker(), timeout=12)
            assert cancelled.is_set()
            assert result['broker_shutdown_forced'] is True
            assert result['broker'] == 'running' and result['subscriber'] == 'connected'
            assert old_broker.cleanup_complete
            assert all(task.done() for task in resources['tasks'])
            assert not old_broker.connection_tasks and not transport._phase_tasks
            assert all(not server.is_serving() for server in resources['servers'])
            await replay(transport)
        finally:
            await transport.close()
    asyncio.run(scenario())


def test_failed_fresh_subscription_returns_error_releases_lease_and_closes_owned_resources(tmp_path, monkeypatch):
    async def scenario():
        data.initialise()
        transport = HistoricalTransport(port=free_port(), runtime=tmp_path, public_mode=True)
        await transport.start()
        policy = PublicDemo(enabled=True, origin='https://example.test')
        monkeypatch.setattr(policy, 'require_session', lambda request: 'controlled-browser')
        password, subscribe = transport.password, transport._start_subscriber
        async def short_subscription_timeout(timeout):
            return await subscribe(.25)
        monkeypatch.setattr(transport, '_start_subscriber', short_subscription_timeout)
        transport.password = 'intentionally-incorrect-for-real-authentication'
        try:
            began = time.monotonic()
            with pytest.raises(RuntimeError, match='subscription did not become ready'):
                with policy.restart(None, transport):
                    await transport.restart_broker()
            assert time.monotonic() - began < 12
            assert policy.busy is False
            assert transport.state['broker'] == 'failed'
            assert transport.state['restart_phase'] == 'failed'
            assert 'subscribe' in transport.state['last_error']
            assert not transport.subscribed.is_set()
            assert transport.broker.cleanup_complete
            assert not transport.broker.connection_tasks and not transport._phase_tasks
            assert all(not server.instance.is_serving() for server in transport.broker._servers.values())
            # Cleanup is idempotent, and a corrected subsequent real replacement
            # can reuse the original port rather than requiring another port.
            transport.password = password
            monkeypatch.setattr(transport, '_start_subscriber', subscribe)
            await asyncio.wait_for(transport.restart_broker(), timeout=12)
            assert transport.subscribed.is_set() and transport.state['broker_restarts'] == 1
            await replay(transport)
        finally:
            await transport.close()
    asyncio.run(scenario())
