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

### 12. Polling rate is worked out from the API budget, not guessed
OpenSky gives 4,000 credits a day, and a query the size of Australia costs 4
credits, so the most we can afford is one call every ~86 s. We poll every
120 s (720 calls, 2,880 credits), which leaves headroom for restarts and
retries. Without credentials it drops to anonymous mode (400 credits, every
15 min). `/health` shows `credits_remaining` from the API's own
`X-Rate-Limit-Remaining` header, so you can see the budget being used.

### 13. OAuth2 tokens are cached, not fetched per request
The access token is reused until a minute before it expires, then refreshed.
If the API still answers 401 (for example the token was revoked), we refresh
once and retry, but never loop. That halves the number of HTTP calls and
avoids hammering the auth server.

### 14. Positions are filtered for freshness
OpenSky returns the last known position even if it is minutes old. Anything
older than 5 minutes at query time is dropped, and aircraft that stop
appearing are removed after 5 minutes, so the map doesn't show "ghost" planes.

### 15. Units follow the domain
The API gives SI units (metres, m/s). The popup shows feet and knots first,
because that's what aviation uses, with metres and km/h alongside for everyone else.

### 16. Streams are batched, not written per message
aisstream pushes many messages a second. Instead of one database write and
one browser update per message, the `StreamRunner` keeps a buffer keyed by
vessel (latest message wins) and flushes it every 5 s as one batch. Twenty
reports from one ship in 5 s become one row update and one entry in one
delta. It's micro-batching, the same idea as Spark Structured Streaming's
trigger interval.

### 17. History is throttled per object
Ships report every few seconds when under way. Keeping all of that for 24 h
would be millions of rows a day for little analytical value, so history keeps
at most one row per ship per minute (`history_min_interval_s`). Flights
already arrive only every 120 s, so they keep every poll.

### 18. Two message types merged into one record
AIS sends position reports (frequent) and ship details such as name, type and
destination (every ~6 min) separately. The connector keeps a small
per-vessel cache keyed by MMSI and merges them, like a stateful join in a
streaming pipeline. The cache is capped (20,000 vessels, least recently seen
dropped first) so memory can't grow without limit.

### 19. "Not available" is data, not a value
AIS encodes missing values as magic numbers: speed 102.3, course 360,
heading 511, latitude 91. These are converted to nulls at the edge, so no
chart ever shows a 102-knot container ship. Same idea as cleaning sentinel
values like -999 in an ETL job.

### 20. Reconnect with backoff, reset on success
A websocket dropping is normal (deploys, network blips). The runner
reconnects with the same exponential backoff as the pollers, and resets it as
soon as messages flow again, so one bad minute doesn't leave us waiting
15 minutes. `/health` shows `connected` and `messages_per_min` for the stream.

### 21. Shapes ride along with points
Every layer stores a point (that's what the common schema, the spatial index
and the bounding-box API are built on). A fire's burnt-area polygon is kept as
an optional `area` property and drawn as a separate shaded map layer
underneath. That supports shapes without changing the schema every other
layer relies on. If more layers needed shapes, the next step would be a real
`geometry` column (PostGIS handles any geometry type).

### 22. Time zones are explicit
The RFS feed mixes a UTC timestamp with a "Sydney local time" one. Local times
are parsed with the `Australia/Sydney` time zone, which knows when daylight
saving starts and ends, and everything is stored in UTC. The `tzdata` package
ships the time zone rules with the app, because slim Docker images don't
include them. Tests cover both AEST (July) and AEDT (January).

### 23. Hazards come with a disclaimer
Fire and earthquake popups say this isn't an official warning service and
point to the official one (NSW RFS Fires Near Me). Data from these feeds can
lag, and a portfolio map must never look like the authoritative source in an
emergency.

### 24. Links from feeds are allow-listed
Popups only link to the source's own domain (rfs.nsw.gov.au,
earthquake.usgs.gov) over https. Anything else in the feed is dropped. Same
reasoning as the https-only camera images: a bad or tampered feed can't send
users somewhere unexpected.

### 25. Leaving a source out is a valid decision
DEA Hotspots would add satellite fire detections, but its licence is listed
as "not specified". Rather than assume, it's left out until Geoscience
Australia confirms.

