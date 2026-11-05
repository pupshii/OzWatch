"""Fires and incidents - NSW Rural Fire Service "Current Incidents" feed.

Source:   https://www.rfs.nsw.gov.au/news-and-media/stay-up-to-date/feeds
Endpoint: GET https://www.rfs.nsw.gov.au/feeds/majorIncidents.json  (GeoJSON)
Auth:     none
Limits:   none published; we poll every 5 minutes.
Licence:  CC BY - (c) State of New South Wales (NSW Rural Fire Service).

Quirks this connector deals with:
* Geometry is often a GeometryCollection: a Point for the incident plus one
  or more Polygons for the fire area. We keep the point as the Feature's
  position and carry the area as an optional `area` property.
* The useful fields (status, type, size, council...) are packed into an
  HTML-ish `description` string: "ALERT LEVEL: Advice <br />STATUS: ...".
* Two date formats: `pubDate` "9/01/2026 3:20:00 AM" (UTC) and
  "UPDATED: 9 Jan 2026 14:20" in the description (Sydney local time).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from app.connectors.base import Connector, LayerInfo
from app.schema import Feature

log = logging.getLogger(__name__)

API_URL = "https://www.rfs.nsw.gov.au/feeds/majorIncidents.json"
SYDNEY = ZoneInfo("Australia/Sydney")

ALERT_LEVELS = ("Emergency Warning", "Watch and Act", "Advice", "Not Applicable")


class NswRfsConnector(Connector):
    info = LayerInfo(
        key="fires",
        title="Fires & incidents (NSW RFS)",
        attribution="NSW Rural Fire Service",
        attribution_url="https://www.rfs.nsw.gov.au/fire-information/fires-near-me",
        licence="CC BY",
        poll_seconds=300,
        keep_history=False,  # incident counts over time come from connector_runs
        stale_after_seconds=30 * 60,
    )

    def __init__(self, client, now: Callable[[], datetime] = lambda: datetime.now(UTC)):
        super().__init__(client)
        self._now = now

    async def fetch(self) -> dict[str, Any]:
        resp = await self.client.get(API_URL, headers={"Accept": "application/json"})
        resp.raise_for_status()
        return resp.json()

    def normalise(self, raw: dict[str, Any]) -> list[Feature]:
        features = []
        for item in raw.get("features") or []:
            try:
                feat = self._to_feature(item)
            except (AttributeError, KeyError, TypeError, ValueError, IndexError) as exc:
                log.debug("skipping RFS record: %s", exc)
                feat = None
            if feat is not None:
                features.append(feat)
        return features

    def _to_feature(self, item: dict[str, Any]) -> Feature | None:
        props = item.get("properties") or {}
        point, polygons = split_geometry(item.get("geometry"))
        if point is None and polygons:
            point = bbox_centre(polygons)
        if point is None:
            return None
        lon, lat = point

        guid = str(props.get("guid") or props.get("link") or "").strip()
        fid = _short_id(guid) or f"{round(lat, 4)},{round(lon, 4)}"
        details = parse_description(props.get("description"))

        alert = (props.get("category") or details.get("ALERT LEVEL") or "").strip()
        updated = parse_pubdate(props.get("pubDate")) or parse_local(details.get("UPDATED")) or self._now()

        feat = Feature(
            id=fid,
            layer=self.info.key,
            lat=float(lat),
            lon=float(lon),
            timestamp=updated,
            properties={
                "title": _clean(props.get("title")) or "Incident",
                "alert_level": alert if alert in ALERT_LEVELS else "Not Applicable",
                "status": details.get("STATUS"),
                "incident_type": details.get("TYPE"),
                "is_fire": (details.get("FIRE") or "").lower() == "yes",
                "size_ha": parse_size_ha(details.get("SIZE")),
                "location": details.get("LOCATION"),
                "council": details.get("COUNCIL AREA"),
                "agency": details.get("RESPONSIBLE AGENCY"),
                "link": _safe_link(props.get("link")),
                "state": "NSW",
                "area": multipolygon(polygons),
            },
        )
        return feat if feat.in_australia() else None


def split_geometry(geom: Any) -> tuple[tuple[float, float] | None, list[list]]:
    """Return (first point as (lon, lat), list of polygon coordinate arrays)."""
    point, polygons = None, []
    stack = [geom] if isinstance(geom, dict) else []
    while stack:
        g = stack.pop(0)
        gtype = g.get("type")
        if gtype == "GeometryCollection":
            stack.extend(x for x in g.get("geometries") or [] if isinstance(x, dict))
        elif gtype == "Point" and point is None:
            lon, lat = g["coordinates"][:2]
            point = (float(lon), float(lat))
        elif gtype == "Polygon":
            polygons.append(g["coordinates"])
        elif gtype == "MultiPolygon":
            polygons.extend(g["coordinates"])
    return point, polygons


def multipolygon(polygons: list[list]) -> dict[str, Any] | None:
    """Merge polygons into one MultiPolygon, rounded to ~1 m to keep payloads small."""
    if not polygons:
        return None
    rounded = [[[[round(x, 5), round(y, 5)] for x, y, *_ in ring] for ring in poly] for poly in polygons]
    return {"type": "MultiPolygon", "coordinates": rounded}


def bbox_centre(polygons: list[list]) -> tuple[float, float]:
    xs = [pt[0] for poly in polygons for ring in poly for pt in ring]
    ys = [pt[1] for poly in polygons for ring in poly for pt in ring]
    return (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2


def parse_description(text: Any) -> dict[str, str]:
    """'ALERT LEVEL: Advice <br />STATUS: Under control <br />...' -> dict."""
    if not isinstance(text, str):
        return {}
    out = {}
    for part in re.split(r"<br\s*/?>", text, flags=re.I):
        key, sep, value = part.partition(":")
        if sep and key.strip() and value.strip():
            out[key.strip().upper()] = re.sub(r"<[^>]+>", "", value).strip()
    return out


def parse_pubdate(text: Any) -> datetime | None:
    """'9/01/2026 3:20:00 AM' (day first, UTC)."""
    if not isinstance(text, str):
        return None
    try:
        return datetime.strptime(text.strip(), "%d/%m/%Y %I:%M:%S %p").replace(tzinfo=UTC)
    except ValueError:
        return None


def parse_local(text: Any) -> datetime | None:
    """'9 Jan 2026 14:20' in Sydney local time (handles AEST/AEDT) -> UTC."""
    if not isinstance(text, str):
        return None
    try:
        local = datetime.strptime(text.strip(), "%d %b %Y %H:%M").replace(tzinfo=SYDNEY)
    except ValueError:
        return None
    return local.astimezone(UTC)


def parse_size_ha(text: Any) -> float | None:
    if not isinstance(text, str):
        return None
    m = re.search(r"([\d,]+(?:\.\d+)?)\s*ha", text)
    return float(m.group(1).replace(",", "")) if m else None


def _short_id(guid: str) -> str | None:
    m = re.search(r"(\d{4,})\D*$", guid)
    return m.group(1) if m else (guid or None)


def _safe_link(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    u = urlparse(value.strip())
    rfs_host = u.netloc == "rfs.nsw.gov.au" or u.netloc.endswith(".rfs.nsw.gov.au")
    if u.scheme not in ("http", "https") or not rfs_host:
        return None
    return u._replace(scheme="https").geturl()  # the feed often uses http://


def _clean(value: Any) -> str | None:
    return value.strip() or None if isinstance(value, str) else None
