"""Public transport - live positions of Sydney trains, metro, buses, ferries and light rail.

Source:   Transport for NSW "Public Transport - Realtime Vehicle Positions" (v1 + v2)
          https://opendata.transport.nsw.gov.au
Format:   GTFS-realtime (Google's binary protobuf standard for transit data)
Auth:     the same TfNSW Open Data Hub key as the cameras (`Authorization: apikey ...`)
Limits:   5 requests/second, 60,000/day on the default plan. We poll 7 feeds every
          30 s, one after another with a short pause: ~20,000 calls/day.
Licence:  CC BY - credit Transport for NSW.

Endpoints were checked against TfNSW's forum and Transitland's feed registry
(Nov 2026). Sydney Trains, Metro and Inner West light rail are on v2 (their v1
feeds were retired). Parramatta Light Rail is not included: no confirmed endpoint.

Thousands of buses move at once, so this layer uses viewport delivery: the map
asks only for vehicles on screen, and only when zoomed in (see LayerInfo).
"""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from google.protobuf.message import DecodeError
from google.transit import gtfs_realtime_pb2 as gtfs

from app.connectors.base import Connector, LayerInfo
from app.schema import Feature

log = logging.getLogger(__name__)

BASE = "https://api.transport.nsw.gov.au"
MAX_POSITION_AGE_S = 300
PAUSE_BETWEEN_FEEDS_S = 0.3  # stay well under 5 requests/second


@dataclass(frozen=True)
class Feed:
    key: str
    path: str
    mode: str  # train | metro | bus | ferry | lightrail
    label: str


FEEDS = [
    Feed("sydneytrains", "/v2/gtfs/vehiclepos/sydneytrains", "train", "Sydney Trains"),
    Feed("nswtrains", "/v1/gtfs/vehiclepos/nswtrains", "train", "NSW TrainLink"),
    Feed("metro", "/v2/gtfs/vehiclepos/metro", "metro", "Sydney Metro"),
    Feed("buses", "/v1/gtfs/vehiclepos/buses", "bus", "Bus"),
    Feed("ferries", "/v1/gtfs/vehiclepos/ferries/sydneyferries", "ferry", "Sydney Ferries"),
    Feed("lr_innerwest", "/v2/gtfs/vehiclepos/lightrail/innerwest", "lightrail", "Light rail (Inner West)"),
    Feed(
        "lr_cbd",
        "/v1/gtfs/vehiclepos/lightrail/cbdandsoutheast",
        "lightrail",
        "Light rail (CBD & South East)",
    ),
]
FEEDS_BY_KEY = {f.key: f for f in FEEDS}

STATUS = {0: "Arriving at", 1: "Stopped at", 2: "On the way to"}
OCCUPANCY = {
    0: "Empty",
    1: "Many seats available",
    2: "Few seats available",
    3: "Standing room only",
    4: "Crowded",
    5: "Full",
    6: "Not taking passengers",
}

INFO = LayerInfo(
    key="transit",
    title="Public transport (Sydney)",
    attribution="Transport for NSW",
    attribution_url="https://opendata.transport.nsw.gov.au",
    licence="CC BY",
    poll_seconds=30,
    keep_history=False,  # vehicles-in-service over time comes from connector_runs
    stale_after_seconds=120,  # a vehicle that stops reporting (end of service) disappears
    delivery="viewport",
    min_zoom=11,
)


