import math
from datetime import UTC, datetime

import httpx
import pytest
import respx
from google.transit import gtfs_realtime_pb2 as gtfs

from app.broadcaster import Broadcaster
from app.connectors.tfnsw_transit import (
    BASE,
    FEEDS,
    TfnswTransitConnector,
    route_short_name,
)
from app.scheduler import Poller
from app.store import MemoryStore

NOW = datetime(2026, 11, 6, 8, 0, tzinfo=UTC)
T = int(NOW.timestamp())


def feed(*vehicles, header_ts=T):
    msg = gtfs.FeedMessage()
    msg.header.gtfs_realtime_version = "2.0"
    msg.header.timestamp = header_ts
    for i, v in enumerate(vehicles):
        e = msg.entity.add()
        e.id = f"e{i}"
        vp = e.vehicle
        vp.vehicle.id = v.get("id", f"v{i}")
        if "label" in v:
            vp.vehicle.label = v["label"]
        if "route" in v:
            vp.trip.route_id = v["route"]
        if v.get("position", True):
            vp.position.latitude = v.get("lat", -33.87)
            vp.position.longitude = v.get("lon", 151.21)
            if "bearing" in v:
                vp.position.bearing = v["bearing"]
            if "speed" in v:
                vp.position.speed = v["speed"]
        if "ts" in v:
            vp.timestamp = v["ts"]
        if "occupancy" in v:
            vp.occupancy_status = v["occupancy"]
        if "status" in v:
            vp.current_status = v["status"]
    return msg.SerializeToString()


def make(api_key="k", client=None, sleeps=None):
    async def fake_sleep(s):
        if sleeps is not None:
            sleeps.append(s)

    return TfnswTransitConnector(
        client or httpx.AsyncClient(), api_key=api_key, now=lambda: NOW, sleep=fake_sleep
    )


def test_bus_vehicle_normalised():
    raw = [("buses", feed({"id": "bus42", "route": "2436_333", "bearing": 91.6, "speed": 10.0,
                           "occupancy": 2, "status": 2, "ts": T - 20}))]  # fmt: skip
    (f,) = make().normalise(raw)
    assert f.id == "buses:bus42" and (f.lat, f.lon) == (-33.87, 151.21)  # float32 noise rounded away
    p = f.properties
    assert p["mode"] == "bus" and p["route"] == "333" and p["route_id"] == "2436_333"
    assert p["bearing"] == 92 and p["speed_kmh"] == 36
    assert p["occupancy"] == "Few seats available" and p["status"] == "On the way to"
    assert p["title"] == "333" and p["sample"] is False


def test_train_uses_label_and_feed_mode():
    (f,) = make().normalise([("sydneytrains", feed({"route": "BMT_1", "label": "14:12 Central to Hornsby"}))])
    p = f.properties
    assert p["mode"] == "train" and p["operator"] == "Sydney Trains"
    assert p["route"] == "BMT_1" and p["label"] == "14:12 Central to Hornsby"
    assert p["occupancy"] is None and p["bearing"] is None  # optional fields absent


def test_bad_vehicles_skipped():
    raw = [("buses", feed(
        {"id": "nopos", "position": False},
        {"id": "stale", "ts": T - 600},
        {"id": "null-island", "lat": 0.0, "lon": 0.0},
        {"id": "nan", "bearing": math.nan},
        {"id": "ok"},
    ))]  # fmt: skip
    feats = {f.id: f for f in make().normalise(raw)}
    assert set(feats) == {"buses:nan", "buses:ok"}
    assert feats["buses:nan"].properties["bearing"] is None


def test_duplicate_vehicle_counted_once_and_corrupt_feed_ignored():
    raw = [
        ("buses", feed({"id": "dup", "lat": -33.80}, {"id": "dup", "lat": -33.81})),
        ("ferries", b"\x08\x96\x01not-protobuf"),
        ("lr_cbd", feed({"id": "lr1", "route": "L2"})),
    ]
    feats = {f.id: f for f in make().normalise(raw)}
    assert set(feats) == {"buses:dup", "lr_cbd:lr1"}
    assert feats["buses:dup"].lat == -33.81  # last report wins
    assert feats["lr_cbd:lr1"].properties["mode"] == "lightrail"


def test_route_short_name():
    assert route_short_name("bus", "2436_333") == "333"
    assert route_short_name("bus", "M30") == "M30"
    assert route_short_name("ferry", "F1") == "F1"
    assert route_short_name("train", None) is None


@respx.mock
async def test_fetch_all_feeds_paced_and_partial_failure_tolerated():
    for f in FEEDS:
        respx.get(BASE + f.path).mock(return_value=httpx.Response(200, content=feed({"id": f.key})))
    respx.get(BASE + "/v1/gtfs/vehiclepos/ferries/sydneyferries").mock(return_value=httpx.Response(503))

    sleeps = []
    conn = make(api_key="secret", sleeps=sleeps)
    feats = await conn.run_once()

    assert len(feats) == len(FEEDS) - 1
    assert conn.meta["feeds_ok"] == len(FEEDS) - 1 and set(conn.meta["feeds_failed"]) == {"ferries"}
    assert len(sleeps) == len(FEEDS) - 1 and all(s >= 0.2 for s in sleeps)  # <= 5 req/s
    assert respx.calls.last.request.headers["Authorization"] == "apikey secret"


@respx.mock
async def test_all_feeds_failing_raises():
    respx.route(host="api.transport.nsw.gov.au").mock(return_value=httpx.Response(403))
    with pytest.raises(RuntimeError, match="all 7 transit feeds failed"):
        await make().fetch()


async def test_demo_mode_offline():
    with respx.mock(assert_all_called=False) as mock:
        feats = await make(api_key="").run_once()
        assert not mock.calls
    assert len(feats) == 6
    assert {f.properties["mode"] for f in feats} == {"train", "metro", "bus", "ferry", "lightrail"}
    assert all(f.properties["sample"] for f in feats)


async def test_viewport_layer_sends_status_but_never_position_deltas():
    bc = Broadcaster()
    q = bc.subscribe()
    store = MemoryStore()
    poller = Poller(make(api_key=""), store, bc, clock=lambda: NOW)
    assert await poller.poll_once()
    msgs = [q.get_nowait() for _ in range(q.qsize())]
    assert [m["type"] for m in msgs] == ["status"]
    assert len(await store.get_layer("transit")) == 6  # still stored for bbox queries
