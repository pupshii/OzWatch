import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from app.connectors.nsw_rfs import (
    NswRfsConnector,
    parse_description,
    parse_local,
    parse_pubdate,
    parse_size_ha,
)
from app.connectors.usgs_quakes import UsgsQuakesConnector

FIX = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 11, 5, 2, 0, tzinfo=UTC)


def rfs():
    return NswRfsConnector(httpx.AsyncClient(), now=lambda: NOW)


# ---------- NSW RFS ----------


def test_rfs_sample():
    feats = {f.id: f for f in rfs().normalise(json.loads((FIX / "rfs_incidents.json").read_text()))}
    assert set(feats) == {"612345", "612346", "612347"}  # no-geometry and 0,0 dropped

    f = feats["612345"]
    assert (f.lat, f.lon) == (-33.71, 150.31)  # the Point, not a polygon corner
    assert f.timestamp == datetime(2026, 11, 5, 1, 20, tzinfo=UTC)  # pubDate is UTC
    p = f.properties
    assert p["alert_level"] == "Watch and Act" and p["status"] == "Out of control"
    assert p["incident_type"] == "Bush Fire" and p["is_fire"] is True
    assert p["size_ha"] == 1250.0 and p["council"] == "Blue Mountains"
    assert p["link"] == "https://www.rfs.nsw.gov.au/fire-information/fires-near-me"  # http -> https
    assert p["area"]["type"] == "MultiPolygon" and len(p["area"]["coordinates"]) == 2  # nested collection


def test_rfs_falls_back_to_local_updated_time_and_rejects_foreign_links():
    f = {f.id: f for f in rfs().normalise(json.loads((FIX / "rfs_incidents.json").read_text()))}["612346"]
    # 15 Jul 09:05 Sydney = AEST (UTC+10) -> 23:05 UTC the day before
    assert f.timestamp == datetime(2026, 7, 14, 23, 5, tzinfo=UTC)
    assert f.properties["link"] is None and f.properties["area"] is None
    assert f.properties["size_ha"] == 0.0


def test_rfs_polygon_only_uses_centre():
    f = {f.id: f for f in rfs().normalise(json.loads((FIX / "rfs_incidents.json").read_text()))}["612347"]
    assert (f.lat, f.lon) == pytest.approx((-32.01, 151.01))
    assert f.properties["alert_level"] == "Not Applicable" and f.properties["is_fire"] is False


def test_parse_description_and_dates():
    d = parse_description("ALERT LEVEL: Advice <br/>LOCATION: A: B road<BR />junk")
    assert d == {"ALERT LEVEL": "Advice", "LOCATION": "A: B road"}
    assert parse_pubdate("9/01/2026 3:20:00 PM") == datetime(2026, 1, 9, 15, 20, tzinfo=UTC)
    # January is daylight saving (AEDT, UTC+11)
    assert parse_local("9 Jan 2026 14:20") == datetime(2026, 1, 9, 3, 20, tzinfo=UTC)
    assert parse_local("rubbish") is None and parse_pubdate(None) is None
    assert parse_size_ha("12.5 ha") == 12.5 and parse_size_ha("unknown") is None


# ---------- USGS ----------


def test_usgs_filters_to_australia_and_cleans():
    conn = UsgsQuakesConnector(httpx.AsyncClient())
    feats = {f.id: f for f in conn.normalise(json.loads((FIX / "usgs_week.json").read_text()))}
    # dropped: NZ (outside box), no magnitude, mining explosion
    assert set(feats) == {"us7000abcd", "us7000nodp"}

    q = feats["us7000abcd"].properties
    assert q["magnitude"] == 4.2 and q["depth_km"] == 10.3 and q["felt_reports"] == 120
    assert q["link"].startswith("https://earthquake.usgs.gov/")
    assert feats["us7000abcd"].timestamp == datetime.fromtimestamp(1762300000, UTC)

    assert feats["us7000nodp"].properties["depth_km"] is None
    assert feats["us7000nodp"].properties["link"] is None  # unsafe URL dropped
