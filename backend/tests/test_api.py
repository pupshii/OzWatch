import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.connectors.nsw_cameras import NswCamerasConnector
from app.main import create_app, default_connectors
from app.store import MemoryStore


def cameras_only(settings, client):
    return [(NswCamerasConnector(client, api_key=""), None)]


@pytest.fixture
def client():
    app = create_app(
        Settings(database_url="", tfnsw_api_key=""),
        store=MemoryStore(),
        connectors=cameras_only,
        start_pollers=False,
    )
    with TestClient(app) as c:
        # Run one poll synchronously (demo mode -> bundled sample data, no network).
        c.portal.call(app.state.runtime.pollers["cameras"].poll_once)
        yield c


def test_layers_include_attribution_and_status(client):
    (layer,) = client.get("/api/layers").json()
    assert layer["key"] == "cameras"
    assert layer["attribution"] == "Transport for NSW"
    assert layer["status"]["state"] == "ok"
    assert layer["status"]["demo"] is True


def test_features_geojson(client):
    body = client.get("/api/layers/cameras/features").json()
    assert body["type"] == "FeatureCollection"
    assert len(body["features"]) == 8
    f = body["features"][0]
    assert f["geometry"]["type"] == "Point" and len(f["geometry"]["coordinates"]) == 2


def test_bbox_filter(client):
    # Roughly Greater Sydney only -> excludes the Newcastle and Wollongong sample cameras.
    body = client.get("/api/layers/cameras/features", params={"bbox": "150.5,-34.2,151.4,-33.5"}).json()
    ids = {f["id"] for f in body["features"]}
    assert "anzac_bridge" in ids and "newcastle_pacific_hwy" not in ids and "mount_ousley" not in ids


@pytest.mark.parametrize("bbox", ["1,2,3", "a,b,c,d", "152,-33,151,-34"])
def test_bad_bbox_is_422(client, bbox):
    assert client.get("/api/layers/cameras/features", params={"bbox": bbox}).status_code == 422


def test_unknown_layer_404(client):
    assert client.get("/api/layers/nope/features").status_code == 404


def test_health(client):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["store"] == "MemoryStore"
    assert body["connectors"][0]["feature_count"] == 8


def test_frontend_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "OzWatch" in r.text


def test_default_connectors_register_all_layers():
    keys = [c.info.key for c, _ in default_connectors(Settings(), httpx.AsyncClient())]
    assert keys == ["cameras", "flights"]
