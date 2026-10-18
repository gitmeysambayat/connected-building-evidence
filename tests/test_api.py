"""Real-source API agreement and bounded-query negative cases.

Databases, graph, model artefacts, geometry cache and MQTT receipt store are
isolated in the test temporary directory. The source IFC and CSVs are unchanged.
The real TCP broker is exercised separately by the transport integration tests.
"""

from __future__ import annotations

import csv
import hashlib
from io import StringIO
from urllib.parse import quote

from fastapi.testclient import TestClient
import pytest

from twin import analytics, geometry, main, semantic, settings
from twin.transport import HistoricalTransport


@pytest.fixture(scope="module")
def isolated_services(tmp_path_factory):
    directory = tmp_path_factory.mktemp("api-services")
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("TWIN_DISABLE_MQTT", "1")
        patch.setenv("TWIN_GEOMETRY_PATH", str(directory / "geometry.json"))
        patch.setattr(settings, "DB_PATH", directory / "measured.sqlite3")
        patch.setattr(settings, "GRAPH_PATH", directory / "semantic-graph")
        patch.setattr(analytics, "OUTPUT", directory / "temperature-experiment.json")
        patch.setattr(main, "transport", HistoricalTransport(runtime=directory / "mqtt"))
        yield directory
        semantic.close()


@pytest.fixture
def client(isolated_services):
    with TestClient(main.app, raise_server_exceptions=False) as session:
        yield session


def _graphql(client, query, variables=None):
    response = client.post("/graphql", json={"query": query, "variables": variables or {}})
    assert response.status_code == 200
    return response.json()


def test_summary_reports_real_source_and_disabled_transport_cleanly(client):
    response = client.get("/api/summary")
    assert response.status_code == 200
    summary = response.json()
    assert summary["spaces"] == 9
    assert summary["monitored_spaces"] == 6
    assert summary["channels"] == 37
    assert summary["verified_channels"] == 36
    assert summary["unverified_channels"] == 1
    assert summary["geometry"]["source_ifc_sha256"] == summary["model_sha256"]
    assert summary["historical_measurements"] is True
    assert summary["physical_sensor_connection"] is False
    assert summary["live_bms_connection"] is False
    assert summary["deployment"]["cloud_deployment_verified"] is False
    assert summary["transport"]["broker"] == "stopped"
    assert summary["transport"]["persistent_unique_events"] == 0
    assert summary["transport"]["model_sha256"] == summary["model_sha256"]
    health = client.get("/health").json()
    assert health["source_model_sha256"] == summary["model_sha256"]


def test_rest_graphql_and_actual_csv_agree_at_real_source_rows(client):
    channel_id = "Kitchen_Temperature"
    rows = list(csv.reader(StringIO((settings.SOURCE_DIR / "Measurements" / (channel_id + ".csv")).read_text(encoding="utf-8")), delimiter="\t"))
    expected = rows[10:20]
    start, end = int(expected[0][0]), int(expected[-1][0])
    response = client.get(f"/api/series/{channel_id}", params={"start": start, "end": end, "limit": 100})
    assert response.status_code == 200
    rest = response.json()
    assert rest["total"] == rest["returned"] == len(expected)
    assert [row["epoch"] for row in rest["readings"]] == [int(row[0]) for row in expected]
    assert [row["value"] for row in rest["readings"]] == [float(row[1]) for row in expected]
    assert [row["source_line"] for row in rest["readings"]] == list(range(11, 21))
    result = _graphql(client, """query Readings($id: String!, $start: String!, $end: String!) {
      channel(id: $id) {
        id roomGuid mappingVerified sourceSha256 modelSha256
        readings(start: $start, end: $end, limit: 100) { timestamp epoch value sourceLine }
      }
    }""", {"id": channel_id, "start": str(start), "end": str(end)})
    assert "errors" not in result
    channel = result["data"]["channel"]
    assert channel["roomGuid"] == rest["channel"]["room_guid"]
    assert channel["mappingVerified"] is True
    assert channel["sourceSha256"] == rest["channel"]["source_sha256"]
    assert channel["modelSha256"] == rest["channel"]["model_sha256"]
    assert channel["readings"] == [{"timestamp": row["timestamp"], "epoch": row["epoch"], "value": row["value"], "sourceLine": row["source_line"]} for row in rest["readings"]]


