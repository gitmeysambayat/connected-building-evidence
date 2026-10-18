# Public cloud application

This profile runs the complete Python application in one container: REST and
GraphQL query SQLite, fixed SPARQL templates execute against Oxigraph/RocksDB,
and real publisher/subscriber clients send historical records through an
authenticated MQTT broker inside the container. It uses the same pinned IFC
and published 2017 records as the browser viewer.

## Deployment status

The deployment files are being verified. This document alone is not evidence
that a public service has been created. A deployment receipt must identify
the actual host, source revision, container checks and public HTTP checks.

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

The cloud-container workflow is intended to build and run the image on Linux
under a 512 MB memory limit and fractional CPU allowance, inspect real HTTP
responses, exercise MQTT delivery and check a fresh container restart. Its
executed report, including any failures, supplies the evidence for this scope.
A Dockerfile or workflow definition by itself does not establish success.

## Interpretation

The broker is running at the time of replay. Measurement timestamps still
belong to the 2017 source dataset. There is no current sensor or BMS link and
no control of a physical building. The six Ridge candidates remain rejected
against persistence. Hosting does not establish sensor calibration, energy
savings, improved forecasts or the owner's independent proficiency.
