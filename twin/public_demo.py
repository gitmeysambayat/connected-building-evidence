"""Bounded, anonymous browser access to one shared historical MQTT demonstrator.

The signed cookie grants control of a short lease, not a user account. Public
source data remain readable. All leases, quotas and signatures are per process.
"""
from __future__ import annotations

from collections import deque
from contextlib import contextmanager
import hashlib
import hmac
import math
import os
import secrets
import threading
import time
from urllib.parse import urlsplit

from fastapi import HTTPException, Request, Response

COOKIE = 'connected_building_demo'


class PublicDemo:
    max_events = 300
    max_duration_seconds = 30
    max_events_per_second = 100
    lease_seconds = 45
    start_cooldown_seconds = 20
    global_start_cooldown_seconds = 15
    restart_cooldown_seconds = 60
    delivery_retention = 10000
    session_seconds = 3600
    max_body_bytes = 65536

    def __init__(self, *, enabled=False, origin='', environment='local', persistence='local',
                 secret=None, clock=time.monotonic, wall_clock=time.time):
        self.enabled = enabled
        self.origin = origin.rstrip('/')
        parsed = urlsplit(self.origin)
        if enabled and (parsed.scheme not in ('http', 'https') or not parsed.netloc or
                        parsed.path not in ('', '/') or parsed.query or parsed.fragment or
                        parsed.username or parsed.password or
                        (parsed.scheme != 'https' and parsed.hostname not in ('localhost', '127.0.0.1', '::1'))):
            raise ValueError('Public mode requires an HTTPS TWIN_PUBLIC_ORIGIN; HTTP is allowed only on loopback for QA.')
        self.secure_cookie = parsed.scheme == 'https'
        self.environment = environment
        self.persistence = persistence
        self.secret = secret or secrets.token_bytes(32)
        if isinstance(self.secret, str):
            self.secret = self.secret.encode()
        self.clock, self.wall_clock = clock, wall_clock
        self.lock = threading.RLock()
        self.owner = None
        self.expires = 0.0
        self.busy = False
        self.last_start = None
        self.last_restart = None
        self.sessions = {}
        self.global_commands = deque()
        self.http_requests = deque()
        self.client_requests = {}

    @classmethod
    def from_environment(cls):
        return cls(enabled=os.environ.get('TWIN_PUBLIC_DEMO') == '1',
                   origin=os.environ.get('TWIN_PUBLIC_ORIGIN') or os.environ.get('RENDER_EXTERNAL_URL', ''),
                   environment=os.environ.get('TWIN_DEPLOYMENT_ENVIRONMENT', 'local'),
                   persistence=os.environ.get('TWIN_STATE_PERSISTENCE', 'local'),
                   secret=os.environ.get('TWIN_SESSION_SECRET') or None)

    def deployment(self):
        result = {'environment': self.environment, 'profile': 'public_demo' if self.enabled else 'local',
                  'public_demo': self.enabled, 'state_persistence': self.persistence,
                  'verification_scope': 'Configured runtime metadata. Actual hosting and restart persistence are recorded separately in external deployment evidence.'}
        if self.enabled:
            result['public_url'] = self.origin
            result['configured_profile'] = 'public_demo'
        else:
            result['cloud_deployment_verified'] = False
        return result

    def _sign(self, payload):
        return hmac.new(self.secret, payload.encode(), hashlib.sha256).hexdigest()

    def _session(self, request):
        token = request.cookies.get(COOKIE, '')
        if len(token) > 180:
            return None
        try:
            sid, issued, signature = token.split('.')
            if len(sid) != 32 or not all(c.isalnum() or c in '-_' for c in sid):
                return None
            epoch = int(issued)
            age = self.wall_clock() - epoch
            if age < -30 or age > self.session_seconds:
                return None
            if not hmac.compare_digest(signature, self._sign(sid + '.' + issued)):
                return None
            return sid
        except (ValueError, TypeError):
            return None

    def session(self, request, response, transport):
        if not self.enabled:
            response.headers['Cache-Control'] = 'no-store'
            return {'public_demo': False, 'session_ready': True}
        sid = self._session(request)
        if sid is None:
            sid = secrets.token_urlsafe(24)
            payload = sid + '.' + str(int(self.wall_clock()))
            response.set_cookie(COOKIE, payload + '.' + self._sign(payload), max_age=self.session_seconds,
                                secure=self.secure_cookie, httponly=True, samesite='lax', path='/')
        response.headers['Cache-Control'] = 'no-store'
        return self.metadata(sid, transport)

    def require_session(self, request):
        if request.headers.get('origin') != self.origin:
            raise HTTPException(403, detail={'code': 'origin_required', 'message': 'A matching browser Origin is required for public replay controls.'})
        sid = self._session(request)
        if sid is None:
            raise HTTPException(403, detail={'code': 'session_required', 'message': 'Initialise the anonymous browser session before using public replay controls.'})
        return sid

    @staticmethod
    def _running(transport):
        worker = getattr(transport, 'worker', None)
        return bool((worker and worker.is_alive()) or transport.state.get('replay') == 'running')

    def _expire(self, transport):
        if self.owner is not None and self.clock() >= self.expires and not self.busy and not self._running(transport):
            self.owner = None
            self.expires = 0

    def _entry(self, sid):
        now = self.clock()
        stale = [key for key, value in self.sessions.items() if now - value['seen'] > 120]
        for key in stale:
            self.sessions.pop(key, None)
        if sid not in self.sessions:
            if len(self.sessions) >= 1024:
                oldest = min(self.sessions, key=lambda key: self.sessions[key]['seen'])
                self.sessions.pop(oldest)
            self.sessions[sid] = {'seen': now, 'last_start': None, 'commands': deque()}
        self.sessions[sid]['seen'] = now
        return self.sessions[sid]

    @staticmethod
    def _trim(queue, now, seconds=60):
        while queue and queue[0] <= now - seconds:
            queue.popleft()

    def _rate(self, sid, action, *, commit=False):
        now = self.clock()
        entry = self._entry(sid)
        for queue in (entry['commands'], self.global_commands):
            self._trim(queue, now)
        retry = 0.0
        if len(entry['commands']) >= 10:
            retry = max(retry, entry['commands'][0] + 60 - now)
        if len(self.global_commands) >= 60:
            retry = max(retry, self.global_commands[0] + 60 - now)
        if action == 'start':
            if self.last_start is not None:
                retry = max(retry, self.last_start + self.global_start_cooldown_seconds - now)
            if entry['last_start'] is not None:
                retry = max(retry, entry['last_start'] + self.start_cooldown_seconds - now)
        if action == 'restart' and self.last_restart is not None:
            retry = max(retry, self.last_restart + self.restart_cooldown_seconds - now)
        if retry > 0:
            raise HTTPException(429, detail={'code': 'control_cooldown', 'message': 'The shared demonstration is cooling down.', 'retry_after_seconds': math.ceil(retry)}, headers={'Retry-After': str(math.ceil(retry))})
        if commit:
            entry['commands'].append(now)
            self.global_commands.append(now)
            if action == 'start':
                self.last_start = entry['last_start'] = now
            elif action == 'restart':
                self.last_restart = now

    def _ownership(self, sid, transport, *, acquire=False):
        self._expire(transport)
        if self.busy:
            raise HTTPException(409, detail={'code': 'control_busy', 'message': 'A broker operation is already running.'})
        if self.owner is not None and self.owner != sid:
            raise HTTPException(403, detail={'code': 'lease_owned_by_another_visitor', 'message': 'Another visitor holds the shared replay lease.'})
        if self.owner is None:
            if self._running(transport):
                raise HTTPException(409, detail={'code': 'replay_busy', 'message': 'A replay is already running.'})
            if not acquire:
                raise HTTPException(403, detail={'code': 'lease_required', 'message': 'This browser does not hold a replay lease.'})
        return self.owner

    def validate_replay(self, speed, limit):
        if type(limit) is not int or type(speed) not in (int, float) or not math.isfinite(speed):
            raise HTTPException(422, detail={'code': 'invalid_public_replay', 'message': 'Use numeric speed and integer event count.'})
        if not 1 <= limit <= self.max_events or not 1 <= speed <= self.max_events_per_second or limit / speed > self.max_duration_seconds:
            raise HTTPException(422, detail={'code': 'public_replay_limit', 'message': 'Public replay permits 1-300 events at 1-100 events/s, with limit/speed no greater than 30 seconds.'})

    def start(self, request, transport, speed, limit, channel_id):
        sid = self.require_session(request)
        self.validate_replay(speed, limit)
        with self.lock:
            self._ownership(sid, transport, acquire=True)
            if self._running(transport):
                raise HTTPException(409, detail={'code': 'replay_busy', 'message': 'A replay is already running.'})
            self._rate(sid, 'start')
            result = transport.begin_replay(speed, limit, channel_id, max_duration_seconds=self.max_duration_seconds)
            self.owner, self.expires = sid, self.clock() + self.lease_seconds
            self._rate(sid, 'start', commit=True)
            return {**result, 'public_demo': self.metadata(sid, transport)}

    def stop(self, request, transport):
        sid = self.require_session(request)
        with self.lock:
            self._ownership(sid, transport)
            self._rate(sid, 'stop')
            result = transport.stop()
            self._rate(sid, 'stop', commit=True)
            return {**result, 'public_demo': self.metadata(sid, transport)}

    @contextmanager
    def restart(self, request, transport):
        sid = self.require_session(request)
        with self.lock:
            self._ownership(sid, transport, acquire=True)
            if self._running(transport):
                raise HTTPException(409, detail={'code': 'replay_busy', 'message': 'Wait for the replay to finish before testing broker reconnection.'})
            self._rate(sid, 'restart')
            self.owner, self.expires, self.busy = sid, self.clock() + self.lease_seconds, True
            self._rate(sid, 'restart', commit=True)
        try:
            yield sid
        finally:
            with self.lock:
                self.busy = False

    def metadata(self, sid, transport):
        with self.lock:
            self._expire(transport)
            running = self._running(transport)
            owned = bool(sid and self.owner == sid)
            available = self.owner is None or owned
            broker_ready = transport.state.get('broker') == 'running'
            cooldowns = {}
            if sid:
                for action in ('start', 'restart'):
                    try:
                        self._rate(sid, action)
                        cooldowns[action] = 0
                    except HTTPException as exc:
                        cooldowns[action] = int(exc.headers['Retry-After'])
            return {'public_demo': True, 'session_ready': sid is not None,
                    'scope': 'One shared anonymous historical replay sandbox; this is not multi-user account authentication.',
                    'capabilities': {'max_events': self.max_events, 'max_duration_seconds': self.max_duration_seconds,
                                     'max_events_per_second': self.max_events_per_second, 'lease_seconds': self.lease_seconds,
                                     'start_cooldown_seconds': self.start_cooldown_seconds,
                                     'restart_cooldown_seconds': self.restart_cooldown_seconds},
                    'lease': {'active': self.owner is not None, 'owned_by_you': owned,
                              'expires_in_seconds': max(0, math.ceil(self.expires - self.clock())) if self.owner else 0,
                              'replay_running': running},
                    'controls': {'can_start': bool(sid and available and broker_ready and not running and not self.busy and not cooldowns.get('start')),
                                 'can_stop': bool(owned and running and not self.busy),
                                 'can_restart_broker': bool(sid and available and broker_ready and not running and not self.busy and not cooldowns.get('restart'))},
                    'cooldowns': cooldowns,
                    'retention': {'delivery_rows': self.delivery_retention,
                                  'accepted_events': 'Pinned source-event identities retained until runtime reset',
                                  'state_persistence': self.persistence,
                                  'description': 'Only the newest 10,000 delivery rows are retained; accepted-event deduplication lasts until this runtime is reset.'}}

    def status(self, request, transport):
        result = transport.status()
        if self.enabled:
            result['public_demo'] = self.metadata(self._session(request), transport)
        return result

    def allow_read_request(self, client):
        """Bound reads per signed browser session, with socket-IP fallback only."""
        with self.lock:
            now = self.clock()
            self._trim(self.http_requests, now)
            if len(self.http_requests) >= 240:
                return False
            queue = self.client_requests.get(client)
            if queue is None:
                if len(self.client_requests) >= 128:
                    self.client_requests.pop(next(iter(self.client_requests)))
                queue = self.client_requests[client] = deque()
            self._trim(queue, now)
            if len(queue) >= 120:
                return False
            queue.append(now)
            self.http_requests.append(now)
            return True