def test_unmonitored_ifc_spaces_remain_empty_in_both_apis(client):
    rooms = client.get("/api/rooms").json()
    unmonitored = [room for room in rooms if room["verified_channels"] == 0]
    assert len(unmonitored) == 3
    for room in unmonitored:
        response = client.get("/api/channels", params={"room_guid": room["guid"]})
        assert response.status_code == 200
        assert response.json() == []
        result = _graphql(client, "query Empty($guid: String!) { room(guid: $guid) { guid verifiedChannels channels { id } } }", {"guid": room["guid"]})
        assert "errors" not in result
        assert result["data"]["room"] == {"guid": room["guid"], "verifiedChannels": 0, "channels": []}
        evidence = client.get(f'/api/rooms/{room["guid"]}/evidence')
        assert evidence.status_code == 200
        assert evidence.json()["channels"] == []
        assert evidence.json()["semantic_query"]["rows"] == []


def test_filename_only_channel_is_explicit_and_not_promoted_to_room(client):
    channel_id = "Toilet_ThermostatTemperature"
    verified = client.get("/api/channels").json()
    all_channels = client.get("/api/channels", params={"include_unverified": "true"}).json()
    assert channel_id not in {item["id"] for item in verified}
    candidate = next(item for item in all_channels if item["id"] == channel_id)
    assert candidate["mapping_verified"] is False
    assert candidate["mapping_status"] == "inferred_filename_unverified"
    assert candidate["room_guid"] is None
    assert candidate["candidate_room_guid"]
    result = _graphql(client, "query { channel(id: \"Toilet_ThermostatTemperature\") { id roomGuid mappingVerified mappingStatus } }")
    assert "errors" not in result
    assert result["data"]["channel"] == {"id": channel_id, "roomGuid": None, "mappingVerified": False, "mappingStatus": "inferred_filename_unverified"}
    replay = client.post("/api/replay/start", json={"channel_id": channel_id})
    assert replay.status_code == 400
    assert channel_id not in main.transport.source


def test_semantic_room_evidence_uses_same_ifc_guid_and_channel_set(client):
    room = next(item for item in client.get("/api/rooms").json() if item["room_id"] == "Kitchen")
    response = client.get(f'/api/rooms/{room["guid"]}/evidence')
    assert response.status_code == 200
    evidence = response.json()
    assert evidence["model_sha256"] == room["model_sha256"]
    sql_channels = {item["id"] for item in evidence["channels"]}
    graph_rows = evidence["semantic_query"]["rows"]
    assert {item["channelId"] for item in graph_rows} == sql_channels
    assert {item["guid"] for item in graph_rows} == {room["guid"]}
    assert evidence["semantic_query"]["namespace_normalisation"]["source_uris_are_equal"] is False


@pytest.mark.parametrize("parameters, expected_status", [
    ({"limit": 0}, 422),
    ({"limit": 10001}, 422),
    ({"start": "2017-04-01"}, 400),
    ({"start": "2017-05-01T00:00:00Z", "end": "2017-04-01T00:00:00Z"}, 400),
    ({"start": "99999999999999999999999999999999"}, 400),
    ({"start": "not-a-time"}, 400),
])
def test_rest_rejects_unsupported_time_ranges_and_query_sizes(client, parameters, expected_status):
    response = client.get("/api/series/Kitchen_Temperature", params=parameters)
    assert response.status_code == expected_status
    assert "detail" in response.json()


def test_graphql_reading_limit_and_get_query_are_bounded(client):
    result = _graphql(client, 'query { channel(id: "Kitchen_Temperature") { readings(limit: 10001) { value } } }')
    assert result.get("errors")
    assert "limit" in result["errors"][0]["message"]
    response = client.get("/graphql", params={"query": "{ rooms { guid } }"})
    assert response.status_code in (400, 405)


def test_graphql_rejects_excessive_aliases_before_resolving(client):
    query = "query { " + " ".join(f"a{i}: sourceSummary" for i in range(21)) + " }"
    result = _graphql(client, query)
    assert result.get("errors")
    assert result.get("data") is None
    assert "alias" in " ".join(item["message"] for item in result["errors"]).lower()


def test_graphql_aliases_share_a_request_budget_and_new_requests_reset_it(client):
    result = _graphql(client, """query CombinedBudget {
      a: channel(id: "Kitchen_Temperature") { readings(limit: 10000) { value } }
      b: channel(id: "Room1_Temperature") { readings(limit: 10000) { value } }
      c: channel(id: "Room2_Temperature") { readings(limit: 1) { value } }
    }""")
    assert result.get("errors")
    assert "20,000" in " ".join(item["message"] for item in result["errors"])
    assert result["data"]["c"] is None
    assert len(result["data"]["a"]["readings"]) == 10000
    assert len(result["data"]["b"]["readings"]) == 10000
    # The budget is scoped to this request, rather than retained globally or
    # reset for each aliased field, channel or room.
    fresh = _graphql(client, """query FreshBudget {
      a: channel(id: "Kitchen_Temperature") { readings(limit: 10000) { value } }
      b: channel(id: "Room1_Temperature") { readings(limit: 10000) { value } }
    }""")
    assert "errors" not in fresh
    assert len(fresh["data"]["a"]["readings"]) == 10000
    assert len(fresh["data"]["b"]["readings"]) == 10000


