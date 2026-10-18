# Connected Building

A reproducible BIM-linked monitoring demonstrator connecting an actual IFC
model to historical measurements from the same published flat. The repository
contains the complete Python application and a read-only GitHub Pages viewer.
Neither requires the previous ChatGPT-hosted site.

**[Open the live browser demonstration](https://gitmeysambayat.github.io/connected-building-evidence/)**

The viewer is hosted on GitHub Pages. The [first successful deployment](https://github.com/gitmeysambayat/connected-building-evidence/actions/runs/39110024607/attempts/2)
was checked in a browser, and all 62 data and evidence assets matched their
recorded hashes. Downloaded CSVs were checked against all 10,598 original Room1
temperature records and a selected 52-record Room2 interval.

The [application verification run](https://github.com/gitmeysambayat/connected-building-evidence/actions/runs/39110024621)
passed all 110 tests. The separate [viewer test report](pages/evidence/20261018_v2_Data_Adapter_Test_Report.json)
records 13 passing tests, including room-switching and delayed-response
regressions. See the [current workflow runs](https://github.com/gitmeysambayat/connected-building-evidence/actions)
for later deployments and checks. The [initial verification receipt](reports/20261018_v1_GitHub_Verification.json)
preserves the earlier attempts and their exact execution scope.

## Full application hosting

The complete application is prepared for Render's Free plan. Its
[Linux container verification](https://github.com/gitmeysambayat/connected-building-evidence/actions/runs/39119255027)
passed under 512 MiB RAM and 0.1 CPU, with a 231.72 MiB peak. Real database
queries, MQTT replay, duplicate handling, reconnection and restart checks
passed. The [current backend suite](https://github.com/gitmeysambayat/connected-building-evidence/actions/runs/39119255021)
passed 134 tests.

**Public backend hosting awaits the owner's Render account connection.**
[Review the free deployment on Render](https://render.com/deploy?repo=https%3A%2F%2Fgithub.com%2Fgitmeysambayat%2Fconnected-building-evidence)
or read the [cloud deployment guide](docs/20261018_v1_Public_Cloud_Deployment.md).
This deployment link creates a service; it is not a live application URL.
Free hosting sleeps when idle and resets temporary replay receipts on restart.

## Available application profiles

| Profile | What actually runs |
|---|---|
| **GitHub Pages** | The browser renders exported IFC geometry, reads the original measurement files, checks source hashes and displays preserved quality, semantic and analysis evidence. It supports room selection, date ranges and complete selected-row CSV downloads. It is read-only. |
| **Full local application** | Python imports the original records into SQLite, extracts IFC geometry, maintains an Oxigraph/RocksDB graph, executes bounded SPARQL, serves REST and GraphQL, and runs historical replay through a real local MQTT broker and publisher/subscriber clients. |

GitHub Pages does not run the Python service, a database, MQTT, live SPARQL,
authenticated ingestion or model fitting. Archived semantic-query and model
results in the viewer are labelled as such. The full implementation remains
in this repository and can be run locally without an external account.

Use the [GitHub demonstration guide](docs/20261018_v1_GitHub_Demonstration.md)
for a short walkthrough, and the [full backend guide](docs/20261018_v1_Local_Backend.md)
for architecture, API examples, methods and detailed setup.

## One building, traceable evidence

The source is [Open Smart Home Data](https://github.com/TechnicalBuildingSystems/OpenSmartHomeData/tree/34acb1fb7a24005ddf29f7f232081713c2f78291),
pinned to commit `34acb1fb7a24005ddf29f7f232081713c2f78291`.
It supplies the IFC, room alignment, RDF metadata and original measurement files
together. The [source audit](data/source/20261018_v1_Exact_Mapping_and_Data_Audit.json)
and [manifest](data/source/20261018_v1_Manifest.json) record:

- Nine IFC spaces, six with associated monitoring records.
- 285,954 original records across 37 channels, dated March to June 2017.
- Thirty-six publisher-RDF-supported channel associations. The remaining
  Toilet thermostat filename suggestion stays unverified.
- One exact IFC identity, SHA-256
  `42ffba5ac38db6def62df2c1ea000dce1d2139dc0100e16e1ede8ee0562fef03`.

The model contains no `IfcSensor` instances. The project maps records to source
rooms and does not invent sensor positions. Outdoor records are copied virtual
weather data; recorded setpoints are controller events. The
[quality implementation](twin/data.py) and preserved evidence expose these
qualifications rather than counting every channel as an independent sensor.

## Run the full application

Use Python 3.12. On Windows, from the repository root:

```powershell
python -m venv "$env:USERPROFILE\venvs\connected-building"
$py = "$env:USERPROFILE\venvs\connected-building\Scripts\python.exe"
& $py -m pip install -r 20261018_v1_requirements_lock.txt
$env:CONNECTED_BUILDING_PYTHON = $py
.\20261018_v1_Start.ps1 -Port 8775
```

Open [http://127.0.0.1:8775/](http://127.0.0.1:8775/).
The launcher keeps mutable state outside the checkout. Stop its recorded service
with `.\20261018_v1_Stop.ps1`. For manual Python startup, custom state paths and
the optional full upstream RDF download, follow the
[backend guide](docs/20261018_v1_Local_Backend.md#reproduce-from-source).

To rebuild and test after installation, stop the service first:

```powershell
& $py scripts/build_data.py
& $py scripts/build_geometry.py
& $py scripts/build_analytics.py
& $py scripts/20261018_v1_Verify.py
```

The [archived local test report](reports/20261018_v1_Test_Results.json) records
its own source snapshot and execution scope. Current GitHub Actions results are
separate evidence. Prepared Docker files do not establish that a container has
been executed or deployed.

## What the analysis supports

The [forecast experiment](twin/analytics.py) uses chronological training,
validation and test periods with purged target boundaries. **All six validation
gates select persistence over Ridge.** The rejected candidates and later test
results are retained in the
[forecast report](data/derived/20261018_v1_temperature_forecast.json).
No successful model or energy-saving result is claimed.

The [heating-command review](twin/operations.py) compares measured air
temperature with the last recorded same-room command, at most 24 hours old.
Its illustrative 1 degree Celsius band is not an established comfort or fault
criterion. There is no current physical sensor/BMS connection, building control,
independent calibration, surveyed as-built validation or cross-building
validation in this project.

The project was assembled with AI assistance. Its working capabilities do not
establish the owner's independent proficiency, qualifications or prior
experience. The [learning exercises](docs/20261018_v1_Demonstration_and_Learning.md)
and [role-evidence matrix](docs/20261018_v1_Role_Evidence_Matrix.json) distinguish
artefact evidence from personal competence.

## Repository and licences

`twin/`, `static/`, `tests/` and `scripts/` contain the complete application;
`data/source/` preserves pinned source bytes; `pages/` contains the separate
browser viewer. Runtime databases, broker credentials and local cloud-hosting
metadata are excluded from version control.

New code is [MIT licensed](LICENSE). Source IFC, CSV, RDF, excerpts and derived
data retain [CC BY-SA 4.0](data/source/LICENSE.md) attribution and terms.
Three.js retains its [MIT notice](static/vendor/THREE-LICENSE.txt).
See [NOTICE](NOTICE). No institutional endorsement is implied.
