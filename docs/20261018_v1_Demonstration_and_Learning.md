# Connected Building demonstration guide

Use this walkthrough to inspect the building model, trace source records and
exercise the application services. Keep the revision, date and observed result
with any verification record.

## A 15-minute walkthrough

| Time | Action | Evidence |
|---|---|---|
| 0:00-2:00 | Open the model and Sources & evidence. | One pinned source supplies the flat's IFC, room identifiers, RDF and 2017 observations. |
| 2:00-4:00 | Select Room1, choose air temperature and inspect its connection. | GlobalId `05i4VutGDCsQKCrT6CQvhu`, IFC entity 165 and original CSV rows share a documented identity chain. The first observation is 19.53 degrees Celsius at `2017-03-09T00:51:30Z`, source line 1. |
| 4:00-6:00 | Inspect coverage, external weather and the unresolved thermostat channel. | Sensor observations, controller events, external feeds and unmapped candidates keep their own classifications. |
| 6:00-8:00 | Run a short historical replay, inspect receipts, repeat the interval and exercise recovery. | Current delivery receipts preserve original observation times, source lines, units and identities; repeat deliveries are recorded as duplicates. |
| 8:00-10:00 | Query a room through REST, GraphQL and fixed SPARQL. | SQLite observations and Oxigraph source relationships resolve to the same catalogue. |
| 10:00-12:00 | Inspect the temperature forecast and heating-command analysis. | Chronological validation selects persistence in all six rooms. The command comparison retains source rows and command ages. |
| 12:00-14:00 | Open Sensor station. | Before commissioning, the station awaits its first device report. After installation, inspect temperature, humidity, observation time, receipt time and freshness. |
| 14:00-15:00 | Export observations and inspect the verification records. | Original source files and retained station records have separate exports and provenance. |

The source-record example is reproducible from
`data/source/Measurements/Room1_Temperature.csv`, its preserved RDF excerpt,
SQLite and the HTTP APIs. The IFC room alignment is documented in
`data/source/01_LinkOsh.ttl`.

## Practical exercises

1. Trace a different room's observation through its CSV line, UTC timestamp,
   unit, RDF identity, IFC GlobalId and file hash. Compare the API response.
2. Inspect a source gap and compare original timestamps with plotted rows.
   Check that external weather and controller events remain correctly labelled.
3. Query a temperature count in SQL and the room-alignment SPARQL template.
   Close and reopen the graph, then compare the query result.
4. Repeat a historical source interval and compare accepted-event identities
   with duplicate-delivery receipts. Exercise subscriber recovery after a
   broker replacement.
5. Reproduce one forecast error and explain the persistence comparator,
   chronological split and validation-based model choice.
6. Commission the separate device using the
   [sensor setup guide](20261018_v1_Real_Sensor_Setup.md). Record installation,
   observed and received times, data continuity, a reference comparison and
   recovery after a network interruption.
7. Make and test one bounded change, such as a source-preserving filter or an
   improved handling of stale station readings. Record the expected behaviour,
   actual result and relevant test output.

## Device commissioning record

Keep a dated record of the board and sensor model, firmware revision, sampling
interval, generic station identity and authorised network. Keep device secrets
and precise location outside the public repository.

Record initial hardware readings, the displayed API values, receipt times,
duplicate handling, disconnection and subsequent recovery. Save an export of
the retained readings. If comparing with another thermometer, record its
specification and placement alongside the observed difference.

## Research extension

For a later building-performance study, define a measured target, forecast
horizon and evaluation protocol before collecting the test period. Compare a
justified model with persistence and suitable simple baselines. Report missing
coverage, latency, room differences and uncertainty. Energy-savings studies
require measured energy and operational context; occupancy studies require
appropriate labels.

The [technical capability record](20261018_v2_Technical_Capability_Record.json)
and dated verification receipts identify the implementation and execution
evidence supporting each workflow.
