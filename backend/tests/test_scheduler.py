from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.broadcaster import Broadcaster
from app.connectors.base import Connector, LayerInfo
from app.scheduler import RETRY_BASE_SECONDS, RETRY_MAX_SECONDS, Poller
from app.schema import Feature
from app.store import MemoryStore

T0 = datetime(2026, 11, 3, 0, 0, tzinfo=UTC)


class FakeConnector(Connector):
    info = LayerInfo(
        key="fake",
        title="Fake",
        attribution="x",
        attribution_url="x",
        licence="x",
        poll_seconds=60,
        keep_history=True,
        stale_after_seconds=300,
    )

    def __init__(self):
        super().__init__(client=None)
        self.payload: list[dict] | Exception = []

    async def fetch(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload

    def normalise(self, raw):
        return [Feature(layer="fake", timestamp=T0, **r) for r in raw]


class Clock:
    def __init__(self):
        self.now = T0

    def __call__(self):
        return self.now


@pytest.fixture
def setup():
    conn, store, bc, clock = FakeConnector(), MemoryStore(), Broadcaster(), Clock()
    poller = Poller(conn, store, bc, clock=clock)
    q = bc.subscribe()
    return conn, store, poller, q, clock


def drain(q):
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


def deltas(msgs):
    return [m for m in msgs if m["type"] == "delta"]


A = {"id": "a", "lat": -33.8, "lon": 151.2, "properties": {"v": 1}}
B = {"id": "b", "lat": -37.8, "lon": 145.0, "properties": {"v": 1}}


async def test_first_poll_sends_everything_then_only_changes(setup):
    conn, store, poller, q, clock = setup
    conn.payload = [A, B]
    assert await poller.poll_once()
    (d,) = deltas(drain(q))
    assert {f["id"] for f in d["upserts"]} == {"a", "b"}

    # identical data -> no delta, just a status heartbeat
    await poller.poll_once()
    msgs = drain(q)
    assert deltas(msgs) == [] and msgs[-1]["type"] == "status"

    # one feature moves -> delta containing only that one
    conn.payload = [A, {**B, "lat": -37.9}]
    await poller.poll_once()
    (d,) = deltas(drain(q))
    assert [f["id"] for f in d["upserts"]] == ["b"]
    assert poller.status.state == "ok" and poller.status.feature_count == 2


async def test_features_not_seen_for_a_while_are_removed(setup):
    conn, store, poller, q, clock = setup
    conn.payload = [A, B]
    await poller.poll_once()
    drain(q)

    conn.payload = [A]
    clock.now = T0 + timedelta(seconds=200)  # B not stale yet (stale_after=300)
    await poller.poll_once()
    assert all(not d["removed"] for d in deltas(drain(q)))

    clock.now = T0 + timedelta(seconds=400)
    await poller.poll_once()
    (d,) = deltas(drain(q))
    assert d["removed"] == ["b"]
    assert [f.id for f in await store.get_layer("fake")] == ["a"]


async def test_failure_keeps_last_good_data_and_backs_off(setup):
    conn, store, poller, q, clock = setup
    conn.payload = [A]
    await poller.poll_once()
    assert poller.next_delay() == 60

    conn.payload = httpx.ConnectError("boom")
    delays = []
    for _ in range(12):
        assert not await poller.poll_once()
        delays.append(poller.next_delay())

    assert poller.status.state == "degraded"
    assert poller.status.consecutive_failures == 12
    assert "ConnectError" in poller.status.last_error
    assert RETRY_BASE_SECONDS * 0.8 <= delays[0] <= RETRY_BASE_SECONDS * 1.2
    assert delays[3] > delays[0]  # grows
    assert max(delays) <= RETRY_MAX_SECONDS * 1.2  # but is capped
    assert [f.id for f in await store.get_layer("fake")] == ["a"]  # still serving old data
    assert sum(not r["ok"] for r in store.runs) == 12

    conn.payload = [A]
    await poller.poll_once()
    assert poller.status.state == "ok" and poller.next_delay() == 60


async def test_error_state_when_never_succeeded(setup):
    conn, _, poller, _, _ = setup
    conn.payload = RuntimeError("bad key")
    await poller.poll_once()
    assert poller.status.state == "error"


async def test_honours_retry_after_on_429(setup):
    conn, _, poller, _, _ = setup
    resp = httpx.Response(429, headers={"Retry-After": "120"}, request=httpx.Request("GET", "https://x"))
    conn.payload = httpx.HTTPStatusError("429", request=resp.request, response=resp)
    await poller.poll_once()
    assert poller.next_delay() == 120
    assert poller.status.last_error.startswith("HTTP 429")


async def test_history_written_for_moving_layers(setup):
    conn, store, poller, _, _ = setup
    conn.payload = [A]
    await poller.poll_once()
    assert len(store.history) == 1
