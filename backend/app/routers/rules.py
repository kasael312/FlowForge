"""Rules CRUD (global / flow / step scope)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import CurrentUser
from app.db import get_db
from app.models import Flow, Rule
from app.schemas import RuleCreate, RuleOut, RuleUpdate
from app.services.rules import ensure_builtin_rules

router = APIRouter(prefix="/rules", tags=["rules"])
DbSession = Annotated[Session, Depends(get_db)]


def _owned(db: Session, user_id: str, rule_id: str) -> Rule:
    r = db.get(Rule, rule_id)
    if r is None or r.user_id != user_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "rule not found")
    return r


def _check_scope(
    db: Session, user_id: str, scope: str, flow_id: str | None, step_id: str | None
) -> None:
    if scope == "global":
        return
    if not flow_id:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"{scope} rules need flow_id")
    flow = db.get(Flow, flow_id)
    if flow is None or flow.user_id != user_id:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "flow_id not found")
    if scope == "step":
        if not step_id:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "step rules need step_id")
        if step_id not in {n.get("id") for n in (flow.nodes or [])}:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "step_id not in that flow")


@router.get("", response_model=list[RuleOut])
def list_rules(
    user: CurrentUser, db: DbSession, scope: str | None = None, flow_id: str | None = None
) -> list[Rule]:
    """My rules (built-ins copied in on first call), optionally filtered by scope / flow."""
    ensure_builtin_rules(db, user.id)
    q = select(Rule).where(Rule.user_id == user.id)
    if scope:
        q = q.where(Rule.scope == scope)
    if flow_id:
        q = q.where(Rule.flow_id == flow_id)
    return list(db.scalars(q.order_by(Rule.scope, Rule.priority, Rule.name)).all())


@router.post("", response_model=RuleOut, status_code=status.HTTP_201_CREATED)
def create_rule(body: RuleCreate, user: CurrentUser, db: DbSession) -> Rule:
    _check_scope(db, user.id, body.scope, body.flow_id, body.step_id)
    r = Rule(
        user_id=user.id,
        name=body.name.strip(),
        content=body.content,
        scope=body.scope,
        flow_id=body.flow_id if body.scope != "global" else None,
        step_id=body.step_id if body.scope == "step" else None,
        priority=body.priority,
        enabled=body.enabled,
    )
    db.add(r)
    db.commit()
    db.refresh(r)
    return r


@router.get("/{rule_id}", response_model=RuleOut)
def get_rule(rule_id: str, user: CurrentUser, db: DbSession) -> Rule:
    return _owned(db, user.id, rule_id)


@router.put("/{rule_id}", response_model=RuleOut)
def update_rule(rule_id: str, body: RuleUpdate, user: CurrentUser, db: DbSession) -> Rule:
    r = _owned(db, user.id, rule_id)
    scope = body.scope or r.scope
    flow_id = body.flow_id if body.flow_id is not None else r.flow_id
    step_id = body.step_id if body.step_id is not None else r.step_id
    if body.scope is not None or body.flow_id is not None or body.step_id is not None:
        _check_scope(db, user.id, scope, flow_id, step_id)
        r.scope = scope
        r.flow_id = flow_id if scope != "global" else None
        r.step_id = step_id if scope == "step" else None
    if body.name is not None:
        r.name = body.name.strip()
    if body.content is not None:
        r.content = body.content
    if body.priority is not None:
        r.priority = body.priority
    if body.enabled is not None:
        r.enabled = body.enabled
    db.commit()
    db.refresh(r)
    return r


@router.delete("/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_rule(rule_id: str, user: CurrentUser, db: DbSession) -> None:
    db.delete(_owned(db, user.id, rule_id))
    db.commit()
