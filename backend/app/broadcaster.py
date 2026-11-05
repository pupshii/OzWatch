"""In-process publish/subscribe used to push live updates to browsers (SSE).

Each connected browser gets its own bounded queue. If a browser falls behind
(queue full), we drop it rather than letting memory grow; the SSE loop sees it
is no longer subscribed and closes the stream, and the browser's EventSource
reconnects automatically and re-downloads the current snapshot.

With a single backend process this is all we need. Running several backend
replicas would need a shared channel (Redis pub/sub or Postgres
LISTEN/NOTIFY) - that is the point where Redis would earn its place.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

log = logging.getLogger(__name__)


class Broadcaster:
    def __init__(self, queue_size: int = 100) -> None:
        self._subscribers: set[asyncio.Queue] = set()
        self._queue_size = queue_size

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=self._queue_size)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    def is_subscribed(self, q: asyncio.Queue) -> bool:
        return q in self._subscribers

    def publish(self, message: dict[str, Any]) -> None:
        for q in list(self._subscribers):
            try:
                q.put_nowait(message)
            except asyncio.QueueFull:
                log.warning("dropping slow SSE subscriber")
                self._subscribers.discard(q)
