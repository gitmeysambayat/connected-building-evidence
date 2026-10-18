# Public cloud application

**[Open Connected Building](https://connected-building-evidence.onrender.com/)**

This profile runs the complete Python application in one container: REST and
GraphQL query SQLite, fixed SPARQL templates execute against Oxigraph/RocksDB,
and real publisher/subscriber clients send historical records through an
authenticated MQTT broker inside the container. It uses the same pinned IFC
and published 2017 records as the browser viewer.

## Deployment status

All nine external HTTPS checks of the repaired public revision `90782821`
passed at 11:31:26 UTC on 18 October 2026. The
[Render deployment receipt](../reports/20261018_v1_Render_Deployment_Receipt.json)
records the exact hosted revision, source integrity, live queries, visitor
controls and successful MQTT acceptance, reconnection and duplicate handling.

The public service has now been created from this public GitHub repository
using Render's Blueprint deployment flow. Its Compute settings were inspected
as Free, $0 per month, 0.1 CPU and 512 MB RAM. No paid disk or database was
created. These plan settings were observed separately from the application
responses; page content alone does not establish the billed plan.

| Phase | Observed outcome |
|---|---|
| Initial public revision `596f10d` | HTTPS health and actual source counts passed. IFC bytes, 9/36/1 live semantic-query results, REST/GraphQL source-row agreement, the secure browser cookie and visitor-lease separation were verified. A real Kitchen temperature replay published and accepted 20 events, with no duplicates or rejected events. |
| Initial broker-reconnection attempt | Failed. The HTTPS request exceeded 45 seconds. Later responses still showed a restarting broker and disconnected subscriber while other read APIs remained responsive. This attempt did not complete a repeat-delivery or reconnection cycle. |
| Provider-level service restart | Restored the running broker and connected subscriber. Retained unique events and delivery rows changed from 20 to zero. This demonstrates temporary-state loss and service recovery; it does not establish that the in-application broker restart succeeded. |
| Repaired public revision `90782821` | Passed all nine external checks. Twenty Kitchen temperature events were accepted; an authenticated broker replacement and fresh subscriber succeeded; a repeated batch produced 20 duplicates and zero newly accepted or rejected events. All 20 retained source-event receipts matched the original CSV values, source hashes, IFC GUID and historical timestamps. |

<!-- render-recovery-status:start -->
The repaired deployment was observed Live on Render at exact source revision
`90782821c0a31e1e1a2d6d5997913939231a6d3c`. External tests confirmed the real
broker replacement, new authenticated subscriber and duplicate cycle. Public
graph metadata now describes the configured Render environment and temporary
filesystem, with no self-certifying deployment-verification flag. The receipt
preserves the earlier failure alongside this later successful attempt.
<!-- render-recovery-status:end -->

The initial receipt records source revision
`596f10d7a9476e459259faaf5d04ad9c475b0c2f`. Its first replay completed at
11:09:06 UTC. The failed broker state was checked again at 11:11:46 UTC; the
provider-restarted service was checked at 11:12:57 UTC. These are dated
observations. Repaired-revision checks ran from 11:30:59 to 11:31:26 UTC.
They establish the recorded prototype operation rather than an uptime or
production-reliability guarantee.

GitHub Pages continues to serve the separate read-only viewer. GitHub stores
the application source and provides the launch link; a container host runs the
Python process. [GitHub Pages is a static hosting service](https://docs.github.com/en/pages/getting-started-with-github-pages/what-is-github-pages).
The repository's About website link points directly to the Render application;
the read-only Pages viewer remains available through its separate link.

## Free-host configuration

The supplied [render.yaml](../render.yaml) selects **Render Free**, with no paid database or
persistent disk. The service has one application worker and exposes HTTP only.
The MQTT listener stays on container loopback. The host's assigned public URL
supplies the accepted browser origin.

The Blueprint reads the public repository. No additional GitHub App access
grant was used to create this service. The owner started the initial and
repaired deployments through Render's controls using the GitHub source.
Automatic deployment of a future push has not been observed; later repository
commits are not assumed to be running until the hosted revision is checked.

Render documents 512 MB RAM and 0.1 CPU for this plan. Its free service sleeps
after 15 minutes without inbound traffic and wakes on a later request. Free
runtime files disappear on sleep, restart and deployment. The image therefore
contains prepared source-derived stores, while startup creates a separate,
writable runtime copy. Source records remain intact; replay receipts and
visitor control leases reset with the runtime. The provider-level restart
above confirmed receipt reset for this particular service. Disk-backed SQLite
and RocksDB files do not establish durable hosting storage.

Free instance hours, bandwidth and build minutes have monthly limits. With no
payment method, exceeding the documented allowances suspends free services or
new builds rather than purchasing additional capacity. Keep the service on
Free and review any account-level billing settings before enabling paid usage.

Sources, checked 18 October 2026:

- [Render compute plans](https://render.com/docs/compute-plans).
- [Render free-service limits and billing behaviour](https://render.com/docs/free).
- [Render web-service and HTTP-port requirements](https://render.com/docs/web-services).

## Visitor access

Visitors can inspect the source building, query original records, export CSV,
execute the fixed graph queries and start a bounded historical MQTT replay.
The public profile creates an anonymous browser cookie. It grants a short
control lease over the shared replay, rather than creating a user account.
A different visitor cannot stop or restart the current owner's run.

Public replays are limited to 300 events and 30 seconds. The backend also
checks the requested event rate and applies control cooldowns and request
budgets. The interface shows the server's current limits and control
permissions. Only the newest 10,000 delivery rows are retained. Cumulative
counts, retained rows and unique accepted source events have separate meanings.
These bounds support a shared demonstration; they do not establish production
multi-tenant isolation or a service-level guarantee.

The local profile remains available without public visitor leases. Refer to
[the complete backend guide](20261018_v1_Local_Backend.md) for local operation.

## Build and verification

The Docker build prepares the actual SQL/RDF stores, IFC geometry and
chronological forecast experiment. Runtime serves those computed artefacts
and executes database queries and MQTT transport. It does not replace them
with HTTP snapshots.

The [Linux container run for revision `90782821`](https://github.com/gitmeysambayat/connected-building-evidence/actions/runs/39123160789)
passed all six integration checks under 512 MiB RAM and 0.1 CPU. Peak memory
was 230.18 MiB, with no out-of-memory events; three starts took 33.44 to 35.74
seconds. Its exact capacity report and artifact identity are embedded under
`repaired_revision_ci.linux_container` in the
[deployment receipt](../reports/20261018_v1_Render_Deployment_Receipt.json).
These are Linux-container measurements, not provider telemetry.

The [backend verification for that revision](https://github.com/gitmeysambayat/connected-building-evidence/actions/runs/39123160782)
passed 140 Python tests. The
[Pages workflow](https://github.com/gitmeysambayat/connected-building-evidence/actions/runs/39123160724)
passed 13 viewer JavaScript checks, and the cloud-container workflow passed
eight interface JavaScript checks, for 21 in total. Those checks are separate
from actual browser rendering and the external Render execution above.

The cloud-container workflow builds and runs the image on Linux under a
512 MiB memory limit and 0.1 CPU allowance. Its successful run inspected real
HTTP responses, executed GraphQL and SPARQL, accepted 20 MQTT source events,
identified 20 repeat deliveries, tested subscriber reconnection and rejected
another visitor's attempt to stop the owner's replay. It also checked that
a same-container restart preserved receipts and a replacement container reset
them. The report records the tested source revision and execution scope.

These are bounded integration checks. The recorded Linux container and actual
Render service are separate execution environments. Successful container tests
do not override the failed initial Render attempt; the later external checks
independently verified the repair on the host. See the current
[workflow runs](https://github.com/gitmeysambayat/connected-building-evidence/actions)
and the external deployment receipt for revision-specific outcomes.

## Interpretation

The broker is running at the time of replay. Measurement timestamps still
belong to the 2017 source dataset. There is no current sensor or BMS link and
no control of a physical building. The six Ridge candidates remain rejected
against persistence. Hosting does not establish sensor calibration, energy
savings, improved forecasts or the owner's independent proficiency.
