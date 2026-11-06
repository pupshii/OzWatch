"""Train, metro and light rail lines on the map, from TfNSW GTFS timetables.

Pipeline (runs weekly, in the background):
  1. Download each mode's GTFS timetable zip (same TfNSW key as the live feeds).
  2. Read routes.txt and keep rail-type routes only (no replacement buses), with
     their official colours; read trips.txt to map each shape to its line.
  3. Stream shapes.txt rows into PostGIS with COPY (memory-safe on a small VM).
  4. In SQL: build each shape as a line, simplify, merge every variant of a line
     into one geometry (ST_Union removes the duplicated track), store per line.

Endpoint paths: Sydney Trains is confirmed (Transitland registry). Metro and light
rail aren't documented anywhere we could verify, so each mode has a short list of
candidate paths (named like the live feeds); the first that returns a valid zip
is used, and /health shows which one worked or why none did.
"""

from __future__ import annotations

import asyncio
import csv
import io
import logging
import re
import tempfile
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger(__name__)

BASE = "https://api.transport.nsw.gov.au"
MAX_DOWNLOAD_BYTES = 300 * 1024 * 1024
REBUILD_AFTER = timedelta(days=7)
CHECK_EVERY_S = 6 * 3600


@dataclass(frozen=True)
class StaticFeed:
    key: str
    mode: str  # train | metro | lightrail
    candidates: tuple[str, ...]


FEEDS = [
    StaticFeed("sydneytrains", "train", ("/v1/gtfs/schedule/sydneytrains",)),
    StaticFeed("metro", "metro", ("/v2/gtfs/schedule/metro", "/v1/gtfs/schedule/metro")),
    StaticFeed(
        "lr_innerwest",
        "lightrail",
        ("/v1/gtfs/schedule/lightrail/innerwest", "/v2/gtfs/schedule/lightrail/innerwest"),
    ),
    StaticFeed("lr_cbd", "lightrail", ("/v1/gtfs/schedule/lightrail/cbdandsoutheast",)),
    StaticFeed("lr_parramatta", "lightrail", ("/v1/gtfs/schedule/lightrail/parramatta",)),
]

FALLBACK_COLOR = {"train": "f6891f", "metro": "168388", "lightrail": "ee343f"}


# ---------- GTFS parsing (pure, unit-tested) ----------


@dataclass(frozen=True)
class RouteInfo:
    line: str
    name: str | None
    color: str | None


def is_rail_route_type(value: str) -> bool:
    """Basic and extended GTFS route types for trams, metro and rail. Excludes buses,
    coaches and ferries, so rail-replacement buses in a train feed are dropped."""
    try:
        t = int(value)
    except (TypeError, ValueError):
        return False
    return t in {0, 1, 2, 5, 6, 7, 11, 12} or 100 <= t <= 199 or 400 <= t <= 499 or 900 <= t <= 999


def open_member(zf: zipfile.ZipFile, name: str) -> io.TextIOWrapper | None:
    """Open a GTFS file whether it sits at the zip root or inside a folder."""
    for member in zf.namelist():
        if member == name or member.endswith("/" + name):
            return io.TextIOWrapper(zf.open(member), encoding="utf-8-sig", newline="")
    return None


def rail_routes(zf: zipfile.ZipFile) -> dict[str, RouteInfo]:
    f = open_member(zf, "routes.txt")
    if f is None:
        raise ValueError("routes.txt missing")
    out = {}
    with f:
        for r in csv.DictReader(f):
            if not is_rail_route_type(r.get("route_type", "")):
                continue
            line = (r.get("route_short_name") or "").strip() or (r.get("route_long_name") or "").strip()
            line = line or r["route_id"]
            color = (r.get("route_color") or "").strip().lstrip("#").lower()
            out[r["route_id"]] = RouteInfo(
                line=line[:40],
                name=(r.get("route_long_name") or "").strip() or None,
                color=color if re.fullmatch(r"[0-9a-f]{6}", color) else None,
            )
    return out


def shape_routes(zf: zipfile.ZipFile, routes: dict[str, RouteInfo]) -> dict[str, str]:
    """shape_id -> route_id, for trips on rail routes that have a shape."""
    f = open_member(zf, "trips.txt")
    if f is None:
        raise ValueError("trips.txt missing")
    out = {}
    with f:
        for t in csv.DictReader(f):
            sid = (t.get("shape_id") or "").strip()
            if sid and t.get("route_id") in routes:
                out.setdefault(sid, t["route_id"])
    return out


def shape_points(zf: zipfile.ZipFile, wanted: set[str]) -> Iterator[tuple[str, int, float, float]]:
    f = open_member(zf, "shapes.txt")
    if f is None:
        raise ValueError("shapes.txt missing")
    with f:
        for p in csv.DictReader(f):
            sid = p.get("shape_id", "")
            if sid not in wanted:
                continue
            try:
                yield sid, int(p["shape_pt_sequence"]), float(p["shape_pt_lon"]), float(p["shape_pt_lat"])
            except (KeyError, ValueError):
                continue


# ---------- builder ----------


@dataclass
class LinesStatus:
    state: str = "idle"  # idle | building | ok | partial | error | disabled
    last_build: datetime | None = None
    feeds: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "last_build": self.last_build.isoformat() if self.last_build else None,
            "feeds": self.feeds,
        }


