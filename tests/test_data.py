"""Source fidelity and identity tests, independently comparing source bytes to SQL."""
from pathlib import Path
from datetime import datetime
import csv
import hashlib
import sqlite3

import ifcopenshell
import pytest
from twin import data, settings


@pytest.fixture(scope='module', autouse=True)
def derived_database(tmp_path_factory):
    patch = pytest.MonkeyPatch()
    patch.setattr(settings, 'DB_PATH', tmp_path_factory.mktemp('measured_sql') / 'test.sqlite3')
    data.initialise(force=True)
    yield
    patch.undo()


def test_all_285954_source_rows_are_unchanged_in_sql():
    count = 0
    with data.connection() as conn:
        for path in sorted((settings.SOURCE_DIR / 'Measurements').glob('*.csv')):
            # Independent CSV parsing, not the production parser.
            with path.open() as stream:
                source = [(int(row[0]),float(row[1]),index) for index,row in enumerate(csv.reader(stream,delimiter='\t'),1)]
            stored = [tuple(row) for row in conn.execute('SELECT observed_at,value,source_line FROM observations WHERE channel_id=? ORDER BY source_line',(path.stem,))]
            assert stored == source
            count += len(stored)
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
    assert count == 285954


def test_room_guid_scope_is_actual_pinned_ifc_not_a_named_placeholder():
    model = ifcopenshell.open(settings.SOURCE_IFC)
    actual_hash = hashlib.sha256(settings.SOURCE_IFC.read_bytes()).hexdigest()
    room_records = data.rooms()
    assert len(room_records) == 9
    assert sum(row['verified_channels'] > 0 for row in room_records) == 6
    for row in room_records:
        entity = model.by_id(row['ifc_step_id'])
        assert entity.is_a('IfcSpace')
        assert entity.GlobalId == row['guid']
        assert row['model_sha256'] == actual_hash
    assert len(model.by_type('IfcSensor')) == 0


def test_unmonitored_spaces_do_not_gain_a_sensor_observation_chain():
    lobby = next(room for room in data.rooms() if room['room_id']=='Lobby')
    evidence = data.room_evidence(lobby['guid'])
    assert evidence['has_verified_observations'] is False
    assert evidence['channel_chains'] == []
    assert not any('hasFeatureOfInterest' in link['relation'] for link in evidence['chain'])


def test_unverified_toilet_channel_has_no_asserted_room_or_device_uri():
    channel = data.get_channel('Toilet_ThermostatTemperature')
    assert channel['room_guid'] is None
    assert channel['device_uri'] is None
    assert channel['mapping_verified'] is False
    assert channel['mapping_status'] == 'inferred_filename_unverified'
    assert channel['id'] not in {item['id'] for item in data.channels()}
    assert channel['id'] in {item['id'] for item in data.channels(channel['candidate_room_guid'],include_unverified=True)}


def test_epoch_conversion_matches_explicit_offset_source_rdf():
    series = data.series('Room1_Temperature', limit=1)
    first = series['readings'][0]
    assert first['epoch'] == 1489020690
    assert first['value'] == 19.53
    assert first['timestamp'] == '2017-03-09T00:51:30Z'
    assert int(datetime.fromisoformat('2017-03-09T01:51:30+01:00').timestamp()) == first['epoch']
    assert data.parse_time('2017-03-09T01:51:30+01:00') == first['epoch']
    with pytest.raises(ValueError, match='offset'):
        data.parse_time('2017-03-09T01:51:30')


def test_query_filters_and_sampling_return_only_exact_source_rows():
    result = data.series('Room1_Temperature', start='2017-04-01T00:00:00Z', end='2017-04-02T00:00:00Z',limit=7)
    assert result['total'] > 7 and result['returned'] == 7
    assert all(data.parse_time('2017-04-01T00:00:00Z') <= row['epoch'] <= data.parse_time('2017-04-02T00:00:00Z') for row in result['readings'])
    with data.connection() as conn:
        for row in result['readings']:
            stored = conn.execute('SELECT observed_at,value,source_line FROM observations WHERE id=?',(row['id'],)).fetchone()
            assert tuple(stored) == (row['epoch'],row['value'],row['source_line'])
    with pytest.raises(ValueError):
        data.series('Room1_Temperature',limit=10001)
    with pytest.raises(ValueError):
        data.series('Room1_Temperature',start='2017-05-01T00:00:00Z',end='2017-04-01T00:00:00Z')
    with pytest.raises(KeyError):
        data.series("x' OR 1=1; DROP TABLE rooms;--")


