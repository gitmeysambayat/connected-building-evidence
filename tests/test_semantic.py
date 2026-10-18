"""Execute actual SPARQL against the persisted NoSQL graph and reopen it."""
import pytest
from twin import data, semantic, settings


@pytest.fixture(scope='module', autouse=True)
def graph_database(tmp_path_factory):
    root = tmp_path_factory.mktemp('semantic_store')
    patch = pytest.MonkeyPatch()
    patch.setattr(settings,'DB_PATH',root/'source.sqlite3')
    patch.setattr(settings,'GRAPH_PATH',root/'rocksdb')
    data.initialise()
    semantic.initialise()
    yield
    semantic.close()
    patch.undo()


def test_actual_nine_room_alignment_query_matches_sql_ifc_guids():
    result = semantic.query('room_alignment')
    assert len(result['rows']) == 9
    assert {row['guid'] for row in result['rows']} == {row['guid'] for row in data.rooms()}
    assert {row['modelSha256'] for row in result['rows']} == {data.summary()['model_sha256']}


def test_verified_channel_query_crosses_source_and_derived_named_graphs():
    result = semantic.query('verified_channels')
    assert len(result['rows']) == 36
    expected = {row['id']:row for row in data.channels()}
    for row in result['rows']:
        assert row['guid'] == expected[row['channelId']]['room_guid']
        assert row['sourceFeature'] == expected[row['channelId']]['raw_feature_uri']
        assert row['sourceDevice'] == expected[row['channelId']]['device_uri']
    room = next(row for row in data.rooms() if row['room_id']=='Room3')
    evidence = semantic.room_evidence(room['guid'])
    assert len(evidence['rows']) == 7
    assert {row['guid'] for row in evidence['rows']} == {room['guid']}


def test_unverified_channel_does_not_gain_a_guid_via_the_graph():
    result = semantic.query('unverified_channels')
    assert result['rows'] == [{'channelId':'Toilet_ThermostatTemperature','candidateRoomId':'Toilet','mappingStatus':'inferred_filename_unverified'}]
    assert 'Toilet_ThermostatTemperature' not in {row['channelId'] for row in semantic.query('verified_channels')['rows']}


def test_graph_is_a_persisted_rocksdb_database_and_survives_reopening():
    before = semantic.query('room_alignment')['rows']
    summary = semantic.summary()
    assert summary['quad_count'] > 1000
    assert summary['database_type'] == 'RDF graph / NoSQL'
    assert len(summary['named_graphs']) == 5
    assert any(path.name.startswith('MANIFEST') for path in settings.GRAPH_PATH.iterdir())
    semantic.close()
    assert semantic.query('room_alignment')['rows'] == before


def test_source_room_relation_is_not_promoted_to_same_as():
    result = semantic.store().query(f'ASK {{ GRAPH <{semantic.DERIVED_GRAPH}> {{ ?s <http://www.w3.org/2002/07/owl#sameAs> ?o }} }}')
    assert bool(result) is False
    with pytest.raises(ValueError,match='fixed read-only'):
        semantic.query('DELETE WHERE { ?s ?p ?o }')



def test_public_graph_summary_scopes_disk_backing_to_configured_temporary_runtime(monkeypatch):
    monkeypatch.setenv('TWIN_PUBLIC_DEMO', '1')
    monkeypatch.setenv('TWIN_DEPLOYMENT_ENVIRONMENT', 'render')
    monkeypatch.setenv('TWIN_STATE_PERSISTENCE', 'ephemeral')
    summary = semantic.summary()
    assert summary['engine'] == 'Oxigraph'
    assert summary['disk_backed'] is True
    assert summary['environment'] == 'render'
    assert summary['profile'] == 'public_demo'
    assert summary['state_persistence'] == 'ephemeral'
    assert 'temporary runtime filesystem' in summary['persistence_scope']
    assert 'external evidence' in summary['verification_scope']
    assert not {'persistent', 'local_operation', 'cloud_deployment_verified'} & summary.keys()
    # Storage presentation changes do not replace the actual graph with a snapshot.
    assert len(semantic.query('room_alignment')['rows']) == 9
    assert summary['quad_count'] > 1000


def test_default_local_graph_summary_preserves_local_compatibility(monkeypatch):
    monkeypatch.delenv('TWIN_PUBLIC_DEMO', raising=False)
    summary = semantic.summary()
    assert summary['persistent'] is True
    assert summary['local_operation'] is True
    assert summary['cloud_deployment_verified'] is False
