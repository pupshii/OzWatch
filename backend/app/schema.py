"""The common schema every connector normalises into.

Every source (cameras, flights, ships, fires...) is turned into a list of
`Feature` objects. Everything downstream - the database, the API, the
browser - only ever deals with this one shape, so adding a new source never
touches the map or the storage code.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

# Generous bounding box around Australia incl. external territories' waters.
# Used to reject obviously bad coordinates (e.g. 0,0 or swapped lat/lon).
AUS_BBOX = {"lat_min": -48.0, "lat_max": -8.0, "lon_min": 108.0, "lon_max": 160.0}


class Feature(BaseModel):
    """One thing on the map at one point in time."""

    id: str = Field(description="Stable ID, unique within its layer")
    layer: str = Field(description="Layer key, e.g. 'cameras'")
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    timestamp: datetime = Field(description="When the source says this was observed (UTC)")
    properties: dict[str, Any] = Field(default_factory=dict)

    @field_validator("timestamp")
    @classmethod
    def _must_be_tz_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("timestamp must be timezone-aware")
        return v

    def in_australia(self) -> bool:
        b = AUS_BBOX
        return b["lat_min"] <= self.lat <= b["lat_max"] and b["lon_min"] <= self.lon <= b["lon_max"]

    def to_geojson(self) -> dict[str, Any]:
        return {
            "type": "Feature",
            "id": self.id,
            "geometry": {"type": "Point", "coordinates": [self.lon, self.lat]},
            "properties": {
                **self.properties,
                "id": self.id,
                "layer": self.layer,
                "timestamp": self.timestamp.isoformat(),
            },
        }
