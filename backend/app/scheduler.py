"""Background polling with retries, backoff and per-connector health.

One `Poller` wraps one connector. Each cycle it:
  1. fetches + normalises (the connector's job),
  2. writes to the store,
  3. works out what actually changed since last time,
  4. pushes only those changes to browsers,
  5. records the outcome for /health.

Failures never crash the app: the poller backs off exponentially (with
jitter, so retries don't line up) and keeps serving the last good data.
If the source sends 429 Too Many Requests with a Retry-After header, that
wait is honoured instead.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import httpx

from app.broadcaster import Broadcaster
from app.connectors.base import Connector
from app.schema import Feature
from app.store import Store

log = logging.getLogger(__name__)

RETRY_BASE_SECONDS = 15
RETRY_MAX_SECONDS = 15 * 60


@dataclass
class ConnectorStatus:
    layer: str
    title: str
    poll_seconds: int
    demo: bool
    state: str = "starting"  # starting | ok | degraded | error
    last_attempt: datetime | None = None
    last_success: datetime | None = None
    last_error: str | None = None
    consecutive_failures: int = 0
    feature_count: int = 0
    extra: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        iso = lambda d: d.isoformat() if d else None  # noqa: E731
        return {
            "layer": self.layer,
            "title": self.title,
            "state": self.state,
            "demo": self.demo,
            "poll_seconds": self.poll_seconds,
            "last_attempt": iso(self.last_attempt),
            "last_success": iso(self.last_success),
            "last_error": self.last_error,
            "consecutive_failures": self.consecutive_failures,
            "feature_count": self.feature_count,
            "extra": self.extra,
        }


def _fingerprint(f: Feature) -> tuple:
    # What counts as "changed" for the browser: position or any property.
    # Deliberately excludes timestamp, so a camera whose details are identical
    # on every poll isn't re-sent every 10 minutes.
    return (round(f.lat, 6), round(f.lon, 6), tuple(sorted((k, str(v)) for k, v in f.properties.items())))


class Poller:
    def __init__(
        self,
        connector: Connector,
        store: Store,
        broadcaster: Broadcaster,
        poll_seconds: int | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.connector = connector
        self.store = store
        self.broadcaster = broadcaster
        self.poll_seconds = poll_seconds or connector.info.poll_seconds
        self._clock = clock
        self._sleep = sleep
        self._last: dict[str, tuple] = {}
        self._retry_after: float | None = None
        self.status = ConnectorStatus(
            layer=connector.info.key,
            title=connector.info.title,
            poll_seconds=self.poll_seconds,
            demo=connector.demo_mode,
        )

    async def poll_once(self) -> bool:
        info = self.connector.info
        started = self._clock()
        self.status.last_attempt = started
        try:
            features = await self.connector.run_once()
            await self.store.upsert(info.key, features, seen_at=started, keep_history=info.keep_history)
            removed = await self.store.prune_stale(
                info.key, older_than=started - timedelta(seconds=info.stale_after_seconds)
            )
        except Exception as exc:  # noqa: BLE001 - any failure is recorded, never fatal
            self._on_failure(exc)
            await self._safe_record(started, ok=False, n=0, error=self.status.last_error)
            return False

        changed = [f for f in features if self._last.get(f.id) != _fingerprint(f)]
        self._last = {f.id: _fingerprint(f) for f in features}
        for fid in removed:
            self._last.pop(fid, None)

        if changed or removed:
            self.broadcaster.publish(
                {
                    "type": "delta",
                    "layer": info.key,
                    "upserts": [f.to_geojson() for f in changed],
                    "removed": removed,
                }
            )

        self.status.state = "ok"
        self.status.last_success = started
        self.status.last_error = None
        self.status.consecutive_failures = 0
        self.status.feature_count = len(features)
        self.status.extra = dict(self.connector.meta)
        self.broadcaster.publish({"type": "status", "layer": info.key, "status": self.status.as_dict()})
        await self._safe_record(started, ok=True, n=len(features), error=None)
        log.info(
            "%s: %d features (%d changed, %d removed)", info.key, len(features), len(changed), len(removed)
        )
        return True

    def _on_failure(self, exc: Exception) -> None:
        self.status.consecutive_failures += 1
        self.status.last_error = _describe(exc)
        self.status.extra = dict(self.connector.meta)
        self.status.state = "degraded" if self.status.last_success else "error"
        self._retry_after = None
        if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429:
            header = exc.response.headers.get("Retry-After") or exc.response.headers.get(
                "X-Rate-Limit-Retry-After-Seconds"
            )
            if header and header.isdigit():
                self._retry_after = float(header)
        log.warning(
            "%s: poll failed (%d in a row): %s",
            self.connector.info.key,
            self.status.consecutive_failures,
            self.status.last_error,
        )
        self.broadcaster.publish(
            {"type": "status", "layer": self.connector.info.key, "status": self.status.as_dict()}
        )

    def next_delay(self) -> float:
        n = self.status.consecutive_failures
        if n == 0:
            return float(self.poll_seconds)
        if self._retry_after is not None:
            return self._retry_after
        backoff = min(RETRY_BASE_SECONDS * 2 ** (n - 1), RETRY_MAX_SECONDS)
        return backoff * random.uniform(0.8, 1.2)

    async def run_forever(self) -> None:
        while True:
            await self.poll_once()
            await self._sleep(self.next_delay())

    async def _safe_record(self, started, ok, n, error) -> None:
        try:
            await self.store.record_run(self.connector.info.key, started, self._clock(), ok, n, error)
        except Exception:  # noqa: BLE001
            log.exception("could not record connector run")


def _describe(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        detail = exc.response.headers.get("X-Error-Detail", "")
        return f"HTTP {exc.response.status_code} {detail}".strip()
    return f"{type(exc).__name__}: {exc}"[:300]
