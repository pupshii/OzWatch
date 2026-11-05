"""OzWatch backend entry point.

Run locally:  uvicorn app.main:app --reload   (from the backend/ folder)

On start-up the app connects to the store, builds one Poller per connector
and runs each as a background asyncio task inside the web process. For a
handful of polled sources that's simpler than a separate worker + queue,
and it fits on one small free-tier VM.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.broadcaster import Broadcaster
from app.config import Settings, get_settings
from app.connectors.aisstream import AisStreamConnector
from app.connectors.base import Connector, StreamConnector
from app.connectors.nsw_cameras import NswCamerasConnector
from app.connectors.opensky import OpenSkyConnector
from app.scheduler import Poller
from app.store import Store, make_store
from app.streaming import StreamRunner

log = logging.getLogger("ozwatch")

FRONTEND_DIR = Path(os.environ.get("FRONTEND_DIR", Path(__file__).resolve().parents[2] / "frontend"))


AnyConnector = Connector | StreamConnector


def default_connectors(
    settings: Settings, client: httpx.AsyncClient
) -> list[tuple[AnyConnector, int | None]]:
    """(connector, poll-interval override) pairs. New sources get registered here."""
    return [
        (NswCamerasConnector(client, api_key=settings.tfnsw_api_key), settings.poll_cameras),
        # Order = drawing order on the map: ships under flights.
        (AisStreamConnector(api_key=settings.aisstream_api_key), None),
        (
            OpenSkyConnector(
                client,
                client_id=settings.opensky_client_id,
                client_secret=settings.opensky_client_secret,
            ),
            settings.poll_flights,
        ),
    ]


@dataclass
class Runtime:
    store: Store
    broadcaster: Broadcaster
    client: httpx.AsyncClient
    workers: dict[str, Poller | StreamRunner] = field(default_factory=dict)  # one per layer
    tasks: list[asyncio.Task] = field(default_factory=list)


def create_app(
    settings: Settings | None = None,
    store: Store | None = None,
    connectors: Callable[
        [Settings, httpx.AsyncClient], list[tuple[AnyConnector, int | None]]
    ] = default_connectors,
    start_workers: bool = True,
) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        client = httpx.AsyncClient(
            timeout=httpx.Timeout(20.0), headers={"User-Agent": settings.user_agent}, follow_redirects=True
        )
        rt = Runtime(
            store=store or make_store(settings.database_url), broadcaster=Broadcaster(), client=client
        )
        await rt.store.init()
        for conn, override in connectors(settings, client):
            rt.workers[conn.info.key] = (
                StreamRunner(conn, rt.store, rt.broadcaster)
                if isinstance(conn, StreamConnector)
                else Poller(conn, rt.store, rt.broadcaster, poll_seconds=override)
            )
            if conn.demo_mode:
                log.warning("%s is running on SAMPLE data (no API key configured)", conn.info.key)
            if conn.meta.get("auth") == "anonymous":
                log.warning("%s: no credentials, using anonymous access (slower polling)", conn.info.key)
        app.state.runtime = rt
        if start_workers:
            rt.tasks += [
                asyncio.create_task(w.run_forever(), name=f"worker:{k}") for k, w in rt.workers.items()
            ]
            rt.tasks.append(
                asyncio.create_task(_history_janitor(rt.store, settings.history_hours), name="janitor")
            )
        try:
            yield
        finally:
            for t in rt.tasks:
                t.cancel()
            for t in rt.tasks:
                with contextlib.suppress(asyncio.CancelledError):
                    await t
            await client.aclose()
            await rt.store.close()

    app = FastAPI(title="OzWatch", version="0.1.0", lifespan=lifespan)
    app.include_router(router)
    if FRONTEND_DIR.is_dir():
        app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
    return app


async def _history_janitor(store: Store, hours: int) -> None:
    """Keep the history table to a rolling window so storage stays small."""
    while True:
        try:
            n = await store.trim_history(datetime.now(UTC) - timedelta(hours=hours))
            if n:
                log.info("history janitor removed %d rows", n)
        except Exception:  # noqa: BLE001
            log.exception("history janitor failed")
        await asyncio.sleep(600)


app = create_app()
