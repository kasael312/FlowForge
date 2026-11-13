"""Flow templates: built-ins plus the user's own."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.auth import CurrentUser
from app.db import get_db
from app.models import Flow, FlowTemplate
from app.routers.flows import get_owned_flow
from app.schemas import FlowOut, TemplateCreate, TemplateOut
from app.services.flowio import (
    attach_template_skills,
    create_flow_rules,
    flow_rules_spec,
    skills_by_name,
)

router = APIRouter(prefix="/templates", tags=["templates"])
DbSession = Annotated[Session, Depends(get_db)]


def _out(t: FlowTemplate) -> TemplateOut:
    return TemplateOut(
        id=t.id,
        key=t.key,
        name=t.name,
        description=t.description,
        builtin=t.user_id is None,
        node_count=len(t.nodes or []),
        nodes=t.nodes or [],
        edges=t.edges or [],
        input_schema=t.input_schema or [],
        sandbox=t.sandbox,
        skills=t.skills or {},
        rules=t.rules or [],
        created_at=t.created_at,
    )


def _visible(db: Session, user_id: str, template_id: str) -> FlowTemplate:
    t = db.get(FlowTemplate, template_id)
    if t is None or (t.user_id is not None and t.user_id != user_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "template not found")
    return t


@router.get("", response_model=list[TemplateOut])
def list_templates(user: CurrentUser, db: DbSession) -> list[TemplateOut]:
    """Built-in templates first, then the user's own (newest first)."""
    rows = db.scalars(
        select(FlowTemplate)
        .where(or_(FlowTemplate.user_id.is_(None), FlowTemplate.user_id == user.id))
        .order_by(FlowTemplate.user_id.is_not(None), FlowTemplate.created_at, FlowTemplate.id)
    ).all()
    return [_out(t) for t in rows]


@router.post("", response_model=TemplateOut, status_code=status.HTTP_201_CREATED)
def save_as_template(body: TemplateCreate, user: CurrentUser, db: DbSession) -> TemplateOut:
    """Snapshot one of my flows as a personal template."""
    flow = get_owned_flow(db, user, body.flow_id)
    t = FlowTemplate(
        user_id=user.id,
        name=(body.name or flow.name).strip(),
        description=body.description if body.description is not None else flow.description,
        nodes=list(flow.nodes or []),
        edges=list(flow.edges or []),
        input_schema=list(flow.input_schema or []),
        sandbox=dict(flow.sandbox or {}) or None,
        skills=skills_by_name(db, user.id, list(flow.nodes or [])),
        rules=flow_rules_spec(db, user.id, flow.id),
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    return _out(t)


@router.post("/{template_id}/use", response_model=FlowOut, status_code=status.HTTP_201_CREATED)
def use_template(template_id: str, user: CurrentUser, db: DbSession) -> Flow:
    """Create a new flow of mine from a template."""
    t = _visible(db, user.id, template_id)
    nodes, _warnings = attach_template_skills(db, user.id, list(t.nodes or []), t.skills or {})
    flow = Flow(
        user_id=user.id,
        name=t.name,
        description=t.description,
        nodes=nodes,
        edges=list(t.edges or []),
        input_schema=list(t.input_schema or []),
        sandbox=dict(t.sandbox or {}) or None,
    )
    db.add(flow)
    db.flush()
    create_flow_rules(db, user.id, flow.id, list(t.rules or []))
    db.commit()
    db.refresh(flow)
    return flow


@router.delete("/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_template(template_id: str, user: CurrentUser, db: DbSession) -> None:
    """Delete one of my templates (built-ins cannot be deleted)."""
    t = _visible(db, user.id, template_id)
    if t.user_id is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "built-in templates cannot be deleted")
    db.delete(t)
    db.commit()
