"""Runs a streaming (push) connector, e.g. AIS ships over a websocket.

Why this differs from `Poller`:
* Messages arrive continuously (many per second), so we don't write each one.
  They go into an in-memory buffer keyed by feature id (latest wins), and
  every few seconds the buffer is flushed to the store as one batch and one
  delta to browsers. Twenty position reports from one ship within 5 s become
  a single database write.
* History is throttled per feature (e.g. at most one row per ship per
  minute), otherwise a busy coastline would write millions of rows a day.
* A dropped connection is normal. We reconnect with exponential backoff, and
  the backoff resets as soon as messages flow again.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from app.broadcaster import Broadcaster
from app.connectors.base import StreamConnector
from app.scheduler import ConnectorStatus, backoff_delay, describe_error, fingerprint
from app.schema import Feature
from app.store import Store

log = logging.getLogger(__name__)

SILENCE_IS_DEGRADED_S = 120  # connected but nothing for 2 min -> not "ok"
STATUS_PUBLISH_EVERY_S = 30
RECORD_RUN_EVERY_S = 60


class StreamRunner:
    def __init__(
        self,
        connector: StreamConnector,
        store: Store,
        broadcaster: Broadcaster,
        flush_seconds: float = 5.0,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.connector = connector
        self.store = store
        self.broadcaster = broadcaster
        self.flush_seconds = flush_seconds
        self._clock = clock
        self._sleep = sleep

        self._buffer: dict[str, Feature] = {}
        self._sent: dict[str, tuple] = {}  # id -> fingerprint last sent to browsers
        self._last_history: dict[str, datetime] = {}
        self._connected = False
        self._last_message_at: datetime | None = None
        self._window_start = clock()
        self._window_messages = 0
        self._window_errors = 0
        self._last_status_pub: datetime | None = None
        self._last_state: str | None = None

        self.status = ConnectorStatus(
            layer=connector.info.key,
            title=connector.info.title,
            poll_seconds=int(flush_seconds),
            demo=connector.demo_mode,
            mode="stream",
        )

    # ---------- receiving ----------

    def ingest(self, raw) -> None:
        """Handle one raw message. A bad message is counted and skipped, never fatal."""
        self._window_messages += 1
        self._last_message_at = self._clock()
        if self.status.consecutive_failures:
            # Messages are flowing again: the connection is healthy, reset backoff.
            self.status.consecutive_failures = 0
        try:
            feat = self.connector.handle(raw)
        except Exception as exc:  # noqa: BLE001
            self._window_errors += 1
            log.debug("%s: bad message skipped: %s", self.connector.info.key, exc)
            return
        if feat is not None:
            self._buffer[feat.id] = feat

    async def consume_once(self) -> None:
        """One connection's lifetime. Returns after it ends (error or clean close)."""
        self.status.last_attempt = self._clock()
        try:
            async for raw in self.connector.stream():
                self._connected = True
                self.ingest(raw)
            raise ConnectionError("server closed the stream")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            self._connected = False
            self.status.consecutive_failures += 1
            self.status.last_error = describe_error(exc)
            log.warning(
                "%s: stream ended (%d in a row): %s",
                self.connector.info.key, self.status.consecutive_failures, self.status.last_error,
            )  # fmt: skip
            self._refresh_state()
            self._publish_status(force=True)

    async def run_forever(self) -> None:
        await self._load_existing()
        flusher = asyncio.create_task(self._flush_loop())
        try:
            while True:
                await self.consume_once()
                await self._sleep(backoff_delay(max(1, self.status.consecutive_failures)))
        finally:
            flusher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await flusher

    # ---------- flushing ----------

    async def _flush_loop(self) -> None:
        while True:
            await self._sleep(self.flush_seconds)
            try:
                await self.flush()
            except Exception:  # noqa: BLE001
                log.exception("%s: flush failed", self.connector.info.key)

    async def flush(self) -> None:
        info = self.connector.info
        now = self._clock()
        batch = list(self._buffer.values())
        self._buffer.clear()
        history = [f for f in batch if self._history_due(f)] if info.keep_history else []

        try:
            await self.store.upsert(info.key, batch, seen_at=now, history=history)
            removed = await self.store.prune_stale(
                info.key, older_than=now - timedelta(seconds=info.stale_after_seconds)
            )
        except Exception as exc:  # noqa: BLE001
            # Put the batch back (newer messages win) and try again next flush.
            for f in batch:
                self._buffer.setdefault(f.id, f)
            self.status.last_error = f"store: {describe_error(exc)}"
            log.warning("%s: could not save batch: %s", info.key, exc)
            return

        for f in history:
            self._last_history[f.id] = f.timestamp
        for fid in removed:
            self._sent.pop(fid, None)
            self._last_history.pop(fid, None)

        changed = [f for f in batch if self._sent.get(f.id) != fingerprint(f)]
        for f in changed:
            self._sent[f.id] = fingerprint(f)
        if changed or removed:
            self.broadcaster.publish(
                {
                    "type": "delta",
                    "layer": info.key,
                    "upserts": [f.to_geojson() for f in changed],
                    "removed": removed,
                }
            )

        if batch:
            self.status.last_success = now
        self.status.feature_count = len(self._sent)
        self._refresh_state()
        self._publish_status()
        await self._maybe_record_run(now)

    def _history_due(self, f: Feature) -> bool:
        last = self._last_history.get(f.id)
        gap = self.connector.info.history_min_interval_s
        return last is None or (f.timestamp - last).total_seconds() >= gap

    # ---------- status ----------

    def _refresh_state(self) -> None:
        now = self._clock()
        fresh = self._last_message_at is not None and (
            (now - self._last_message_at).total_seconds() < SILENCE_IS_DEGRADED_S
        )
        if self._connected and fresh:
            self.status.state = "ok"
            self.status.last_error = None
        elif self.status.last_success:
            self.status.state = "degraded"
        elif self.status.consecutive_failures:
            self.status.state = "error"
        else:
            self.status.state = "starting"
        elapsed_min = max((now - self._window_start).total_seconds() / 60, 1 / 60)
        self.status.extra = {
            **self.connector.meta,
            "connected": self._connected,
            "messages_per_min": round(self._window_messages / elapsed_min),
        }

    def _publish_status(self, force: bool = False) -> None:
        now = self._clock()
        due = (
            force
            or self.status.state != self._last_state
            or self._last_status_pub is None
            or (now - self._last_status_pub).total_seconds() >= STATUS_PUBLISH_EVERY_S
        )
        if due:
            self._last_status_pub = now
            self._last_state = self.status.state
            self.broadcaster.publish(
                {"type": "status", "layer": self.connector.info.key, "status": self.status.as_dict()}
            )

    async def _maybe_record_run(self, now: datetime) -> None:
        """One connector_runs row per minute summarising the stream (not one per flush)."""
        if (now - self._window_start).total_seconds() < RECORD_RUN_EVERY_S:
            return
        try:
            await self.store.record_run(
                self.connector.info.key, self._window_start, now,
                self._connected, self._window_messages, self.status.last_error,
            )  # fmt: skip
        except Exception:  # noqa: BLE001
            log.exception("could not record stream stats")
        self._window_start, self._window_messages, self._window_errors = now, 0, 0

    async def _load_existing(self) -> None:
        """After a restart, know what's already on the map so counts and deltas are right."""
        try:
            existing = await self.store.get_layer(self.connector.info.key)
            for f in existing:
                self._sent[f.id] = fingerprint(f)
            self.connector.seed(existing)
            self.status.feature_count = len(self._sent)
        except Exception:  # noqa: BLE001
            log.exception("could not load existing features")
