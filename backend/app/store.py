"""Storage. Two interchangeable implementations behind one interface:

* PostgresStore - PostGIS, used in Docker / production.
* MemoryStore   - plain dicts, used in tests and when DATABASE_URL is unset,
                  so the app can be tried with zero setup.

The rest of the app only talks to the `Store` protocol, so swapping the
database (or adding Redis later) doesn't ripple through the code.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from app.schema import Feature

log = logging.getLogger(__name__)
SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


@dataclass
class BBox:
    west: float
    south: float
    east: float
    north: float

    def contains(self, f: Feature) -> bool:
        return self.south <= f.lat <= self.north and self.west <= f.lon <= self.east


class Store(Protocol):
    async def init(self) -> None: ...
    async def close(self) -> None: ...
    async def upsert(
        self, layer: str, features: list[Feature], seen_at: datetime, history: list[Feature] | None = None
    ) -> None:
        """Upsert `features` as the latest state; also append `history` rows (may be a subset)."""
        ...

    async def prune_stale(self, layer: str, older_than: datetime) -> list[str]: ...
    async def get_layer(self, layer: str, bbox: BBox | None = None) -> list[Feature]: ...
    async def record_run(
        self, layer: str, started: datetime, finished: datetime, ok: bool, n: int, error: str | None
    ) -> None: ...
    async def trim_history(self, before: datetime) -> int: ...


class MemoryStore:
    def __init__(self) -> None:
        self.latest: dict[str, dict[str, tuple[Feature, datetime]]] = {}
        self.history: list[Feature] = []
        self._history_keys: set[tuple] = set()
        self.runs: list[dict] = []

    async def init(self) -> None:
        pass

    async def close(self) -> None:
        pass

    async def upsert(self, layer, features, seen_at, history=None) -> None:
        bucket = self.latest.setdefault(layer, {})
        for f in features:
            bucket[f.id] = (f, seen_at)
        for f in history or []:
            key = (f.layer, f.id, f.timestamp)
            if key not in self._history_keys:
                self._history_keys.add(key)
                self.history.append(f)

    async def prune_stale(self, layer, older_than) -> list[str]:
        bucket = self.latest.get(layer, {})
        gone = [fid for fid, (_, seen) in bucket.items() if seen < older_than]
        for fid in gone:
            del bucket[fid]
        return gone

    async def get_layer(self, layer, bbox=None) -> list[Feature]:
        feats = [f for f, _ in self.latest.get(layer, {}).values()]
        return [f for f in feats if bbox.contains(f)] if bbox else feats

    async def record_run(self, layer, started, finished, ok, n, error) -> None:
        self.runs.append(dict(layer=layer, started=started, finished=finished, ok=ok, n=n, error=error))

    async def trim_history(self, before) -> int:
        n0 = len(self.history)
        self.history = [h for h in self.history if h.timestamp >= before]
        self._history_keys = {(h.layer, h.id, h.timestamp) for h in self.history}
        return n0 - len(self.history)


class PostgresStore:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        self.pool = None

    async def init(self) -> None:
        import asyncpg  # imported lazily so tests without Postgres don't need it

        self.pool = await asyncpg.create_pool(self.dsn, min_size=1, max_size=5, init=_init_conn)
        async with self.pool.acquire() as conn:
            for sql_file in sorted(SQL_DIR.glob("*.sql")):
                await conn.execute(sql_file.read_text())
        log.info("connected to Postgres and applied %s", [p.name for p in sorted(SQL_DIR.glob("*.sql"))])

    async def close(self) -> None:
        if self.pool:
            await self.pool.close()

    async def upsert(self, layer, features, seen_at, history=None) -> None:
        if not features and not history:
            return
        rows = [(layer, f.id, f.lon, f.lat, f.timestamp, seen_at, f.properties) for f in features]
        async with self.pool.acquire() as conn, conn.transaction():
            await conn.executemany(  # no-op for an empty list
                """
                INSERT INTO features_latest (layer, id, geom, observed_at, last_seen_at, properties)
                VALUES ($1, $2, ST_SetSRID(ST_MakePoint($3, $4), 4326), $5, $6, $7)
                ON CONFLICT (layer, id) DO UPDATE SET
                    geom = EXCLUDED.geom,
                    observed_at = EXCLUDED.observed_at,
                    last_seen_at = EXCLUDED.last_seen_at,
                    properties = EXCLUDED.properties
                """,
                rows,
            )
            if history:
                await conn.executemany(
                    """
                    INSERT INTO features_history (layer, id, geom, observed_at, properties)
                    VALUES ($1, $2, ST_SetSRID(ST_MakePoint($3, $4), 4326), $5, $6)
                    ON CONFLICT DO NOTHING
                    """,
                    [(layer, f.id, f.lon, f.lat, f.timestamp, f.properties) for f in history],
                )

    async def prune_stale(self, layer, older_than) -> list[str]:
        rows = await self.pool.fetch(
            "DELETE FROM features_latest WHERE layer = $1 AND last_seen_at < $2 RETURNING id",
            layer,
            older_than,
        )
        return [r["id"] for r in rows]

    async def get_layer(self, layer, bbox=None) -> list[Feature]:
        sql = """
            SELECT id, layer, ST_Y(geom) AS lat, ST_X(geom) AS lon, observed_at, properties
            FROM features_latest WHERE layer = $1
        """
        args: list = [layer]
        if bbox:
            # `&&` is an index-assisted bounding-box overlap test (uses the GiST index).
            sql += " AND geom && ST_MakeEnvelope($2, $3, $4, $5, 4326)"
            args += [bbox.west, bbox.south, bbox.east, bbox.north]
        rows = await self.pool.fetch(sql, *args)
        return [
            Feature(
                id=r["id"],
                layer=r["layer"],
                lat=r["lat"],
                lon=r["lon"],
                timestamp=r["observed_at"],
                properties=r["properties"],
            )
            for r in rows
        ]

    async def record_run(self, layer, started, finished, ok, n, error) -> None:
        await self.pool.execute(
            "INSERT INTO connector_runs (layer, started_at, finished_at, ok, n_features, error)"
            " VALUES ($1, $2, $3, $4, $5, $6)",
            layer,
            started,
            finished,
            ok,
            n,
            error,
        )

    async def trim_history(self, before) -> int:
        result = await self.pool.execute("DELETE FROM features_history WHERE observed_at < $1", before)
        await self.pool.execute("DELETE FROM connector_runs WHERE started_at < $1", before)
        return int(result.split()[-1])


async def _init_conn(conn) -> None:
    # Let asyncpg hand us Python dicts for jsonb columns (and accept dicts on insert).
    await conn.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


def make_store(database_url: str) -> Store:
    return PostgresStore(database_url) if database_url else MemoryStore()