def test_graphql_rejects_excessive_depth_and_token_count(client):
    # The business schema has no recursive relationship. Even an invalid deeply
    # nested request is bounded before execution, rather than being traversed.
    deep = "query TooDeep { " + "rooms { " * 12 + "guid" + " }" * 12 + " }"
    result = _graphql(client, deep)
    assert result.get("errors")
    assert result.get("data") is None
    assert "depth" in " ".join(item["message"] for item in result["errors"]).lower()
    large = "query TooManyTokens { rooms { " + "guid " * 3100 + "} }"
    response = client.post("/graphql", json={"query": large})
    assert response.status_code in (200, 400)
    result = response.json()
    assert result.get("errors")
    assert result.get("data") is None
    assert "token" in " ".join(item["message"] for item in result["errors"]).lower()


@pytest.mark.parametrize("template", ["DROP ALL", "LOAD <https://example.org/private>", "room_alignment; CLEAR ALL", "SERVICE <http://127.0.0.1:1> {}"])
def test_arbitrary_or_mutating_sparql_is_rejected(client, template):
    before = client.get("/api/graph").json()["summary"]["quad_count"]
    response = client.get("/api/graph/query", params={"template": template})
    assert response.status_code == 400
    assert "read-only" in response.json()["detail"]
    assert client.get("/api/graph").json()["summary"]["quad_count"] == before


def test_unknown_and_injection_like_ids_do_not_resolve_to_other_source_records(client):
    response = client.get("/api/channels", params={"room_guid": "' OR 1=1 --"})
    assert response.status_code == 404
    response = client.get("/api/series/" + quote("' OR 1=1 --", safe=""))
    assert response.status_code == 404
    response = client.get("/api/model/elements/not-a-guid")
    assert response.status_code == 404
    result = _graphql(client, "query Missing($id: String!) { channel(id: $id) { id } room(guid: $id) { guid } }", {"id": "' OR 1=1 --"})
    assert "errors" not in result
    assert result["data"] == {"channel": None, "room": None}


def test_a_real_space_guid_from_another_building_cannot_acquire_flat_measurements(client):
    # This is the published AccesS CERTH Living Room GUID, independently read
    # from certh_smarthouse_bim_file.ifc. It is valid IFC syntax but belongs to
    # a different physical building and must not be assigned this flat's data.
    foreign_guid = "1F8oBIUbz1LwYG6clvlyFY"
    assert client.get("/api/channels", params={"room_guid": foreign_guid}).status_code == 404
    assert client.get(f"/api/rooms/{foreign_guid}/evidence").status_code == 404
    assert client.get(f"/api/model/elements/{foreign_guid}").status_code == 404
    result = _graphql(client, "query Foreign($guid: String!) { room(guid: $guid) { guid channels { id } } }", {"guid": foreign_guid})
    assert "errors" not in result
    assert result["data"]["room"] is None


def test_valid_interval_without_observations_returns_no_invented_values(client):
    parameters = {"start": "2020-01-01T00:00:00Z", "end": "2020-01-02T00:00:00Z"}
    response = client.get("/api/series/Kitchen_Temperature", params=parameters)
    assert response.status_code == 200
    assert response.json()["total"] == response.json()["returned"] == 0
    assert response.json()["readings"] == []
    result = _graphql(client, 'query { channel(id: "Kitchen_Temperature") { readings(start: "2020-01-01T00:00:00Z", end: "2020-01-02T00:00:00Z") { value } } }')
    assert "errors" not in result
    assert result["data"]["channel"]["readings"] == []


def test_cross_origin_posts_and_invalid_replay_parameters_are_rejected(client):
    response = client.post("/api/replay/stop", headers={"Origin": "https://unrelated.example"})
    assert response.status_code == 403
    response = client.post("/graphql", headers={"Origin": "https://unrelated.example"}, json={"query": "{ rooms { guid } }"})
    assert response.status_code == 403
    response = client.post("/graphql", headers={"Origin": "http://testserver"}, json={"query": "{ rooms { guid } }"})
    assert response.status_code == 200
    assert "errors" not in response.json()
    for body in ({"speed": 101}, {"limit": 2001}, {"speed": 0}, {"unknown": True}):
        response = client.post("/api/replay/start", json=body)
        assert response.status_code == 422
    status = client.get("/api/replay/status")
    assert status.headers["cache-control"] == "no-store"
    assert status.headers["x-content-type-options"] == "nosniff"
    assert "password" not in status.json()


