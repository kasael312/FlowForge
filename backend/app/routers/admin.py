"""System-admin panel: workspaces with usage and trial dates, invoices to confirm, alerts, logs."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import AdminUser
from app.billing import service as billing
from app.billing.models import Invoice, InvoiceStatus
from app.biz.automation_models import AdminAlert, AutomationLog, Notification
from app.biz.models import Conversation, Customer
from app.db import get_db
from app.models import Flow, Run, User, Workspace, WorkspaceMember

router = APIRouter(prefix="/admin", tags=["admin"])
DbSession = Annotated[Session, Depends(get_db)]


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=UTC)).isoformat()


def _workspace(db: Session, workspace_id: str) -> Workspace:
    ws = db.get(Workspace, workspace_id)
    if ws is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "workspace not found")
    return ws


def _row(db: Session, ws: Workspace, now: datetime) -> dict[str, Any]:
    st = billing.state(db, ws, now)
    owner = db.scalar(
        select(User.email)
        .join(WorkspaceMember, WorkspaceMember.user_id == User.id)
        .where(WorkspaceMember.workspace_id == ws.id, WorkspaceMember.role == "owner")
        .order_by(WorkspaceMember.id)
    )
    customers = (
        db.scalar(select(func.count()).select_from(Customer).where(Customer.workspace_id == ws.id))
        or 0
    )
    last_message = db.scalar(
        select(func.max(Conversation.last_message_at)).where(Conversation.workspace_id == ws.id)
    )
    members = (
        db.scalar(
            select(func.count())
            .select_from(WorkspaceMember)
            .where(WorkspaceMember.workspace_id == ws.id)
        )
        or 0
    )
    alerts = (
        db.scalar(
            select(func.count())
            .select_from(AdminAlert)
            .where(AdminAlert.workspace_id == ws.id, AdminAlert.read.is_(False))
        )
        or 0
    )
    return {
        "id": ws.id,
        "name": ws.name,
        "industry": ws.industry,
        "owner_email": owner,
        "members": members,
        "customers": customers,
        "created_at": _iso(ws.created_at),
        "last_activity_at": _iso(last_message),
        "plan": st["plan"]["key"],
        "plan_name": st["plan"]["name"],
        "status": st["status"],
        "trial_ends_at": st["trial_ends_at"],
        "current_period_end": st["current_period_end"],
        "days_left": st["days_left"],
        "usage": st["usage"],
        "open_alerts": alerts,
    }


@router.get("/summary")
def summary(_admin: AdminUser, db: DbSession) -> dict[str, Any]:
    now = datetime.now(UTC)
    rows = [_row(db, ws, now) for ws in db.scalars(select(Workspace)).all()]
    pending = (
        db.scalar(
            select(func.count()).select_from(Invoice).where(Invoice.status == InvoiceStatus.pending)
        )
        or 0
    )
    alerts = (
        db.scalar(select(func.count()).select_from(AdminAlert).where(AdminAlert.read.is_(False)))
        or 0
    )
    return {
        "workspaces": len(rows),
        "trials": len([r for r in rows if r["status"] == "trial"]),
        "trials_ending_7d": len(
            [r for r in rows if r["status"] == "trial" and (r["days_left"] or 0) <= 7]
        ),
        "paid": len([r for r in rows if r["status"] in ("active", "past_due")]),
        "expired": len([r for r in rows if r["status"] == "expired"]),
        "pending_invoices": pending,
        "open_alerts": alerts,
    }


@router.get("/workspaces")
def list_workspaces(_admin: AdminUser, db: DbSession, q: str | None = None) -> list[dict[str, Any]]:
    now = datetime.now(UTC)
    stmt = select(Workspace).order_by(Workspace.created_at.desc())
    if q:
        stmt = stmt.where(Workspace.name.ilike(f"%{q}%"))
    return [_row(db, ws, now) for ws in db.scalars(stmt).all()]


class ExtendTrial(BaseModel):
    days: int = Field(default=14, ge=1, le=365)
    note: str = ""


@router.post("/workspaces/{workspace_id}/extend-trial")
def extend_trial(
    workspace_id: str, body: ExtendTrial, admin: AdminUser, db: DbSession
) -> dict[str, Any]:
    ws = _workspace(db, workspace_id)
    billing.extend_trial(
        db, ws, body.days, f"{admin.email}: +{body.days} ngày. {body.note}".strip()
    )
    return _row(db, ws, datetime.now(UTC))


class SetPlan(BaseModel):
    plan: str
    months: int = Field(default=1, ge=1, le=24)
    note: str = ""


@router.post("/workspaces/{workspace_id}/plan")
def set_plan(workspace_id: str, body: SetPlan, admin: AdminUser, db: DbSession) -> dict[str, Any]:
    ws = _workspace(db, workspace_id)
    try:
        billing.set_plan(db, ws, body.plan, body.months, f"{admin.email}: {body.note}".strip())
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    return _row(db, ws, datetime.now(UTC))


@router.get("/workspaces/{workspace_id}/logs")
def workspace_logs(
    workspace_id: str,
    _admin: AdminUser,
    db: DbSession,
    limit: int = Query(default=200, ge=1, le=1000),
) -> list[dict[str, Any]]:
    """Automation log, notifications, alerts and runs of one workspace, newest first."""
    ws = _workspace(db, workspace_id)
    out: list[dict[str, Any]] = []
    for a in db.scalars(
        select(AutomationLog)
        .where(AutomationLog.workspace_id == ws.id)
        .order_by(AutomationLog.created_at.desc())
        .limit(limit)
    ).all():
        out.append(
            {
                "time": _iso(a.created_at),
                "kind": "automation",
                "text": f"{a.flow_key} · {a.entity_type} {a.entity_id} · {a.milestone or '-'} → {a.outcome}",
                "detail": {"run_id": a.run_id, **(a.detail or {})},
            }
        )
    for n in db.scalars(
        select(Notification)
        .where(Notification.workspace_id == ws.id)
        .order_by(Notification.created_at.desc())
        .limit(limit)
    ).all():
        out.append(
            {
                "time": _iso(n.created_at),
                "kind": n.kind,
                "text": f"{n.title}: {n.body[:200]}",
                "detail": {"read": n.read, "delivered_via": n.delivered_via},
            }
        )
    flow_ids = db.scalars(select(Flow.id).where(Flow.workspace_id == ws.id)).all()
    if flow_ids:
        for r in db.scalars(
            select(Run)
            .where(Run.flow_id.in_(flow_ids))
            .order_by(Run.created_at.desc())
            .limit(limit)
        ).all():
            out.append(
                {
                    "time": _iso(r.created_at),
                    "kind": "run",
                    "text": f"{r.task_input or r.flow_id} → {r.status.value}"
                    + (f" ({r.error})" if r.error else ""),
                    "detail": {"run_id": r.id, "cost_usd": r.cost_usd},
                }
            )
    out.sort(key=lambda e: e["time"] or "", reverse=True)
    return out[:limit]


@router.get("/invoices")
def list_invoices(
    _admin: AdminUser,
    db: DbSession,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
) -> list[dict[str, Any]]:
    stmt = select(Invoice).order_by(Invoice.created_at.desc()).limit(500)
    if status_filter:
        stmt = stmt.where(Invoice.status == InvoiceStatus(status_filter))
    rows = db.scalars(stmt).all()
    names = {w.id: w.name for w in db.scalars(select(Workspace)).all()}
    return [
        {
            **billing.invoice_out(i),
            "workspace_id": i.workspace_id,
            "workspace_name": names.get(i.workspace_id, "?"),
        }
        for i in rows
    ]


@router.post("/invoices/{invoice_id}/confirm")
def confirm_invoice(invoice_id: str, admin: AdminUser, db: DbSession) -> dict[str, Any]:
    inv = db.get(Invoice, invoice_id)
    if inv is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "invoice not found")
    try:
        sub = billing.confirm_invoice(db, inv, admin.id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return {
        **billing.invoice_out(inv),
        "subscription": {"plan": sub.plan, "current_period_end": _iso(sub.current_period_end)},
    }


@router.post("/invoices/{invoice_id}/cancel")
def cancel_invoice(invoice_id: str, _admin: AdminUser, db: DbSession) -> dict[str, Any]:
    inv = db.get(Invoice, invoice_id)
    if inv is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "invoice not found")
    try:
        billing.cancel_invoice(db, inv)
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return billing.invoice_out(inv)


@router.get("/alerts")
def list_alerts(
    _admin: AdminUser,
    db: DbSession,
    unread_only: bool = False,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[dict[str, Any]]:
    stmt = select(AdminAlert).order_by(AdminAlert.created_at.desc()).limit(limit)
    if unread_only:
        stmt = stmt.where(AdminAlert.read.is_(False))
    names = {w.id: w.name for w in db.scalars(select(Workspace)).all()}
    return [
        {
            "id": a.id,
            "workspace_id": a.workspace_id,
            "workspace_name": names.get(a.workspace_id or "", "-"),
            "key": a.key,
            "title": a.title,
            "body": a.body,
            "detail": a.detail or {},
            "read": a.read,
            "created_at": _iso(a.created_at),
        }
        for a in db.scalars(stmt).all()
    ]


@router.post("/alerts/{alert_id}/read")
def read_alert(alert_id: str, _admin: AdminUser, db: DbSession) -> dict[str, Any]:
    a = db.get(AdminAlert, alert_id)
    if a is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "alert not found")
    a.read = True
    db.commit()
    return {"id": a.id, "read": True}


@router.get("/trials-ending")
def trials_ending(_admin: AdminUser, db: DbSession, days: int = 7) -> list[dict[str, Any]]:
    now = datetime.now(UTC)
    horizon = now + timedelta(days=days)
    out = []
    for ws in db.scalars(select(Workspace)).all():
        sub = billing.get_subscription(db, ws)
        end = (
            sub.trial_ends_at if sub.trial_ends_at.tzinfo else sub.trial_ends_at.replace(tzinfo=UTC)
        )
        if billing.status_of(sub, now) == "trial" and end <= horizon:
            out.append(_row(db, ws, now))
    return out
