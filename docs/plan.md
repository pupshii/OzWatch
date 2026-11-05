# Milestone plan

Rough sizing assumes ~8–10 h/week part-time.

| Milestone | Scope | Est. | Status |
|-----------|-------|------|--------|
| **M1 — Skeleton + NSW cameras** | Connector interface, poller (backoff, health), PostGIS store, SSE, MapLibre map with toggle panel, camera popups, demo mode, Docker, tests | 3–4 days | ✅ done |
| **M2 — Flights (OpenSky)** | OAuth2 token refresh, credit-aware polling (120 s), aircraft icons rotated by heading, 24 h history | 2–3 days | ✅ done |
| **M3 — Ships (aisstream)** | First *streaming* connector: websocket consumer with reconnect, buffered batch writes, vessel popups | 2–3 days | ✅ done |
| **M4 — Hazards** | NSW RFS incidents (polygons + points), USGS earthquakes, DEA hotspots (after licence check) | 2 days | next |
| **M5 — Analytics panel** | SQL views + `/api/analytics/*`: flights over Sydney per hour, cameras by region, active fires by state, connector uptime; small charts in the panel | 2–3 days | |
| **M6 — More states + polish** | QLD cameras, clustering at low zoom, URL state (shareable views), dark basemap | 2 days | |
| **M7 — Deploy + README** | CI (GitHub Actions: ruff + pytest + PostGIS service), deploy to a free/cheap VM, screenshots, architecture diagram | 1–2 days | |

## Repo structure

```
OzWatch/
├── backend/
│   ├── app/
│   │   ├── main.py            # FastAPI app, starts one Poller per connector
│   │   ├── config.py          # settings from env / .env
│   │   ├── schema.py          # common Feature schema (id, layer, lat, lon, timestamp, properties)
│   │   ├── store.py           # Store interface: PostgresStore (PostGIS) + MemoryStore
│   │   ├── scheduler.py       # Poller: interval, backoff, change detection, health
│   │   ├── streaming.py       # StreamRunner: websocket sources, batching, reconnects
│   │   ├── broadcaster.py     # in-process pub/sub feeding Server-Sent Events
│   │   ├── api/routes.py      # /api/layers, /api/layers/{key}/features, /api/stream, /health
│   │   └── connectors/
│   │       ├── base.py        # Connector ABC + LayerInfo
│   │       ├── nsw_cameras.py
│   │       ├── opensky.py     # flights: OAuth2 + credit-aware polling
│   │       ├── aisstream.py   # ships: websocket stream, merges position + static AIS
│   │       └── samples/       # sample payloads for demo mode
│   ├── sql/001_init.sql       # schema (idempotent)
│   ├── tests/                 # pytest: per-connector, scheduler, API, PostGIS integration
│   ├── Dockerfile
│   └── requirements*.txt
├── frontend/                  # index.html, app.js, style.css (no build step)
├── docs/                      # data-sources.md, decisions.md, plan.md
├── docker-compose.yml
└── .env.example
```

## Cheap deployment options (decide at M7)

- **One small VM running `docker compose`.** Oracle Cloud "Always Free" ARM,
  or a ~US$5/month VPS. Simplest, and it matches local dev exactly.
- **Split.** App on Fly.io / Render, Postgres on a free PostGIS-capable host
  (Neon or Supabase free tier). More moving parts; free tiers may sleep, which
  breaks the background pollers.

Recommendation: one VM, because the pollers need an always-on process.
