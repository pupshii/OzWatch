import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import respx

from app.connectors.opensky import (
    API_URL,
    POLL_ANONYMOUS,
    POLL_AUTHENTICATED,
    TOKEN_URL,
    OpenSkyConnector,
)

SAMPLE = json.loads((Path(__file__).parent / "fixtures" / "opensky_states.json").read_text())


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def make(client=None, creds=True, clock=None):
    return OpenSkyConnector(
        client or httpx.AsyncClient(),
        client_id="me-api-client" if creds else "",
        client_secret="s3cret" if creds else "",
        token_clock=clock or FakeClock(),
    )


def by_id(feats):
    return {f.id: f for f in feats}


def test_normalise_sample():
    feats = by_id(make().normalise(SAMPLE))
    # kept: two airborne + one on the ground. dropped: no position, stale, 0/0, malformed
    assert set(feats) == {"7c6b2d", "7c4ef5", "7c1abc"}

    q = feats["7c6b2d"]
    assert (q.lat, q.lon) == (-33.94, 151.17)
    assert q.timestamp == datetime.fromtimestamp(1762299998, UTC)
    p = q.properties
    assert p["callsign"] == "QFA401" and p["title"] == "QFA401"
    assert p["altitude_ft"] == 4050  # 1234.4 m baro
    assert p["speed_kt"] == 273  # 140.2 m/s
    assert p["heading"] == 182  # rounded (banker's rounding of 182.5)
    assert p["vertical_rate_fpm"] == -1024
    assert p["category"] == "Heavy" and p["on_ground"] is False


def test_falls_back_to_geo_altitude_and_handles_blank_callsign():
    feats = by_id(make().normalise(SAMPLE))
    assert feats["7c4ef5"].properties["altitude_ft"] == 35000  # 10668 m geo altitude
    ground = feats["7c1abc"].properties
    assert ground["callsign"] is None and ground["title"] == "7C1ABC"
    assert ground["on_ground"] is True and ground["category"] == "Light"


def test_null_states_means_empty_sky():
    assert make().normalise({"time": 1, "states": None}) == []


def test_poll_interval_depends_on_auth():
    assert make(creds=True).info.poll_seconds == POLL_AUTHENTICATED
    assert make(creds=False).info.poll_seconds == POLL_ANONYMOUS
    # the class-level default must not be mutated by instances
    assert make(creds=False).info is not make(creds=True).info


@respx.mock
async def test_token_is_fetched_once_and_reused():
    token = respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "tok1", "expires_in": 1800})
    )
    states = respx.get(API_URL).mock(
        return_value=httpx.Response(200, json=SAMPLE, headers={"X-Rate-Limit-Remaining": "3996"})
    )
    conn = make()
    await conn.run_once()
    await conn.run_once()

    assert token.call_count == 1
    form = token.calls.last.request.content.decode()
    assert "grant_type=client_credentials" in form and "client_id=me-api-client" in form
    assert states.calls.last.request.headers["Authorization"] == "Bearer tok1"
    q = states.calls.last.request.url.params
    assert (q["lamin"], q["lamax"], q["lomin"], q["lomax"]) == ("-44.0", "-10.0", "112.0", "154.0")
    assert conn.meta == {"auth": "oauth2", "credits_remaining": 3996}


@respx.mock
async def test_token_refreshed_before_expiry():
    respx.post(TOKEN_URL).mock(
        side_effect=[
            httpx.Response(200, json={"access_token": "tok1", "expires_in": 1800}),
            httpx.Response(200, json={"access_token": "tok2", "expires_in": 1800}),
        ]
    )
    states = respx.get(API_URL).mock(return_value=httpx.Response(200, json=SAMPLE))
    clock = FakeClock()
    conn = make(clock=clock)
    await conn.fetch()
    clock.t += 1800 - 30  # inside the 60 s refresh margin
    await conn.fetch()
    assert states.calls.last.request.headers["Authorization"] == "Bearer tok2"


@respx.mock
async def test_401_triggers_one_token_refresh_and_retry():
    respx.post(TOKEN_URL).mock(
        side_effect=[
            httpx.Response(200, json={"access_token": "old", "expires_in": 1800}),
            httpx.Response(200, json={"access_token": "new", "expires_in": 1800}),
        ]
    )
    respx.get(API_URL).mock(side_effect=[httpx.Response(401), httpx.Response(200, json=SAMPLE)])
    feats = await make().run_once()
    assert len(feats) == 3


@respx.mock
async def test_anonymous_sends_no_auth_header():
    token = respx.post(TOKEN_URL)
    states = respx.get(API_URL).mock(return_value=httpx.Response(200, json=SAMPLE))
    conn = make(creds=False)
    await conn.fetch()
    assert not token.called
    assert "Authorization" not in states.calls.last.request.headers
    assert conn.meta["auth"] == "anonymous"


@respx.mock
async def test_429_raises_with_retry_header_for_the_poller():
    respx.get(API_URL).mock(
        return_value=httpx.Response(
            429, headers={"X-Rate-Limit-Retry-After-Seconds": "3600", "X-Rate-Limit-Remaining": "0"}
        )
    )
    conn = make(creds=False)
    with pytest.raises(httpx.HTTPStatusError) as err:
        await conn.fetch()
    assert err.value.response.headers["X-Rate-Limit-Retry-After-Seconds"] == "3600"
    assert conn.meta["credits_remaining"] == 0
