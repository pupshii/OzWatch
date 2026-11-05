import json
from contextlib import aclosing
from datetime import UTC, datetime

import pytest

from app.connectors import aisstream
from app.connectors.aisstream import (
    AUS_BOXES,
    MESSAGE_TYPES,
    AisStreamConnector,
    AisStreamError,
    parse_ais_time,
    ship_category,
)

NOW = datetime(2026, 11, 5, 1, 0, tzinfo=UTC)
MMSI = 503123456


def make(api_key="k", connect=None):
    return AisStreamConnector(api_key=api_key, connect=connect, now=lambda: NOW)


def position(
    mmsi=MMSI,
    lat=-33.86,
    lon=151.21,
    sog=12.3,
    cog=45.0,
    heading=44,
    status=0,
    mtype="PositionReport",
    name="OCEAN TEST@@@@",
    t="2026-11-05 00:59:58.123456789 +0000 UTC",
    valid=True,
):
    body = {
        "UserID": mmsi,
        "Valid": valid,
        "Latitude": lat,
        "Longitude": lon,
        "Sog": sog,
        "Cog": cog,
        "TrueHeading": heading,
    }
    if mtype == "PositionReport":
        body["NavigationalStatus"] = status
    return {"MessageType": mtype, "MetaData": {"MMSI": mmsi, "ShipName": name, "time_utc": t},
            "Message": {mtype: body}}  # fmt: skip


def static(mmsi=MMSI, type_=70, dest="AU SYD@@@@", name="OCEAN TEST"):
    return {"MessageType": "ShipStaticData", "MetaData": {"MMSI": mmsi, "ShipName": name},
            "Message": {"ShipStaticData": {"UserID": mmsi, "Valid": True, "Name": name, "Type": type_,
                                           "Destination": dest, "CallSign": "VJAB ", "ImoNumber": 9123456,
                                           "Dimension": {"A": 150, "B": 30, "C": 15, "D": 13},
                                           "MaximumStaticDraught": 9.8}}}  # fmt: skip


def test_position_report_becomes_feature():
    f = make().handle(position())
    assert f.id == "503123456" and (f.lat, f.lon) == (-33.86, 151.21)
    assert f.timestamp == datetime(2026, 11, 5, 0, 59, 58, 123456, tzinfo=UTC)
    p = f.properties
    assert p["name"] == "OCEAN TEST"  # '@' padding removed
    assert p["speed_kn"] == 12.3 and p["course"] == 45 and p["bearing"] == 44
    assert p["moving"] is True and p["nav_status"] == "Under way using engine"
    assert p["ais_class"] == "A" and p["category"] == "Unknown" and p["sample"] is False


def test_static_then_position_are_merged():
    conn = make()
    assert conn.handle(static()) is None  # no position yet -> nothing to draw
    p = conn.handle(position()).properties
    assert p["category"] == "Cargo"
    assert p["destination"] == "AU SYD" and p["callsign"] == "VJAB"
    assert p["imo"] == 9123456 and p["length_m"] == 180 and p["draught_m"] == 9.8


def test_static_after_position_updates_feature():
    conn = make()
    conn.handle(position())
    f = conn.handle(static(type_=84))
    assert f is not None and f.properties["category"] == "Tanker"
    assert (f.lat, f.lon) == (-33.86, 151.21)


def test_not_available_values():
    f = make().handle(position(sog=102.3, cog=360.0, heading=511))
    p = f.properties
    assert p["speed_kn"] is None and p["course"] is None and p["bearing"] is None
    assert p["moving"] is False


def test_bearing_falls_back_to_course_when_no_heading():
    assert make().handle(position(heading=511, cog=270.4)).properties["bearing"] == 270


@pytest.mark.parametrize(
    "msg",
    [
        position(lat=91, lon=181),  # AIS "not available"
        position(valid=False),
        position(mmsi=12345),  # not a 9-digit MMSI
        position(lat=0.0, lon=0.0),  # outside Australia
        {"MessageType": "BaseStationReport", "MetaData": {"MMSI": MMSI}, "Message": {}},
        {},
    ],
)
def test_ignored_messages(msg):
    assert make().handle(msg) is None


def test_class_b_position_and_static():
    conn = make()
    conn.handle(
        {
            "MessageType": "StaticDataReport",
            "MetaData": {"MMSI": MMSI},
            "Message": {
                "StaticDataReport": {
                    "ReportA": {"Valid": True, "Name": "SEA BREEZE"},
                    "ReportB": {
                        "Valid": True,
                        "ShipType": 37,
                        "CallSign": "AB12",
                        "Dimension": {"A": 8, "B": 4, "C": 2, "D": 2},
                    },
                }
            },
        }
    )
    p = conn.handle(position(mtype="StandardClassBPositionReport", name="")).properties
    assert p["name"] == "SEA BREEZE" and p["category"] == "Sailing / pleasure"
    assert p["length_m"] == 12 and p["ais_class"] == "B" and p["nav_status"] is None


