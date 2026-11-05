# OzWatch

A live open-data map of Australia: traffic cameras, flights, ships and hazards
on one map, with toggleable layers and a small analytics panel.

> **Status: Milestone 5 of 7.** Five live layers (cameras, flights, ships, fires, earthquakes) and an analytics panel. See
> [docs/plan.md](docs/plan.md). The full README (screenshots, architecture
> diagram, "what I'd do next") comes at the final milestone.

## Run it

### With Docker (recommended)

```bash
cp .env.example .env          # optionally add TFNSW_API_KEY
docker compose up --build
# open http://localhost:8000      health: http://localhost:8000/health
```

Without an API key the cameras layer runs on **sample data** (marked in the
UI). Flights work without credentials too (anonymous OpenSky access, updated
every 15 min). Add `OPENSKY_CLIENT_ID` / `OPENSKY_CLIENT_SECRET` from your
OpenSky account's API client page for updates every 2 min. Ships need a free
[aisstream.io](https://aisstream.io) key (`AISSTREAM_API_KEY`); without one, a few
sample vessels are shown. Fires (NSW RFS) and earthquakes (USGS) need no keys. For live data, register free at the
[TfNSW Open Data Hub](https://opendata.transport.nsw.gov.au), create an
application, subscribe it to the Live Traffic APIs, and put the key in `.env`.

### Without Docker (fast dev loop)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements-dev.txt
cd backend && uvicorn app.main:app --reload --timeout-graceful-shutdown 3   # in-memory store, no Postgres needed
```

### Tests

```bash
cd backend && pytest                    # unit + API tests
ruff check . && ruff format --check .   # lint
# PostGIS integration tests (e.g. against the compose db with its port published):
TEST_DATABASE_URL=postgresql://ozwatch:ozwatch@localhost:5432/ozwatch pytest -m postgres
```

## API

| Endpoint | Purpose |
|---|---|
| `GET /api/layers` | Layers, attribution, live status |
| `GET /api/layers/{key}/features?bbox=w,s,e,n` | GeoJSON FeatureCollection |
| `GET /api/stream` | Server-Sent Events: `delta` and `status` messages |
| `GET /api/analytics` | Analytics panel data (SQL in `backend/sql/analytics/`; needs PostgreSQL) |
| `GET /health` | Per-connector health (`ok` / `degraded` / `error`) |
| `GET /docs` | Interactive OpenAPI docs (FastAPI) |

## Docs

- [Data sources, access, limits, licences, go/no-go](docs/data-sources.md)
- [Design decisions](docs/decisions.md)
- [Milestone plan and repo layout](docs/plan.md)

## Attribution

Camera data © Transport for NSW (CC BY).
Flight data from [The OpenSky Network](https://opensky-network.org), used under its
non-commercial terms: Matthias Schäfer, Martin Strohmeier, Vincent Lenders, Ivan
Martinovic and Matthias Wilhelm, "Bringing Up OpenSky: A Large-scale ADS-B Sensor
Network for Research", IPSN 2014.
Ship positions from [aisstream.io](https://aisstream.io).
Fire incidents © State of New South Wales (NSW Rural Fire Service), CC BY.
Earthquakes: U.S. Geological Survey (public domain).
Basemap © OpenStreetMap contributors,
OpenMapTiles, served by OpenFreeMap. Map rendering by MapLibre GL JS.
