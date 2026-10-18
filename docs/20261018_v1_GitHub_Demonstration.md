# Connected Building: GitHub demonstration

This repository has two execution profiles built around the same published
flat. The GitHub Pages viewer is a read-only browser demonstration. The Python
application supplies the live local services. Neither profile needs the former
ChatGPT-hosted site.

The Pages address is
[gitmeysambayat.github.io/connected-building-evidence](https://gitmeysambayat.github.io/connected-building-evidence/).
Publication status must be established by the actual deployment and subsequent
browser checks. This guide does not treat an expected address as a deployment
receipt.

## Five minutes in the browser

1. **Identify the building.** Open the IFC view and inspect its nine real spaces.
   Select Room 1 and inspect its published IFC GlobalId
   `05i4VutGDCsQKCrT6CQvhu`. The geometry is a preserved extraction of the
   source IFC, not an illustrative building model.
2. **Follow a measurement.** Select room air temperature, change the date range
   and inspect the reported source and returned-row counts. Download all rows
   in the selected range. Display sampling selects original rows; the complete
   export must not silently become a sample.
3. **Inspect a limitation.** Select an unmonitored space such as Lobby, then
   inspect the unresolved Toilet thermostat association and the virtual outdoor
   feed. Room membership does not establish a physical sensor position.
4. **Check the evidence.** Inspect source hashes, the publisher's room alignment
   and the preserved semantic-query output. In this profile the graph response
   is a snapshot of a query executed by the local application. The browser does
   not run a persistent RDF database.
5. **Explain the analysis.** Open the temperature experiment. Validation favours
   persistence in all six rooms, so Ridge remains a rejected candidate. Inspect
   the heating-command comparison and explain why its observation-weighted
   fractions cannot establish comfort, faults or energy savings.

Source counts, mappings and qualifications are documented in the
[source audit](../data/source/20261018_v1_Exact_Mapping_and_Data_Audit.json),
[source manifest](../data/source/20261018_v1_Manifest.json) and
[forecast report](../data/derived/20261018_v1_temperature_forecast.json).
The source commit is
[`34acb1fb7a24005ddf29f7f232081713c2f78291`](https://github.com/TechnicalBuildingSystems/OpenSmartHomeData/tree/34acb1fb7a24005ddf29f7f232081713c2f78291).

## Show the engineering behind the viewer

Run the [full local application](20261018_v1_Local_Backend.md#reproduce-from-source)
and use its local address for the following exercises. GitHub Pages cannot
perform them on its own.

| Exercise | Evidence to inspect |
|---|---|
| Query one room through REST and GraphQL | Same source GUID, channel, exact original value and source line in SQLite and both responses. |
| Execute a fixed SPARQL template | Actual query against persistent Oxigraph/RocksDB, with source and derived named graphs distinguished. |
| Replay historical measurements | Actual publisher and subscriber clients communicate through the authenticated local MQTT broker. |
| Repeat a replay interval | Durable source-event identities prevent duplicate acceptance; delivery and unique-event counts remain distinct. |
| Restart the broker after stopping replay | Inspect reconnection and retained receipt history rather than inferring recovery from a status badge. |
| Rebuild the temperature experiment | Chronological partitions, target-boundary purging, validation selection and frozen later test scores can be inspected in the implementation and reports. |

Historical `observed_at` timestamps belong to the 2017 source records. A new
`received_at` timestamp records replay delivery today. Neither is evidence of a
current sensor connection or operational BMS integration. The model contains
no `IfcSensor` entities, so no sensor coordinates or hardware GUIDs are invented.

The [full demonstration and learning guide](20261018_v1_Demonstration_and_Learning.md)
provides source-row examples and personal exercises. The
[role-evidence matrix](20261018_v1_Role_Evidence_Matrix.json) covers the original
local artefact. Repository publication adds inspectable source and a browser
viewer; it does not confer qualifications or independently establish mastery.

## Reproduce and inspect verification

From the repository root, after installing the locked Python dependencies and
stopping the local service:

```powershell
& $py scripts/build_data.py
& $py scripts/build_geometry.py
& $py scripts/build_analytics.py
& $py scripts/20261018_v1_Verify.py
```

`$py` is the Python executable selected during the
[setup steps](20261018_v1_Local_Backend.md#reproduce-from-source).
The verification script records test outcomes and before/after source hashes.
A complete upstream RDF cross-check is optional and described in that guide;
normal startup uses the compact pinned source and preserved audit evidence.

The archived reports describe their own execution date, profile and source
snapshot. A successful current GitHub Actions run is additional evidence, not
something implied by a workflow file. Likewise, deployment completion, browser
operation and Docker execution need their own observations.

The Pages source and evidence manifest live under `pages/`. Its original data
and exported JSON are preserved byte for byte. The viewer uses local assets
under the repository URL and does not call the former ChatGPT site. Live local
API and transport functionality remains available in the full Python profile.

## GitHub Pages publishing

The repository was published through GitHub Desktop. Pages was then enabled
with **GitHub Actions** as its publishing source. The
[first successful deployment](https://github.com/gitmeysambayat/connected-building-evidence/actions/runs/39110024607/attempts/2)
and subsequent HTTP and browser checks established that the viewer was live.
The following setup steps are retained for reproducing the deployment in a fork.

1. Sign in as the repository owner and open
   [Settings, Pages](https://github.com/gitmeysambayat/connected-building-evidence/settings/pages).
2. Under **Build and deployment**, set **Source** to **GitHub Actions**.
3. Run **Publish verified data explorer** from the repository's Actions tab.
4. Check that deployment succeeds, then open
   [the published viewer](https://gitmeysambayat.github.io/connected-building-evidence/).
   Select a room and use **Sources & evidence**, **Verify all 37 source channels**
   to check the deployed files.

These steps follow GitHub's
[publishing-source instructions](https://docs.github.com/en/pages/getting-started-with-github-pages/configuring-a-publishing-source-for-your-github-pages-site).
For a fork, substitute its owner and repository in the workflow links and
viewer address. Deployment completion and browser operation need their own checks.

## Scope and attribution

The project was assembled with AI assistance. Before presenting personal
competence, complete the exercises and be able to trace a record, explain the
mapping, reproduce the checks and describe a change you made yourself.

New code is MIT licensed. The published IFC, observations, RDF, excerpts and
derived data retain their source attribution and CC BY-SA 4.0 terms. See
[NOTICE](../NOTICE), [LICENSE](../LICENSE) and
[the source licence](../data/source/LICENSE.md). This work does not imply
endorsement by the dataset authors, their institutions or the advertised role.
