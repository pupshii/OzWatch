"""Analytics panel: a handful of SQL queries over the live and 24 h history tables.

Each query lives in its own file under sql/analytics/ so it can be read (and
run in psql) on its own. This module only runs them, caches the result for a
minute, and shapes rows into JSON for the frontend.

Analytics need PostgreSQL/PostGIS. With the in-memory store the endpoint says
so instead of returning made-up numbers.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.store import PostgresStore, Store

log = logging.getLogger(__name__)

SQL_DIR = Path(__file__).resolve().parent.parent / "sql" / "analytics"
CACHE_SECONDS = 60

CAMERA_REGIONS = {
    "SYD_MET": "Sydney (central)",
    "SYD_NORTH": "Sydney (north)",
    "SYD_SOUTH": "Sydney (south)",
    "SYD_WEST": "Sydney (west)",
    "REG_NORTH": "Regional NSW (north)",
    "REG_SOUTH": "Regional NSW (south)",
}

# key -> (sql file, title, description shown under the chart)
SECTIONS: dict[str, tuple[str, str, str]] = {
    "flights_sydney_hourly": (
        "flights_sydney_hourly.sql",
        "Aircraft within 50 km of Sydney, per hour",
        "Distinct aircraft seen each hour over the last 24 h (OpenSky).",
    ),
    "flight_altitudes": (
        "flight_altitudes.sql",
        "Airborne aircraft by altitude",
        "Aircraft in the air over Australia right now, in 5,000 ft bands.",
    ),
    "ships_by_type": (
        "ships_by_type.sql",
        "Vessels by type",
        "Ships on the map right now; type comes from AIS static data.",
    ),
    "busiest_ports": (
        "busiest_ports.sql",
        "Busiest ports right now",
        "Vessels within 30 km of each port, each counted at its nearest port only.",
    ),
    "fires_by_level": (
        "fires_by_level.sql",
        "NSW RFS incidents by alert level",
        "Incidents currently listed by the NSW Rural Fire Service.",
    ),
    "fires_hourly": (
        "fires_hourly.sql",
        "NSW RFS incidents over 24 h",
        "Highest number of listed incidents in each hour.",
    ),
    "cameras_by_region": (
        "cameras_by_region.sql",
        "Traffic cameras by region",
        "TfNSW cameras currently online, by region.",
    ),
    "source_health": (
        "source_health.sql",
        "Data source reliability (24 h)",
        "Share of successful polls (streams: minutes connected) per source.",
    ),
}


class Analytics:
    def __init__(self, store: Store, clock=time.monotonic) -> None:
        self.store = store
        self._clock = clock
        self._sql = {key: (SQL_DIR / fname).read_text() for key, (fname, _, _) in SECTIONS.items()}
        self._cache: dict[str, Any] | None = None
        self._cached_at = 0.0
        self._lock = asyncio.Lock()

    @property
    def available(self) -> bool:
        return isinstance(self.store, PostgresStore)

    async def get(self) -> dict[str, Any]:
        if not self.available:
            return {
                "available": False,
                "reason": "Analytics need PostgreSQL. Run the full stack with `docker compose up`.",
            }
        async with self._lock:  # many viewers at once -> still one set of queries per minute
            if self._cache is None or self._clock() - self._cached_at >= CACHE_SECONDS:
                self._cache = await self._compute()
                self._cached_at = self._clock()
            return self._cache

    async def _compute(self) -> dict[str, Any]:
        sections: dict[str, Any] = {}
        for key, (_, title, description) in SECTIONS.items():
            try:
                rows = [dict(r) for r in await self.store.pool.fetch(self._sql[key])]
                sections[key] = {
                    "title": title,
                    "description": description,
                    "rows": [_jsonable(_shape(key, r)) for r in rows],
                }
            except Exception as exc:  # noqa: BLE001 - one bad query shouldn't blank the panel
                log.exception("analytics query %s failed", key)
                sections[key] = {"title": title, "description": description, "rows": [], "error": str(exc)}
        return {"available": True, "generated_at": datetime.now(UTC).isoformat(), "sections": sections}


def _shape(key: str, row: dict[str, Any]) -> dict[str, Any]:
    if key == "cameras_by_region":
        row["label"] = CAMERA_REGIONS.get(row["region"], row["region"])
    if key == "flight_altitudes":
        lo, hi = row["from_ft"], row["to_ft"]
        row["label"] = f"{lo // 1000}–{hi // 1000}k ft" if hi is not None else f"{lo // 1000}k+ ft"
    return row


def _jsonable(row: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for k, v in row.items():
        if isinstance(v, Decimal):
            v = float(v)
        elif isinstance(v, datetime):
            v = v.isoformat()
        out[k] = v
    return out
