"""Background loop that starts due industry runs for every workspace."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.biz import automations
from app.models import Flow, Workspace

log = logging.getLogger(__name__)


class BizScheduler:
    def __init__(
        self, session_factory: sessionmaker[Session], starter, interval_seconds: int = 60
    ) -> None:
        self.session_factory = session_factory
        self.starter = starter
        self.interval = max(10, interval_seconds)
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="biz-scheduler")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception) as exc:  # noqa: BLE001
                if not isinstance(exc, asyncio.CancelledError):
                    log.warning("biz scheduler stopped with %r", exc)
            self._task = None

    async def _loop(self) -> None:
        loop = asyncio.get_running_loop()
        # ``tick`` runs in a worker thread; runs must be launched on the event loop.
        starter = self.starter
        threadsafe = lambda run_id: loop.call_soon_threadsafe(starter, run_id)
        while True:
            try:
                await asyncio.to_thread(self.tick, None, threadsafe)
            except Exception:
                log.exception("biz scheduler tick failed")
            await asyncio.sleep(self.interval)

    def tick(self, now: datetime | None = None, starter=None) -> int:
        """One pass over workspaces that have at least one enabled scheduled flow."""
        now = now or datetime.now(UTC)
        starter = starter or self.starter
        started = 0
        with self.session_factory() as db:
            ws_ids = set(
                db.scalars(
                    select(Flow.workspace_id).where(
                        Flow.workspace_id.is_not(None), Flow.enabled.is_(True)
                    )
                ).all()
            )
            for ws_id in ws_ids:
                ws = db.get(Workspace, ws_id)
                if ws is None:
                    continue
                started += len(automations.tick_workspace(db, ws, starter, now))
        return started
