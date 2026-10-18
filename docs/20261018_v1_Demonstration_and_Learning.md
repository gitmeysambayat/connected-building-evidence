# Demonstration and learning guide

This guide supports an honest demonstration of Connected Building for a digital
twins, BIM and AI research role. The software was assembled with AI assistance.
Its capabilities do not establish the candidate's independent competence.
Complete the exercises and keep an account of what you personally checked,
changed and explained before using application wording based on the project.

## A 15-minute evidence-led demonstration

| Time | Action | Evidence to explain |
|---|---|---|
| 0:00-2:00 | Open the dashboard and provenance view. | One published flat, its actual IFC model and 2017 measured observations come from the same pinned source. Give the source commit and distinguish observation time from today's receipt time. |
| 2:00-5:00 | Select Room 1 in the spatial viewer, choose air temperature and open its evidence. | GlobalId `05i4VutGDCsQKCrT6CQvhu` resolves to source IFC entity 165. Publisher RDF associates the room and sensor. First source observation is 19.53 °C at `2017-03-09T00:51:30Z`, CSV line 1. Its RDF time `2017-03-09T01:51:30+01:00` is the same instant. |
| 5:00-7:00 | Inspect a long data gap, the outdoor source, an unmonitored room and the unverified Toilet thermostat channel. | Missing coverage remains missing. Six weather files are copies of one external feed. Lobby has no invented readings. The filename-only Toilet association does not gain a verified IFC room join. |
| 7:00-9:00 | Start a short historical replay, inspect receipts, stop it, then repeat a source interval. | Two actual MQTT clients communicate through a local broker. Check original event time, current receipt time, source line, model hash and duplicate-delivery handling. Restart the broker only after replay stops and inspect reconnection status. |
| 9:00-11:00 | Query the same room through REST, GraphQL and the fixed SPARQL template. | SQL stores observations and constrained identities; the persistent graph stores source relations and explicit derivations. Explain the setpoint namespace normalisation and why no `owl:sameAs` was invented. |
| 11:00-12:00 | Open the air-temperature versus recorded heating-command review at `/api/operations`. | Inspect a traceable air/setpoint source-row pair and command age. Differences use original observations and a past command no older than 24 hours. The illustrative 1 °C band is not a comfort or fault limit, and observation fractions are not percentages of time. |
| 12:00-14:00 | Open the forecasting experiment and show validation and test scores. | The target is actual future room air temperature. Training, validation and test are separated chronologically. Validation selects persistence in all six rooms, so the fitted Ridge candidate is not recommended. Test results remain frozen. |
| 14:00-15:00 | Export a complete channel, identify one source hash and describe a reproducible rebuild. | Demonstrate traceability and acknowledge current boundaries: local operation, historical replay, no live BMS, no measured energy-saving result, no independent calibration. State the next experiment you would design. |

The first-observation example is reproducible in
`data/source/Measurements/Room1_Temperature.csv`, its preserved RDF excerpt, the
SQLite database, REST and GraphQL. Source alignment is in
`data/source/01_LinkOsh.ttl`. A full upstream consistency check and its optional
reproduction command are described in the main README.

## Exercises to complete personally

1. **Trace a real observation.** Pick a room other than Room 1. Record a CSV
   line, Unix timestamp, UTC time, value, unit, sensor or actuator URI, IFC STEP
   entity, GlobalId and IFC SHA-256. Confirm the same observation through the
   API. Explain which links are publisher assertions and which are application
   derivations.
2. **Explain an unknown.** Find the CSV-only Toilet thermostat channel. Show
   that its candidate room is separate from a verified association. Identify
   the additional publisher evidence that would justify changing its status.
   A similar name or nearby geometry alone is insufficient.
3. **Investigate coverage.** Find a long source gap. Compare its original
   interval with the displayed plot sample. Explain why the plot must not
   invent a line across a missing interval and why setpoint event spacing is a
   different case. Report source observations, not an unsupported percentage of
   elapsed monitoring time. In `/api/operations`, check one air/setpoint
   source-row pair and subtract the recorded setpoint from the measured air
   temperature. Explain why its command age and illustrative comparison band
   do not establish controller state, comfort, a fault or energy consumption.
