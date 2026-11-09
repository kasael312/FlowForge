"""Endpoints to start, inspect and cancel runs."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.executor.workspace import WorkspaceError
from app.models import Flow, Run, RunStatus, StepRun
from app.schemas import RunCreate, RunDetail, RunOut, StepRunOut
from app.services.runs import run_manager

router = APIRouter(tags=["runs"])
DbSession = Annotated[Session, Depends(get_db)]


def _get_run_or_404(db: Session, run_id: str) -> Run:
    run = db.get(Run, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found")
    return run


@router.post("/flows/{flow_id}/run", response_model=RunOut, status_code=status.HTTP_202_ACCEPTED)
async def start_run(flow_id: str, body: RunCreate, db: DbSession) -> Run:
    """Queue a run of ``flow_id`` and start executing it in the background.

    Declared ``async`` so the background task is created on the server's loop.
    """
    flow = db.get(Flow, flow_id)
    if flow is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "flow not found")
    if not flow.nodes:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "flow has no steps")
    try:
        run = run_manager.create_run(db, flow, body)
    except WorkspaceError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    run_manager.launch(run.id)
    return run


@router.get("/runs", response_model=list[RunOut])
def list_runs(
    db: DbSession,
    flow_id: str | None = None,
    run_status: Annotated[RunStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[Run]:
    """List runs, newest first, optionally filtered by flow and status."""
    stmt = select(Run).order_by(Run.created_at.desc()).limit(limit)
    if flow_id:
        stmt = stmt.where(Run.flow_id == flow_id)
    if run_status:
        stmt = stmt.where(Run.status == run_status)
    return list(db.scalars(stmt).all())


@router.get("/runs/{run_id}", response_model=RunDetail)
def get_run(run_id: str, db: DbSession) -> Run:
    """Run details including the final diff."""
    return _get_run_or_404(db, run_id)


@router.get("/runs/{run_id}/steps", response_model=list[StepRunOut])
def list_step_runs(run_id: str, db: DbSession) -> list[StepRun]:
    """Every step execution of a run, in order, with full messages."""
    _get_run_or_404(db, run_id)
    stmt = select(StepRun).where(StepRun.run_id == run_id).order_by(StepRun.started_at)
    return list(db.scalars(stmt).all())


@router.post("/runs/{run_id}/cancel", response_model=RunOut)
def cancel_run(run_id: str, db: DbSession) -> Run:
    """Cancel a queued or running run."""
    run = _get_run_or_404(db, run_id)
    if run.status not in (RunStatus.queued, RunStatus.running):
        raise HTTPException(status.HTTP_409_CONFLICT, f"run is already {run.status.value}")
    if not run_manager.cancel(run_id):
        # Not active in this process (e.g. after a restart): mark it directly.
        run.status = RunStatus.cancelled
        run.error = "cancelled (no active executor)"
        db.commit()
        db.refresh(run)
    return run
