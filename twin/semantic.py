"""A real persistent RDF/NoSQL graph of the publisher and derived alignment.

Oxigraph stores RDF quads in RocksDB and executes SPARQL. Source assertions,
namespace normalisation and extracted IFC identities occupy separate named
graphs. No owl:sameAs assertion is added for a room association.
"""
from __future__ import annotations

from pathlib import Path
import atexit
import gc
import hashlib
import json
import threading

from pyoxigraph import Store, Quad, NamedNode, Literal, RdfFormat
from twin import settings, data

APP = 'https://example.org/city-connected-building/v1/'
RDF_TYPE = NamedNode('http://www.w3.org/1999/02/22-rdf-syntax-ns#type')
SOURCE_GRAPH = APP + 'graph/source/'
DERIVED_GRAPH = APP + 'graph/derived-alignment'
GRAPH_VERSION = '1'
_STORE: Store | None = None
_STORE_PATH: str | None = None
_LOCK = threading.RLock()


def close() -> None:
    """Release the process's one read-write RocksDB handle."""
    global _STORE, _STORE_PATH
    with _LOCK:
        if _STORE is not None:
            _STORE.flush()
        _STORE = None
        _STORE_PATH = None
        gc.collect()


atexit.register(close)


def store() -> Store:
    global _STORE, _STORE_PATH
    path = str(settings.GRAPH_PATH.resolve())
    with _LOCK:
        if _STORE is not None and _STORE_PATH != path:
            close()
        if _STORE is None:
            settings.GRAPH_PATH.parent.mkdir(parents=True, exist_ok=True)
            _STORE = Store(path)
            _STORE_PATH = path
        return _STORE


def _node(value: str) -> NamedNode:
    return NamedNode(value)


def _quad(subject: str, predicate: str, value: str | bool | int, iri: bool = False) -> Quad:
    return Quad(_node(subject), _node(APP + predicate), _node(str(value)) if iri else Literal(value), _node(DERIVED_GRAPH))


def initialise(force: bool = False) -> dict:
    # Data initialisation independently checks all compact source hashes.
    data.initialise()
    graph = store()
    info = data.manifest()
    manifest_digest = hashlib.sha256((settings.SOURCE_DIR / settings.MANIFEST_NAME).read_bytes()).hexdigest()
    dataset = APP + 'dataset/' + settings.SOURCE_COMMIT
    marker = Quad(_node(dataset), _node(APP + 'graphVersion'), Literal(GRAPH_VERSION), _node(DERIVED_GRAPH))
    source_marker = Quad(_node(dataset),_node(APP+'sourceManifestSha256'),Literal(manifest_digest),_node(DERIVED_GRAPH))
    if marker in graph and source_marker in graph and not force:
        return summary()
    with _LOCK:
        graph.clear()
        for filename in ('00_OpenSmartHomeData.ttl', '01_LinkOsh.ttl', '02_BotFromRevit.ttl', '02_PropsFromRevit.ttl'):
            graph.load(path=str(settings.SOURCE_DIR / filename), format=RdfFormat.TURTLE,
                       to_graph=_node(SOURCE_GRAPH + filename))
        quads = [marker, source_marker, _quad(dataset,'sourceCommit',settings.SOURCE_COMMIT),
                 _quad(dataset,'ifcSha256',info['ifc_sha256']),
                 _quad(dataset,'licence',info['licence']),
                 _quad(dataset,'sourceRepository',info['repository'],True)]
        for room in data.rooms():
            entity = settings.IFC_NAMESPACE + 'IfcSpace_' + str(room['ifc_step_id'])
            scoped = 'urn:city-connected:ifc:' + info['ifc_sha256'] + ':' + room['guid']
            quads.extend([_quad(entity,'ifcGlobalId',room['guid']),
                          _quad(entity,'ifcSha256',info['ifc_sha256']),
                          _quad(entity,'scopedIdentity',scoped,True),
                          _quad(scoped,'roomId',room['room_id'])])
        for channel in data.channels(include_unverified=True):
            subject = APP + 'channel/' + channel['id']
            quads.extend([_quad(subject,'channelId',channel['id']),
                          _quad(subject,'mappingStatus',channel['mapping_status']),
                          _quad(subject,'mappingVerified',channel['mapping_verified']),
                          _quad(subject,'sourceKind',channel['source_kind']),
                          _quad(subject,'sourceCsv',channel['source_csv']),
                          _quad(subject,'sourceSha256',channel['source_sha256']),
                          _quad(subject,'unit',channel['unit'])])
            if channel['device_uri']:
                quads.append(_quad(subject,'sourceDevice',channel['device_uri'],True))
            if channel['raw_feature_uri']:
                quads.append(_quad(subject,'sourceFeature',channel['raw_feature_uri'],True))
            if channel['mapping_verified']:
                quads.append(_quad(subject,'normalisedFeature',channel['canonical_feature_uri'],True))
                if channel['raw_feature_uri'] != channel['canonical_feature_uri']:
                    quads.append(_quad(subject,'normalisationReason','Source SetpointHistory namespace differs; publisher room and actuator local names are aligned explicitly.'))
            else:
                quads.append(_quad(subject,'candidateRoomId',channel['source_room_id']))
        graph.extend(quads)
        graph.flush()
    return summary()


