"""Paths for the pinned public source and derived local application stores."""
from pathlib import Path
import os

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get('CONNECTED_BUILDING_DATA_DIR', ROOT / 'data')).resolve()
SOURCE_DIR = ROOT / 'data' / 'source'
DB_PATH = Path(os.environ.get('CONNECTED_BUILDING_DB', DATA_DIR / 'connected_building.sqlite3')).resolve()
LIVE_SENSOR_DB_PATH = Path(os.environ.get('TWIN_SENSOR_DB', DATA_DIR / '20261018_v1_live_sensor.sqlite3')).resolve()
GRAPH_PATH = Path(os.environ.get('CONNECTED_BUILDING_GRAPH', DATA_DIR / 'semantic_graph')).resolve()
SOURCE_IFC = SOURCE_DIR / '04_Flat.ifc'
MANIFEST_NAME = '20261018_v1_Manifest.json'
AUDIT_NAME = '20261018_v1_Exact_Mapping_and_Data_Audit.json'
EXTRACTS_NAME = '20261018_v1_RDF_Observation_Extracts.json'
SOURCE_COMMIT = '34acb1fb7a24005ddf29f7f232081713c2f78291'
OSH_NAMESPACE = 'https://w3id.org/ibp/osh/OpenSmartHomeDataSet#'
SETPOINT_SOURCE_NAMESPACE = 'https://www.w3id.org/ibp/OpenSmartHomeDataSet#'
IFC_NAMESPACE = 'http://linkedbuildingdata.net/ifc/resources20180427_085111/'
TIMESTAMP_BASIS = 'Source Unix seconds converted to UTC; source RDF offsets independently cross-checked against the same epochs.'
BUILD_VERSION = '20261018-v1'
