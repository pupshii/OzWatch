"""Analytics SQL, run against a real PostGIS database with seeded data.

Skipped unless TEST_DATABASE_URL is set (see test_store_postgres.py).
"""

import os
from datetime import UTC, datetime, timedelta

import pytest

from app.analytics import Analytics
from app.schema import Feature
from app.store import MemoryStore, PostgresStore

DSN = os.environ.get("TEST_DATABASE_URL")
pg = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")


def f(layer, fid, lat, lon, ts, **props):
    return Feature(id=fid, layer=layer, lat=lat, lon=lon, timestamp=ts, properties=props)


async def test_memory_store_says_unavailable():
    result = await Analytics(MemoryStore()).get()
    assert result["available"] is False and "PostgreSQL" in result["reason"]


@pytest.fixture
async def seeded():
    store = PostgresStore(DSN)
    await store.init()
    async with store.pool.acquire() as c:
        await c.execute("TRUNCATE features_latest, features_history, connector_runs")

    now = datetime.now(UTC)
    # middle of the hour two hours ago, so the two sightings below share a bucket
    two_h = now.replace(minute=20, second=0, microsecond=0) - timedelta(hours=2)

    history = [
        f("flights", "a", -33.95, 151.18, two_h, on_ground=False),  # near Sydney airborne
        f("flights", "a", -33.90, 151.10, two_h + timedelta(minutes=2), on_ground=False),  # same plane again
        f("flights", "b", -33.94, 151.17, two_h, on_ground=True),  # near Sydney on the ground
        f("flights", "c", -37.67, 144.84, two_h, on_ground=False),  # Melbourne: outside 50 km
        f("flights", "d", -33.94, 151.17, now - timedelta(hours=30), on_ground=False),  # too old
        f("flights", "e", -33.20, 151.20, two_h, on_ground=False),  # ~74 km north: outside radius
    ]
    await store.upsert("flights", [], seen_at=now, history=history)

    latest_flights = [
        f("flights", "p1", -30, 140, now, on_ground=False, altitude_ft=2000),
        f("flights", "p2", -30, 141, now, on_ground=False, altitude_ft=-50),  # below sea level (baro)
        f("flights", "p3", -30, 142, now, on_ground=False, altitude_ft=37000),
        f("flights", "p4", -30, 143, now, on_ground=False, altitude_ft=50000),
        f("flights", "p5", -30, 144, now, on_ground=True, altitude_ft=0),  # excluded: on ground
    ]
    await store.upsert("flights", latest_flights, seen_at=now)

    ships = [
        f("ships", "s1", -33.975, 151.215, now, category="Cargo", moving=True),  # Port Botany
        f("ships", "s2", -33.93, 151.21, now, category="Tanker", moving=False),  # between Harbour & Botany
        f("ships", "s3", -32.93, 151.80, now, category="Unknown", moving=False),  # Newcastle
        f("ships", "s4", -36.00, 155.00, now, category="Unknown", moving=True),  # open ocean
    ]
    await store.upsert("ships", ships, seen_at=now)

    fires = [
        f("fires", "f1", -33.7, 150.3, now, alert_level="Advice", is_fire=True),
        f("fires", "f2", -33.8, 150.4, now, alert_level="Advice", is_fire=False),
        f("fires", "f3", -33.9, 150.5, now, alert_level="Emergency Warning", is_fire=True),
    ]
    await store.upsert("fires", fires, seen_at=now)
    await store.upsert(
        "cameras",
        [
            f("cameras", "c1", -33.87, 151.2, now, region="SYD_MET"),
            f("cameras", "c2", -33.88, 151.2, now, region="SYD_MET"),
            f("cameras", "c3", -32.9, 151.7, now, region="REG_NORTH"),
        ],
        seen_at=now,
    )

    one_h = now - timedelta(hours=1)
    for started, ok, n, err in [
        (one_h, True, 10, None),
        (one_h + timedelta(minutes=1), True, 12, None),
        (one_h + timedelta(minutes=2), False, 0, "HTTP 503"),
    ]:
        await store.record_run("fires", started, started, ok, n, err)

    yield store
    await store.close()


def section(result, key):
    s = result["sections"][key]
    assert "error" not in s, s.get("error")
    return s["rows"]


@pg
async def test_flights_over_sydney_hourly(seeded):
    rows = section(await Analytics(seeded).get(), "flights_sydney_hourly")
    assert len(rows) == 24  # gap-filled: every hour present
    busy = [r for r in rows if r["aircraft"]]
    assert len(busy) == 1
    assert busy[0]["aircraft"] == 2  # a (counted once despite 2 sightings) + b
    assert busy[0]["airborne"] == 1


@pg
async def test_altitude_bands(seeded):
    rows = {r["label"]: r["aircraft"] for r in section(await Analytics(seeded).get(), "flight_altitudes")}
    assert len(rows) == 10
    assert rows["0–5k ft"] == 2  # includes the -50 ft reading
    assert rows["35–40k ft"] == 1 and rows["45k+ ft"] == 1
    assert sum(rows.values()) == 4


@pg
async def test_ships_and_ports(seeded):
    result = await Analytics(seeded).get()
    by_type = {r["category"]: (r["ships"], r["moving"]) for r in section(result, "ships_by_type")}
    assert by_type == {"Unknown": (2, 1), "Cargo": (1, 1), "Tanker": (1, 0)}

    ports = {r["port"]: (r["ships"], r["stationary"]) for r in section(result, "busiest_ports")}
    # s2 is within 30 km of both Sydney Harbour and Port Botany but counted once, at the nearer
    assert ports == {"Port Botany": (2, 1), "Newcastle": (1, 1)}


@pg
async def test_fires_and_cameras(seeded):
    result = await Analytics(seeded).get()
    levels = section(result, "fires_by_level")
    assert [r["alert_level"] for r in levels] == ["Emergency Warning", "Advice"]  # urgency order
    assert levels[1]["incidents"] == 2 and levels[1]["fires"] == 1

    hourly = section(result, "fires_hourly")
    assert len(hourly) == 24
    assert [r["incidents"] for r in hourly if r["incidents"] is not None] == [12]  # max in that hour
    assert hourly[-1]["incidents"] is None  # no poll this hour -> unknown, not zero

    cams = {r["label"]: r["cameras"] for r in section(result, "cameras_by_region")}
    assert cams == {"Sydney (central)": 2, "Regional NSW (north)": 1}


@pg
async def test_source_health(seeded):
    (fires,) = section(await Analytics(seeded).get(), "source_health")
    assert fires["layer"] == "fires" and fires["runs"] == 3 and fires["ok_runs"] == 2
    assert fires["success_pct"] == pytest.approx(66.7)
    assert fires["avg_features"] == 11 and fires["last_error"] == "HTTP 503"


@pg
async def test_results_are_cached(seeded):
    t = [0.0]
    a = Analytics(seeded, clock=lambda: t[0])
    first = await a.get()
    async with seeded.pool.acquire() as c:
        await c.execute("TRUNCATE features_latest")
    assert await a.get() is first  # within 60 s: cached
    t[0] = 61
    assert section(await a.get(), "ships_by_type") == []  # refreshed