class TfnswTransitConnector(Connector):
    info = INFO

    def __init__(
        self,
        client,
        api_key: str = "",
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        super().__init__(client)
        self.api_key = api_key
        self._now = now
        self._sleep = sleep

    @property
    def demo_mode(self) -> bool:
        return not self.api_key

    async def fetch(self) -> list[tuple[str, bytes]]:
        """Fetch every feed. One failing feed is reported, not fatal; all failing raises."""
        if self.demo_mode:
            return [("demo", demo_feed(self._now()).SerializeToString())]
        results, errors = [], {}
        for i, feed in enumerate(FEEDS):
            if i:
                await self._sleep(PAUSE_BETWEEN_FEEDS_S)
            try:
                resp = await self.client.get(
                    BASE + feed.path, headers={"Authorization": f"apikey {self.api_key}"}
                )
                resp.raise_for_status()
                results.append((feed.key, resp.content))
            except Exception as exc:  # noqa: BLE001 - recorded per feed
                errors[feed.key] = f"{type(exc).__name__}: {exc}"[:120]
        self.meta = {"feeds_ok": len(results), "feeds_failed": errors}
        if not results:
            raise RuntimeError(f"all {len(FEEDS)} transit feeds failed: {errors}")
        return results

    def normalise(self, raw: list[tuple[str, bytes]]) -> list[Feature]:
        now_ts = int(self._now().timestamp())
        features: dict[str, Feature] = {}
        for feed_key, payload in raw:
            msg = gtfs.FeedMessage()
            try:
                msg.ParseFromString(payload)
            except DecodeError as exc:
                log.warning("transit: could not decode %s feed: %s", feed_key, exc)
                continue
            header_ts = msg.header.timestamp or now_ts
            for entity in msg.entity:
                if not entity.HasField("vehicle"):
                    continue
                try:
                    feat = self._to_feature(feed_key, entity, header_ts, now_ts)
                except (ValueError, KeyError) as exc:
                    log.debug("transit: skipping entity: %s", exc)
                    continue
                if feat is not None:
                    features[feat.id] = feat  # duplicates within a feed: last wins
        return list(features.values())

    def _to_feature(self, feed_key: str, entity: Any, header_ts: int, now_ts: int) -> Feature | None:
        v = entity.vehicle
        if not v.HasField("position"):
            return None
        lat, lon = _f32(v.position.latitude), _f32(v.position.longitude)
        if lat == 0 and lon == 0:
            return None
        ts = v.timestamp or header_ts
        if now_ts - ts > MAX_POSITION_AGE_S:
            return None

        feed = FEEDS_BY_KEY.get(feed_key) or demo_feed_info(entity)
        vehicle_id = v.vehicle.id or entity.id
        route_id = v.trip.route_id or None
        bearing = v.position.bearing if v.position.HasField("bearing") else None
        speed = v.position.speed if v.position.HasField("speed") else None
        occupancy = OCCUPANCY.get(v.occupancy_status) if v.HasField("occupancy_status") else None
        status = STATUS.get(v.current_status) if v.HasField("current_status") else None
        label = v.vehicle.label.strip() or None
        route = route_short_name(feed.mode, route_id)

        feat = Feature(
            id=f"{feed.key}:{vehicle_id}",
            layer=INFO.key,
            lat=lat,
            lon=lon,
            timestamp=datetime.fromtimestamp(ts, UTC),
            properties={
                "title": route or label or feed.label,
                "mode": feed.mode,
                "operator": feed.label,
                "route": route,
                "route_id": route_id,
                "label": label,
                "bearing": round(bearing) if bearing is not None and not math.isnan(bearing) else None,
                "speed_kmh": round(speed * 3.6) if speed is not None and not math.isnan(speed) else None,
                "status": status,
                "occupancy": occupancy,
                "sample": self.demo_mode,
            },
        )
        return feat if feat.in_australia() else None


def _f32(x: float) -> float:
    """GTFS-realtime stores coordinates as 32-bit floats (~7 significant digits), so
    -33.87 arrives as -33.8699989... Keeping 7 significant digits recovers the
    value that was sent (still ~1 m precision)."""
    return float(f"{x:.7g}")


def route_short_name(mode: str, route_id: str | None) -> str | None:
    """Best-effort readable route. TfNSW bus route_ids look like '2436_333'
    (operator/region prefix + route number); other modes are shown as-is."""
    if not route_id:
        return None
    if mode == "bus" and "_" in route_id:
        return route_id.rsplit("_", 1)[-1] or route_id
    return route_id


# ---------- demo mode: a few clearly fictional vehicles around the CBD ----------

_DEMO = [
    # (id, mode, route, start lon, start lat, bearing)
    ("SAMPLE-T1", "train", "T1", 151.2065, -33.8830, 0),
    ("SAMPLE-M1", "metro", "M1", 151.2070, -33.8650, 340),
    ("SAMPLE-333", "bus", "333", 151.2200, -33.8780, 100),
    ("SAMPLE-389", "bus", "389", 151.2100, -33.8700, 200),
    ("SAMPLE-F1", "ferry", "F1", 151.2120, -33.8580, 60),
    ("SAMPLE-L2", "lightrail", "L2", 151.2075, -33.8760, 150),
]
_DEMO_LABELS = {"train": "Sydney Trains", "metro": "Sydney Metro", "bus": "Bus", "ferry": "Sydney Ferries",
                "lightrail": "Light rail"}  # fmt: skip


def demo_feed(now: datetime) -> gtfs.FeedMessage:
    msg = gtfs.FeedMessage()
    msg.header.gtfs_realtime_version = "2.0"
    msg.header.timestamp = int(now.timestamp())
    drift = (now.timestamp() % 600) / 600 * 0.01  # ~1 km loop every 10 min
    for vid, mode, route, lon, lat, bearing in _DEMO:
        e = msg.entity.add()
        e.id = f"{mode}|{vid}"
        v = e.vehicle
        v.vehicle.id = vid
        v.vehicle.label = f"SAMPLE {route}"
        v.trip.route_id = route
        v.position.longitude = lon + drift * math.sin(math.radians(bearing))
        v.position.latitude = lat + drift * math.cos(math.radians(bearing))
        v.position.bearing = bearing
        v.position.speed = 8.0
        v.timestamp = int(now.timestamp())
    return msg


def demo_feed_info(entity: Any) -> Feed:
    mode = entity.id.split("|", 1)[0]
    return Feed("demo", "", mode, _DEMO_LABELS.get(mode, "Sample"))
