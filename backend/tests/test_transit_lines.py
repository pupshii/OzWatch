import io
import os
import zipfile

import httpx
import pytest
import respx

from app.transit_lines import (
    BASE,
    StaticFeed,
    TransitLinesBuilder,
    is_rail_route_type,
    lines_geojson,
    rail_routes,
    shape_points,
    shape_routes,
)

DSN = os.environ.get("TEST_DATABASE_URL")


def make_zip(folder="gtfs/") -> bytes:
    def pts(shape, lons, lat=-33.8):
        return "".join(f"{shape},{lat},{lon},{i + 1}\n" for i, lon in enumerate(lons))

    files = {
        "routes.txt": (
            "route_id,agency_id,route_short_name,route_long_name,route_type,route_color\n"
            "T1a,ST,T1,North Shore & Western Line,2,F99D1C\n"
            "T1b,ST,T1,North Shore & Western Line,2,#F99D1C\n"
            "BUS1,ST,T1 Bus,Rail replacement,714,\n"
            "M1x,ST,M1,Metro North West,401,\n"
        ),
        "trips.txt": (
            "route_id,service_id,trip_id,shape_id\n"
            "T1a,s,t1,shpA\nT1b,s,t2,shpB\nBUS1,s,t3,shpBus\nM1x,s,t4,shpM\nT1a,s,t5,\n"
        ),
        "shapes.txt": "shape_id,shape_pt_lat,shape_pt_lon,shape_pt_sequence\n"
        + pts("shpA", [151.00, 151.01, 151.02, 151.03, 151.04, 151.05])
        + pts(
            "shpB", [151.00, 151.01, 151.02, 151.03, 151.04, 151.05, 151.06, 151.07, 151.08]
        )  # same track, longer
        + pts("shpBus", [150.0, 150.1])
        + pts("shpM", [150.95, 150.96, 150.97], lat=-33.7),
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, text in files.items():
            zf.writestr(folder + name, "﻿" + text)  # BOM, as some exports include one
    return buf.getvalue()


def test_rail_route_types():
    assert all(is_rail_route_type(t) for t in ["0", "1", "2", "401", "900", "109"])
    assert not any(is_rail_route_type(t) for t in ["3", "4", "714", "700", "1000", "", "x"])


def test_parse_keeps_rail_only_and_cleans_colours():
    zf = zipfile.ZipFile(io.BytesIO(make_zip()))
    routes = rail_routes(zf)
    assert set(routes) == {"T1a", "T1b", "M1x"}  # replacement bus dropped
    assert routes["T1b"].color == "f99d1c" and routes["M1x"].color is None
    shapes = shape_routes(zf, routes)
    assert shapes == {"shpA": "T1a", "shpB": "T1b", "shpM": "M1x"}  # trip without shape ignored
    points = list(shape_points(zf, {"shpM"}))
    assert points[0] == ("shpM", 1, 150.95, -33.7) and len(points) == 3


@pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")
@respx.mock
async def test_build_merges_variants_into_one_line_per_route():
    from app.store import PostgresStore

    store = PostgresStore(DSN)
    await store.init()
    async with store.pool.acquire() as c:
        await c.execute("TRUNCATE transit_lines, transit_shape_points, transit_shape_routes")

    respx.get(BASE + "/v2/x").mock(return_value=httpx.Response(404))
    respx.get(BASE + "/v1/x").mock(return_value=httpx.Response(200, content=make_zip()))
    async with httpx.AsyncClient() as client:
        builder = TransitLinesBuilder(client, store.pool, api_key="k")
        path, n = await builder.build_feed(StaticFeed("test", "train", ("/v2/x", "/v1/x")))

    assert path == "/v1/x" and n == 2  # tried v2 (404), used v1
    rows = {r["line"]: r for r in await store.pool.fetch(
        "SELECT line, name, color, ST_Length(geom) AS len, ST_NumGeometries(geom) AS parts FROM transit_lines"
    )}  # fmt: skip
    assert set(rows) == {"T1", "M1"}
    # two variants sharing 0.05 deg of track -> merged: total 0.08, not 0.13
    assert rows["T1"]["len"] == pytest.approx(0.08, abs=1e-6) and rows["T1"]["parts"] == 1
    assert rows["T1"]["color"] == "f99d1c" and rows["M1"]["color"] == "f6891f"  # mode fallback colour
    assert await store.pool.fetchval("SELECT count(*) FROM transit_shape_points") == 0  # staging cleared

    fc = await lines_geojson(store.pool)
    t1 = next(f for f in fc["features"] if f["properties"]["line"] == "T1")
    assert t1["geometry"]["type"] == "MultiLineString" and t1["properties"]["color"] == "#f99d1c"
    await store.close()


@respx.mock
async def test_all_candidates_failing_is_reported():
    respx.get(BASE + "/a").mock(return_value=httpx.Response(404))
    respx.get(BASE + "/b").mock(return_value=httpx.Response(200, content=b"<html>not a zip</html>"))
    async with httpx.AsyncClient() as client:
        builder = TransitLinesBuilder(client, pool=None, api_key="k")
        with pytest.raises(RuntimeError, match="/a: HTTP 404; /b: not a zip"):
            await builder.build_feed(StaticFeed("t", "metro", ("/a", "/b")))