def _term(value):
    if value is None:
        return None
    if isinstance(value, NamedNode):
        return value.value
    if isinstance(value, Literal):
        if value.datatype.value.endswith('#boolean'):
            return value.value in ('true','1')
        if value.datatype.value.endswith(('#integer','#int')):
            return int(value.value)
        return value.value
    return str(value)


def _select(sparql: str) -> dict:
    result = store().query(sparql)
    columns = [variable.value for variable in result.variables]
    return {'columns': columns, 'rows': [{name: _term(row[name]) for name in columns} for row in result]}


def query_templates() -> dict[str, str]:
    return {
        'room_alignment': f'''PREFIX skos: <http://www.w3.org/2004/02/skos/core#>
PREFIX app: <{APP}>
SELECT ?room ?ifcEntity ?guid ?modelSha256 WHERE {{
  GRAPH <{SOURCE_GRAPH}01_LinkOsh.ttl> {{ ?room skos:related ?ifcEntity . }}
  FILTER(STRSTARTS(STR(?room), "{settings.OSH_NAMESPACE}"))
  GRAPH <{DERIVED_GRAPH}> {{ ?ifcEntity app:ifcGlobalId ?guid ; app:ifcSha256 ?modelSha256 . }}
}} ORDER BY ?room LIMIT 100''',
        'verified_channels': f'''PREFIX skos: <http://www.w3.org/2004/02/skos/core#>
PREFIX app: <{APP}>
SELECT ?channelId ?sourceDevice ?sourceFeature ?room ?ifcEntity ?guid ?mappingStatus WHERE {{
  GRAPH <{DERIVED_GRAPH}> {{
    ?channel app:channelId ?channelId ; app:mappingVerified true ; app:normalisedFeature ?room ;
             app:sourceFeature ?sourceFeature ; app:mappingStatus ?mappingStatus .
    OPTIONAL {{ ?channel app:sourceDevice ?sourceDevice . }}
    ?ifcEntity app:ifcGlobalId ?guid .
  }}
  GRAPH <{SOURCE_GRAPH}01_LinkOsh.ttl> {{ ?room skos:related ?ifcEntity . }}
}} ORDER BY ?channelId LIMIT 100''',
        'unverified_channels': f'''PREFIX app: <{APP}>
SELECT ?channelId ?candidateRoomId ?mappingStatus WHERE {{
  GRAPH <{DERIVED_GRAPH}> {{
    ?channel app:channelId ?channelId ; app:mappingVerified false ;
             app:candidateRoomId ?candidateRoomId ; app:mappingStatus ?mappingStatus .
  }}
}} ORDER BY ?channelId LIMIT 100''',
    }


def query(template: str = 'room_alignment') -> dict:
    templates = query_templates()
    if template not in templates:
        raise ValueError('Choose one of the fixed read-only SPARQL templates: ' + ', '.join(templates))
    sparql = templates[template]
    return {'template': template, 'sparql': sparql, **_select(sparql),
            'engine': 'Oxigraph SPARQL over persistent RDF quads in RocksDB',
            'source_and_derived_assertions_separated': True}


def room_evidence(guid: str) -> dict:
    # Parameter validation and room existence come from the SQL record.
    evidence = data.room_evidence(guid)
    literal = str(Literal(guid))
    sparql = query_templates()['verified_channels']
    # A VALUES binding inside the WHERE group is serialised as an RDF literal.
    sparql = sparql.replace('WHERE {', 'WHERE {\n  VALUES ?guid { ' + literal + ' }', 1)
    return {'room': evidence['room'], 'sparql': sparql, **_select(sparql),
            'engine': 'Oxigraph persistent RDF graph database',
            'mapping_scope': 'Room association through original skos:related; extracted IFC GlobalIds belong to the pinned IFC SHA-256.',
            'namespace_normalisation': data.quality()['namespace_normalisation'],
            'unverified_candidates': evidence['unverified_candidates']}


def summary() -> dict:
    graph = store()
    named_graphs = [name.value for name in graph.named_graphs()]
    return {'engine': 'Oxigraph', 'library': 'pyoxigraph', 'storage': 'Persistent RocksDB key-value database',
            'database_type': 'RDF graph / NoSQL', 'query_language': 'SPARQL 1.1',
            'quad_count': len(graph), 'named_graphs': sorted(named_graphs),
            'persistent': True, 'local_operation': True, 'cloud_deployment_verified': False,
            'assertion_policy': 'Original publisher RDF lives in source named graphs. Extracted IFC GUIDs and explicit namespace normalisation live in a separate derived graph. No owl:sameAs assertion is fabricated.',
            'source_commit': settings.SOURCE_COMMIT}
