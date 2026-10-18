"""Pinned measured data, scoped IFC room identities and transparent source links.

Every source CSV row is retained. An unverified filename-only association is not
promoted to a verified IFC mapping. Observed and received times are separate
concepts; this module stores historical observation times only.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
from typing import Iterator
import csv
import hashlib
import io
import json
import math
import os
import sqlite3
import statistics
import uuid

from twin import settings

SCHEMA_VERSION = 1
MAX_EPOCH = 253402300799  # 9999-12-31T23:59:59Z, the supported datetime range.

SCHEMA = '''
CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE rooms (
    guid TEXT PRIMARY KEY, room_id TEXT NOT NULL UNIQUE, ifc_step_id INTEGER NOT NULL UNIQUE,
    name TEXT, long_name TEXT, model_sha256 TEXT NOT NULL, mapping_status TEXT NOT NULL,
    UNIQUE(guid, model_sha256)
);
CREATE TABLE channels (
    id TEXT PRIMARY KEY, room_guid TEXT REFERENCES rooms(guid),
    candidate_room_guid TEXT REFERENCES rooms(guid), source_room_id TEXT NOT NULL,
    model_sha256 TEXT NOT NULL, metric TEXT NOT NULL, unit TEXT NOT NULL,
    source_kind TEXT NOT NULL CHECK(source_kind IN ('measured_sensor','controller_setpoint','external_virtual_weather')),
    device_id TEXT, device_uri TEXT, raw_feature_uri TEXT, raw_namespace TEXT,
    canonical_feature_uri TEXT, mapping_status TEXT NOT NULL,
    mapping_verified INTEGER NOT NULL CHECK(mapping_verified IN (0,1)), mapping_note TEXT NOT NULL,
    source_csv TEXT NOT NULL, source_sha256 TEXT NOT NULL,
    source_rdf TEXT, source_rdf_sha256 TEXT,
    source_rows INTEGER NOT NULL, first_ts INTEGER NOT NULL, last_ts INTEGER NOT NULL,
    min_value REAL NOT NULL, max_value REAL NOT NULL,
    median_step_seconds REAL, max_gap_seconds INTEGER,
    duplicate_timestamps INTEGER NOT NULL, out_of_order_steps INTEGER NOT NULL,
    gaps_over_one_hour INTEGER NOT NULL,
    CHECK ((mapping_verified=1 AND room_guid IS NOT NULL) OR (mapping_verified=0 AND room_guid IS NULL)),
    FOREIGN KEY(room_guid,model_sha256) REFERENCES rooms(guid,model_sha256)
);
CREATE TABLE observations (
    id INTEGER PRIMARY KEY, channel_id TEXT NOT NULL REFERENCES channels(id),
    observed_at INTEGER NOT NULL, value REAL NOT NULL, source_line INTEGER NOT NULL,
    UNIQUE(channel_id,source_line)
);
CREATE INDEX observations_channel_time ON observations(channel_id,observed_at,id);
CREATE INDEX channels_room ON channels(room_guid,metric);
'''


def manifest() -> dict:
    return json.loads((settings.SOURCE_DIR / settings.MANIFEST_NAME).read_text(encoding='utf-8'))


def source_audit() -> dict:
    return json.loads((settings.SOURCE_DIR / settings.AUDIT_NAME).read_text(encoding='utf-8'))


def _iso(epoch: int | float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


def parse_time(value: str | int | None) -> int | None:
    """Accept explicit UTC/offset timestamps or Unix seconds, never invent an offset."""
    if value is None or value == '':
        return None
    if isinstance(value, bool):
        raise ValueError('A timestamp must be an ISO timestamp or integer Unix seconds.')
    if isinstance(value, int) or str(value).isdigit():
        try:
            epoch = int(value)
        except (ValueError, OverflowError) as exc:
            raise ValueError('Timestamp exceeds the supported Unix-second range.') from exc
        if not 0 <= epoch <= MAX_EPOCH:
            raise ValueError('Timestamp must lie between 0 and 253402300799 Unix seconds.')
        return epoch
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError as exc:
        raise ValueError('Use an ISO timestamp with a UTC offset, for example 2017-04-01T00:00:00Z.') from exc
    if parsed.tzinfo is None:
        raise ValueError('A timestamp must include Z or an explicit UTC offset.')
    try:
        epoch = int(parsed.timestamp())
    except (ValueError, OverflowError, OSError) as exc:
        raise ValueError('Timestamp exceeds the supported datetime range.') from exc
    if not 0 <= epoch <= MAX_EPOCH:
        raise ValueError('Timestamp must lie between 0 and 253402300799 Unix seconds.')
    return epoch


def parse_csv(payload: bytes, filename: str) -> list[tuple[int, float, int]]:
    """Parse exact source rows without resampling, rounding or missing-value filling."""
    result = []
    for line_number, row in enumerate(csv.reader(io.StringIO(payload.decode('utf-8-sig')), delimiter='\t'), 1):
        if not row:
            continue
        if len(row) != 2:
            raise ValueError(f'{filename} line {line_number}: expected two tab-separated values.')
        try:
            if str(int(row[0])) != row[0].strip():
                raise ValueError('Timestamp is not an integer Unix second')
            epoch = int(row[0])
            value = float(row[1])
            if not 0 <= epoch <= MAX_EPOCH or not math.isfinite(value):
                raise ValueError('Invalid epoch or non-finite measurement')
        except ValueError as exc:
            raise ValueError(f'{filename} line {line_number}: invalid timestamp or measurement.') from exc
        result.append((epoch, value, line_number))
    if not result:
        raise ValueError(f'{filename}: no source observations.')
    return result


def _verify_sources(info: dict) -> None:
    if info['commit'] != settings.SOURCE_COMMIT:
        raise ValueError('The source manifest is not pinned to the reviewed upstream commit.')
    for item in info['files']:
        target = (settings.SOURCE_DIR / item['path']).resolve()
        if not target.is_relative_to(settings.SOURCE_DIR.resolve()):
            raise ValueError('Source manifest contains an unsafe path.')
        payload = target.read_bytes()
        if len(payload) != item['bytes'] or hashlib.sha256(payload).hexdigest() != item['sha256']:
            raise ValueError('Pinned source hash mismatch: ' + item['path'])


def _metric(filename: str) -> tuple[str, str]:
    if 'OutdoorTemperature' in filename:
        return 'outdoor_temperature', '°C'
    if 'ThermostatTemperature' in filename:
        return 'thermostat_temperature', '°C'
    if 'SetpointHistory' in filename:
        return 'setpoint_temperature', '°C'
    if 'Brightness' in filename:
        return 'brightness', 'lx'
    if 'Humidity' in filename:
        return 'humidity', '%'
    if 'Temperature' in filename:
        return 'temperature', '°C'
    raise ValueError('Unsupported source channel: ' + filename)


@contextmanager
def connection(readonly: bool = True) -> Iterator[sqlite3.Connection]:
    path = settings.DB_PATH.resolve()
    if readonly:
        if not path.exists():
            raise FileNotFoundError('Measured data have not been imported. Run scripts/build_data.py.')
        conn = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=15)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    conn.execute('PRAGMA busy_timeout=15000')
    try:
        yield conn
        if not readonly:
            conn.commit()
    except Exception:
        if not readonly:
            conn.rollback()
        raise
    finally:
        conn.close()


def initialise(force: bool = False) -> dict:
    info, audit = manifest(), source_audit()
    _verify_sources(info)
    manifest_sha256 = hashlib.sha256((settings.SOURCE_DIR / settings.MANIFEST_NAME).read_bytes()).hexdigest()
    if settings.DB_PATH.exists() and not force:
        try:
            with connection() as conn:
                stored = dict(conn.execute('SELECT key,value FROM metadata'))
            if (json.loads(stored.get('schema_version', '0')) == SCHEMA_VERSION
                    and json.loads(stored.get('ifc_sha256', 'null')) == info['ifc_sha256']
                    and json.loads(stored.get('source_commit', 'null')) == settings.SOURCE_COMMIT
                    and json.loads(stored.get('source_manifest_sha256', 'null')) == manifest_sha256):
                return summary()
        except (sqlite3.DatabaseError, json.JSONDecodeError):
            pass
    settings.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    staging = settings.DB_PATH.with_name(settings.DB_PATH.stem + '.' + uuid.uuid4().hex + '.tmp.sqlite3')
    source_rooms = {room['room']: room for room in audit['room_mapping']}
    import ifcopenshell
    source_model = ifcopenshell.open(settings.SOURCE_IFC)
    for room in source_rooms.values():
        entity = source_model.by_id(room['ifc_step_id'])
        if not entity.is_a('IfcSpace') or entity.GlobalId != room['ifc_guid']:
            raise ValueError('The source alignment does not resolve to the claimed IFC space.')
    rdf_checks = {check['csv_file']: check for check in audit['rdf_to_csv_checks']}
    quality_channels = []
    try:
        conn = sqlite3.connect(staging)
        conn.execute('PRAGMA foreign_keys=ON')
        conn.executescript(SCHEMA)
        for room_id, room in source_rooms.items():
            conn.execute('INSERT INTO rooms VALUES (?,?,?,?,?,?,?)',
                         (room['ifc_guid'], room_id, room['ifc_step_id'], room['ifc_name'], room['ifc_long_name'], info['ifc_sha256'], room['status']))
        for source_channel in audit['channels']:
            filename = source_channel['file']
            path = settings.SOURCE_DIR / 'Measurements' / filename
            rows = parse_csv(path.read_bytes(), filename)
            if len(rows) != source_channel['rows']:
                raise ValueError('Source audit row count differs: ' + filename)
            channel_id = path.stem
            room_id = channel_id.split('_')[0]
            room = source_rooms[room_id]
            check = rdf_checks.get(filename)
            verified = bool(check and check['rdf_rows'] == len(rows) and
                            check['feature_ids'] == {room_id: len(rows)} and
                            check['source_osh_namespace'] in (settings.OSH_NAMESPACE, settings.SETPOINT_SOURCE_NAMESPACE) and
                            not check['value_or_timestamp_mismatch_count'] and not check['missing_csv_indices'])
            device_id = next(iter(check['sensor_ids'] or check['actuator_ids']), None) if check else None
            raw_namespace = check['source_osh_namespace'] if check else None
            if verified and raw_namespace == settings.SETPOINT_SOURCE_NAMESPACE:
                status = 'publisher_rdf_namespace_normalised'
                note = 'CSV values and epochs match source actuation RDF. Its different OSH namespace is explicitly normalised by matching the publisher room and actuator local names; raw URIs are retained.'
            elif verified:
                status = 'publisher_rdf_explicit_room_link'
                note = 'Source RDF identifies the sensor and room for the CSV observations; publisher alignment maps that room to the checked IFC space. This establishes a room-level data association, not sensor coordinates or calibration.'
            else:
                status = 'inferred_filename_unverified'
                note = 'The source CSV filename suggests Toilet and thermostat temperature, but no measurement RDF counterpart is supplied. The candidate room is retained separately and excluded from verified mapping by default.'
            metric, unit = _metric(filename)
            stamps = [row[0] for row in rows]
            steps = [b-a for a,b in zip(stamps,stamps[1:])]
            source_kind = source_channel['source_kind']
            item = {'id': channel_id, 'room_guid': room['ifc_guid'] if verified else None,
                    'candidate_room_guid': room['ifc_guid'], 'source_room_id': room_id,
                    'model_sha256': info['ifc_sha256'], 'metric': metric, 'unit': unit,
                    'source_kind': source_kind, 'device_id': device_id,
                    'device_uri': raw_namespace + device_id if device_id else None,
                    'raw_feature_uri': raw_namespace + room_id if check else None,
                    'raw_namespace': raw_namespace,
                    'canonical_feature_uri': settings.OSH_NAMESPACE + room_id if verified else None,
                    'mapping_status': status, 'mapping_verified': int(verified), 'mapping_note': note,
                    'source_csv': 'Measurements/' + filename, 'source_sha256': source_channel['sha256'],
                    'source_rdf': 'MeasurementsTurtle/' + check['rdf_file'] if check else None,
                    'source_rdf_sha256': check['source_sha256'] if check else None,
                    'source_rows': len(rows), 'first_ts': min(stamps), 'last_ts': max(stamps),
                    'min_value': min(row[1] for row in rows), 'max_value': max(row[1] for row in rows),
                    'median_step_seconds': statistics.median(steps) if steps else None,
                    'max_gap_seconds': max(steps) if steps else None,
                    'duplicate_timestamps': len(stamps)-len(set(stamps)),
                    'out_of_order_steps': sum(step<0 for step in steps),
                    'gaps_over_one_hour': sum(step>3600 for step in steps) if source_kind != 'controller_setpoint' else 0}
            columns = tuple(item)
            conn.execute(f'INSERT INTO channels ({",".join(columns)}) VALUES ({",".join("?" for _ in columns)})', tuple(item[key] for key in columns))
            conn.executemany('INSERT INTO observations(channel_id,observed_at,value,source_line) VALUES (?,?,?,?)',
                             ((channel_id,epoch,value,line) for epoch,value,line in rows))
            quality_channels.append({key: item[key] for key in ('id','source_kind','source_rows','mapping_status','duplicate_timestamps','out_of_order_steps','gaps_over_one_hour','median_step_seconds','max_gap_seconds')})
        quality_report = {
            'channels': quality_channels, 'csv_rows_retained': audit['csv_rows'],
            'handling': 'Original source rows and values retained. No interpolation, gap filling or averaging is applied at import. Identical weather files remain traceable source channels and are identified as copies.',
            'gap_rule': 'Steps longer than one hour are flagged for sensor and weather channels. Setpoint histories are controller events and are excluded from this cadence rule. This is a descriptive application rule, not a commissioning specification.',
            'byte_identical_weather_files': audit['byte_identical_source_files'],
            'outdoor_data': 'One external virtual weather series is repeated under six room filenames; it is not six independent physical outdoor sensors.',
            'namespace_normalisation': {'source': settings.SETPOINT_SOURCE_NAMESPACE, 'canonical': settings.OSH_NAMESPACE,
                                        'scope': 'Only the six publisher SetpointHistory files, by their documented room/actuator local names. Original URIs are retained.',
                                        'source_uris_are_equal': False},
            'unverified_channel_ids': ['Toilet_ThermostatTemperature'],
            'source_documentation_discrepancies': ['README names Room 2 as the two-thermostat room; CSV filenames and RDF metadata identify left and right thermostats in Room 3. The application follows the concrete CSV/RDF association and preserves this discrepancy.'],
            'calibration': 'No device calibration or measurement accuracy is independently established by this application.',
            'sensor_locations': 'The IFC contains no IfcSensor instances. Associations terminate at actual IfcSpace GUIDs; sensor positions and device GUIDs are not invented.',
            'rdf_audit': info['rdf_audit'], 'timestamp_basis': settings.TIMESTAMP_BASIS,
        }
        metadata = {'schema_version': SCHEMA_VERSION, 'ifc_sha256': info['ifc_sha256'],
                    'source_manifest_sha256': manifest_sha256,
                    'source_commit': info['commit'], 'imported_at_utc': datetime.now(timezone.utc).isoformat(),
                    'quality': quality_report, 'source': info}
        conn.executemany('INSERT INTO metadata VALUES (?,?)', ((key,json.dumps(value)) for key,value in metadata.items()))
        conn.commit()
        conn.close()
        os.replace(staging, settings.DB_PATH)
    except Exception:
        try:
            conn.close()
        except (NameError, sqlite3.Error):
            pass
        staging.unlink(missing_ok=True)
        raise
    return summary()


def _channel(row: sqlite3.Row | dict) -> dict:
    result = dict(row)
    result['mapping_verified'] = bool(result['mapping_verified'])
    result['first_timestamp'] = _iso(result.pop('first_ts'))
    result['last_timestamp'] = _iso(result.pop('last_ts'))
    result['source_url'] = f'https://raw.githubusercontent.com/TechnicalBuildingSystems/OpenSmartHomeData/{settings.SOURCE_COMMIT}/' + quote(result['source_csv'])
    if result['source_rdf']:
        result['rdf_source_url'] = f'https://raw.githubusercontent.com/TechnicalBuildingSystems/OpenSmartHomeData/{settings.SOURCE_COMMIT}/' + quote(result['source_rdf'])
    result['physical_sensor_connection'] = False
    return result


def rooms() -> list[dict]:
    with connection() as conn:
        rows = conn.execute('''SELECT r.*,COUNT(c.id) AS verified_channels,
            (SELECT COUNT(*) FROM channels x WHERE x.candidate_room_guid=r.guid AND x.mapping_verified=0) AS candidate_channels
            FROM rooms r LEFT JOIN channels c ON c.room_guid=r.guid AND c.mapping_verified=1
            GROUP BY r.guid ORDER BY r.ifc_step_id''').fetchall()
    return [dict(row) for row in rows]


def channels(room_guid: str | None = None, include_unverified: bool = False) -> list[dict]:
    clauses, parameters = [], []
    if room_guid:
        clauses.append('(room_guid=? OR candidate_room_guid=?)' if include_unverified else 'room_guid=?')
        parameters.extend([room_guid, room_guid] if include_unverified else [room_guid])
    if not include_unverified:
        clauses.append('mapping_verified=1')
    where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
    with connection() as conn:
        rows = conn.execute('SELECT * FROM channels' + where + ' ORDER BY source_room_id,metric,id', parameters).fetchall()
    return [_channel(row) for row in rows]


def get_channel(channel_id: str) -> dict:
    with connection() as conn:
        row = conn.execute('SELECT * FROM channels WHERE id=?', (channel_id,)).fetchone()
    if row is None:
        raise KeyError('Unknown source channel: ' + channel_id)
    return _channel(row)


def summary() -> dict:
    with connection() as conn:
        counts = dict(conn.execute('SELECT COUNT(*) AS observations,MIN(observed_at) AS first_ts,MAX(observed_at) AS last_ts FROM observations').fetchone())
        channel_counts = dict(conn.execute('SELECT COUNT(*) AS channels,SUM(mapping_verified) AS verified_channels FROM channels').fetchone())
        kinds = dict(conn.execute('SELECT source_kind,COUNT(*) FROM channels GROUP BY source_kind'))
        meta = {row['key']: json.loads(row['value']) for row in conn.execute('SELECT key,value FROM metadata WHERE key IN ("ifc_sha256","source_commit","imported_at_utc")')}
        room_counts = dict(conn.execute('SELECT COUNT(*) AS spaces,COUNT(DISTINCT c.room_guid) AS monitored_spaces FROM rooms r LEFT JOIN channels c ON c.room_guid=r.guid').fetchone())
        # COUNT(*) above would count joined channels; the space count is independent.
        room_counts['spaces'] = conn.execute('SELECT COUNT(*) FROM rooms').fetchone()[0]
    return {'title': 'Connected building evidence demonstrator', 'source_title': 'Open Smart Home Data Set',
            'observations': counts['observations'], 'total_readings': counts['observations'],
            **channel_counts, **room_counts, 'channel_kinds': kinds,
            'first_timestamp': _iso(counts['first_ts']), 'last_timestamp': _iso(counts['last_ts']),
            **meta, 'model_sha256': meta['ifc_sha256'], 'licence': 'CC BY-SA 4.0',
            'source_url': 'https://github.com/TechnicalBuildingSystems/OpenSmartHomeData/tree/' + settings.SOURCE_COMMIT,
            'timestamp_basis': settings.TIMESTAMP_BASIS, 'historical_measurements': True,
            'physical_sensor_connection': False, 'live_bms_connection': False,
            'mapping_scope': 'Publisher room associations linked to the pinned IFC spaces; sensor coordinates and IFC device instances are unavailable.',
            'unverified_channels': channel_counts['channels']-channel_counts['verified_channels']}


def series(channel_id: str, start: str | int | None = None, end: str | int | None = None, limit: int = 1000) -> dict:
    if isinstance(limit, bool) or not isinstance(limit,int) or not 1 <= limit <= 10000:
        raise ValueError('limit must be an integer between 1 and 10,000.')
    return _series(channel_id,start,end,limit)


def export_series(channel_id: str, start: str | int | None = None, end: str | int | None = None, max_rows: int = 50000) -> dict:
    """Return an exact complete channel interval, or fail rather than sample it."""
    if isinstance(max_rows,bool) or not isinstance(max_rows,int) or not 1 <= max_rows <= 50000:
        raise ValueError('max_rows must be an integer between 1 and 50,000.')
    result = _series(channel_id,start,end,max_rows)
    if result['total']>max_rows:
        raise ValueError('The complete export exceeds max_rows. Narrow the date interval.')
    return result


def _series(channel_id: str, start: str | int | None, end: str | int | None, limit: int) -> dict:
    channel = get_channel(channel_id)
    first, last = parse_time(start), parse_time(end)
    if first is not None and last is not None and first > last:
        raise ValueError('start must not be after end.')
    where, parameters = ['channel_id=?'], [channel_id]
    if first is not None:
        where.append('observed_at>=?'); parameters.append(first)
    if last is not None:
        where.append('observed_at<=?'); parameters.append(last)
    with connection() as conn:
        rows = conn.execute('SELECT id,observed_at,value,source_line FROM observations WHERE ' + ' AND '.join(where) + ' ORDER BY observed_at,id', parameters).fetchall()
    total = len(rows)
    original_rows = rows
    indices = list(range(total))
    if total > limit:
        indices = [int(i*(total-1)/(limit-1)) for i in range(limit)] if limit > 1 else [0]
        rows = [rows[index] for index in indices]
    gap_enabled = channel['source_kind'] != 'controller_setpoint'
    readings = []
    previous_index = None
    for index, row in zip(indices,rows):
        maximum_step = None
        if gap_enabled and previous_index is not None:
            maximum_step = max(original_rows[i]['observed_at']-original_rows[i-1]['observed_at']
                               for i in range(previous_index+1,index+1))
        readings.append({'id': row['id'], 'timestamp': _iso(row['observed_at']), 'epoch': row['observed_at'],
                         'value': row['value'], 'source_line': row['source_line'], 'gap_before_seconds': maximum_step})
        previous_index = index
    return {'channel': channel, 'channel_id': channel_id, 'metric': channel['metric'], 'unit': channel['unit'],
            'total': total, 'returned': len(readings),
            'sampling': 'All original observations in the selected interval.' if total <= limit else 'Evenly spaced source-row selection including the first and last rows; no values are averaged or invented.',
            'timestamp_basis': settings.TIMESTAMP_BASIS, 'start': _iso(first) if first is not None else None,
            'end': _iso(last) if last is not None else None,
            'gap_rule': {'enabled': gap_enabled, 'threshold_seconds': 3600,
                         'definition': 'gap_before_seconds is the maximum consecutive original source-epoch step between the previous returned row and this returned row, not the time between displayed points. First point is null. Controller setpoint events are excluded from the sensor cadence rule.'},
            'historical_measurements': True, 'readings': readings}


def room_evidence(guid: str) -> dict:
    with connection() as conn:
        row = conn.execute('SELECT * FROM rooms WHERE guid=?', (guid,)).fetchone()
    if row is None:
        raise KeyError('Unknown IFC space GUID: ' + guid)
    room = dict(row)
    verified_channels = channels(guid)
    chain = []
    if verified_channels:
        chain.extend([
            {'step': 1, 'relation': 'CSV row equals publisher RDF observation by row index, epoch and value', 'source': settings.AUDIT_NAME},
            {'step': 2, 'relation': 'sosa:hasFeatureOfInterest; raw namespace retained per channel and explicitly normalised where needed', 'object': settings.OSH_NAMESPACE + room['room_id'], 'source': 'MeasurementsTurtle/*.ttl'},
        ])
    chain.extend([
        {'step': len(chain)+1, 'relation': 'skos:related', 'object': settings.IFC_NAMESPACE + 'IfcSpace_' + str(room['ifc_step_id']), 'source': '01_LinkOsh.ttl'},
        {'step': len(chain)+2, 'relation': 'GlobalId extracted from pinned IFC STEP entity', 'object': guid, 'source': '04_Flat.ifc', 'sha256': room['model_sha256']},
    ])
    return {'room': room, 'channels': verified_channels, 'has_verified_observations': bool(verified_channels),
            'unverified_candidates': [item for item in channels(guid, True) if not item['mapping_verified']],
            'chain': chain,
            'channel_chains': [{'channel_id':item['id'],'source_csv':item['source_csv'],'source_rdf':item['source_rdf'],
                                'source_csv_sha256':item['source_sha256'],'source_rdf_sha256':item['source_rdf_sha256'],
                                'raw_feature_uri':item['raw_feature_uri'],'canonical_feature_uri':item['canonical_feature_uri'],
                                'sensor_or_actuator_uri':item['device_uri'],'mapping_status':item['mapping_status'],
                                'namespace_normalisation_applied':item['raw_feature_uri'] != item['canonical_feature_uri']} for item in verified_channels],
            'mapping_statement': ('Source-supported room-level association. This does not establish sensor position, calibration or a current physical connection.' if verified_channels else 'Publisher alignment identifies this IFC space. No verified sensor observations are supplied for it.'),
            'source_commit': settings.SOURCE_COMMIT, 'model_sha256': room['model_sha256']}


def quality() -> dict:
    with connection() as conn:
        row = conn.execute('SELECT value FROM metadata WHERE key="quality"').fetchone()
    return json.loads(row['value'])
