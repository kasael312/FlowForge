"""WebSocket endpoint streaming a run's log events."""

from __future__ import annotations

import asyncio
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.executor.events import bus
from app.models import Run, RunStatus, StepRun
from app.services.replay import replay_events

log = logging.getLogger(__name__)
router = APIRouter(tags=["runs"])

WS_NOT_FOUND = 4404
END_EVENT = {"event": "end"}


@router.websocket("/runs/{run_id}/logs")
async def run_logs(
    websocket: WebSocket, run_id: str, db: Annotated[Session, Depends(get_db)]
) -> None:
    """Stream log events of a run as JSON messages.

    Behaviour:

    * Run still executing in this process: send buffered history, then live
      events until the run finishes.
    * Run already finished (or history evicted): rebuild the events from the
      database and send them in one go.
    * Unknown run: close with code 4404.

    The stream always ends with ``{"event": "end"}`` followed by a close.
    """
    run = db.get(Run, run_id)
    await websocket.accept()
    if run is None:
        # Accept first so the client sees a proper close code instead of HTTP 403.
        await websocket.close(code=WS_NOT_FOUND, reason="run not found")
        return

    live = bool(bus.history(run_id)) or run.status in (RunStatus.queued, RunStatus.running)
    try:
        if live:
            await _stream_live(websocket, run_id)
        else:
            steps = list(
                db.scalars(
                    select(StepRun)
                    .where(StepRun.run_id == run_id)
                    .order_by(StepRun.started_at, StepRun.id)
                ).all()
            )
            for ev in replay_events(run, steps):
                await websocket.send_json(ev)
            await websocket.send_json(END_EVENT)
            await websocket.close()
    except WebSocketDisconnect:
        pass
    except RuntimeError:  # send on closed socket
        log.debug("websocket for run %s closed early", run_id)


async def _stream_live(websocket: WebSocket, run_id: str) -> None:
    """Forward bus events until the run closes or the client disconnects."""

    async def sender() -> None:
        async for ev in bus.subscribe(run_id):
            await websocket.send_json(ev)
        await websocket.send_json(END_EVENT)

    async def receiver() -> None:
        # We never expect client messages; this only detects disconnects.
        while True:
            await websocket.receive_text()

    send_task = asyncio.create_task(sender())
    recv_task = asyncio.create_task(receiver())
    done, pending = await asyncio.wait({send_task, recv_task}, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    for task in done:
        exc = task.exception()
        if exc is not None and not isinstance(exc, WebSocketDisconnect):
            raise exc
    if send_task in done and not send_task.cancelled():
        await websocket.close()
