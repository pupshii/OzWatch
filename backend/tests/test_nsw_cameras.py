import json
from datetime import UTC, datetime

import httpx
import pytest
import respx

from app.connectors.nsw_cameras import API_URL, SAMPLE_FILE, NswCamerasConnector

FIXED_NOW = datetime(2026, 11, 3, 1, 0, tzinfo=UTC)


def make(api_key=""):
    return NswCamerasConnector(httpx.AsyncClient(), api_key=api_key, now=lambda: FIXED_NOW)


def cam(id_="c1", coords=(151.2, -33.87), geom_type="Point", **props):
    base = {
        "title": "Test Cam",
        "view": "Looking north",
        "direction": "N",
        "region": "SYD_MET",
        "href": "https://webcams.example.nsw.gov.au/c1.jpeg",
    }
    base.update(props)
    return {
        "type": "Feature",
        "id": id_,
        "geometry": {"type": geom_type, "coordinates": list(coords)},
        "properties": base,
    }


def test_normalise_sample_payload():
    raw = json.loads(SAMPLE_FILE.read_text())
    feats = make().normalise(raw)

    assert len(feats) == len(raw["features"]) == 8
    assert all(f.layer == "cameras" and f.in_australia() for f in feats)
    anzac = next(f for f in feats if f.id == "anzac_bridge")
    assert anzac.lat == pytest.approx(-33.869) and anzac.lon == pytest.approx(151.1856)
    assert anzac.properties["title"] == "Anzac Bridge"
    assert anzac.properties["image_url"].startswith("https://")
    assert anzac.timestamp == FIXED_NOW


def test_geojson_lon_lat_order_is_respected():
    # GeoJSON is [lon, lat] - a classic bug is swapping them.
    (f,) = make().normalise({"features": [cam(coords=(151.2, -33.87))]})
    assert (f.lat, f.lon) == (-33.87, 151.2)
    assert f.to_geojson()["geometry"]["coordinates"] == [151.2, -33.87]


@pytest.mark.parametrize(
    "bad",
    [
        cam(geom_type="LineString"),
        cam(coords=(0, 0)),  # null island: outside Australia
        cam(coords=(-33.87, 151.2)),  # swapped -> invalid latitude
        {"type": "Feature", "geometry": None, "properties": {}},
        cam(id_=None, href=None),  # no way to identify it
        "not even a dict",
    ],
)
def test_malformed_records_are_skipped_not_fatal(bad):
    feats = make().normalise({"features": [bad, cam(id_="good")]})
    assert [f.id for f in feats] == ["good"]


def test_non_https_image_urls_are_dropped():
    (f,) = make().normalise({"features": [cam(href="javascript:alert(1)")]})
    assert f.properties["image_url"] is None
    (f,) = make().normalise({"features": [cam(href="http://insecure.example/x.jpg")]})
    assert f.properties["image_url"] is None


def test_empty_or_missing_features():
    assert make().normalise({}) == []
    assert make().normalise({"features": None}) == []


def test_demo_mode_without_key():
    assert make().demo_mode is True
    assert make(api_key="abc").demo_mode is False


@respx.mock
async def test_fetch_sends_api_key_header():
    route = respx.get(API_URL).mock(return_value=httpx.Response(200, json={"features": [cam()]}))
    feats = await make(api_key="secret123").run_once()
    assert route.called
    assert route.calls.last.request.headers["Authorization"] == "apikey secret123"
    assert len(feats) == 1


@respx.mock
async def test_fetch_raises_on_rate_limit():
    respx.get(API_URL).mock(
        return_value=httpx.Response(403, headers={"X-Error-Detail": "Account Over Rate Limit"})
    )
    with pytest.raises(httpx.HTTPStatusError):
        await make(api_key="k").fetch()


async def test_demo_fetch_uses_sample_and_no_network():
    with respx.mock(assert_all_called=False) as mock:
        feats = await make().run_once()
        assert not mock.calls
    assert len(feats) == 8
