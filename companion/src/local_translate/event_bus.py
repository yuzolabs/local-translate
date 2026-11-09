"""Fan-out bus for download progress, llama-server lifecycle, and errors."""

from __future__ import annotations

import asyncio
import time
from typing import Any


class EventBus:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue] = set()
        self.recent: list[dict[str, Any]] = []

    def publish(self, event_type: str, **payload: Any) -> dict[str, Any]:
        event = {"type": event_type, "at": time.time(), **payload}
        self.recent.append(event)
        del self.recent[:-200]
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass
        return event

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)
