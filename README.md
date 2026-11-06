# OzWatch

[![CI](https://github.com/pupshii/OzWatch/actions/workflows/ci.yml/badge.svg)](https://github.com/pupshii/OzWatch/actions/workflows/ci.yml)

**A live open-data map of Australia.** Traffic cameras, aircraft, ships, bushfire
incidents and earthquakes on one map, updated in real time, with an analytics
panel built on PostGIS SQL.

![OzWatch: live layers over Australia](docs/screenshots/overview.png)

| Sydney close-up | Popups | Analytics |
|---|---|---|
| ![Sydney with cameras, ships and aircraft](docs/screenshots/sydney.png) | ![Camera and fire popups](docs/screenshots/popups.png) | ![Analytics panel](docs/screenshots/analytics.png) |

Built as a portfolio project to show end-to-end data work: ingesting messy
real-world feeds, normalising them into one schema, streaming and batching,
spatial SQL, and presenting it all clearly. Every source was checked for
access, rate limits and licence before any code was written
([docs/data-sources.md](docs/data-sources.md)).

## Features

- **Six live layers** with recognisable icons (bus, train, ferry, tram, camera, flame badges; planes and ships rotated to their heading), each toggleable, with a "last updated" time, a health dot and source attribution
  - **Traffic cameras (NSW):** about 250 cameras; click for the latest image (Transport for NSW)
  - **Flights:** ADS-B aircraft over Australia, icons rotated to their heading (OpenSky Network)
  - **Ships:** live AIS positions over a websocket, coloured by vessel type (aisstream.io)
  - **Fires & incidents:** NSW RFS incidents by alert level, with fire-area shapes
  - **Earthquakes:** USGS M2.5+ events in Australia and its waters, sized by magnitude
  - **Public transport (Sydney):** live trains, metro, buses, ferries and light rail in their familiar mode colours, with crowding where reported (Transport for NSW). Loaded for the visible area when zoomed in, over the train, metro and light rail lines in their official colours (built weekly from TfNSW timetables).
- **Live updates:** the server pushes only what changed to the browser (Server-Sent Events); popups follow moving aircraft and ships
- **Analytics panel:** eight charts from standalone SQL files (aircraft near Sydney per hour, altitude bands, vessels by type, busiest ports, fire incidents over time, source reliability…), each with tooltips, a table view and CSV export
- **Shareable views:** the URL keeps the map position, visible layers and panel state
- **Runs without any API keys:** sample cameras and ships, anonymous flights, and the keyless fire and earthquake feeds
- **Reliable by design:** retries with backoff, per-source health on `/health`, every poll logged for a reliability report

## Architecture

```mermaid
flowchart LR
    subgraph Sources["Open data sources"]
        TfNSW["TfNSW<br/>cameras + transit (GTFS-RT)"]
        OpenSky["OpenSky<br/>REST + OAuth2"]
        RFS["NSW RFS<br/>GeoJSON feed"]
        USGS["USGS<br/>GeoJSON feed"]
        AIS["aisstream.io<br/>websocket"]
    end

    subgraph API["FastAPI process (one container)"]
        Pollers["Pollers<br/>interval · backoff · change detection"]
        Stream["Stream runner<br/>5 s micro-batches · reconnect"]
        Bus["Broadcaster<br/>(in-process pub/sub)"]
        Analytics["Analytics<br/>sql/analytics/*.sql · 60 s cache"]
    end

    DB[("PostgreSQL + PostGIS<br/>latest · 24 h history · run log")]
    Browser["Browser<br/>MapLibre GL JS"]

    TfNSW & OpenSky & RFS & USGS -- "HTTP poll" --> Pollers
    AIS -- "websocket" --> Stream
    Pollers & Stream -- "upsert" --> DB
    Pollers & Stream -- "deltas" --> Bus
    Bus -- "Server-Sent Events" --> Browser
    DB -- "GeoJSON snapshots" --> Browser
    DB --> Analytics -- "/api/analytics" --> Browser
```

**How a source becomes a layer:** each source has a small connector that turns
its own format into one common record: `id, layer, lat, lon, timestamp,
properties`. Everything after that (storage, API, live stream, map) is shared,
so adding a source is one file plus tests. Polled sources run on intervals
worked out from their rate limits. The AIS websocket is buffered and written
in 5-second batches. The 46 design decisions are explained in plain language in
[docs/decisions.md](docs/decisions.md).

**Stack:** Python 3.12 · FastAPI · asyncpg · PostgreSQL 16 + PostGIS 3.4 ·
MapLibre GL JS (no frontend build step) · Docker Compose · Caddy · GitHub Actions

## Quick start

```bash
git clone https://github.com/pupshii/OzWatch.git && cd OzWatch
cp .env.example .env            # add API keys here (optional)
docker compose up --build
```

Open <http://localhost:8000>. Health: <http://localhost:8000/health>. API docs: <http://localhost:8000/docs>.

### API keys (all free, all optional)

| Variable | Get it from | Without it |
|---|---|---|
| `TFNSW_API_KEY` | [TfNSW Open Data Hub](https://opendata.transport.nsw.gov.au) (API token) | 8 sample cameras, 6 sample transit vehicles |
| `OPENSKY_CLIENT_ID`, `OPENSKY_CLIENT_SECRET` | [OpenSky](https://opensky-network.org) → Account → API client | Anonymous: updates every 15 min instead of 2 |
| `AISSTREAM_API_KEY` | [aisstream.io](https://aisstream.io) (sign in with GitHub) | 6 sample vessels |

Keys live only in `.env` (git-ignored) and stay on the server; the browser never sees them.
Other settings: `POLL_CAMERAS`, `POLL_FLIGHTS`, `HISTORY_HOURS` (default 24),
`LOG_LEVEL`, `POSTGRES_PASSWORD`, `DOMAIN` (production).

### Development without Docker

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements-dev.txt
cd backend && uvicorn app.main:app --reload --timeout-graceful-shutdown 3
```

This uses an in-memory store (no Postgres), so the analytics panel reports itself unavailable.

## Tests

```bash
cd backend
ruff check . && ruff format --check .
pytest                                   # ~90 unit + API tests, no network needed

# PostGIS integration + analytics SQL tests (they TRUNCATE tables: use a throwaway DB)
docker run -d --name ozwatch-testdb -p 5433:5432 -e POSTGRES_PASSWORD=test postgis/postgis:16-3.4
TEST_DATABASE_URL=postgresql://postgres:test@localhost:5433/postgres pytest
```

Tests use saved sample payloads, including the ugly cases: swapped
coordinates, AIS "not available" values, mixed date formats, malformed records
and rate-limit responses. CI runs everything against a PostGIS service, then
builds the Docker image and smoke-tests the running stack.

## API

| Endpoint | Purpose |
|---|---|
| `GET /api/layers` | Layers, attribution, live status |
| `GET /api/layers/{key}/features?bbox=w,s,e,n` | Current GeoJSON for a layer (optional bounding box, PostGIS-indexed) |
| `GET /api/stream` | Server-Sent Events: `delta` (upserts/removals) and `status` messages |
| `GET /api/lines` | Train, metro and light rail line shapes (GeoJSON, rebuilt weekly from GTFS timetables) |
| `GET /api/analytics` | Analytics panel data (needs PostgreSQL) |
| `GET /health` | Per-source health: `ok` / `degraded` / `error`, credits left, stream rate |

## Deployment

One small always-on Linux server (Oracle Cloud Free or a ~A$6–10/month VPS)
running `docker compose` with Caddy for automatic HTTPS. Step by step:
[docs/deploy-gcp.md](docs/deploy-gcp.md) (Google Cloud free VM + free DuckDNS
address) or [docs/deploy.md](docs/deploy.md) (any VPS).

## Data sources & attribution

| Layer | Source | Licence |
|---|---|---|
| Traffic cameras | [Transport for NSW Open Data](https://opendata.transport.nsw.gov.au/dataset/live-traffic-cameras) | CC BY: © Transport for NSW |
| Public transport | [Transport for NSW Open Data](https://opendata.transport.nsw.gov.au) (GTFS-realtime vehicle positions) | CC BY: © Transport for NSW |
| Flights | [The OpenSky Network](https://opensky-network.org) | Non-commercial use. Schäfer, Strohmeier, Lenders, Martinovic & Wilhelm, "Bringing Up OpenSky: A Large-scale ADS-B Sensor Network for Research", IPSN 2014 |
| Ships | [aisstream.io](https://aisstream.io) | Free API, attribution |
| Fires & incidents | [NSW Rural Fire Service](https://www.rfs.nsw.gov.au/news-and-media/stay-up-to-date/feeds) | CC BY: © State of New South Wales (NSW RFS) |
| Earthquakes | [U.S. Geological Survey](https://earthquake.usgs.gov/earthquakes/feed/) | Public domain |
| Basemap | © [OpenStreetMap](https://www.openstreetmap.org/copyright) contributors, [OpenMapTiles](https://openmaptiles.org), served by [OpenFreeMap](https://openfreemap.org) | ODbL / attribution |

**Considered and not used:** Bureau of Meteorology warnings (terms don't
allow redistribution without a paid registration), DEA Hotspots and QLD
traffic cameras (licence not confirmed), VicRoads cameras (no public image
API), AISHub (requires contributing your own receiver). Reasons in
[docs/data-sources.md](docs/data-sources.md).

> **Not an official warning service.** Feeds can be delayed or incomplete.
> For bushfires use [NSW RFS Fires Near Me](https://www.rfs.nsw.gov.au/fire-information/fires-near-me).

## Privacy

Only data the sources publish openly is shown. Aircraft and vessel IDs
identify craft, not people. History is kept for 24 hours and used only for
aggregate charts. There are no per-vehicle tracks or "pattern of life" views.

## Limitations

- **Coverage follows volunteers:** OpenSky and aisstream rely on community
  receivers, so inland Australia and much of WA are sparse.
- **Earthquakes are M2.5+ (USGS):** smaller Australian events aren't in this
  feed; Geoscience Australia would be the better source if it publishes a
  documented one.
- **NSW-only for cameras and fires** until other states' licences are confirmed.
- **Single instance:** live updates fan out in-process (see "next" below).

## What I'd do next

- **Proper geography:** filter by Australia's EEZ polygon in PostGIS instead
  of a hand-drawn region.
- **Longer history, cheaply:** monthly partitions or TimescaleDB, plus hourly
  roll-ups (materialised views) so weeks of trends cost little storage.
- **Analytics as models:** move the SQL into dbt with tests (unique keys,
  accepted values, freshness) and documented lineage.
- **Smoother motion:** interpolate aircraft and ships between updates, and
  show short trails (last 10 min), keeping the privacy rules above.
- **More sources:** other states' fire services and cameras as licences are
  confirmed; TfNSW traffic volumes for real "busiest corridors" metrics.
- **Scale out:** Postgres `LISTEN/NOTIFY` or Redis pub/sub so several API
  replicas can share live updates.
- **Observability:** Prometheus metrics (poll latency, messages/s, queue
  depth) and an end-to-end browser test in CI.

## Project docs

- [docs/data-sources.md](docs/data-sources.md): every source checked for access, limits, licence, go/no-go
- [docs/decisions.md](docs/decisions.md): 46 design decisions in plain language
- [docs/plan.md](docs/plan.md): milestones and repo layout
- [docs/deploy-gcp.md](docs/deploy-gcp.md) / [docs/deploy.md](docs/deploy.md): deployment guides
