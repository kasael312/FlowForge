"""Endpoints to start, inspect and cancel runs. Scoped to the authenticated user."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import CurrentUser
from app.config import get_settings
from app.db import get_db
from app.executor.workspace import WorkspaceError
from app.models import Artifact, Run, RunStatus, StepRun, User
from app.routers.flows import get_owned_flow
from app.schemas import ApprovalDecision, ArtifactOut, RunCreate, RunDetail, RunOut, StepRunOut
from app.services.artifacts import artifact_path, list_artifacts
from app.services.runs import run_manager

router = APIRouter(tags=["runs"])
DbSession = Annotated[Session, Depends(get_db)]


def get_owned_run(db: Session, user: User, run_id: str) -> Run:
    """Fetch a run owned by ``user`` or raise 404."""
    run = db.get(Run, run_id)
    if run is None or run.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found")
    return run


@router.post("/flows/{flow_id}/run", response_model=RunOut, status_code=status.HTTP_202_ACCEPTED)
async def start_run(flow_id: str, body: RunCreate, user: CurrentUser, db: DbSession) -> Run:
    """Queue a run of ``flow_id`` and start executing it in the background.

    Declared ``async`` so the background task is created on the server's loop.
    """
    flow = get_owned_flow(db, user, flow_id)
    if not flow.nodes:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "flow has no steps")
    try:
        run = run_manager.create_run(db, flow, body, user_id=user.id)
    except WorkspaceError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    run_manager.launch(run.id)
    return run


@router.get("/runs", response_model=list[RunOut])
def list_runs(
    user: CurrentUser,
    db: DbSession,
    flow_id: str | None = None,
    run_status: Annotated[RunStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[Run]:
    """List the user's runs, newest first, optionally filtered by flow and status."""
    stmt = select(Run).where(Run.user_id == user.id).order_by(Run.created_at.desc()).limit(limit)
    if flow_id:
        stmt = stmt.where(Run.flow_id == flow_id)
    if run_status:
        stmt = stmt.where(Run.status == run_status)
    return list(db.scalars(stmt).all())


@router.get("/runs/{run_id}", response_model=RunDetail)
def get_run(run_id: str, user: CurrentUser, db: DbSession) -> Run:
    """Run details including the final diff."""
    return get_owned_run(db, user, run_id)


@router.get("/runs/{run_id}/steps", response_model=list[StepRunOut])
def list_step_runs(run_id: str, user: CurrentUser, db: DbSession) -> list[StepRun]:
    """Every step execution of a run, in order, with full messages."""
    get_owned_run(db, user, run_id)
    stmt = select(StepRun).where(StepRun.run_id == run_id).order_by(StepRun.started_at)
    return list(db.scalars(stmt).all())


@router.post("/runs/{run_id}/cancel", response_model=RunOut)
def cancel_run(run_id: str, user: CurrentUser, db: DbSession) -> Run:
    """Cancel a queued or running run."""
    run = get_owned_run(db, user, run_id)
    if run.status not in (RunStatus.queued, RunStatus.running, RunStatus.waiting):
        raise HTTPException(status.HTTP_409_CONFLICT, f"run is already {run.status.value}")
    if not run_manager.cancel(run_id):
        # Not active in this process (e.g. after a restart): mark it directly.
        run.status = RunStatus.cancelled
        run.error = "cancelled (no active executor)"
        db.commit()
        db.refresh(run)
    return run


@router.post("/runs/{run_id}/approve", response_model=RunOut)
def approve_run(run_id: str, body: ApprovalDecision, user: CurrentUser, db: DbSession) -> Run:
    """Approve or reject the step the run is waiting on."""
    run = get_owned_run(db, user, run_id)
    if run.status is not RunStatus.waiting or not run_manager.approve(
        run_id, body.decision, body.comment
    ):
        raise HTTPException(
            status.HTTP_409_CONFLICT, "run is not waiting for approval in this process"
        )
    run.status = RunStatus.running  # the executor flips it too; reflect it in this response
    db.commit()
    db.refresh(run)
    return run


@router.get("/runs/{run_id}/artifacts", response_model=list[ArtifactOut])
def run_artifacts(run_id: str, user: CurrentUser, db: DbSession) -> list[Artifact]:
    """Files kept from ``/repo/output`` of the run."""
    get_owned_run(db, user, run_id)
    return list_artifacts(db, run_id)


@router.get("/runs/{run_id}/artifacts/{artifact_id}/download")
def download_artifact(
    run_id: str, artifact_id: str, user: CurrentUser, db: DbSession
) -> FileResponse:
    """Stream one artifact as an attachment."""
    get_owned_run(db, user, run_id)
    art = db.get(Artifact, artifact_id)
    if art is None or art.run_id != run_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "artifact not found")
    try:
        path = artifact_path(get_settings(), art)
    except FileNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "artifact missing") from exc
    if not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "artifact missing on disk")
    return FileResponse(path, media_type=art.content_type, filename=art.name.rsplit("/", 1)[-1])
