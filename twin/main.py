"""One building, one provenance chain, shared REST and GraphQL data services."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import csv
from dataclasses import asdict
from io import StringIO
import json
import os
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
import strawberry
from strawberry.fastapi import GraphQLRouter
from strawberry.scalars import JSON
from strawberry.extensions import QueryDepthLimiter, MaxAliasesLimiter, SchemaExtension
from graphql import parse as parse_graphql

from twin import analytics, data, geometry, operations, semantic, settings
from twin.transport import HistoricalTransport

transport = HistoricalTransport()


@asynccontextmanager
async def lifespan(app):
    await asyncio.to_thread(data.initialise)
    await asyncio.to_thread(semantic.initialise)
    geometry_result = await asyncio.to_thread(geometry.get_geometry)
    if geometry_result.get("source_ifc_sha256", geometry_result.get("source_sha256")) != data.summary()["model_sha256"]:
        semantic.close()
        raise ValueError("Geometry and measured-room mapping refer to different IFC source hashes.")
    if not analytics.OUTPUT.exists():
        await asyncio.to_thread(analytics.fit)
    mqtt_enabled = os.environ.get("TWIN_DISABLE_MQTT") != "1"
    if mqtt_enabled:
        await transport.start()
    else:
        await asyncio.to_thread(transport.initialise)
    try:
        yield
    finally:
        if mqtt_enabled:
            await transport.close()
        semantic.close()


app = FastAPI(title="Connected Building", version=settings.BUILD_VERSION,
              description="Published Open Smart Home observations linked to their pinned IFC spaces. Historical data, local MQTT replay, SQL and RDF graph.",
              lifespan=lifespan, docs_url="/api/docs", redoc_url=None)


@app.middleware("http")
async def basic_headers(request: Request, call_next):
    if request.method in {"POST", "PUT", "DELETE", "PATCH"}:
        origin = request.headers.get("origin")
        if origin and urlparse(origin).netloc != request.headers.get("host"):
            return JSONResponse({"detail": "Cross-origin mutations are disabled."}, status_code=403)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["X-Frame-Options"] = "DENY"
    if request.url.path.startswith("/api/replay"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(KeyError)
async def missing_handler(request, exc):
    return JSONResponse({"detail": str(exc).strip("'")}, status_code=404)


@app.exception_handler(ValueError)
async def value_handler(request, exc):
    return JSONResponse({"detail": str(exc)}, status_code=400)


@app.get("/health")
def health():
    return {"status": "ok", "build": settings.BUILD_VERSION, "source_model_sha256": data.summary()["model_sha256"],
            "sqlite": "ready", "rdf_graph": semantic.summary()["engine"],
            "mqtt": transport.state["broker"], "historical_measurements": True}


@app.get("/api/summary")
def summary():
    return {**data.summary(), "geometry": geometry.summary(), "semantic": semantic.summary(),
            "analytics_status": analytics.report()["status"], "transport": transport.status(),
            "deployment": {"environment": "local", "cloud_deployment_verified": False}}


@app.get("/api/rooms")
def rooms():
    return data.rooms()


@app.get("/api/channels")
def channels(room_guid: str | None = None, include_unverified: bool = False):
    if room_guid and room_guid not in {r["guid"] for r in data.rooms()}:
        raise KeyError("Unknown IFC space GUID")
    return data.channels(room_guid, include_unverified)


@app.get("/api/series/{channel_id}")
def series(channel_id: str, start: str | None = None, end: str | None = None,
           limit: int = Query(1000, ge=1, le=10000)):
    return data.series(channel_id, start, end, limit)


@app.get("/api/series/{channel_id}/export.csv")
def export_series(channel_id: str, start: str | None = None, end: str | None = None):
    result = data.export_series(channel_id, start, end, max_rows=50000)
    stream = StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(["channel_id", "ifc_space_guid", "source_epoch_utc", "timestamp_utc", "value", "unit", "source_line", "source_sha256", "model_sha256"])
    channel = result["channel"]
    for row in result["readings"]:
        writer.writerow([channel_id, channel["room_guid"], row["epoch"], row["timestamp"], row["value"], result["unit"],
                         row["source_line"], channel["source_sha256"], channel["model_sha256"]])
    return Response(stream.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="20261018_v1_{channel_id}.csv"'})


@app.get("/api/rooms/{guid}/evidence")
def room_evidence(guid: str):
    return {**data.room_evidence(guid), "semantic_query": semantic.room_evidence(guid)}


@app.get("/api/geometry")
def geometry_document():
    return JSONResponse(geometry.get_geometry(), headers={"ETag": '"' + data.summary()["model_sha256"] + '"'})


@app.get("/api/model")
def model():
    return {"identity": geometry.summary(), "spaces": data.rooms(), "versions": [
        {"version": settings.BUILD_VERSION, "source_commit": settings.SOURCE_COMMIT,
         "ifc_sha256": data.summary()["model_sha256"], "status": "pinned immutable published model"}],
        "limitation": "One published source revision. No fabricated design revision or sensor instance is provided."}


@app.get("/api/model/elements/{guid}")
def element(guid: str):
    item = geometry.element(guid)
    if item is None:
        raise KeyError("Unknown IFC element GUID")
    return item


@app.get("/api/model/source.ifc")
def source_ifc():
    return FileResponse(settings.SOURCE_IFC, media_type="application/x-step", filename="20261018_v1_OpenSmartHome_Flat.ifc")


@app.get("/api/quality")
def quality():
    return data.quality()


@app.get("/api/analytics")
def analytics_report():
    return analytics.report()


@app.get("/api/operations")
def operations_report():
    return operations.report()


@app.get("/api/graph")
def graph():
    return {"summary": semantic.summary(), "templates": semantic.query_templates()}


@app.get("/api/graph/query")
def graph_query(template: str = "room_alignment"):
    return semantic.query(template)


@app.get("/api/provenance")
def provenance():
    return {"manifest": data.manifest(), "quality": data.quality(), "licence_url": "https://creativecommons.org/licenses/by-sa/4.0/",
            "source_repository": "https://github.com/TechnicalBuildingSystems/OpenSmartHomeData",
            "source_release_doi": "https://doi.org/10.5281/zenodo.1244602",
            "source_commit": settings.SOURCE_COMMIT, "independent_validation": False,
            "scope": "The application verifies source consistency and mapping; it cannot independently establish as-built accuracy or sensor calibration."}


class ReplayRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    speed: float = Field(20, ge=1, le=100)
    limit: int = Field(300, ge=1, le=2000)
    channel_id: str | None = Field(None, max_length=120)


@app.get("/api/replay/status")
def replay_status():
    return transport.status()


@app.get("/api/replay/events")
def replay_events(limit: int = Query(50, ge=1, le=500)):
    return transport.events(limit)


@app.post("/api/replay/start")
def replay_start(body: ReplayRequest):
    return transport.begin_replay(body.speed, body.limit, body.channel_id)


@app.post("/api/replay/stop")
def replay_stop():
    return transport.stop()


@app.post("/api/replay/restart-broker")
async def restart_broker():
    return await transport.restart_broker()


@strawberry.type
class Reading:
    timestamp: str
    epoch: int
    value: float
    source_line: int


@strawberry.type
class Channel:
    id: str
    room_guid: str | None
    metric: str
    unit: str
    source_kind: str
    mapping_verified: bool
    mapping_status: str
    source_sha256: str
    model_sha256: str

    @strawberry.field
    def readings(self, info: strawberry.Info, start: str | None = None, end: str | None = None, limit: int = 500) -> list[Reading]:
        if not 1 <= limit <= 10000:
            raise ValueError("Each readings field is limited to 1-10,000 source observations.")
        budget = info.context.setdefault("readings_budget", 20000)
        if limit > budget:
            raise ValueError("The request exceeds the combined 20,000-reading budget. Narrow the query.")
        info.context["readings_budget"] = budget - limit
        return [Reading(**{key: row[key] for key in ("timestamp", "epoch", "value", "source_line")})
                for row in data.series(self.id, start, end, limit)["readings"]]


def graph_channel(row):
    return Channel(**{key: row[key] for key in ("id", "room_guid", "metric", "unit", "source_kind", "mapping_verified", "mapping_status", "source_sha256", "model_sha256")})


@strawberry.type
class Room:
    guid: str
    room_id: str
    name: str | None
    long_name: str | None
    verified_channels: int
    model_sha256: str

    @strawberry.field
    def channels(self) -> list[Channel]:
        return [graph_channel(row) for row in data.channels(self.guid)]

    @strawberry.field
    def evidence(self) -> JSON:
        return room_evidence(self.guid)


def graph_room(row):
    return Room(**{key: row[key] for key in ("guid", "room_id", "name", "long_name", "verified_channels", "model_sha256")})


@strawberry.type
class QueryRoot:
    @strawberry.field
    def rooms(self) -> list[Room]:
        return [graph_room(row) for row in data.rooms()]

    @strawberry.field
    def room(self, guid: str) -> Room | None:
        return next((graph_room(row) for row in data.rooms() if row["guid"] == guid), None)

    @strawberry.field
    def channel(self, id: str) -> Channel | None:
        try:
            return graph_channel(data.get_channel(id))
        except KeyError:
            return None

    @strawberry.field
    def source_summary(self) -> JSON:
        return data.summary()


class BoundedParse(SchemaExtension):
    """Enforce the token bound in the actual parser for the pinned Strawberry.

    Strawberry 0.270.1's default async parse does not forward parse_options.
    Installing its MaxTokensLimiter alone therefore does not enforce this bound.
    """
    def on_parse(self):
        self.execution_context.graphql_document = parse_graphql(self.execution_context.query, max_tokens=3000)
        yield


schema = strawberry.Schema(query=QueryRoot, extensions=[BoundedParse, QueryDepthLimiter(max_depth=8),
    MaxAliasesLimiter(max_alias_count=20)])
app.include_router(GraphQLRouter(schema, graphql_ide=None, allow_queries_via_get=False), prefix="/graphql")
app.mount("/static", StaticFiles(directory=settings.ROOT / "static"), name="static")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(settings.ROOT / "static" / "index.html")
