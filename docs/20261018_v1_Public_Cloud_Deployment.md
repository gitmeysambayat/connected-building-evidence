# Public cloud application

This profile runs the complete Python application in one container: REST and
GraphQL query SQLite, fixed SPARQL templates execute against Oxigraph/RocksDB,
and real publisher/subscriber clients send historical records through an
authenticated MQTT broker inside the container. It uses the same pinned IFC
and published 2017 records as the browser viewer.

## Deployment status

The [Linux container run](https://github.com/gitmeysambayat/connected-building-evidence/actions/runs/39119255027)
passed for source revision `5fff2ccfc8b84cfd7b29defe61809a5032d462d2`.
Its [executed capacity report](../reports/20261018_v1_Cloud_Container_Verification.json)
records six successful integration checks under 512 MiB RAM and 0.1 CPU.
Peak memory was 231.72 MiB, with no out-of-memory events. Three starts took
35.64 to 37.52 seconds. The [backend run](https://github.com/gitmeysambayat/connected-building-evidence/actions/runs/39119255021)
also passed all 134 tests.

**A public Render service has not yet been created.** The host account must be
connected before that step. The verified container is ready for the supplied
free-plan configuration; it is not a receipt for operation on Render itself.

The owner can [review and deploy this exact repository on Render](https://render.com/deploy?repo=https%3A%2F%2Fgithub.com%2Fgitmeysambayat%2Fconnected-building-evidence).
This opens Render's account and deployment flow, rather than an already running
application. Check that the proposed service uses the Free plan.

GitHub Pages continues to serve the separate read-only viewer. GitHub stores
the application source and provides the launch link; a container host runs the
Python process. [GitHub Pages is a static hosting service](https://docs.github.com/en/pages/getting-started-with-github-pages/what-is-github-pages).

## Proposed zero-cost host

The supplied `render.yaml` selects **Render Free**, with no paid database or
persistent disk. The service has one application worker and exposes HTTP only.
The MQTT listener stays on container loopback. The host's assigned public URL
supplies the accepted browser origin.

Render documents 512 MB RAM and 0.1 CPU for this plan. Its free service sleeps
after 15 minutes without inbound traffic and wakes on a later request. Free
runtime files disappear on sleep, restart and deployment. The image therefore
contains prepared source-derived stores, while startup creates a separate,
writable runtime copy. Source records remain intact; replay receipts and
visitor control leases reset with the runtime.

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

These are bounded integration checks. Concurrent use on the actual host and
public browser operation require separate observations after deployment.

## Interpretation

The broker is running at the time of replay. Measurement timestamps still
belong to the 2017 source dataset. There is no current sensor or BMS link and
no control of a physical building. The six Ridge candidates remain rejected
against persistence. Hosting does not establish sensor calibration, energy
savings, improved forecasts or the owner's independent proficiency.
