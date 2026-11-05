# Design decisions (and how to explain them)

Short, plain-language notes, one per decision, written so you can say them out
loud in an interview.

### 1. One connector per source, all producing the same `Feature` shape
Every source is messy in its own way: GeoJSON `[lon, lat]`, CSV, websockets,
odd date formats. Each connector's only job is to turn its source into a list
of `Feature(id, layer, lat, lon, timestamp, properties)`. Everything after that
(database, API, map) is shared. Adding a new layer means one new file plus a
test, with no change to the map or storage. *It's the "transform" step of ETL
with a fixed target schema.*

### 2. `normalise()` is a pure function
It takes the raw payload and returns Features, with no network or database.
So each connector is tested against a saved sample payload, including the ugly
cases (swapped coordinates, missing IDs, `javascript:` URLs), in milliseconds
and without an API key.

### 3. Pollers run inside the web process, not a separate worker
A handful of sources polled every few minutes doesn't need Celery or a queue.
`asyncio` tasks inside FastAPI are enough and fit on one free VM. *When it
would change:* many sources or heavy processing, at which point you split the
pollers into their own container.

### 4. Failures are expected, not fatal
Each poller catches errors, keeps serving the last good data, and retries with
exponential backoff plus jitter (15 s, 30 s, 60 s … capped at 15 min). If the
source sends `429 Retry-After`, that wait is used instead. `/health` shows each
connector as `ok`, `degraded` (has old data, currently failing) or `error`
(never succeeded), and every run is logged to `connector_runs`. That table
doubles as a data-quality / uptime dataset for the analytics panel.

### 5. PostGIS: a `latest` table plus a short `history` table
`features_latest` has one row per object (upserted), so the map loads with one
indexed query. `features_history` keeps 24 h for moving things (planes, ships)
to power analytics like "flights over Sydney per hour". A janitor task deletes
older rows, so storage stays small and free-tier friendly. The GiST index means
bounding-box queries (`geom && ST_MakeEnvelope(...)`) don't scan the table.

### 6. Server-Sent Events instead of WebSockets
Updates only flow server → browser. SSE is plain HTTP, reconnects
automatically, and passes through proxies easily. On every (re)connect the
browser re-downloads the snapshot, so it can't drift out of sync. The server
sends only *deltas*: features whose position or properties changed, plus
removed IDs. That matters once ships update every few seconds. (The AIS
*source* is a websocket, but that's server-side only.)

### 7. No Redis (yet)
With one backend process, an in-memory pub/sub is enough. Redis earns its
place when you run several backend replicas that all need the same updates.
Postgres `LISTEN/NOTIFY` is another option at that point.

### 8. Frontend without a build step
Plain HTML + ES module + MapLibre from a CDN: no Node toolchain to maintain,
and anyone can read it. MapLibre's GeoJSON sources handle thousands of points.
deck.gl only becomes worth it at tens of thousands of moving points.
Third-party text is inserted with `textContent` (never `innerHTML`), and image
URLs must be `https`, so a bad feed can't inject script into the page.

### 9. API keys stay on the server
The browser never sees TfNSW / OpenSky / aisstream keys. Camera *images* are
loaded straight from TfNSW's public image URLs, so they cost no quota and
need no proxy.

### 10. Demo mode
With no API key, the cameras layer runs on a bundled sample file and the UI
shows a "sample data" badge. Reviewers can `docker compose up` and see a
working map without registering anywhere.

### 11. Storage behind an interface
`PostgresStore` and `MemoryStore` implement the same `Store` protocol. Tests
run in milliseconds against memory. A separate integration test (enabled with
`TEST_DATABASE_URL`) checks the real PostGIS SQL.
