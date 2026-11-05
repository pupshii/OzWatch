# Data sources

Checked November 2026 against each provider's own documentation. The build
sandbox could not reach these hosts, so every endpoint still needs a first
live call from your own machine; anything marked **confirm** was not settled
by the documentation.

| # | Layer | Source | Access | Key? | Rate limit | Licence / terms | Verdict |
|---|-------|--------|--------|------|------------|-----------------|---------|
| 1 | Cameras (NSW) | [TfNSW Live Traffic Cameras](https://opendata.transport.nsw.gov.au/dataset/live-traffic-cameras) — `api.transport.nsw.gov.au/v1/live/cameras` | REST, GeoJSON; images are public JPEG URLs | Yes, free Open Data Hub account. Header `Authorization: apikey …` | 5 req/s, 60,000/day ("Bronze"). Over-limit = HTTP 403 | CC BY ([data.gov.au mirror](https://data.gov.au/data/dataset/nsw-2-live-traffic-cameras) says CC BY 3.0 AU). Credit Transport for NSW | **GO** |
| 1b | Cameras (QLD) | [QLDTraffic GeoJSON API](https://www.data.qld.gov.au/dataset/131940-traffic-and-travel-information-geojson-api) (includes web cameras) | REST, GeoJSON | Optional (preferred), `apikey` query param | See [spec v1.8](https://QLDtraffic.qld.gov.au/media/more/qldtraffic-website-api-specification-V1-8.pdf) | Listed as CC BY 4.0 in one catalogue, "not specified" in another. **Confirm** | **GO after licence check** |
| 1c | Cameras (VIC) | [VicRoads Data Exchange](https://catalogue.data.infrastructure.gov.au/dataset/vicroads-data-exchange) | REST | Yes | — | — | **NO-GO for now**: no public camera-image API found. Don't scrape VicTraffic |
| 2 | Flights | [OpenSky Network REST API](https://openskynetwork.github.io/opensky-api/rest.html) `/states/all` with bounding box | REST, JSON | Free account. **OAuth2 client-credentials** (basic auth removed 18 Mar 2026). Anonymous also works | Credits/day: 400 anon, 4,000 account, 8,000 feeder. An Australia-sized box (>400 sq°) costs 4 credits → poll every ~120 s on a free account | Research and **non-commercial** only. Cite OpenSky. A portfolio project fits; selling it would not | **GO** (coverage over inland Australia is patchy) |
| 3 | Ships | [aisstream.io](https://aisstream.io/documentation) | WebSocket, subscribe with bounding boxes | Yes, free (sign in with GitHub) | Fair use, no SLA; marked experimental | No formal terms published that I could find. Their docs say keep the key server-side. **Read terms at sign-up** | **GO** (backend-only websocket) |
| 3b | Ships (alt) | [AISHub](https://www.aishub.net/join-us) | REST | Yes | — | Free only if you **contribute your own AIS receiver feed** | **NO-GO** unless you own a receiver |
| 4 | Fires (NSW) | [NSW RFS Current Incidents](https://www.rfs.nsw.gov.au/news-and-media/stay-up-to-date/feeds) — `www.rfs.nsw.gov.au/feeds/majorIncidents.json` | GeoJSON file | No | None stated; poll ≥5 min | CC BY, © State of NSW (NSW RFS) | **GO** (quirks: nested GeometryCollections, two date formats) |
| 4b | Hotspots | [DEA Hotspots](https://www.dea.ga.gov.au/products/dea-hotspots) WFS `hotspots.dea.ga.gov.au/geoserver/…` (feature type `Hotspots`) | OGC WFS (request JSON output) | No | Updates every 10 min | GA products are usually CC BY 4.0, but the catalogue says "not specified". **Confirm**. "Not for safety-of-life decisions" | **GO after licence check** |
| 5 | Earthquakes | [USGS GeoJSON summary feeds](https://earthquake.usgs.gov/earthquakes/feed/v1.0/geojson.php), filtered to an Australia bounding box | GeoJSON | No | Feeds update every minute; poll every 5 min | US public domain; credit USGS | **GO** |
| 5b | Earthquakes (GA) | [Earthquakes@GA](https://earthquakes.ga.gov.au/) | — | — | — | CC BY 3.0 AU (catalogue) | **Not yet**: no documented public GeoJSON endpoint found. Use USGS; revisit |
| 6 | Weather warnings | [BoM RSS](https://www.bom.gov.au/rss/) / [anonymous FTP](https://www.bom.gov.au/catalogue/anon-ftp.shtml) | RSS / FTP | No | — | RSS: personal, non-commercial, must link through to BoM, logo rules. FTP: no redistribution; publishing requires paid Registered User service | **NO-GO as a data layer**. Alternative: per-state "Warnings" links to bom.gov.au, or email BoM to ask |

## Notes per source

**TfNSW cameras.** The API returns camera metadata only. The image URL in each
record is public, so the browser loads it directly: the key stays server-side
and image views cost no quota. The camera list rarely changes, so we poll every
10 minutes (144 calls/day of 60,000).

**OpenSky.** Credit cost per call depends on the area of the box (0–25 sq° = 1,
25–100 = 2, 100–400 = 3, >400 = 4). Australia (≈ lat −44…−10, lon 112…154) is
about 1,400 sq°, so 4 credits per call: 4,000 ÷ 4 = 1,000 calls/day, which is
one call every ~86 s. We use 120 s to leave headroom. The rate-limit headers
`X-Rate-Limit-Remaining` and `X-Rate-Limit-Retry-After-Seconds` are honoured.

**aisstream.io.** One websocket subscription, bounding box roughly
`[[-45, 110], [-9, 156]]`. Messages are batched into the database every few
seconds instead of written one by one. Coverage depends on volunteer
receivers, so expect gaps away from major ports.

**Privacy.** Aircraft ICAO24/callsign and vessel MMSI/name are published by the
sources themselves and identify craft, not people. We store only 24 h of
history and don't build per-craft "pattern of life" views.
