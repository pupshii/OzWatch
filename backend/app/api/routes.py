"""HTTP API.

GET /api/layers                     layer metadata, attribution and live status
GET /api/layers/{key}/features      current GeoJSON FeatureCollection (optional ?bbox=w,s,e,n)
GET /api/stream                     Server-Sent Events: live deltas + status changes
GET /health                         per-connector health, for humans and uptime checks
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.store import BBox

router = APIRouter()

KEEPALIVE_SECONDS = 15


def _runtime(request: Request):
    return request.app.state.runtime


@router.get("/api/layers")
async def list_layers(request: Request):
    rt = _runtime(request)
    return [{**asdict(p.connector.info), "status": p.status.as_dict()} for p in rt.workers.values()]


@router.get("/api/layers/{key}/features")
async def layer_features(
    request: Request, key: str, bbox: str | None = Query(None, description="west,south,east,north")
):
    rt = _runtime(request)
    if key not in rt.workers:
        raise HTTPException(404, f"unknown layer '{key}'")
    box = _parse_bbox(bbox) if bbox else None
    features = await rt.store.get_layer(key, box)
    return {"type": "FeatureCollection", "features": [f.to_geojson() for f in features]}


@router.get("/api/stream")
async def stream(request: Request):
    rt = _runtime(request)
    queue = rt.broadcaster.subscribe()

    async def events():
        try:
            yield "retry: 5000\n\n"  # tell the browser how long to wait before reconnecting
            while rt.broadcaster.is_subscribed(queue):
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=KEEPALIVE_SECONDS)
                except TimeoutError:
                    yield ": keepalive\n\n"  # comment line keeps proxies from closing idle streams
                    continue
                yield f"data: {json.dumps(msg, default=str)}\n\n"
        finally:
            rt.broadcaster.unsubscribe(queue)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/health")
async def health(request: Request):
    rt = _runtime(request)
    connectors = [p.status.as_dict() for p in rt.workers.values()]
    states = {c["state"] for c in connectors}
    overall = "ok" if states <= {"ok"} else ("error" if states == {"error"} else "degraded")
    return {
        "status": overall,
        "store": type(rt.store).__name__,
        "sse_clients": rt.broadcaster.subscriber_count,
        "connectors": connectors,
    }


def _parse_bbox(raw: str) -> BBox:
    try:
        west, south, east, north = (float(x) for x in raw.split(","))
    except ValueError as exc:
        raise HTTPException(422, "bbox must be 'west,south,east,north'") from exc
    if not (west < east and south < north):
        raise HTTPException(422, "bbox must have west<east and south<north")
    return BBox(west, south, east, north)
