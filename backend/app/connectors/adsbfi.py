"""Flights from adsb.fi, a volunteer ADS-B network (alternative to OpenSky).

Why it exists: OpenSky timed out from our Google Cloud server (both its API and
its login host), while adsb.fi answered normally. Both connectors produce the
same Feature properties, so the map, popups and analytics don't care which one
runs. Pick with FLIGHTS_SOURCE=opensky|adsbfi.

Source:   https://github.com/adsbfi/opendata  (base https://opendata.adsb.fi/api/)
Endpoint: GET /v3/lat/{lat}/lon/{lon}/dist/{nm}   (radius up to 250 NM)
Auth:     none
Limits:   1 request/second on public endpoints; repeated 4xx can get an IP
          temporarily restricted. We pace requests 1.2 s apart.
Terms:    personal, non-commercial use only; must cite adsb.fi with a link to
          its home page (done in the layer attribution and every popup).

One request covers a 250 NM circle, so Australia takes ~20 circles per update.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from app.connectors.base import Connector, LayerInfo
from app.schema import Feature

log = logging.getLogger(__name__)

BASE = "https://opendata.adsb.fi/api"
RADIUS_NM = 250
PAUSE_S = 1.2  # stay under 1 request/second
MAX_POSITION_AGE_S = 300

# Circle centres (lat, lon): coast, capitals and the main inland air corridors.
CENTRES = [
    (-12.4, 130.9),  # Darwin
    (-16.9, 145.8),  # Cairns
    (-20.6, 148.6),  # Townsville / Mackay
    (-27.4, 153.0),  # Brisbane
    (-30.3, 152.0),  # Coffs Harbour / New England
    (-33.9, 151.2),  # Sydney
    (-35.3, 148.0),  # Canberra / Wagga
    (-37.8, 145.0),  # Melbourne
    (-42.0, 146.8),  # Hobart
    (-34.9, 138.6),  # Adelaide
    (-31.5, 135.0),  # Port Augusta / Ceduna
    (-31.9, 141.5),  # Broken Hill
    (-25.5, 145.0),  # Longreach / Charleville
    (-20.7, 139.5),  # Mount Isa
    (-23.7, 133.9),  # Alice Springs
    (-17.0, 123.5),  # Broome / Kimberley
    (-20.5, 117.5),  # Pilbara
    (-25.5, 114.5),  # Carnarvon
    (-31.9, 115.9),  # Perth
    (-30.8, 121.5),  # Kalgoorlie
]

# ADS-B emitter categories -> the same names the OpenSky connector uses.
CATEGORY = {
    "A1": "Light", "A2": "Small", "A3": "Large", "A4": "High vortex large", "A5": "Heavy",
    "A6": "High performance", "A7": "Rotorcraft", "B1": "Glider", "B2": "Lighter-than-air",
    "B3": "Parachutist", "B4": "Ultralight", "B6": "Drone", "B7": "Space vehicle",
}  # fmt: skip

INFO = LayerInfo(
    key="flights",
    title="Flights (ADS-B)",
    attribution="adsb.fi",
    attribution_url="https://adsb.fi",
    licence="Personal, non-commercial use",
    poll_seconds=120,
    keep_history=True,
    stale_after_seconds=300,
)


class AdsbfiConnector(Connector):
    info = INFO

    def __init__(
        self,
        client,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        super().__init__(client)
        self._now = now
        self._sleep = sleep
        self.meta = {"source": "adsb.fi"}

    async def fetch(self) -> list[dict[str, Any]]:
        payloads, failed = [], {}
        for i, (lat, lon) in enumerate(CENTRES):
            if i:
                await self._sleep(PAUSE_S)
            url = f"{BASE}/v3/lat/{lat}/lon/{lon}/dist/{RADIUS_NM}"
            try:
                resp = await self.client.get(url)
                if resp.status_code == 429:
                    failed[f"{lat},{lon}"] = "HTTP 429 (rate limited): stopping this round"
                    break  # back off for the rest of this cycle instead of piling on
                resp.raise_for_status()
                payloads.append(resp.json())
            except Exception as exc:  # noqa: BLE001 - one circle failing shouldn't sink the round
                failed[f"{lat},{lon}"] = f"{type(exc).__name__}: {exc}"[:120]
        self.meta = {"source": "adsb.fi", "circles_ok": len(payloads), "circles_failed": failed}
        if not payloads:
            raise RuntimeError(f"all adsb.fi requests failed: {failed}")
        return payloads

    def normalise(self, raw: list[dict[str, Any]]) -> list[Feature]:
        best: dict[str, tuple[float, Feature]] = {}
        for payload in raw:
            now_s = (payload.get("now") or self._now().timestamp() * 1000) / 1000
            for ac in payload.get("ac") or []:
                try:
                    item = self._to_feature(ac, now_s)
                except (KeyError, TypeError, ValueError) as exc:
                    log.debug("adsb.fi: skipping aircraft: %s", exc)
                    continue
                if item is None:
                    continue
                age, feat = item
                # Overlapping circles report the same aircraft: keep the freshest position.
                if feat.id not in best or age < best[feat.id][0]:
                    best[feat.id] = (age, feat)
        return [f for _, f in best.values()]

    def _to_feature(self, ac: dict[str, Any], now_s: float) -> tuple[float, Feature] | None:
        lat, lon = ac.get("lat"), ac.get("lon")
        hex_id = str(ac.get("hex") or "").strip().lower()
        if lat is None or lon is None or not hex_id:
            return None
        age = float(ac.get("seen_pos", ac.get("seen", 0)) or 0)
        if age > MAX_POSITION_AGE_S:
            return None

        alt = ac.get("alt_baro")
        on_ground = alt == "ground"
        if _num(alt) is None:
            alt = 0 if on_ground else _num(ac.get("alt_geom"))
        callsign = (ac.get("flight") or "").strip() or None
        registration = (ac.get("r") or "").strip() or None

        feat = Feature(
            id=hex_id.lstrip("~"),  # '~' marks non-ICAO (TIS-B) addresses
            layer=INFO.key,
            lat=float(lat),
            lon=float(lon),
            timestamp=datetime.fromtimestamp(now_s - age, UTC),
            properties={
                "title": callsign or registration or hex_id.upper(),
                "callsign": callsign,
                "icao24": hex_id,
                "registration": registration,
                "aircraft_type": (ac.get("t") or "").strip() or None,
                "origin_country": None,  # not in this feed
                "on_ground": on_ground,
                "altitude_ft": _round(alt),
                "speed_kt": _round(ac.get("gs")),
                "heading": _round(ac.get("track")),
                "vertical_rate_fpm": _round(ac.get("baro_rate")),
                "squawk": ac.get("squawk") or None,
                "category": CATEGORY.get(ac.get("category")),
            },
        )
        return (age, feat) if feat.in_australia() else None


def _num(value: Any) -> float | None:
    """Numbers only: the feed uses strings like "ground" in numeric fields."""
    return value if isinstance(value, int | float) and not isinstance(value, bool) else None


def _round(value: Any) -> int | None:
    n = _num(value)
    return None if n is None else round(n)