4. **Query SQL and the graph.** Write one read-only SQL query to count room
   temperature observations and inspect the supplied SPARQL room-alignment
   query. Explain why time-series storage and semantic relationships have
   different responsibilities. Close and reopen the graph and verify that the
   result survives.
5. **Inspect network delivery.** Run a small replay. Find the corresponding
   accepted receipt in the transport database and compare `observed_at` with
   `received_at`. Repeat the interval and explain duplicate delivery versus
   duplicate physical measurements. Inspect an invalid-payload test and its
   rejection reason.
6. **Test recovery.** Stop replay, restart the local broker and check whether
   the subscriber reconnects before starting another replay. Record actual
   behaviour rather than assuming the existence of a reconnect function proves
   recovery.
7. **Defend the negative forecasting result.** Explain the persistence
   comparator and the validation gate. Reproduce one absolute prediction error
   from a measured target. Explain why selecting a model after observing test
   scores would change the experiment and require a new holdout.
8. **Make one bounded code change.** For example, add a source-preserving query
   filter or improve an error message. Write a test for the behaviour that
   matters, execute the relevant checks and explain the change without reading
   generated prose. Keep the original pinned public source unchanged.
9. **Plan the first real device exercise.** Specify a consented test sensor,
   its identity and physical location, timestamp synchronisation, units,
   calibration evidence, expected cadence, missing-data handling and a
   reversible acceptance test. A successful historical replay cannot supply
   this physical evidence.
10. **Design a later research study.** Choose a useful question, an actual
    measured target and a predeclared validation protocol. For energy
    optimisation, obtain measured energy and relevant operational context
    before claiming savings. For occupancy, obtain genuine labels rather than
    threshold-derived surrogate truth.

Record each exercise with the date, your own action, a concise result and a
pointer to the observation or output that supports it. A completed learning log
is stronger evidence of personal ownership than repeating a list of libraries.

## Interview questions to answer in your own words

1. What makes this a coherent asset-linked project, and which operational
   digital-twin capabilities remain absent?
2. Why is an IFC GlobalId scoped by the source file hash in this project?
3. What does the publisher's `skos:related` relation support, and why is it not
   equivalent to asserting identical physical entities?
4. How do you know a displayed temperature is a measured source value rather
   than generated data?
5. What is unknown about sensor position, calibration and source arrival time?
6. Why are the six outdoor files not six independent sensors?
7. Why does the Toilet thermostat association remain unverified?
8. What happened to the differing setpoint namespace, and where is that
   transformation recorded?
9. Why do historical observation time and present-day receipt time require
   different fields?
10. What does MQTT QoS or a duplicate receipt establish, and what does it leave
    unproven about a physical measurement?
11. What belongs in SQL here, and what does the RDF/NoSQL graph add?
12. How do the REST, GraphQL and SPARQL responses remain consistent?
13. How were temporal leakage and missing target coverage handled?
14. Why is the Ridge model rejected, and why is its error band not a calibrated
    uncertainty guarantee?
15. What additional evidence would be needed before operating a real building,
    claiming energy savings or describing a cloud deployment as verified?

## Application wording after personal verification

Only after independently running, checking and explaining the relevant parts,
a proportionate CV statement is:

> Developed practical experience with an AI-assisted BIM-linked building-data
> demonstrator by tracing published measured observations to verified IFC
> spaces, querying SQL and a persistent RDF graph, inspecting MQTT replay and
> evaluating chronological room-temperature forecasts against persistence.

Adjust the sentence to the work you actually performed. Do not describe this as
a production digital twin, live building commissioning, cloud deployment,
independent implementation, energy optimisation or mastery of every advertised
technology. The evidence matrix evaluates the artefact's coverage of the role;
qualifications, prior collaborative delivery and personal competence require
their own evidence.

## Research extension with a meaningful decision

A useful next study would pre-register a new test period or acquire a separate
building, define the decision and forecast horizon, and compare persistence,
simple physical knowledge and a justified learned model. Include missing-data
coverage, source latency, room differences and uncertainty assessment. Keep
observational association separate from causal claims about control or savings.
The current frozen experiment provides a reproducible baseline for that work;
it does not already establish a novel generalisable research contribution.
