"""Integration tests against a real PostGIS database.

Skipped unless TEST_DATABASE_URL is set, e.g.
  TEST_DATABASE_URL=postgresql://ozwatch:ozwatch@localhost:5432/ozwatch_test pytest
"""

import os
from datetime import UTC, datetime, timedelta

import pytest

from app.schema import Feature
from app.store import BBox, PostgresStore

DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = [pytest.mark.postgres, pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")]

T0 = datetime(2026, 11, 3, tzinfo=UTC)


def feat(fid, lat, lon, ts=T0, **props):
    return Feature(id=fid, layer="t", lat=lat, lon=lon, timestamp=ts, properties=props)


@pytest.fixture
async def store():
    s = PostgresStore(DSN)
    await s.init()
    async with s.pool.acquire() as c:
        await c.execute("TRUNCATE features_latest, features_history, connector_runs")
    yield s
    await s.close()


async def test_upsert_roundtrip_and_update(store):
    await store.upsert("t", [feat("a", -33.87, 151.2, title="Anzac")], seen_at=T0, keep_history=False)
    await store.upsert("t", [feat("a", -33.88, 151.21, title="Anzac 2")], seen_at=T0, keep_history=False)
    (f,) = await store.get_layer("t")
    assert (f.lat, f.lon) == pytest.approx((-33.88, 151.21))
    assert f.properties == {"title": "Anzac 2"}
    assert f.timestamp == T0


async def test_bbox_uses_postgis(store):
    await store.upsert(
        "t", [feat("syd", -33.87, 151.2), feat("mel", -37.81, 144.96)], seen_at=T0, keep_history=False
    )
    got = await store.get_layer("t", BBox(150.5, -34.2, 151.4, -33.5))
    assert [f.id for f in got] == ["syd"]


async def test_prune_and_history(store):
    await store.upsert("t", [feat("a", -33.0, 151.0)], seen_at=T0, keep_history=True)
    await store.upsert(
        "t",
        [feat("b", -34.0, 151.0, ts=T0 + timedelta(hours=1))],
        seen_at=T0 + timedelta(hours=1),
        keep_history=True,
    )
    assert await store.prune_stale("t", older_than=T0 + timedelta(minutes=30)) == ["a"]
    assert await store.trim_history(before=T0 + timedelta(minutes=30)) == 1
    await store.record_run("t", T0, T0, True, 1, None)
