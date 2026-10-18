# Connected Building

A reproducible BIM-linked monitoring demonstrator connecting an actual IFC
model to historical measurements from the same published flat. GitHub holds
the source, tests and deployment configuration. The complete Python application
runs on Render; GitHub Pages provides a separate read-only data explorer.

**[Open the full application](https://connected-building-evidence.onrender.com/)**

[Open the read-only GitHub Pages explorer](https://gitmeysambayat.github.io/connected-building-evidence/)

Free hosting sleeps after inactivity. Allow about a minute for a cold start.
Replay receipts and visitor leases use temporary runtime storage and reset on
sleep, restart or redeployment. The pinned source building and measurements
are rebuilt from the repository. See [Render's free-service limits](https://render.com/docs/free).

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

The service is deployed on **Render Free**. All nine external HTTPS checks of
revision `90782821` passed on 18 October 2026. They verified the source IFC,
SQL-backed REST and GraphQL responses, live Oxigraph/SPARQL, visitor controls
and real internal MQTT: 20 source events were accepted, then a broker
replacement and fresh subscriber correctly recognised 20 repeat deliveries.
The [Render verification receipt](reports/20261018_v1_Render_Deployment_Receipt.json)
records the exact source revision, source-row receipt checks and execution scope.

<!-- render-recovery-status:start -->
**Repaired reconnection and repeat-delivery handling passed on Render.** The
receipt also preserves the initial revision's failed restart and the subsequent
provider-level recovery, which reset temporary receipts from 20 to zero.
The [deployment guide](docs/20261018_v1_Public_Cloud_Deployment.md) explains the
recovery sequence and the remaining free-host limits.
<!-- render-recovery-status:end -->

The [Linux container verification for that revision](https://github.com/gitmeysambayat/connected-building-evidence/actions/runs/39123160789)
passed six integration checks under 512 MiB RAM and 0.1 CPU, with a 230.18 MiB
peak and no out-of-memory events. The
[backend verification](https://github.com/gitmeysambayat/connected-building-evidence/actions/runs/39123160782)
passed 140 Python tests. The
[Pages workflow](https://github.com/gitmeysambayat/connected-building-evidence/actions/runs/39123160724)
and cloud-container workflow together passed 21 JavaScript checks: 13 for the
viewer and eight for the full application interface. These are recorded
revision-specific checks, rather than a production reliability guarantee.

## Available application profiles

| Profile | What actually runs |
|---|---|
| **Full hosted application** | The Python service queries SQLite, executes fixed SPARQL against Oxigraph/RocksDB, serves the actual IFC model and original records, and runs bounded historical replay through an authenticated internal MQTT broker and publisher/subscriber clients. Source-derived stores and analyses are prepared during the image build. Replay controls share one short visitor lease; runtime receipts are temporary. |
| **GitHub Pages** | The browser renders exported IFC geometry, reads the original measurement files, checks source hashes and displays preserved quality, semantic and analysis evidence. It supports room selection, date ranges and complete selected-row CSV downloads. It is read-only. |
| **Full local application** | Python imports the original records into SQLite, extracts IFC geometry, maintains an Oxigraph/RocksDB graph, executes bounded SPARQL, serves REST and GraphQL, and runs historical replay through a real local MQTT broker and publisher/subscriber clients. |

GitHub Pages serves static files. Its archived semantic-query and model results
are labelled as snapshots. The hosted application executes the database and
transport services; current physical sensors and building control are absent
from all three profiles. The same implementation can also run locally.

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
its own source snapshot and execution scope. GitHub Actions, Linux-container
and Render receipts provide separate, revision-specific evidence for their
respective execution environments.

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
artefact evidence from personal competence. The matrix describes the earlier
local profile; its cloud-status entries predate the Render deployment. Use the
[deployment guide](docs/20261018_v1_Public_Cloud_Deployment.md) for current hosting evidence.

## Repository and licences

`twin/`, `static/`, `tests/` and `scripts/` contain the complete application;
`data/source/` preserves pinned source bytes; `pages/` contains the separate
browser viewer. Runtime databases, broker credentials and local cloud-hosting
metadata are excluded from version control.

New code is [MIT licensed](LICENSE). Source IFC, CSV, RDF, excerpts and derived
data retain [CC BY-SA 4.0](data/source/LICENSE.md) attribution and terms.
Three.js retains its [MIT notice](static/vendor/THREE-LICENSE.txt).
See [NOTICE](NOTICE). No institutional endorsement is implied.
