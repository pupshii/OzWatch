"""Earthquakes - USGS real-time GeoJSON summary feed, filtered to Australia.

Source:   https://earthquake.usgs.gov/earthquakes/feed/v1.0/geojson.php
Endpoint: GET https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/2.5_week.geojson
Auth:     none
Limits:   feeds regenerate about every minute; USGS recommends the summary
          feeds (not the query API) for frequent polling. We poll every 10 min.
Licence:  US Government public domain; credit "U.S. Geological Survey".

Why USGS and not Geoscience Australia: GA's Earthquakes@GA site has no
documented public GeoJSON endpoint at the time of writing. USGS covers M2.5+
worldwide, so small Australian events can be missing. Noted in the README.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from app.connectors.base import Connector, LayerInfo
from app.schema import Feature

log = logging.getLogger(__name__)

API_URL = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/2.5_week.geojson"
# Same box as the other layers: lat -45..-9, lon 110..156.
LAT_MIN, LAT_MAX, LON_MIN, LON_MAX = -45.0, -9.0, 110.0, 156.0


class UsgsQuakesConnector(Connector):
    info = LayerInfo(
        key="quakes",
        title="Earthquakes (M2.5+, 7 days)",
        attribution="U.S. Geological Survey",
        attribution_url="https://earthquake.usgs.gov/earthquakes/feed/",
        licence="Public domain",
        poll_seconds=600,
        keep_history=False,
        stale_after_seconds=60 * 60,  # drops out of the 7-day feed -> gone an hour later
    )

    async def fetch(self) -> dict[str, Any]:
        resp = await self.client.get(API_URL)
        resp.raise_for_status()
        return resp.json()

    def normalise(self, raw: dict[str, Any]) -> list[Feature]:
        features = []
        for item in raw.get("features") or []:
            try:
                feat = self._to_feature(item)
            except (AttributeError, KeyError, TypeError, ValueError, IndexError) as exc:
                log.debug("skipping USGS record: %s", exc)
                feat = None
            if feat is not None:
                features.append(feat)
        return features

    def _to_feature(self, item: dict[str, Any]) -> Feature | None:
        geom = item.get("geometry") or {}
        if geom.get("type") != "Point":
            return None
        lon, lat, *rest = geom["coordinates"]
        if not (LAT_MIN <= lat <= LAT_MAX and LON_MIN <= lon <= LON_MAX):
            return None
        p = item.get("properties") or {}
        mag = p.get("mag")
        if mag is None or p.get("type", "earthquake") != "earthquake":
            return None  # skip quarry blasts etc. and events without a magnitude
        depth = rest[0] if rest else None

        return Feature(
            id=str(item["id"]),
            layer=self.info.key,
            lat=float(lat),
            lon=float(lon),
            timestamp=datetime.fromtimestamp(p["time"] / 1000, UTC),
            properties={
                "title": p.get("place") or "Earthquake",
                "magnitude": round(float(mag), 1),
                "mag_type": p.get("magType"),
                "depth_km": round(float(depth), 1) if depth is not None else None,
                "place": p.get("place"),
                "felt_reports": p.get("felt"),
                "tsunami_flag": bool(p.get("tsunami")),
                "review_status": p.get("status"),
                "link": _usgs_link(p.get("url")),
            },
        )


def _usgs_link(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    u = urlparse(value)
    return value if u.scheme == "https" and u.netloc == "earthquake.usgs.gov" else None
