from datetime import UTC, datetime, timedelta

import pytest

from app.broadcaster import Broadcaster
from app.connectors.base import LayerInfo, StreamConnector
from app.schema import Feature
from app.store import MemoryStore
from app.streaming import StreamRunner

T0 = datetime(2026, 11, 5, tzinfo=UTC)


class FakeStream(StreamConnector):
    info = LayerInfo(
        key="boats",
        title="Boats",
        attribution="x",
        attribution_url="x",
        licence="x",
        poll_seconds=5,
        keep_history=True,
        stale_after_seconds=600,
        history_min_interval_s=60,
    )

    def __init__(self):
        super().__init__()
        self.messages: list = []
        self.error: Exception | None = None

    async def stream(self):
        for m in self.messages:
            yield m
        if self.error:
            raise self.error

    def handle(self, raw):
        if raw == "bad":
            raise ValueError("unparseable")
        return Feature(
            layer="boats", timestamp=raw.get("ts", T0), **{k: v for k, v in raw.items() if k != "ts"}
        )


class Clock:
    def __init__(self):
        self.now = T0

    def __call__(self):
        return self.now


@pytest.fixture
def rig():
    conn, store, bc, clock = FakeStream(), MemoryStore(), Broadcaster(), Clock()
    runner = StreamRunner(conn, store, bc, clock=clock)
    q = bc.subscribe()
    return conn, store, runner, q, clock


def drain(q):
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


def deltas(msgs):
    return [m for m in msgs if m["type"] == "delta"]


def boat(id_="a", lat=-33.9, lon=151.2, ts=T0, **props):
    return {"id": id_, "lat": lat, "lon": lon, "ts": ts, "properties": props}


async def test_many_messages_one_write_latest_wins(rig):
    conn, store, runner, q, clock = rig
    for i in range(20):
        runner.ingest(boat(lat=-33.9 + i * 0.001))
    runner.ingest(boat("b"))
    await runner.flush()

    (d,) = deltas(drain(q))
    assert sorted(f["id"] for f in d["upserts"]) == ["a", "b"]
    (a,) = [f for f in await store.get_layer("boats") if f.id == "a"]
    assert a.lat == pytest.approx(-33.9 + 19 * 0.001)
    assert runner.status.feature_count == 2

    # nothing new -> no delta
    await runner.flush()
    assert deltas(drain(q)) == []


async def test_unchanged_position_not_resent(rig):
    conn, store, runner, q, clock = rig
    runner.ingest(boat())
    await runner.flush()
    drain(q)
    runner.ingest(boat(ts=T0 + timedelta(seconds=10)))  # same place, newer report
    await runner.flush()
    assert deltas(drain(q)) == []


async def test_history_is_throttled_per_feature(rig):
    conn, store, runner, q, clock = rig
    for secs in (0, 30, 70, 100, 140):
        runner.ingest(boat(lon=151.2 + secs / 1e4, ts=T0 + timedelta(seconds=secs)))
        await runner.flush()
    kept = [int((h.timestamp - T0).total_seconds()) for h in store.history]
    assert kept == [0, 70, 140]


async def test_bad_messages_are_skipped(rig):
    conn, store, runner, q, clock = rig
    runner.ingest("bad")
    runner.ingest(boat())
    await runner.flush()
    assert [f.id for f in await store.get_layer("boats")] == ["a"]


async def test_stream_failure_then_recovery(rig):
    conn, store, runner, q, clock = rig
    conn.error = ConnectionError("refused")
    await runner.consume_once()
    assert runner.status.consecutive_failures == 1 and runner.status.state == "error"

    conn.messages, conn.error = [boat()], None
    await runner.consume_once()  # messages flowed (backoff reset), then server closed
    assert runner.status.consecutive_failures == 1
    assert "closed" in runner.status.last_error
    await runner.flush()
    assert runner.status.state == "degraded"  # has data, but not connected right now


async def test_ok_while_connected_and_receiving(rig):
    conn, store, runner, q, clock = rig
    runner._connected = True
    runner.ingest(boat())
    await runner.flush()
    s = runner.status.as_dict()
    assert s["state"] == "ok" and s["mode"] == "stream" and s["extra"]["connected"] is True

    clock.now += timedelta(minutes=3)  # connected but silent
    await runner.flush()
    assert runner.status.state == "degraded"


async def test_stale_features_removed(rig):
    conn, store, runner, q, clock = rig
    runner.ingest(boat())
    await runner.flush()
    drain(q)
    clock.now += timedelta(seconds=601)
    await runner.flush()
    (d,) = deltas(drain(q))
    assert d["removed"] == ["a"] and runner.status.feature_count == 0


async def test_store_failure_keeps_batch_for_next_flush(rig):
    conn, store, runner, q, clock = rig
    real = store.upsert

    async def broken(*a, **k):
        raise OSError("db down")

    store.upsert = broken
    runner.ingest(boat())
    await runner.flush()
    assert "db down" in runner.status.last_error and await store.get_layer("boats") == []

    store.upsert = real
    await runner.flush()
    assert [f.id for f in await store.get_layer("boats")] == ["a"]


async def test_records_one_run_per_minute(rig):
    conn, store, runner, q, clock = rig
    for _ in range(13):  # 13 flushes, 5 s apart = 60 s
        runner.ingest(boat())
        await runner.flush()
        clock.now += timedelta(seconds=5)
    assert len(store.runs) == 1 and store.runs[0]["n"] == 13
