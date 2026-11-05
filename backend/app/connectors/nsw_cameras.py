"""NSW traffic cameras - Transport for NSW Live Traffic Cameras API.

Source:   https://opendata.transport.nsw.gov.au/dataset/live-traffic-cameras
Endpoint: GET https://api.transport.nsw.gov.au/v1/live/cameras  (GeoJSON)
Auth:     free Open Data Hub account; header `Authorization: apikey <key>`
Limits:   default "Bronze" plan = 5 requests/second, 60,000/day
Licence:  Creative Commons Attribution - credit Transport for NSW

The API returns camera *metadata* (location, title, image URL). The JPEG
itself is served from a public URL and refreshes about once a minute, so the
browser loads it directly. That keeps the API key on the server and costs
no API quota per image view.

The camera list changes rarely, so polling every 10 minutes is plenty
(~144 calls/day against a 60,000/day quota).
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from app.connectors.base import Connector, LayerInfo
from app.schema import Feature

log = logging.getLogger(__name__)

API_URL = "https://api.transport.nsw.gov.au/v1/live/cameras"
SAMPLE_FILE = Path(__file__).parent / "samples" / "nsw_cameras.json"


class NswCamerasConnector(Connector):
    info = LayerInfo(
        key="cameras",
        title="Traffic cameras (NSW)",
        attribution="Transport for NSW",
        attribution_url="https://opendata.transport.nsw.gov.au/dataset/live-traffic-cameras",
        licence="CC BY (see dataset page)",
        poll_seconds=600,
        keep_history=False,
        stale_after_seconds=3 * 3600,
    )

    def __init__(self, client, api_key: str = "", now=lambda: datetime.now(UTC)):
        super().__init__(client)
        self.api_key = api_key
        self._now = now

    @property
    def demo_mode(self) -> bool:
        return not self.api_key

    async def fetch(self) -> dict[str, Any]:
        if self.demo_mode:
            return json.loads(SAMPLE_FILE.read_text())
        resp = await self.client.get(
            API_URL,
            headers={"Authorization": f"apikey {self.api_key}", "Accept": "application/json"},
        )
        resp.raise_for_status()
        return resp.json()

    def normalise(self, raw: dict[str, Any]) -> list[Feature]:
        fetched_at = self._now()
        features: list[Feature] = []
        skipped = 0
        for item in raw.get("features") or []:
            try:
                feat = self._to_feature(item, fetched_at)
            except (AttributeError, KeyError, TypeError, ValueError) as exc:
                log.debug("skipping camera record: %s", exc)
                feat = None
            if feat is None:
                skipped += 1
                continue
            features.append(feat)
        if skipped:
            log.info("nsw_cameras: skipped %d malformed/out-of-area records", skipped)
        return features

    def _to_feature(self, item: dict[str, Any], fetched_at: datetime) -> Feature | None:
        geom = item.get("geometry") or {}
        if geom.get("type") != "Point":
            return None
        lon, lat = geom["coordinates"][:2]
        props = item.get("properties") or {}

        image_url = _safe_https_url(props.get("href"))
        cam_id = str(item.get("id") or props.get("id") or image_url or "")
        if not cam_id:
            return None

        feat = Feature(
            id=cam_id,
            layer=self.info.key,
            lat=float(lat),
            lon=float(lon),
            # The feed has no per-image capture time; we record when we saw it.
            timestamp=fetched_at,
            properties={
                "title": _clean(props.get("title")),
                "view": _clean(props.get("view")),
                "direction": _clean(props.get("direction")),
                "region": _clean(props.get("region")),
                # Sample cameras have no real image: don't send the browser to a dead link.
                "image_url": None if self.demo_mode else image_url,
                "state": "NSW",
                "sample": self.demo_mode,
            },
        )
        return feat if feat.in_australia() else None


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _safe_https_url(value: Any) -> str | None:
    """Only pass through absolute https URLs - we put these in <img src>."""
    if not isinstance(value, str):
        return None
    parsed = urlparse(value.strip())
    if parsed.scheme != "https" or not parsed.netloc:
        return None
    return value.strip()
