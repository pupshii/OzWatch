from datetime import UTC, datetime

import httpx
import pytest
import respx

from app.config import Settings
from app.connectors.adsbfi import BASE, CENTRES, AdsbfiConnector
from app.main import default_connectors

NOW_MS = 1794964070002


def ac(hex_, lat=-33.78, lon=151.38, seen_pos=0.5, **kw):
    # Field names and value shapes as returned by opendata.adsb.fi /v3 (sample from the live API)
    base = {"hex": hex_, "type": "adsb_icao", "flight": "QFA401  ", "r": "VH-XZA", "t": "B738",
            "alt_baro": 9675, "alt_geom": 10325, "gs": 203.9, "track": 5.35, "baro_rate": 1312,
            "squawk": "1402", "category": "A3", "lat": lat, "lon": lon,
            "seen_pos": seen_pos, "seen": 0.0}  # fmt: skip
    base.update(kw)
    return base


def payload(*aircraft):
    return {"ac": list(aircraft), "msg": "No error", "now": NOW_MS, "total": len(aircraft)}


def make(sleeps=None):
    async def fake_sleep(s):
        if sleeps is not None:
            sleeps.append(s)

    return AdsbfiConnector(httpx.AsyncClient(), sleep=fake_sleep)


def test_normalise_matches_opensky_properties():
    (f,) = make().normalise([payload(ac("7c6b2d"))])
    p = f.properties
    assert f.id == "7c6b2d" and (f.lat, f.lon) == (-33.78, 151.38)
    assert f.timestamp == datetime.fromtimestamp(NOW_MS / 1000 - 0.5, UTC)
    assert p["callsign"] == "QFA401" and p["title"] == "QFA401"
    assert p["altitude_ft"] == 9675 and p["speed_kt"] == 204 and p["heading"] == 5
    assert p["vertical_rate_fpm"] == 1312 and p["category"] == "Large" and p["on_ground"] is False
    assert p["registration"] == "VH-XZA" and p["aircraft_type"] == "B738"


def test_ground_stale_missing_and_tisb():
    feats = {f.id: f for f in make().normalise([payload(
        ac("aa0001", alt_baro="ground", gs=3),
        ac("aa0002", seen_pos=400),  # position too old
        ac("aa0003", lat=None),  # no position
        ac("~2c0001", flight="", r=None),  # non-ICAO (TIS-B) address
        ac("aa0004", lat=0.0, lon=0.0),  # outside Australia
    )])}  # fmt: skip
    assert set(feats) == {"aa0001", "2c0001"}
    assert feats["aa0001"].properties["on_ground"] is True and feats["aa0001"].properties["altitude_ft"] == 0
    assert feats["2c0001"].properties["title"] == "~2C0001"


def test_overlapping_circles_keep_freshest_position():
    feats = make().normalise(
        [payload(ac("bb0001", lat=-33.70, seen_pos=8)), payload(ac("bb0001", lat=-33.71, seen_pos=1))]
    )
    assert len(feats) == 1 and feats[0].lat == -33.71


@respx.mock
async def test_fetch_paces_requests_and_tolerates_a_failing_circle():
    # respx matches routes in the order added: the specific failing circle must come first
    first = CENTRES[0]
    respx.get(f"{BASE}/v3/lat/{first[0]}/lon/{first[1]}/dist/250").mock(return_value=httpx.Response(500))
    route = respx.get(url__startswith=BASE).mock(return_value=httpx.Response(200, json=payload(ac("cc0001"))))
    sleeps = []
    conn = make(sleeps)
    feats = await conn.run_once()
    assert len(feats) == 1  # same aircraft in every circle -> one feature
    assert conn.meta["circles_ok"] == len(CENTRES) - 1 and len(conn.meta["circles_failed"]) == 1
    assert len(sleeps) == len(CENTRES) - 1 and min(sleeps) >= 1.0  # under 1 request/second
    assert route.called


@respx.mock
async def test_rate_limit_stops_the_round():
    calls = respx.get(url__startswith=BASE).mock(
        side_effect=[httpx.Response(200, json=payload(ac("dd0001"))), httpx.Response(429)]
        + [httpx.Response(200, json=payload())] * 30
    )
    conn = make()
    await conn.fetch()
    assert calls.call_count == 2  # stopped right after the 429
    assert any("429" in v for v in conn.meta["circles_failed"].values())


@respx.mock
async def test_all_failing_raises():
    respx.get(url__startswith=BASE).mock(return_value=httpx.Response(503))
    with pytest.raises(RuntimeError, match="all adsb.fi requests failed"):
        await make().fetch()


def test_setting_selects_the_flights_source():
    def flights(settings):
        return next(
            c for c, _ in default_connectors(settings, httpx.AsyncClient()) if c.info.key == "flights"
        )

    assert type(flights(Settings(flights_source="adsbfi"))).__name__ == "AdsbfiConnector"
    assert type(flights(Settings())).__name__ == "OpenSkyConnector"
