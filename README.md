# OzWatch

A live open-data map of Australia: traffic cameras, flights, ships and hazards
on one map, with toggleable layers and a small analytics panel.

> **Status: Milestone 1 of 7.** NSW traffic cameras work end to end. See
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
UI). For live data, register free at the
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
| `GET /health` | Per-connector health (`ok` / `degraded` / `error`) |
| `GET /docs` | Interactive OpenAPI docs (FastAPI) |

## Docs

- [Data sources, access, limits, licences, go/no-go](docs/data-sources.md)
- [Design decisions](docs/decisions.md)
- [Milestone plan and repo layout](docs/plan.md)

## Attribution

Camera data © Transport for NSW (CC BY). Basemap © OpenStreetMap contributors,
OpenMapTiles, served by OpenFreeMap. Map rendering by MapLibre GL JS.
