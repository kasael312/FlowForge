"""“Việc tự động”: cards for the industry flows, config, try-run, notifications."""

from __future__ import annotations

import asyncio
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.biz import automations
from app.biz.automation_models import Notification
from app.db import get_db
from app.models import Flow, Run, RunStatus, StepRun
from app.schemas import UtcDateTime
from app.services.runs import run_manager
from app.workspace import CurrentWorkspace, OwnerWorkspace, WorkspaceContext

router = APIRouter(prefix="/biz", tags=["automations"])
DbSession = Annotated[Session, Depends(get_db)]


class AutomationOut(BaseModel):
    id: str
    key: str
    name: str
    description: str
    trigger_label: str
    enabled: bool
    config_schema: list[dict[str, Any]]
    config: dict[str, Any]
    runs_7d: int
    failed_7d: int
    last_run_at: UtcDateTime | None
    last_error: str | None
    #: True when the flow needs an LLM model (reply flow).
    needs_model: bool


class AutomationUpdate(BaseModel):
    enabled: bool | None = None
    config: dict[str, Any] | None = None


class TryResult(BaseModel):
    run_id: str
    status: str
    error: str | None
    #: Messages the flow would send (step outputs / delivered outputs), already marked [chạy thử].
    messages: list[str]
    inputs: dict[str, Any]


class NotificationOut(BaseModel):
    id: str
    kind: str
    title: str
    body: str
    read: bool
    delivered_via: str
    created_at: UtcDateTime


def _out(db: Session, ctx: WorkspaceContext, f: Flow) -> AutomationOut:
    d = automations.DEFS.get(f.flow_key or "")
    stats = automations.card_stats(db, ctx.id, f)
    return AutomationOut(
        id=f.id,
        key=f.flow_key or "",
        name=f.name,
        description=f.description,
        trigger_label=d.trigger_label if d else "",
        enabled=bool(f.enabled),
        config_schema=list(f.config_schema or []),
        config=dict(f.config or {}),
        needs_model=(f.flow_key == "reply_and_book"),
        **stats,
    )


def _flow(db: Session, ctx: WorkspaceContext, key: str) -> Flow:
    f = db.scalar(select(Flow).where(Flow.workspace_id == ctx.id, Flow.flow_key == key))
    if f is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "automation not found")
    return f


@router.get("/automations", response_model=list[AutomationOut])
def list_automations(ctx: OwnerWorkspace, db: DbSession) -> list[AutomationOut]:
    """Seeds the cards on first call for workspaces created before this feature."""
    if not automations.workspace_flows(db, ctx.id):
        automations.seed_flows(db, ctx.workspace, ctx.member.user_id)
    return [_out(db, ctx, f) for f in automations.workspace_flows(db, ctx.id)]


@router.put("/automations/{key}", response_model=AutomationOut)
def update_automation(
    key: str, body: AutomationUpdate, ctx: OwnerWorkspace, db: DbSession
) -> AutomationOut:
    f = _flow(db, ctx, key)
    if body.enabled is not None:
        f.enabled = body.enabled
    if body.config is not None:
        allowed = {x["key"] for x in (f.config_schema or [])}
        cfg = dict(f.config or {})
        for k, v in body.config.items():
            if k in allowed:
                cfg[k] = v if not isinstance(v, str) else v[:4000]
        f.config = cfg
    db.commit()
    db.refresh(f)
    return _out(db, ctx, f)


@router.post("/automations/{key}/try", response_model=TryResult)
async def try_automation(
    key: str, ctx: OwnerWorkspace, db: DbSession, wait_seconds: int = 60
) -> TryResult:
    """Run the flow once with a simulated customer; nothing is sent or saved (dry run)."""
    f = _flow(db, ctx, key)
    d = automations.DEFS.get(key)
    if d is None or d.sample is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "automation has no dry run")
    inputs = d.sample(db, ctx.workspace, f)
    run = automations.start_run(db, f, inputs, None, task=f"Chạy thử: {f.name}", dry_run=True)
    task = run_manager.launch(run.id)
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=max(5, min(wait_seconds, 180)))
    except TimeoutError:
        pass
    db.expire_all()
    row = db.get(Run, run.id)
    steps = db.scalars(
        select(StepRun).where(StepRun.run_id == run.id).order_by(StepRun.started_at)
    ).all()
    messages: list[str] = []
    for o in row.outputs or []:
        delivered = o.get("delivered") if isinstance(o, dict) else None
        if isinstance(delivered, dict) and delivered.get("result"):
            messages.append(str(delivered["result"]))
        elif isinstance(o, dict) and o.get("value"):
            messages.append(str(o["value"]))
    if not messages:
        messages = [s.output for s in steps if s.output][-1:]
    return TryResult(
        run_id=run.id,
        status=(row.status or RunStatus.queued).value,
        error=row.error,
        messages=messages,
        inputs={k: v for k, v in inputs.items() if k != "system_context"},
    )


@router.post("/automations/tick")
def tick_now(ctx: OwnerWorkspace, db: DbSession) -> dict[str, Any]:
    """Evaluate the scheduled flows right now (what the background loop does every minute)."""
    started = automations.tick_workspace(db, ctx.workspace, run_manager.launch)
    return {"started": len(started), "run_ids": [r.id for r in started]}


@router.get("/notifications", response_model=list[NotificationOut])
def list_notifications(ctx: CurrentWorkspace, db: DbSession) -> list[Notification]:
    return automations.unread_notifications(db, ctx.id)


@router.post("/notifications/{notification_id}/read", response_model=NotificationOut)
def mark_read(notification_id: str, ctx: CurrentWorkspace, db: DbSession) -> Notification:
    n = db.get(Notification, notification_id)
    if n is None or n.workspace_id != ctx.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "notification not found")
    n.read = True
    db.commit()
    return n