def test_vessel_cache_is_bounded(monkeypatch):
    monkeypatch.setattr(aisstream, "MAX_VESSELS", 3)
    conn = make()
    for i in range(5):
        conn.handle(position(mmsi=503000000 + i))
    assert list(conn._vessels) == ["503000002", "503000003", "503000004"]


@pytest.mark.parametrize(
    "code,cat",
    [(70, "Cargo"), (89, "Tanker"), (60, "Passenger"), (30, "Fishing"), (52, "Tug / service"),
     (37, "Sailing / pleasure"), (40, "High-speed craft"), (0, "Unknown"), (None, "Unknown"), (99, "Other")],
)  # fmt: skip
def test_ship_category(code, cat):
    assert ship_category(code) == cat


def test_parse_ais_time():
    assert parse_ais_time("2026-11-05 01:02:03 +0000 UTC") == datetime(2026, 11, 5, 1, 2, 3, tzinfo=UTC)
    assert parse_ais_time("garbage") is None and parse_ais_time(None) is None


# ---------- websocket protocol (with a fake connection) ----------


class FakeWS:
    def __init__(self, frames):
        self.frames, self.sent = frames, []

    async def send(self, text):
        self.sent.append(json.loads(text))

    def __aiter__(self):
        self._i = 0
        return self

    async def __anext__(self):
        if self._i >= len(self.frames):
            raise StopAsyncIteration
        self._i += 1
        return self.frames[self._i - 1]


class FakeConnect:
    def __init__(self, frames):
        self.ws, self.url = FakeWS(frames), None

    def __call__(self, url, **kwargs):
        self.url = url
        return self

    async def __aenter__(self):
        return self.ws

    async def __aexit__(self, *exc):
        return False


async def test_stream_subscribes_then_yields_messages():
    fake = FakeConnect([json.dumps(position()).encode(), json.dumps(static())])
    conn = make(api_key="secret", connect=fake)
    got = [m async for m in conn.stream()]

    assert fake.url == aisstream.WS_URL
    (sub,) = fake.ws.sent
    assert sub["APIKey"] == "secret"
    assert sub["BoundingBoxes"] == AUS_BOXES and AUS_BOXES[0][0] == [-45.0, 110.0]  # [lat, lon]!
    assert set(sub["FilterMessageTypes"]) == set(MESSAGE_TYPES)
    assert [m["MessageType"] for m in got] == ["PositionReport", "ShipStaticData"]
    assert conn.meta["connected"] is False  # reset after the stream ends


async def test_server_error_message_raises():
    conn = make(connect=FakeConnect([json.dumps({"error": "Api Key Is Not Valid"})]))
    with pytest.raises(AisStreamError, match="Api Key"):
        async with aclosing(conn.stream()) as stream:
            async for _ in stream:
                pass


async def test_demo_mode_yields_sample_vessels_without_network():
    conn = AisStreamConnector(api_key="", now=lambda: NOW, demo_interval_s=0)
    assert conn.demo_mode
    feats, n = [], 0
    async with aclosing(conn.stream()) as stream:
        async for msg in stream:
            f = conn.handle(msg)
            if f:
                feats.append(f)
            n += 1
            if n >= len(aisstream.DEMO_SHIPS) * 2:
                break
    assert len({f.id for f in feats}) == len(aisstream.DEMO_SHIPS)
    assert all(f.properties["sample"] and f.properties["name"].startswith("SAMPLE") for f in feats)


# ---------- real websockets client against a local fake aisstream server ----------


async def test_real_websocket_client_against_local_server(monkeypatch):
    from websockets.asyncio.server import serve

    received = []

    async def fake_aisstream(ws):
        received.append(json.loads(await ws.recv()))  # the subscription
        if received[-1]["APIKey"] != "good":
            await ws.send(json.dumps({"error": "Api Key Is Not Valid"}))
            return
        await ws.send(json.dumps(position()))
        await ws.send(json.dumps(static()))

    async with serve(fake_aisstream, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        monkeypatch.setattr(aisstream, "WS_URL", f"ws://127.0.0.1:{port}")

        conn = AisStreamConnector(api_key="good", now=lambda: NOW)
        msgs = [m async for m in conn.stream()]  # ends when the server closes
        assert [m["MessageType"] for m in msgs] == ["PositionReport", "ShipStaticData"]
        assert received[0]["BoundingBoxes"] == AUS_BOXES

        bad = AisStreamConnector(api_key="nope", now=lambda: NOW)
        with pytest.raises(AisStreamError):
            async with aclosing(bad.stream()) as stream:
                async for _ in stream:
                    pass
