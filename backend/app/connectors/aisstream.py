"""Ships - live AIS vessel positions from aisstream.io (websocket).

Docs:     https://aisstream.io/documentation
          message models: https://github.com/aisstream/ais-message-models
Endpoint: wss://stream.aisstream.io/v0/stream
Auth:     free API key (sign in with GitHub). Must stay server-side: aisstream
          asks you not to connect browsers directly, as that exposes the key.
Protocol: connect, then within 3 s send a subscription:
            {"APIKey": ..., "BoundingBoxes": [[[lat, lon], [lat, lon]]],
             "FilterMessageTypes": [...]}
          Note: bounding boxes are [lat, lon] - the opposite of GeoJSON.
Limits:   fair use, no SLA; the service describes itself as experimental.
Licence:  no formal terms published at the time of writing; attribute aisstream.io.

AIS sends *positions* and *ship details* in separate messages (details only
every ~6 min), so this connector remembers what it has learnt per vessel
(keyed by MMSI, the vessel's radio ID) and merges the two into one Feature.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections import OrderedDict
from collections.abc import AsyncIterator, Callable
from contextlib import aclosing
from datetime import UTC, datetime
from typing import Any

from app.connectors.base import LayerInfo, StreamConnector
from app.schema import Feature

log = logging.getLogger(__name__)

WS_URL = "wss://stream.aisstream.io/v0/stream"
# [[lat, lon], [lat, lon]] - mainland, Tasmania and surrounding shipping lanes.
AUS_BOXES = [[[-45.0, 110.0], [-9.0, 156.0]]]

POSITION_TYPES = {"PositionReport", "StandardClassBPositionReport", "ExtendedClassBPositionReport"}
STATIC_TYPES = {"ShipStaticData", "StaticDataReport"}
MESSAGE_TYPES = sorted(POSITION_TYPES | STATIC_TYPES)

MAX_VESSELS = 20_000  # bound the memory used by the per-vessel cache

# AIS uses magic numbers for "not available".
SOG_NA = 102.3
COG_NA = 360.0
HEADING_NA = 511

NAV_STATUS = {
    0: "Under way using engine", 1: "At anchor", 2: "Not under command",
    3: "Restricted manoeuvrability", 4: "Constrained by draught", 5: "Moored",
    6: "Aground", 7: "Engaged in fishing", 8: "Under way sailing",
    14: "AIS-SART active",
}  # fmt: skip

INFO = LayerInfo(
    key="ships",
    title="Ships (AIS)",
    attribution="aisstream.io",
    attribution_url="https://aisstream.io",
    licence="Free API, attribution",
    poll_seconds=5,
    keep_history=True,
    stale_after_seconds=30 * 60,  # ships at anchor report only every few minutes
    history_min_interval_s=60,
)


class AisStreamError(RuntimeError):
    """The server sent an error message (e.g. invalid API key)."""


# A representative AIS code per category: lets rows saved before `ship_type`
# existed (category only) be restored after a restart.
CATEGORY_CODE = {
    "Cargo": 70, "Tanker": 80, "Passenger": 60, "Fishing": 30, "Tug / service": 52,
    "Sailing / pleasure": 37, "High-speed craft": 40, "Military": 35, "Other": 90,
}  # fmt: skip


def ship_category(type_code: int | None) -> str:
    """Group the ~100 AIS ship type codes into a handful a map can colour."""
    if type_code is None or type_code == 0:
        return "Unknown"
    if 70 <= type_code <= 79:
        return "Cargo"
    if 80 <= type_code <= 89:
        return "Tanker"
    if 60 <= type_code <= 69:
        return "Passenger"
    if type_code == 30:
        return "Fishing"
    if type_code in (31, 32, 33, 50, 51, 52, 53, 54, 55, 58):
        return "Tug / service"
    if type_code in (36, 37):
        return "Sailing / pleasure"
    if 40 <= type_code <= 49:
        return "High-speed craft"
    if type_code == 35:
        return "Military"
    return "Other"


class AisStreamConnector(StreamConnector):
    info = INFO

    def __init__(
        self,
        api_key: str = "",
        connect: Callable[..., Any] | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        demo_interval_s: float = 20.0,
    ) -> None:
        super().__init__()
        self.api_key = api_key
        self._connect = connect
        self._now = now
        self._demo_interval_s = demo_interval_s
        self._vessels: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self.meta = {"connected": False}

    @property
    def demo_mode(self) -> bool:
        return not self.api_key

    def subscription(self) -> dict[str, Any]:
        return {"APIKey": self.api_key, "BoundingBoxes": AUS_BOXES, "FilterMessageTypes": MESSAGE_TYPES}

    # ---------- connection ----------

    async def stream(self) -> AsyncIterator[dict[str, Any]]:
        if self.demo_mode:
            async with aclosing(self._demo_stream()) as demo:
                async for msg in demo:
                    yield msg
            return

        connect = self._connect
        if connect is None:
            from websockets.asyncio.client import connect  # lazy: only needed for live mode

        async with connect(WS_URL, open_timeout=15, ping_interval=20, ping_timeout=20, max_size=2**20) as ws:
            await ws.send(json.dumps(self.subscription()))  # must arrive within 3 s
            self.meta["connected"] = True
            log.info("aisstream: connected and subscribed")
            try:
                async for frame in ws:
                    msg = json.loads(frame)
                    if isinstance(msg, dict) and "error" in msg:
                        raise AisStreamError(str(msg["error"]))
                    yield msg
            finally:
                self.meta["connected"] = False

    # ---------- message handling ----------

    def seed(self, features: list[Feature]) -> None:
        """Restore what we knew about each vessel, so a restart doesn't turn every
        ship back into "Unknown" until its next static message (~6 min)."""
        for f in features:
            p = f.properties
            v = self._vessel(f.id)
            v.update(lat=f.lat, lon=f.lon, ts=f.timestamp, sog=p.get("speed_kn"), cog=p.get("course"))
            for key, prop in (
                ("name", "name"), ("callsign", "callsign"), ("destination", "destination"),
                ("imo", "imo"), ("length_m", "length_m"), ("draught_m", "draught_m"),
                ("ais_class", "ais_class"), ("nav_status", "nav_status"),
            ):  # fmt: skip
                if p.get(prop) is not None:
                    v[key] = p[prop]
            code = p.get("ship_type")
            v["type"] = code if code is not None else CATEGORY_CODE.get(p.get("category"))

    def handle(self, raw: dict[str, Any]) -> Feature | None:
        mtype = raw.get("MessageType")
        meta = raw.get("MetaData") or {}
        body = (raw.get("Message") or {}).get(mtype) or {}
        mmsi = str(meta.get("MMSI") or body.get("UserID") or "").strip()
        if not re.fullmatch(r"\d{9}", mmsi):
            return None

        vessel = self._vessel(mmsi)
        name = _clean_text(meta.get("ShipName"))
        if name:
            vessel["name"] = name

        if mtype in POSITION_TYPES:
            if not self._apply_position(vessel, body, meta):
                return None
        elif mtype == "ShipStaticData":
            self._apply_static(vessel, body)
        elif mtype == "StaticDataReport":
            self._apply_class_b_static(vessel, body)
        else:
            return None

        return self._feature(mmsi, vessel) if "lat" in vessel else None

    def _vessel(self, mmsi: str) -> dict[str, Any]:
        vessel = self._vessels.pop(mmsi, None) or {}
        self._vessels[mmsi] = vessel  # (re)insert as most recent
        while len(self._vessels) > MAX_VESSELS:
            self._vessels.popitem(last=False)
        return vessel

    def _apply_position(self, v: dict[str, Any], body: dict[str, Any], meta: dict[str, Any]) -> bool:
        if body.get("Valid") is False:
            return False
        lat = body.get("Latitude", meta.get("latitude"))
        lon = body.get("Longitude", meta.get("longitude"))
        if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
            return False  # AIS uses 91 / 181 for "not available"
        v["lat"], v["lon"] = float(lat), float(lon)
        v["ts"] = parse_ais_time(meta.get("time_utc")) or self._now()
        sog, cog, hdg = body.get("Sog"), body.get("Cog"), body.get("TrueHeading")
        v["sog"] = sog if sog is not None and sog < SOG_NA else None
        v["cog"] = cog if cog is not None and 0 <= cog < COG_NA else None
        v["heading"] = hdg if hdg is not None and 0 <= hdg < 360 and hdg != HEADING_NA else None
        if "NavigationalStatus" in body:
            v["nav_status"] = NAV_STATUS.get(body["NavigationalStatus"])
            v["ais_class"] = "A"
        else:
            v.setdefault("ais_class", "B")
        return True

    def _apply_static(self, v: dict[str, Any], body: dict[str, Any]) -> None:
        v["ais_class"] = "A"
        v["name"] = _clean_text(body.get("Name")) or v.get("name")
        v["callsign"] = _clean_text(body.get("CallSign"))
        v["type"] = body.get("Type")
        v["destination"] = _clean_text(body.get("Destination"))
        v["imo"] = body.get("ImoNumber") or None
        v["length_m"] = _length(body.get("Dimension"))
        draught = body.get("MaximumStaticDraught")
        v["draught_m"] = draught if draught else None

    def _apply_class_b_static(self, v: dict[str, Any], body: dict[str, Any]) -> None:
        a, b = body.get("ReportA") or {}, body.get("ReportB") or {}
        if a.get("Valid") and _clean_text(a.get("Name")):
            v["name"] = _clean_text(a.get("Name"))
        if b.get("Valid"):
            v["type"] = b.get("ShipType")
            v["callsign"] = _clean_text(b.get("CallSign"))
            v["length_m"] = _length(b.get("Dimension"))

    def _feature(self, mmsi: str, v: dict[str, Any]) -> Feature | None:
        bearing = v.get("heading") if v.get("heading") is not None else v.get("cog")
        sog = v.get("sog")
        feat = Feature(
            id=mmsi,
            layer=INFO.key,
            lat=v["lat"],
            lon=v["lon"],
            timestamp=v["ts"],
            properties={
                "title": v.get("name") or f"MMSI {mmsi}",
                "name": v.get("name"),
                "mmsi": mmsi,
                "imo": v.get("imo"),
                "callsign": v.get("callsign"),
                "category": ship_category(v.get("type")),
                "nav_status": v.get("nav_status"),
                "speed_kn": round(sog, 1) if sog is not None else None,
                "course": round(v["cog"]) if v.get("cog") is not None else None,
                "bearing": round(bearing) if bearing is not None else None,
                "moving": sog is not None and sog >= 0.5,
                "destination": v.get("destination"),
                "length_m": v.get("length_m"),
                "draught_m": v.get("draught_m"),
                "ais_class": v.get("ais_class"),
                "ship_type": v.get("type"),  # raw AIS code, lets us restore state after a restart
                "sample": self.demo_mode,
            },
        )
        return feat if feat.in_australia() else None

    # ---------- demo mode ----------

    async def _demo_stream(self) -> AsyncIterator[dict[str, Any]]:
        """Clearly fictional sample vessels, gently moving, for running without a key."""
        self.meta["connected"] = True
        ships = [dict(s) for s in DEMO_SHIPS]
        for s in ships:
            yield _demo_static(s)
        while True:
            now = self._now()
            for s in ships:
                if s["sog"]:
                    s["lat"] += s["dlat"]
                    s["lon"] += s["dlon"]
                yield _demo_position(s, now)
            await asyncio.sleep(self._demo_interval_s)


def parse_ais_time(text: Any) -> datetime | None:
    """aisstream sends e.g. '2026-11-05 01:23:45.123456789 +0000 UTC'."""
    if not isinstance(text, str):
        return None
    m = re.match(r"(\d{4}-\d\d-\d\d) (\d\d:\d\d:\d\d)(?:\.(\d+))?", text)
    if not m:
        return None
    micro = (m.group(3) or "0")[:6].ljust(6, "0")
    return datetime.fromisoformat(f"{m.group(1)}T{m.group(2)}.{micro}+00:00")


def _clean_text(value: Any) -> str | None:
    """AIS pads text with '@' and spaces."""
    if not isinstance(value, str):
        return None
    text = value.replace("@", " ").strip()
    return re.sub(r"\s+", " ", text) or None


def _length(dim: Any) -> int | None:
    if not isinstance(dim, dict):
        return None
    total = (dim.get("A") or 0) + (dim.get("B") or 0)
    return total or None


# Fictional vessels (MMSIs in a range no real ship uses: 9-digit numbers starting 999).
DEMO_SHIPS = [
    dict(mmsi=999000001, name="SAMPLE TRADER", type=70, lat=-33.995, lon=151.235, sog=0.0, dlat=0, dlon=0,
         cog=0, dest="AU BTB", status=5),
    dict(mmsi=999000002, name="SAMPLE SPIRIT", type=80, lat=-34.06, lon=151.32, sog=11.5, dlat=0.004,
         dlon=-0.002, cog=330, dest="AU BTB", status=0),
    dict(mmsi=999000003, name="SAMPLE FERRY", type=60, lat=-33.855, lon=151.22, sog=14.0, dlat=0.0005,
         dlon=0.003, cog=80, dest="MANLY", status=0),
    dict(mmsi=999000004, name="SAMPLE TUG", type=52, lat=-33.97, lon=151.21, sog=6.0, dlat=-0.001,
         dlon=0.001, cog=135, dest=None, status=0),
    dict(mmsi=999000005, name="SAMPLE STAR", type=70, lat=-32.92, lon=151.79, sog=0.0, dlat=0, dlon=0,
         cog=0, dest="AU NTL", status=1),
    dict(mmsi=999000006, name="SAMPLE CATCH", type=30, lat=-34.45, lon=150.95, sog=4.5, dlat=-0.001,
         dlon=0.002, cog=120, dest=None, status=7),
]  # fmt: skip


def _demo_static(s: dict[str, Any]) -> dict[str, Any]:
    return {
        "MessageType": "ShipStaticData",
        "MetaData": {"MMSI": s["mmsi"], "ShipName": s["name"]},
        "Message": {
            "ShipStaticData": {
                "UserID": s["mmsi"], "Valid": True, "Name": s["name"], "Type": s["type"],
                "Destination": s["dest"] or "", "CallSign": "", "ImoNumber": 0,
                "Dimension": {"A": 100, "B": 20, "C": 10, "D": 10}, "MaximumStaticDraught": 0,
            }
        },
    }  # fmt: skip


def _demo_position(s: dict[str, Any], now: datetime) -> dict[str, Any]:
    return {
        "MessageType": "PositionReport",
        "MetaData": {
            "MMSI": s["mmsi"], "ShipName": s["name"],
            "time_utc": now.strftime("%Y-%m-%d %H:%M:%S.%f +0000 UTC"),
        },
        "Message": {
            "PositionReport": {
                "UserID": s["mmsi"], "Valid": True, "Latitude": s["lat"], "Longitude": s["lon"],
                "Sog": s["sog"], "Cog": s["cog"], "TrueHeading": s["cog"] if s["sog"] else 511,
                "NavigationalStatus": s["status"],
            }
        },
    }  # fmt: skip