class PublicRequestLimits:
    """Public-only body, read-rate and WebSocket bounds before framework parsing."""
    def __init__(self, app, policy):
        self.app, self.policy = app, policy

    async def __call__(self, scope, receive, send):
        policy = self.policy()
        if not policy.enabled:
            return await self.app(scope, receive, send)
        if scope['type'] == 'websocket':
            return await send({'type': 'websocket.close', 'code': 1008, 'reason': 'This public demonstration serves HTTP GraphQL only.'})
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        path = scope.get('path', '')
        if path.startswith('/api/') or path == '/graphql':
            sid = policy._session(Request(scope))
            client = 'session:' + sid if sid else 'client:' + str((scope.get('client') or ('unknown',))[0])
            if not policy.allow_read_request(client):
                return await self._reject(send, 429, b'Public data request budget exceeded. Retry in one minute.', [(b'retry-after', b'60')])
        if scope.get('method') in ('POST', 'PUT', 'PATCH'):
            chunks, size = [], 0
            while True:
                message = await receive()
                if message['type'] == 'http.disconnect':
                    return
                chunk = message.get('body', b'')
                size += len(chunk)
                if size > policy.max_body_bytes:
                    return await self._reject(send, 413, b'Public request body exceeds 65,536 bytes.')
                chunks.append(chunk)
                if not message.get('more_body', False):
                    break
            consumed = False
            async def bounded_receive():
                nonlocal consumed
                if not consumed:
                    consumed = True
                    return {'type': 'http.request', 'body': b''.join(chunks), 'more_body': False}
                return await receive()
            return await self.app(scope, bounded_receive, send)
        return await self.app(scope, receive, send)

    @staticmethod
    async def _reject(send, status, body, extra=()):
        await send({'type': 'http.response.start', 'status': status,
                    'headers': [(b'content-type', b'text/plain; charset=utf-8'), (b'cache-control', b'no-store'), *extra]})
        await send({'type': 'http.response.body', 'body': body})