def test_csv_download_preserves_real_rows_and_source_model_provenance(client):
    source = settings.SOURCE_DIR / "Measurements" / "Kitchen_Temperature.csv"
    source_rows = list(csv.reader(StringIO(source.read_text(encoding="utf-8")), delimiter="\t"))
    response = client.get("/api/series/Kitchen_Temperature/export.csv", params={"start": source_rows[10][0], "end": source_rows[19][0]})
    assert response.status_code == 200
    exported = list(csv.DictReader(StringIO(response.text)))
    assert len(exported) == 10
    csv_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    model_sha = hashlib.sha256(settings.SOURCE_IFC.read_bytes()).hexdigest()
    for line_number, row in enumerate(exported, 11):
        assert row["source_sha256"] == csv_sha
        assert row["model_sha256"] == model_sha
        assert row["source_line"] == str(line_number)
        assert row["source_epoch_utc"] == source_rows[line_number - 1][0]
        assert float(row["value"]) == float(source_rows[line_number - 1][1])
        assert row["ifc_space_guid"]
    # A full source channel exceeds the plot sampling limit. Its export must
    # nevertheless contain every original row, rather than a sampled chart.
    complete = client.get("/api/series/Kitchen_Temperature/export.csv")
    assert complete.status_code == 200
    complete_rows = list(csv.DictReader(StringIO(complete.text)))
    assert len(complete_rows) == len(source_rows) == 10435
    assert [int(row["source_epoch_utc"]) for row in complete_rows] == [int(row[0]) for row in source_rows]
    assert [float(row["value"]) for row in complete_rows] == [float(row[1]) for row in source_rows]
    assert [int(row["source_line"]) for row in complete_rows] == list(range(1, len(source_rows) + 1))
    assert {row["source_sha256"] for row in complete_rows} == {csv_sha}
    assert {row["model_sha256"] for row in complete_rows} == {model_sha}
    assert {row["ifc_space_guid"] for row in complete_rows} == {exported[0]["ifc_space_guid"]}


def test_ifc_download_geometry_and_rest_model_share_exact_source_hash(client):
    source_bytes = settings.SOURCE_IFC.read_bytes()
    expected_sha = hashlib.sha256(source_bytes).hexdigest()
    model = client.get("/api/model").json()
    geometry_response = client.get("/api/geometry")
    assert geometry_response.status_code == 200
    assert geometry_response.json()["source_ifc_sha256"] == expected_sha
    assert geometry_response.headers["etag"] == '"' + expected_sha + '"'
    assert model["identity"]["source_ifc_sha256"] == expected_sha
    assert model["versions"][0]["ifc_sha256"] == expected_sha
    downloaded = client.get("/api/model/source.ifc")
    assert downloaded.status_code == 200
    assert downloaded.content == source_bytes


def test_startup_rejects_geometry_from_a_different_ifc(isolated_services, monkeypatch):
    monkeypatch.setattr(geometry, "get_geometry", lambda: {"source_ifc_sha256": "f" * 64})
    with pytest.raises(ValueError, match="different IFC source hashes"):
        with TestClient(main.app):
            pass



def test_public_graph_and_project_apis_report_ephemeral_runtime_without_self_certification(client, monkeypatch):
    from twin.public_demo import PublicDemo
    monkeypatch.setenv('TWIN_PUBLIC_DEMO', '1')
    monkeypatch.setenv('TWIN_DEPLOYMENT_ENVIRONMENT', 'render')
    monkeypatch.setenv('TWIN_STATE_PERSISTENCE', 'ephemeral')
    monkeypatch.setattr(main, 'public_demo', PublicDemo(enabled=True, origin='https://building.example',
                                                      environment='render', persistence='ephemeral'))
    graph_response = client.get('/api/graph')
    assert graph_response.status_code == 200
    graph = graph_response.json()['summary']
    project_response = client.get('/api/summary')
    assert project_response.status_code == 200
    project = project_response.json()
    for summary in (graph, project['semantic']):
        assert summary['environment'] == project['deployment']['environment'] == 'render'
        assert summary['profile'] == project['deployment']['profile'] == 'public_demo'
        assert summary['state_persistence'] == project['deployment']['state_persistence'] == 'ephemeral'
        assert summary['disk_backed'] is True
        assert 'temporary runtime filesystem' in summary['persistence_scope']
        assert 'external evidence' in summary['verification_scope']
        assert not {'persistent', 'local_operation', 'cloud_deployment_verified'} & summary.keys()
    assert 'cloud_deployment_verified' not in project['deployment']
    assert len(client.get('/api/graph/query', params={'template': 'room_alignment'}).json()['rows']) == 9