### 26. Analytics are plain SQL files
Each chart is one query in `backend/sql/analytics/`, readable and runnable in
`psql` on its own. Python only runs them and shapes the JSON. The SQL is the
interesting part, so it isn't hidden in strings or behind an ORM.

### 27. Gap filling: a missing hour is still an hour
Hourly charts `LEFT JOIN` a `generate_series` of the last 24 hours, so a quiet
hour shows as a zero column instead of disappearing (which would silently
squash the time axis). Where "no data" and "zero" mean different things, they
stay different: for fires, an hour with no successful poll is `NULL` (a gap
in the chart), not 0 incidents.

### 28. Count distinct things, not rows
"Aircraft over Sydney per hour" is `count(DISTINCT id)`. The same aircraft
seen on 30 polls in an hour is one aircraft, not 30. It's the classic
grain mistake in BI work.

### 29. Spatial joins without double counting
"Busiest ports" joins ships to ports within 30 km, but Sydney Harbour and Port
Botany are only 13 km apart, so a ship could match both. `DISTINCT ON (ship)
... ORDER BY distance` assigns each ship to its nearest port only. Distance
tests use a cheap index-assisted bounding box (`&&`) first, then the exact
geodesic distance (`ST_DWithin` on `geography`, in metres).

### 30. Reliability is a metric too
Every poll is logged in `connector_runs`, so "how reliable is each source?" is
just another query: success rate, runs, last error. It turns operational
logging into a data-quality report.

### 31. Cache, don't recompute per viewer
Results are cached for 60 s behind a lock. Ten people with the panel open
still cost one set of queries a minute.

### 32. Charts without a chart library
The panel's three chart forms (24-hour columns, ranked bars, a table) are
about 150 lines of SVG/HTML, with no dependency and no build step. Every
chart has a hover tooltip, a table view and a CSV download. Bars use one
colour checked for contrast in light and dark mode. Values are always shown
as text, so the chart never relies on colour alone.

### 33. No fake numbers
Without PostgreSQL (in-memory dev mode) the endpoint says analytics are
unavailable instead of returning zeros that look like real data.

### 34. Restarts don't lose what streams taught us
AIS sends ship type only every ~6 minutes, so after a restart every ship used
to show as "Unknown" for a while. Now the stream runner hands the stored
features back to the connector on startup (`seed()`), which rebuilds its
per-vessel cache. The raw AIS type code is stored for exactly this reason.
Older rows that only have a category name map back to a representative code,
so existing databases benefit too.

### 35. "Australia" is a shape, not a box
A lat/lon box tight enough for Australia's north coast also caught the south
coasts of Java, Sumbawa and Timor, which have many more earthquakes than
Australia. The quake filter is now a simple shape: south of −10.5°, plus a
notch for the Torres Strait islands. Edge cases (Cape York's tip, Torres
Strait, Timor, the PNG coast, NZ) are unit tests. A real-world version would
use Australia's EEZ boundary polygon in PostGIS.

### 36. No clustering, on purpose
Clustering replaces nearby points with a numbered bubble. Here, at low zoom,
the information *is* the individual marks: plane headings, ship-type colours,
fire alert levels. Clustering would hide exactly that. Instead camera dots
were made smaller and the heaviest layers draw on top. Clustering would make
sense if one layer grew to tens of thousands of similar points.

### 37. Shareable views live in the URL
`#map=zoom/lat/lon&layers=…&analytics=1`: MapLibre maintains `map=`, the app
adds the rest with `history.replaceState` (no page reloads, no history spam).
Sending a link reproduces exactly what you're looking at, which makes it handy
for bug reports and demos.

### 38. Known gaps are written down, not hidden
Not done, with reasons: QLD cameras (licence unclear), DEA hotspots (licence
unclear), VIC cameras (no public API). Coverage limits that come from the
sources: OpenSky and aisstream rely on volunteer receivers, so inland
Australia and parts of WA are sparse.

### 39. Background tabs stop downloading
The live stream pauses after a tab has been hidden for a minute, and
reconnects (with a fresh snapshot) when it's visible again. Ship and plane
updates arrive every few seconds, so a forgotten background tab would
otherwise download data all day: that costs visitors on mobile data, and it
costs the server outbound traffic, which cloud providers bill by the gigabyte.
A one-minute grace period means quick tab switches don't cause reconnect churn.
