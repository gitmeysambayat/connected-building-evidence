# Connected Building

**Developed by [Meysam Bayat](https://github.com/gitmeysambayat).**

A BIM-linked monitoring application combining IFC geometry, traceable
measurements, relational and semantic databases, REST/GraphQL services and an
interactive dashboard.

**[Open the full application](https://connected-building-evidence.onrender.com/)**
| [GitHub Pages data explorer](https://gitmeysambayat.github.io/connected-building-evidence/)
| [Connect a sensor](docs/20261018_v1_Real_Sensor_Setup.md)

## Monitoring workflows

| Workflow | Implementation |
|---|---|
| Published building case study | Actual IFC spaces, original 2017 observations and publisher RDF from the same flat. Room selection, source-row inspection, complete selected-channel CSV export and reproducible analyses. |
| Semantic integration | Oxigraph/RocksDB stores original source graphs and a separate derived identity graph. Fixed SPARQL queries inspect room alignment and channel associations. |
| Data services | Python/FastAPI REST and typed GraphQL query the same SQLite-backed source catalogue and measurements. |
| Historical transport | An authenticated internal MQTT broker and real publisher/subscriber clients deliver historical source events, record receipts, recognise duplicate deliveries and exercise recovery. |
| Environmental sensor station | A dedicated authenticated HTTPS endpoint, separate station store, current-status dashboard and retained-reading export. ESP32/SHT40 firmware and setup instructions are included. Physical commissioning begins when the device is installed. |

The new station has its own identity. Its observations are stored separately
from the published flat and are not assigned to rooms in that source model.

## Sensor equipment

The recommended solderless kit uses an Adafruit ESP32 Feather V2, an Adafruit
SHT40 temperature/humidity sensor, a 200 mm STEMMA QT cable and a USB data
cable. The [equipment and setup guide](docs/20261018_v1_Real_Sensor_Setup.md)
contains verified product links, wiring, firmware instructions and commissioning
checks. No device credentials are included in the repository or browser code.

The sensor station starts empty. It shows observation time, server receipt time
and freshness once configured hardware reports arrive. A stopped stream becomes
stale. Historical replay does not populate the station.

## Source building and dataset

[Open Smart Home Data](https://github.com/TechnicalBuildingSystems/OpenSmartHomeData/tree/34acb1fb7a24005ddf29f7f232081713c2f78291)
is pinned to commit `34acb1fb7a24005ddf29f7f232081713c2f78291`. It provides the
IFC, room alignment, RDF metadata and original measurement files together.
The [manifest](data/source/20261018_v1_Manifest.json) and
[source audit](data/source/20261018_v1_Exact_Mapping_and_Data_Audit.json) record:

- Nine IFC spaces and six monitored rooms.
- 285,954 original records across 37 channels, from March to June 2017.
- Thirty-six publisher-RDF-supported channel associations and one unresolved
  Toilet thermostat channel, retained separately.
- Original IFC SHA-256
  `42ffba5ac38db6def62df2c1ea000dce1d2139dc0100e16e1ede8ee0562fef03`.

Measured indoor channels, controller setpoints and repeated external weather
feeds retain their source classifications. Spatial joins use the publisher's
documented room relationships. Geometry shows the source building elements and
spaces; the source model contains no sensor coordinates.

### Dataset citation

Schneider, G. F. and Rasmussen, M. H. (2018).
*TechnicalBuildingSystems/OpenSmartHomeData: First release of Open Smart Home
Data Set* (v1.0.0). Zenodo. https://doi.org/10.5281/zenodo.1244602.

The archive citation identifies the first release; the exact Git source used
here is pinned separately above. The source also credits Peter Bonsma, Pieter
Pauwels and Jyrki Oraskari. Measured time series are credited to Fraunhofer
Institute for Building Physics (2018), and RDF metadata to the W3C Linked
Building Data Community Group contributors (2018).

Original source bytes and notices are preserved. IFC geometry extraction,
database imports, explicit room mappings and analyses are documented
transformations. Source and derived data retain
[CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/) terms.

## Analysis and verification

The [temperature-forecast experiment](twin/analytics.py) separates training,
validation and test periods chronologically and purges target boundaries.
All six room validation gates select persistence over Ridge. Candidate and
later test errors remain in the
[forecast report](data/derived/20261018_v1_temperature_forecast.json).

The heating-command review compares each measured air temperature with a
preceding same-room controller command at most 24 hours old. Its illustrative
1 degree Celsius comparison band is a descriptive analysis setting. The
reports retain the original rows, command ages and method definitions.

[GitHub Actions](https://github.com/gitmeysambayat/connected-building-evidence/actions)
runs backend, interface and Linux-container checks. The
[Render deployment receipt](reports/20261018_v1_Render_Deployment_Receipt.json)
records executed external HTTPS, source-integrity, REST/GraphQL, SPARQL and
MQTT checks at their stated revision and time. The
[technical capability record](docs/20261018_v2_Technical_Capability_Record.json)
lists supporting implementation and execution records.

Use the [demonstration guide](docs/20261018_v1_Demonstration_and_Learning.md)
for a short walkthrough and the [backend guide](docs/20261018_v1_Local_Backend.md)
for methods and API examples. Sensor commissioning has its own checklist in
the [device guide](docs/20261018_v1_Real_Sensor_Setup.md).

## Hosting

The full Python application runs on Render Free. GitHub Pages serves a separate
read-only explorer with preserved source files and frozen analysis exports.
The full application executes its database, graph and transport services.

Free hosting sleeps after inactivity, so the first visit can take longer.
Runtime receipts and station observations use temporary storage and can reset
after sleep, restart or redeployment. Export station records when collecting a
commissioning dataset. Pinned source records are rebuilt from the repository.
See [the deployment guide](docs/20261018_v1_Public_Cloud_Deployment.md) and
[Render's service limits](https://render.com/docs/free).

## Run locally

Use Python 3.12 and the pinned requirements. On Windows, from the repository:

```powershell
python -m venv "$env:USERPROFILE\venvs\connected-building"
$py = "$env:USERPROFILE\venvs\connected-building\Scripts\python.exe"
& $py -m pip install -r 20261018_v1_requirements_lock.txt
$env:CONNECTED_BUILDING_PYTHON = $py
.\20261018_v1_Start.ps1 -Port 8775
```

Open <http://127.0.0.1:8775/>. The launcher keeps mutable state outside the
checkout. Use `.\20261018_v1_Stop.ps1` to stop its recorded service. Manual
startup, source rebuilding and optional upstream RDF checks are documented in
the [backend guide](docs/20261018_v1_Local_Backend.md#reproduce-from-source).

## Repository and licences

`twin/`, `static/`, `tests/` and `scripts/` contain the application. `firmware/`
contains the device project, `data/source/` the pinned source bytes, and
`pages/` the read-only explorer. Secrets, runtime stores and private application
documents are excluded from version control.

Application code is [MIT licensed](LICENSE). Source IFC, CSV, RDF, excerpts and
data-derived material retain their [source licence](data/source/LICENSE.md).
Three.js retains its [MIT notice](static/vendor/THREE-LICENSE.txt).
[NOTICE](NOTICE) records attribution and transformations, and
[CITATION.cff](CITATION.cff) supplies citation metadata.
