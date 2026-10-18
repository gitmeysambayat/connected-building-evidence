# Connected Building published data explorer

This folder is the read-only GitHub Pages distribution. The public viewer uses
relative asset URLs and works beneath a repository subpath. It contains no
account credentials, server API, database, ingestion control or ChatGPT Site
integration. The complete Python backend is at the repository root.

The original source corpus is pinned to commit
`34acb1fb7a24005ddf29f7f232081713c2f78291` of
[OpenSmartHomeData](https://github.com/TechnicalBuildingSystems/OpenSmartHomeData).
The browser verifies a pinned SHA-256 manifest and checks the source bytes
before displaying series or geometry. These checks establish file integrity
and consistency with committed metadata. They do not establish sensor
calibration, as-built accuracy or trust in an independently compromised host.

The historical records remain in their original TSV-formatted `.csv` files.
Chart display selects original rows and propagates the maximum intervening
original gap. CSV export includes every original observation in the selected
UTC interval, with source lines, units, IFC identity and source hashes.
Setpoint events and repeated virtual weather are labelled separately.

Geometry, data-quality checks, analyses and semantic-query results are frozen
exports from the local application. Every temperature model failed to beat
persistence in validation. Neither training nor SPARQL executes in this viewer.

Run the dependency-free adapter checks from the repository root with Node 22:

```text
node --test --test-reporter=spec pages/tests/20261018_v1_Data_Adapter.test.mjs
```

The report is in `evidence/20261018_v1_Data_Adapter_Test_Report.json`.
Browser and hosting verification are separate from these adapter checks.
New code is MIT; source and data-derived assets retain CC BY-SA 4.0. See
`NOTICE`, `LICENSE`, `seed/LICENSE.md` and `static/vendor/THREE-LICENSE.txt`.
