# Public cloud application

**[Open Connected Building](https://connected-building-evidence.onrender.com/)**

This profile runs the complete Python application in one container: REST and
GraphQL query SQLite, fixed SPARQL templates execute against Oxigraph/RocksDB,
and real publisher/subscriber clients send historical records through an
authenticated MQTT broker inside the container. It uses the same pinned IFC
and published 2017 records as the browser viewer.

## Deployment status

An earlier [Linux container run](https://github.com/gitmeysambayat/connected-building-evidence/actions/runs/39119255027)
passed for source revision `5fff2ccfc8b84cfd7b29defe61809a5032d462d2`.
Its [executed capacity report](../reports/20261018_v1_Cloud_Container_Verification.json)
records six successful integration checks under 512 MiB RAM and 0.1 CPU.
Peak memory was 231.72 MiB, with no out-of-memory events. Three starts took
35.64 to 37.52 seconds. The [backend run](https://github.com/gitmeysambayat/connected-building-evidence/actions/runs/39119255021)
also passed all 134 tests.

The public service has now been created from this public GitHub repository
using Render's Blueprint deployment flow. Its Compute settings were inspected
as Free, $0 per month, 0.1 CPU and 512 MB RAM. No paid disk or database was
created. The [Render deployment receipt](../reports/20261018_v1_Render_Deployment_Receipt.json)
records the initial deployed source revision and external checks. Status was
recorded on 18 October 2026.

| Phase | Observed outcome |
|---|---|
| Initial public revision `596f10d` | HTTPS health and actual source counts passed. IFC bytes, 9/36/1 live semantic-query results, REST/GraphQL source-row agreement, the secure browser cookie and visitor-lease separation were verified. A real Kitchen temperature replay published and accepted 20 events, with no duplicates or rejected events. |
| Initial broker-reconnection attempt | Failed. The HTTPS request exceeded 45 seconds. Later responses still showed a restarting broker and disconnected subscriber while other read APIs remained responsive. This attempt did not complete a repeat-delivery or reconnection cycle. |
| Provider-level service restart | Restored the running broker and connected subscriber. Retained unique events and delivery rows changed from 20 to zero. This demonstrates temporary-state loss and service recovery; it does not establish that the in-application broker restart succeeded. |
| Repaired revision | Pending deployment and external verification. No successful repaired MQTT duplicate/reconnection cycle is claimed yet. |

<!-- render-recovery-status:start -->
The broker-shutdown/recovery repair and graph storage-metadata correction are
being checked. Their presence in source or a passing local test does not
establish successful operation on Render. The final receipt must identify the
actual deployed commit and its external verification outcome.
<!-- render-recovery-status:end -->

The initial receipt records source revision
`596f10d7a9476e459259faaf5d04ad9c475b0c2f`. Its first replay completed at
11:09:06 UTC. The failed broker state was checked again at 11:11:46 UTC; the
provider-restarted service was checked at 11:12:57 UTC. These are dated
observations rather than an uptime or production-reliability guarantee.

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
grant was used to create this service. The configuration requests deployment
after checks pass, but automatic deployment of a future push has not yet been
observed. A deployment may instead be started through Render's service controls;
the actual deployed revision must always be checked.

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

The cloud-container workflow builds and runs the image on Linux under a
512 MiB memory limit and 0.1 CPU allowance. Its successful run inspected real
HTTP responses, executed GraphQL and SPARQL, accepted 20 MQTT source events,
identified 20 repeat deliveries, tested subscriber reconnection and rejected
another visitor's attempt to stop the owner's replay. It also checked that
a same-container restart preserved receipts and a replacement container reset
them. The report records the tested source revision and execution scope.

These are bounded integration checks. The recorded Linux container and actual
Render service are separate execution environments. The successful container
reconnection does not override the failed initial Render attempt or prove that
the pending repair works on the host. See the current
[workflow runs](https://github.com/gitmeysambayat/connected-building-evidence/actions)
and the external deployment receipt for revision-specific outcomes.

## Interpretation

The broker is running at the time of replay. Measurement timestamps still
belong to the 2017 source dataset. There is no current sensor or BMS link and
no control of a physical building. The six Ridge candidates remain rejected
against persistence. Hosting does not establish sensor calibration, energy
savings, improved forecasts or the owner's independent proficiency.