BUILD_SQL = """
WITH shapes AS (
    SELECT shape_id,
           ST_Simplify(ST_MakeLine(ST_SetSRID(ST_MakePoint(lon, lat), 4326) ORDER BY seq), 0.00002) AS geom
    FROM transit_shape_points
    WHERE feed = $1
    GROUP BY shape_id
    HAVING count(*) >= 2
)
INSERT INTO transit_lines (feed, mode, line, name, color, geom, built_at)
SELECT $1, $2, r.line, max(r.name), max(r.color),
       -- ST_Union nodes + de-duplicates the track shared by a line's many variants;
       -- ST_LineMerge stitches the pieces back into long lines; a final simplify keeps it light.
       ST_Multi(ST_Simplify(ST_LineMerge(ST_Union(s.geom)), 0.00004)),
       now()
FROM shapes s
JOIN transit_shape_routes r ON r.feed = $1 AND r.shape_id = s.shape_id
GROUP BY r.line
"""


class TransitLinesBuilder:
    def __init__(self, client: httpx.AsyncClient, pool, api_key: str) -> None:
        self.client = client
        self.pool = pool
        self.api_key = api_key
        self.status = LinesStatus(state="idle" if api_key else "disabled")

    async def run_forever(self) -> None:
        if not self.api_key:
            return
        while True:
            try:
                if await self._needs_build():
                    await self.build_all()
            except Exception:  # noqa: BLE001
                log.exception("transit lines: build failed")
                self.status.state = "error"
            await asyncio.sleep(CHECK_EVERY_S)

    async def _needs_build(self) -> bool:
        last = await self.pool.fetchval("SELECT max(built_at) FROM transit_lines")
        if last:
            self.status.last_build = last
            if self.status.state == "idle":
                self.status.state = "ok"
        return last is None or datetime.now(UTC) - last > REBUILD_AFTER

    async def build_all(self) -> None:
        self.status.state = "building"
        ok = 0
        for feed in FEEDS:
            try:
                path, n_lines = await self.build_feed(feed)
                self.status.feeds[feed.key] = {"ok": True, "path": path, "lines": n_lines}
                ok += 1
            except Exception as exc:  # noqa: BLE001 - one missing feed must not stop the others
                self.status.feeds[feed.key] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:200]}
                log.warning("transit lines: %s failed: %s", feed.key, exc)
            await asyncio.sleep(1)
        self.status.state = "ok" if ok == len(FEEDS) else ("partial" if ok else "error")
        self.status.last_build = datetime.now(UTC)

    async def build_feed(self, feed: StaticFeed) -> tuple[str, int]:
        with tempfile.TemporaryDirectory() as tmp:
            zpath = Path(tmp) / "gtfs.zip"
            path = await self._download_first(feed, zpath)
            with zipfile.ZipFile(zpath) as zf:
                routes = rail_routes(zf)
                shapes = shape_routes(zf, routes)
                if not shapes:
                    raise ValueError("no rail routes with shapes in this feed")
                n = await self._load(feed, zf, routes, shapes)
        log.info("transit lines: %s -> %d lines (from %s)", feed.key, n, path)
        return path, n

    async def _download_first(self, feed: StaticFeed, dest: Path) -> str:
        errors = []
        for path in feed.candidates:
            try:
                async with self.client.stream(
                    "GET", BASE + path, headers={"Authorization": f"apikey {self.api_key}"}, timeout=120
                ) as resp:
                    if resp.status_code != 200:
                        errors.append(f"{path}: HTTP {resp.status_code}")
                        continue
                    size = 0
                    with dest.open("wb") as out:
                        async for chunk in resp.aiter_bytes():
                            size += len(chunk)
                            if size > MAX_DOWNLOAD_BYTES:
                                raise ValueError(f"{path}: larger than {MAX_DOWNLOAD_BYTES // 2**20} MB")
                            out.write(chunk)
                if zipfile.is_zipfile(dest):
                    return path
                errors.append(f"{path}: not a zip file")
            except httpx.HTTPError as exc:
                errors.append(f"{path}: {type(exc).__name__}")
        raise RuntimeError("; ".join(errors))

    async def _load(self, feed: StaticFeed, zf, routes, shapes) -> int:
        fallback = FALLBACK_COLOR.get(feed.mode)
        route_rows = [
            (feed.key, sid, routes[rid].line, routes[rid].name, routes[rid].color or fallback)
            for sid, rid in shapes.items()
        ]
        async with self.pool.acquire() as conn, conn.transaction():
            await conn.execute("DELETE FROM transit_shape_points WHERE feed = $1", feed.key)
            await conn.execute("DELETE FROM transit_shape_routes WHERE feed = $1", feed.key)
            await conn.copy_records_to_table(
                "transit_shape_routes", records=route_rows,
                columns=["feed", "shape_id", "line", "name", "color"],
            )  # fmt: skip
            await conn.copy_records_to_table(
                "transit_shape_points",
                records=((feed.key, *pt) for pt in shape_points(zf, set(shapes))),
                columns=["feed", "shape_id", "seq", "lon", "lat"],
            )
            await conn.execute("DELETE FROM transit_lines WHERE feed = $1", feed.key)
            await conn.execute(BUILD_SQL, feed.key, feed.mode)
            n = await conn.fetchval("SELECT count(*) FROM transit_lines WHERE feed = $1", feed.key)
            # staging rows are no longer needed
            await conn.execute("DELETE FROM transit_shape_points WHERE feed = $1", feed.key)
        return n


async def lines_geojson(pool) -> dict[str, Any]:
    rows = await pool.fetch(
        """
        SELECT mode, line, name, color, ST_AsGeoJSON(geom, 5)::jsonb AS geometry
        FROM transit_lines
        ORDER BY CASE mode WHEN 'lightrail' THEN 1 WHEN 'metro' THEN 2 ELSE 3 END, line
        """
    )
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": r["geometry"],
                "properties": {
                    "mode": r["mode"],
                    "line": r["line"],
                    "name": r["name"],
                    "color": f"#{r['color']}",
                },  # fmt: skip
            }
            for r in rows
        ],
    }
