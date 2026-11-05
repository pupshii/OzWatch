"""The shared interface every data source implements.

Two kinds of source:

* `Connector` (polled, e.g. cameras, flights) does two things and nothing else:
    1. fetch()     - get raw data from the source over HTTP
    2. normalise() - turn that raw payload into a list of `Feature`s
* `StreamConnector` (pushed, e.g. ships over a websocket):
    1. stream()    - yield raw messages for as long as the connection lives
    2. handle()    - turn one message into a `Feature` (or None)

Keeping `normalise` a pure function (raw dict in, Features out) means it can
be unit-tested against a saved sample payload with no network at all.
Scheduling, retries, storage and broadcasting live elsewhere.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx

from app.schema import Feature


@dataclass(frozen=True)
class LayerInfo:
    """Static metadata the frontend needs to draw and credit a layer."""

    key: str
    title: str
    attribution: str
    attribution_url: str
    licence: str
    poll_seconds: int
    keep_history: bool  # moving things (planes) yes; fixed things (cameras) no
    stale_after_seconds: int  # drop features not seen for this long
    history_min_interval_s: int = 0  # at most one history row per feature per N seconds
    # "push": every change is streamed to browsers (fine for hundreds of features).
    # "viewport": browsers fetch only what's on screen, when zoomed in past min_zoom
    # (for dense layers like thousands of buses).
    delivery: str = "push"
    min_zoom: float = 0


class Connector(ABC):
    info: LayerInfo

    def __init__(self, client: httpx.AsyncClient):
        self.client = client
        # Small facts about the last fetch worth showing on /health
        # (e.g. remaining API credits). Copied into the poller status.
        self.meta: dict[str, Any] = {}

    @property
    def demo_mode(self) -> bool:
        """True when running on bundled sample data (e.g. no API key set)."""
        return False

    @abstractmethod
    async def fetch(self) -> Any:
        """Return the raw payload from the source."""

    @abstractmethod
    def normalise(self, raw: Any) -> list[Feature]:
        """Pure function: raw payload -> Features. Must not do I/O."""

    async def run_once(self) -> list[Feature]:
        raw = await self.fetch()
        return self.normalise(raw)


class StreamConnector(ABC):
    """A source that pushes messages to us over a long-lived connection."""

    info: LayerInfo

    def __init__(self) -> None:
        self.meta: dict[str, Any] = {}

    @property
    def demo_mode(self) -> bool:
        return False

    @abstractmethod
    def stream(self) -> AsyncIterator[Any]:
        """Connect and yield raw messages. Raise on errors; return when the server closes."""

    @abstractmethod
    def handle(self, raw: Any) -> Feature | None:
        """One raw message -> the updated Feature, or None if it doesn't change the map."""

    def seed(self, features: list[Feature]) -> None:  # noqa: B027 - optional hook, default no-op
        """Optional: restore per-feature state from the store after a restart."""
