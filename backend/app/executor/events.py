"""In-process pub/sub of run events, consumed by the WebSocket log stream."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

MAX_HISTORY = 5000


class RunEventBus:
    """Fan-out of events per run id with a bounded replay buffer.

    Subscribers that connect after a run started receive the buffered history
    first, then live events. ``None`` on the queue signals end of stream.
    """

    def __init__(self) -> None:
        self._history: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self._subs: dict[str, set[asyncio.Queue[dict[str, Any] | None]]] = defaultdict(set)
        self._closed: set[str] = set()

    def publish(self, run_id: str, event: str, **data: Any) -> dict[str, Any]:
        """Record and broadcast an event. Returns the enriched event dict."""
        payload: dict[str, Any] = {
            "event": event,
            "ts": datetime.now(UTC).isoformat(),
            **data,
        }
        hist = self._history[run_id]
        hist.append(payload)
        if len(hist) > MAX_HISTORY:
            del hist[: len(hist) - MAX_HISTORY]
        for q in list(self._subs.get(run_id, ())):
            q.put_nowait(payload)
        return payload

    def close(self, run_id: str) -> None:
        """Mark a run's stream as finished; subscribers receive ``None``."""
        self._closed.add(run_id)
        for q in list(self._subs.get(run_id, ())):
            q.put_nowait(None)

    def history(self, run_id: str) -> list[dict[str, Any]]:
        """Buffered events for a run (empty if the run isn't in memory)."""
        return list(self._history.get(run_id, ()))

    def is_closed(self, run_id: str) -> bool:
        """Whether :meth:`close` has been called for this run."""
        return run_id in self._closed

    async def subscribe(self, run_id: str) -> AsyncIterator[dict[str, Any]]:
        """Yield history then live events until the run closes."""
        q: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self._subs[run_id].add(q)
        try:
            for ev in self.history(run_id):
                yield ev
            if self.is_closed(run_id):
                return
            while True:
                ev = await q.get()
                if ev is None:
                    return
                yield ev
        finally:
            self._subs[run_id].discard(q)

    def forget(self, run_id: str) -> None:
        """Drop buffered state for a run (call once it's persisted)."""
        self._history.pop(run_id, None)
        self._subs.pop(run_id, None)
        self._closed.discard(run_id)

    def reset(self) -> None:
        """Drop everything (tests)."""
        self._history.clear()
        self._subs.clear()
        self._closed.clear()


bus = RunEventBus()
"""Process-wide bus shared by executor and API."""