def test_sampled_series_gap_metadata_uses_original_steps_not_display_intervals():
    result = data.series('Room1_Temperature',limit=7)
    assert result['readings'][0]['gap_before_seconds'] is None
    with data.connection() as conn:
        original = list(conn.execute('SELECT id,observed_at FROM observations WHERE channel_id=? ORDER BY observed_at,id',('Room1_Temperature',)))
    by_id = {row['id']:index for index,row in enumerate(original)}
    for left,right in zip(result['readings'],result['readings'][1:]):
        start,end = by_id[left['id']],by_id[right['id']]
        expected = max(original[i]['observed_at']-original[i-1]['observed_at'] for i in range(start+1,end+1))
        assert right['gap_before_seconds'] == expected
        assert right['gap_before_seconds'] < right['epoch']-left['epoch']
    assert result['gap_rule']['enabled'] is True
    assert any(row['gap_before_seconds'] and row['gap_before_seconds']>3600 for row in result['readings'])
    commands = data.series('Room1_SetpointHistory',limit=5)
    assert commands['gap_rule']['enabled'] is False
    assert all(row['gap_before_seconds'] is None for row in commands['readings'])


def test_export_returns_complete_exact_channel_and_never_silently_samples():
    exported = data.export_series('Room1_Temperature')
    assert exported['total'] == exported['returned'] == 10598
    assert exported['readings'][-1]['source_line'] == 10598
    assert len({row['id'] for row in exported['readings']}) == 10598
    with pytest.raises(ValueError,match='complete export'):
        data.export_series('Room1_Temperature',max_rows=10000)
    for invalid in (0,50001,True):
        with pytest.raises(ValueError,match='max_rows'):
            data.export_series('Room1_Temperature',max_rows=invalid)


def test_virtual_weather_is_flagged_as_one_copied_external_series():
    weather = [row for row in data.channels() if row['source_kind'] == 'external_virtual_weather']
    assert len(weather) == 6
    assert len({row['source_sha256'] for row in weather}) == 1
    assert {row['source_rows'] for row in weather} == {3710}
    assert data.quality()['byte_identical_weather_files'][0] == sorted([Path(row['source_csv']).name for row in weather])


def test_setpoint_namespace_normalisation_is_explicit_and_not_uri_identity():
    setpoints = [row for row in data.channels() if row['source_kind'] == 'controller_setpoint']
    assert len(setpoints) == 6
    for row in setpoints:
        assert row['mapping_status'] == 'publisher_rdf_namespace_normalised'
        assert row['raw_feature_uri'].startswith(settings.SETPOINT_SOURCE_NAMESPACE)
        assert row['canonical_feature_uri'].startswith(settings.OSH_NAMESPACE)
        assert row['raw_feature_uri'] != row['canonical_feature_uri']
    assert data.quality()['namespace_normalisation']['source_uris_are_equal'] is False


def test_quality_exposes_irregular_sampling_and_full_upstream_audit_scope():
    quality = data.quality()
    temperature = next(row for row in quality['channels'] if row['id']=='Room1_Temperature')
    assert temperature['median_step_seconds'] == 608
    assert temperature['max_gap_seconds'] == 79275
    assert temperature['gaps_over_one_hour'] > 0
    assert quality['rdf_audit']['rdf_rows_crosschecked'] == 277155
    assert quality['rdf_audit']['timestamp_or_value_mismatches'] == 0
    assert quality['rdf_audit']['full_rdf_files_in_compact_package'] is False


@pytest.mark.parametrize('payload',[b'1489020690\tnan\n',b'1489020690\t1\textra\n',b'not-a-date\t2\n',b''])
def test_malformed_or_non_finite_source_data_are_rejected(payload):
    with pytest.raises(ValueError):
        data.parse_csv(payload,'test.csv')


def test_sql_foreign_keys_and_default_readonly_are_effective():
    with data.connection() as conn:
        assert conn.execute('PRAGMA foreign_keys').fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError,match='readonly'):
            conn.execute('DELETE FROM rooms')


def test_manifest_hash_verification_is_effective():
    modified = data.manifest()
    modified['files'][0]['sha256'] = '0'*64
    with pytest.raises(ValueError,match='hash mismatch'):
        data._verify_sources(modified)


@pytest.mark.parametrize('value',['9'*1000,253402300800,-1,True,'9999-12-31T23:59:59-14:00'])
def test_unsupported_epoch_bounds_are_validation_errors(value):
    with pytest.raises(ValueError):
        data.parse_time(value)


def test_cached_database_is_bound_to_the_full_source_manifest_and_commit():
    with data.connection(readonly=False) as conn:
        conn.execute('UPDATE metadata SET value=? WHERE key="source_manifest_sha256"', ('"stale-source-manifest"',))
        conn.execute('DELETE FROM observations WHERE id=1')
    data.initialise()
    assert data.summary()['observations'] == 285954
    with data.connection() as conn:
        digest = conn.execute('SELECT value FROM metadata WHERE key="source_manifest_sha256"').fetchone()[0]
    assert 'stale-source-manifest' not in digest
