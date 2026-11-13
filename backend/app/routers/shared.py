"""Public read access to flows shared by link, and cloning them."""

from __future__ import annotations

import re
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import CurrentUser
from app.db import get_db
from app.models import Flow, FlowVisibility
from app.schemas import FlowOut, SharedFlowOut

router = APIRouter(prefix="/shared", tags=["shared"])
DbSession = Annotated[Session, Depends(get_db)]


def _shared(db: Session, token: str) -> Flow:
    flow = db.scalar(select(Flow).where(Flow.share_token == token))
    if flow is None or flow.visibility is not FlowVisibility.shared_link:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "shared flow not found")
    return flow


SECRET_KEY = re.compile(r"(?i)(auth|token|secret|key|password|passwd|cookie|credential)")


def _redact(value: Any) -> Any:
    """Mask dict values whose key looks like a credential (headers, deliver args)."""
    if isinstance(value, dict):
        return {k: ("***" if SECRET_KEY.search(str(k)) else _redact(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(v) for v in value]
    return value


@router.get("/{token}", response_model=SharedFlowOut)
def get_shared(token: str, db: DbSession) -> SharedFlowOut:
    """Read-only view; no authentication required. Credential-looking values are masked."""
    flow = _shared(db, token)
    out = SharedFlowOut.model_validate(flow, from_attributes=True)
    for n in out.nodes:
        if n.data.headers:
            n.data.headers = _redact(n.data.headers)
        if n.data.deliver_args:
            n.data.deliver_args = _redact(n.data.deliver_args)
    return out


@router.post("/{token}/clone", response_model=FlowOut, status_code=status.HTTP_201_CREATED)
def clone_shared(token: str, user: CurrentUser, db: DbSession) -> Flow:
    """Copy a shared flow into my flows (private)."""
    src = _shared(db, token)
    clone = Flow(
        user_id=user.id,
        name=f"{src.name} (clone)" if src.user_id != user.id else f"{src.name} (copy)",
        description=src.description,
        nodes=list(src.nodes or []),
        edges=list(src.edges or []),
        input_schema=list(src.input_schema or []),
        visibility=FlowVisibility.private,
    )
    db.add(clone)
    db.commit()
    db.refresh(clone)
    return clone
