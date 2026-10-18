"""Bounded ingestion for one owner-managed environmental station.

This store is separate from the immutable 2017 research dataset. Possession of
the device token authenticates a sender, not the physical origin or calibration
of its measurements. No configured device or example reading is created here.
"""
from __future__ import annotations

import asyncio
from collections import deque
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import hmac
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
import uuid

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from twin import settings

INGEST_PATH = '/api/live-sensor/readings'


def utc_timestamp(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


class SensorReading(BaseModel):
    model_config = ConfigDict(extra='forbid')
    schema_version: int = Field(1, strict=True, ge=1, le=1)
    device_id: str = Field(strict=True, pattern=r'^[A-Za-z0-9._-]{1,64}$')
    boot_id: str = Field(strict=True, min_length=36, max_length=36)
    sequence: int = Field(strict=True, ge=0, le=2147483647)
    observed_at: datetime
    temperature_c: float = Field(strict=True, ge=-40, le=85, allow_inf_nan=False)
    relative_humidity_pct: float = Field(strict=True, ge=0, le=100, allow_inf_nan=False)
    interval_seconds: int = Field(60, strict=True, ge=60, le=60)

    @field_validator('boot_id')
    @classmethod
    def uuid_identifier(cls, value):
        parsed = uuid.UUID(value)
        if str(parsed) != value.lower():
            raise ValueError('Use a UUID boot identifier.')
        return str(parsed)

    @field_validator('observed_at', mode='before')
    @classmethod
    def explicit_datetime(cls, value):
        if not isinstance(value, str) or len(value) > 40 or 'T' not in value:
            raise ValueError('Use an ISO 8601 date and time with a timezone.')
        return value

    @field_validator('observed_at')
    @classmethod
    def timezone_required(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError('A timezone is required.')
        return value.astimezone(timezone.utc)

    @field_validator('temperature_c', 'relative_humidity_pct', mode='before')
    @classmethod
    def numeric_measurements(cls, value):
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError('A finite number is required.')
        return value


def parse_reading(body):
    """Do not echo raw request contents or identifiers in validation responses."""
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate JSON key.')
            result[key] = value
        return result

    def invalid_constant(value):
        raise ValueError('Non-finite JSON number.')

    try:
        document = json.loads(body, object_pairs_hook=unique_object, parse_constant=invalid_constant)
        return SensorReading.model_validate(document)
    except (ValidationError, ValueError, TypeError, UnicodeDecodeError, OverflowError, RecursionError):
        raise HTTPException(422, detail={'code': 'invalid_sensor_reading',
                            'message': 'Use the documented sensor fields, ranges and timezone-aware timestamp.'}) from None


class LiveSensor:
    max_body_bytes = 2048
    body_timeout_seconds = 5
    max_rows = 10080
    max_age_days = 7
    stale_after_seconds = 180
    max_observation_age_seconds = 900
    max_future_seconds = 120
    max_requests_per_minute = 60
    max_authenticated_per_minute = 12
    min_new_reading_interval_seconds = 5
    station_label = 'Environmental station'

    def __init__(self, *, path=None, token='', device_id='', state_persistence='local',
                 clock=time.monotonic, wall_clock=time.time):
        self.path = Path(path or settings.LIVE_SENSOR_DB_PATH).resolve()
        if self.path == settings.DB_PATH.resolve() or self.path.is_relative_to(settings.SOURCE_DIR.resolve()):
            raise ValueError('The live sensor store must be separate from historical source data.')
        if token or device_id:
            if (not isinstance(token, str) or not 32 <= len(token) <= 256 or
                    not re.fullmatch(r'[!-~]+', token) or
                    not isinstance(device_id, str) or not re.fullmatch(r'[A-Za-z0-9._-]{1,64}', device_id)):
                raise ValueError('Live ingestion requires a 32-256 character server token and a valid device identifier.')
        self._token = token.encode('ascii')
        self._device_id = device_id
        self.enabled = bool(token and device_id)
        self.state_persistence = state_persistence
        self.clock, self.wall_clock = clock, wall_clock
        self.lock = threading.RLock()
        self.requests, self.authenticated_requests = deque(), deque()
        self.last_accepted = None

    @classmethod
    def from_environment(cls):
        return cls(token=os.environ.get('TWIN_SENSOR_INGEST_TOKEN', ''),
                   device_id=os.environ.get('TWIN_SENSOR_DEVICE_ID', ''),
                   state_persistence=os.environ.get('TWIN_STATE_PERSISTENCE', 'local'))

    def initialise(self):
        if not self.enabled:
            return
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as connection:
                connection.execute('''CREATE TABLE IF NOT EXISTS sensor_reading (
                    id INTEGER PRIMARY KEY, event_key TEXT NOT NULL UNIQUE,
                    payload_sha256 TEXT NOT NULL, receipt_id TEXT NOT NULL,
                    observed_epoch REAL NOT NULL, received_epoch REAL NOT NULL,
                    temperature_c REAL NOT NULL, relative_humidity_pct REAL NOT NULL)''')
                connection.execute('CREATE INDEX IF NOT EXISTS sensor_received ON sensor_reading(received_epoch)')
                self._prune(connection)

    def _prune(self, connection):
        cutoff = self.wall_clock() - self.max_age_days * 86400
        connection.execute('DELETE FROM sensor_reading WHERE received_epoch < ?', (cutoff,))
        connection.execute('DELETE FROM sensor_reading WHERE id NOT IN '
                           '(SELECT id FROM sensor_reading ORDER BY id DESC LIMIT ?)', (self.max_rows,))

    @contextmanager
    def _connect(self):
        # A failed disk operation can be retried with the same boot_id/sequence.
        connection = sqlite3.connect(self.path, timeout=2)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def _unavailable():
        return HTTPException(503, detail={'code': 'sensor_store_unavailable',
                             'message': 'Sensor storage is temporarily unavailable. Retry the same reading.'},
                             headers={'Retry-After': '30'})

    @staticmethod
    def public_row(row):
        return {'receipt_id': row['receipt_id'], 'observed_at': utc_timestamp(row['observed_epoch']),
                'received_at': utc_timestamp(row['received_epoch']), 'temperature_c': row['temperature_c'],
                'relative_humidity_pct': row['relative_humidity_pct'], 'source': 'authenticated_device_report'}

    def authorise(self, headers):
        if not self.enabled:
            raise HTTPException(503, detail={'code': 'sensor_not_configured',
                                 'message': 'The environmental station is awaiting configuration.'},
                                 headers={'Retry-After': '60'})
        values = [value for key, value in headers if key.lower() == b'authorization']
        expected = b'Bearer ' + self._token
        supplied = values[0] if len(values) == 1 and len(values[0]) <= 264 else b''
        if not hmac.compare_digest(supplied, expected):
            raise HTTPException(401, detail={'code': 'sensor_authentication_required',
                                 'message': 'A valid sensor credential is required.'},
                                 headers={'WWW-Authenticate': 'Bearer'})

    def request_budget(self, *, authenticated=False):
        with self.lock:
            now = self.clock()
            queue = self.authenticated_requests if authenticated else self.requests
            while queue and queue[0] <= now - 60:
                queue.popleft()
            limit = self.max_authenticated_per_minute if authenticated else self.max_requests_per_minute
            if len(queue) >= limit:
                delay = max(1, math.ceil(queue[0] + 60 - now))
                raise HTTPException(429, detail={'code': 'sensor_request_budget',
                                     'message': 'Sensor request budget exceeded. Retry the same reading later.'},
                                     headers={'Retry-After': str(delay)})
            queue.append(now)

    def accept(self, reading):
        # Endpoint authorisation is performed before body parsing by the middleware.
        if not self.enabled:
            self.authorise([])
        if not hmac.compare_digest(reading.device_id.encode(), self._device_id.encode()):
            raise HTTPException(403, detail={'code': 'sensor_device_not_allowed',
                                 'message': 'This station is not allowlisted.'})
        event_key = hashlib.sha256(f'{reading.device_id}\0{reading.boot_id}\0{reading.sequence}'.encode()).hexdigest()
        payload = reading.model_dump(mode='json')
        payload['observed_at'] = reading.observed_at.isoformat()
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        with self.lock:
            try:
                self.initialise()
                with self._connect() as connection:
                    connection.execute('BEGIN IMMEDIATE')
                    self._prune(connection)
                    previous = connection.execute('SELECT * FROM sensor_reading WHERE event_key = ?', (event_key,)).fetchone()
                    if previous:
                        if previous['payload_sha256'] != digest:
                            raise HTTPException(409, detail={'code': 'sensor_idempotence_conflict',
                                                 'message': 'This reading identity already has different content.'})
                        return {'state': 'duplicate', 'receipt': self.public_row(previous)}, 200
                    now, observed = self.wall_clock(), reading.observed_at.timestamp()
                    if not -self.max_future_seconds <= now - observed <= self.max_observation_age_seconds:
                        raise HTTPException(422, detail={'code': 'sensor_timestamp_out_of_window',
                                             'message': 'New readings must be at most 15 minutes old and no more than 120 seconds ahead.'})
                    elapsed = self.clock() - self.last_accepted if self.last_accepted is not None else math.inf
                    if elapsed < self.min_new_reading_interval_seconds:
                        delay = math.ceil(self.min_new_reading_interval_seconds - elapsed)
                        raise HTTPException(429, detail={'code': 'sensor_sample_cooldown',
                                             'message': 'Wait before sending a new reading.'}, headers={'Retry-After': str(delay)})
                    receipt = str(uuid.uuid4())
                    cursor = connection.execute('''INSERT INTO sensor_reading
                        (event_key, payload_sha256, receipt_id, observed_epoch, received_epoch, temperature_c, relative_humidity_pct)
                        VALUES (?, ?, ?, ?, ?, ?, ?)''',
                        (event_key, digest, receipt, observed, now, reading.temperature_c, reading.relative_humidity_pct))
                    self._prune(connection)
                    row = connection.execute('SELECT * FROM sensor_reading WHERE id = ?', (cursor.lastrowid,)).fetchone()
                self.last_accepted = self.clock()
                return {'state': 'accepted', 'receipt': self.public_row(row)}, 201
            except (sqlite3.Error, OSError):
                raise self._unavailable() from None

    def _rows(self, limit):
        if not self.enabled:
            return [], 0
        with self.lock:
            try:
                self.initialise()
                with self._connect() as connection:
                    count = connection.execute('SELECT COUNT(*) FROM sensor_reading').fetchone()[0]
                    rows = connection.execute('SELECT * FROM sensor_reading ORDER BY observed_epoch DESC, id DESC LIMIT ?', (limit,)).fetchall()
                return [self.public_row(row) for row in reversed(rows)], count
            except (sqlite3.Error, OSError):
                raise self._unavailable() from None

    def readings(self, limit=60):
        rows, count = self._rows(limit)
        return {'station_label': self.station_label, 'model_assignment': 'unassigned', 'count': count, 'readings': rows}

    def status(self):
        rows, count = self._rows(1)
        latest = rows[-1] if rows else None
        age = max(0, self.wall_clock() - datetime.fromisoformat(latest['observed_at'].replace('Z', '+00:00')).timestamp()) if latest else None
        return {'enabled': self.enabled, 'state': 'reporting' if latest else ('awaiting_reading' if self.enabled else 'disabled'),
                'freshness': ('fresh' if age <= self.stale_after_seconds else 'stale') if latest else 'awaiting_reading',
                'station_label': self.station_label, 'model_assignment': 'unassigned',
                'state_persistence': self.state_persistence, 'count': count, 'latest': latest,
                'age_seconds': round(age, 1) if age is not None else None,
                'stale_after_seconds': self.stale_after_seconds,
                'server_time': utc_timestamp(self.wall_clock()),
                'retention': {'max_rows': self.max_rows, 'max_age_days': self.max_age_days},
                'provenance': {'source': 'authenticated_device_report', 'physical_origin_independently_verified': False,
                               'historical_source_dataset': False}}


class SensorIngressLimits:
    """Enforce auth, request/body/time limits before FastAPI parses any body.

    The exact POST path has its own budget so browsing the historical dashboard
    does not consume the device budget. Bodies are bounded also in local mode.
    """
    def __init__(self, app, station):
        self.app, self.station = app, station

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope.get('method') != 'POST' or scope.get('path') != INGEST_PATH:
            return await self.app(scope, receive, send)
        station = self.station()
        try:
            station.request_budget()
            station.authorise(scope.get('headers', []))
            host = dict(scope.get('headers', [])).get(b'host', b'').decode('latin1').split(':')[0]
            # Render's trusted proxy sets the ASGI scheme through Uvicorn. Never
            # accept a client-supplied forwarding header directly here.
            if scope.get('scheme') != 'https':
                try:
                    loopback = ipaddress.ip_address(host).is_loopback
                except ValueError:
                    loopback = host == 'localhost'
                try:
                    local_client = ipaddress.ip_address((scope.get('client') or ('',))[0]).is_loopback
                except ValueError:
                    local_client = False
                if not (loopback and local_client):
                    raise HTTPException(400, detail={'code': 'sensor_https_required', 'message': 'Use HTTPS for sensor ingestion.'})
            station.request_budget(authenticated=True)
            headers = {key.lower(): value for key, value in scope.get('headers', [])}
            if headers.get(b'content-type', b'').split(b';', 1)[0].strip().lower() != b'application/json':
                raise HTTPException(415, detail={'code': 'sensor_json_required', 'message': 'Use application/json.'})
            if b'content-encoding' in headers and headers[b'content-encoding'].lower() != b'identity':
                raise HTTPException(415, detail={'code': 'sensor_encoding_unsupported', 'message': 'Compressed request bodies are unsupported.'})
            if b'content-length' in headers:
                length = headers[b'content-length']
                if not length.isdigit():
                    raise HTTPException(400, detail={'code': 'sensor_invalid_length', 'message': 'Invalid request length.'})
                if len(length) > 10 or int(length) > station.max_body_bytes:
                    raise HTTPException(413, detail={'code': 'sensor_body_too_large', 'message': 'Sensor requests are limited to 2,048 bytes.'})
            chunks, size = [], 0
            async def read_body():
                nonlocal size
                while True:
                    message = await receive()
                    if message['type'] == 'http.disconnect':
                        return None
                    chunk = message.get('body', b'')
                    size += len(chunk)
                    if size > station.max_body_bytes:
                        raise HTTPException(413, detail={'code': 'sensor_body_too_large', 'message': 'Sensor requests are limited to 2,048 bytes.'})
                    chunks.append(chunk)
                    if not message.get('more_body', False):
                        return b''.join(chunks)
            try:
                body = await asyncio.wait_for(read_body(), timeout=station.body_timeout_seconds)
            except asyncio.TimeoutError:
                raise HTTPException(408, detail={'code': 'sensor_body_timeout', 'message': 'Sensor request body timed out.'}) from None
            if body is None:
                return
        except HTTPException as exc:
            response = json.dumps({'detail': exc.detail}).encode()
            headers = [(b'content-type', b'application/json'), (b'cache-control', b'no-store')]
            headers.extend((key.lower().encode(), value.encode()) for key, value in (exc.headers or {}).items())
            await send({'type': 'http.response.start', 'status': exc.status_code, 'headers': headers})
            return await send({'type': 'http.response.body', 'body': response})
        consumed = False
        async def bounded_receive():
            nonlocal consumed
            if not consumed:
                consumed = True
                return {'type': 'http.request', 'body': body, 'more_body': False}
            return await receive()
        return await self.app(scope, bounded_receive, send)
