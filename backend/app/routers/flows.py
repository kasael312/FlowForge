"""CRUD endpoints for flows."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Flow
from app.schemas import FlowCreate, FlowOut, FlowSummary, FlowUpdate, validate_graph

router = APIRouter(prefix="/flows", tags=["flows"])
DbSession = Annotated[Session, Depends(get_db)]


def _get_flow_or_404(db: Session, flow_id: str) -> Flow:
    flow = db.get(Flow, flow_id)
    if flow is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "flow not found")
    return flow


def _dump(items: list) -> list[dict]:
    return [i.model_dump(mode="json") for i in items]


@router.get("", response_model=list[FlowSummary])
def list_flows(db: DbSession) -> list[FlowSummary]:
    """List all flows, newest first."""
    rows = db.scalars(select(Flow).order_by(Flow.updated_at.desc())).all()
    return [
        FlowSummary(
            id=f.id,
            name=f.name,
            description=f.description,
            node_count=len(f.nodes or []),
            created_at=f.created_at,
            updated_at=f.updated_at,
        )
        for f in rows
    ]


@router.post("", response_model=FlowOut, status_code=status.HTTP_201_CREATED)
def create_flow(body: FlowCreate, db: DbSession) -> Flow:
    """Create a flow from a validated graph."""
    flow = Flow(
        name=body.name,
        description=body.description,
        nodes=_dump(body.nodes),
        edges=_dump(body.edges),
    )
    db.add(flow)
    db.commit()
    db.refresh(flow)
    return flow


@router.get("/{flow_id}", response_model=FlowOut)
def get_flow(flow_id: str, db: DbSession) -> Flow:
    """Fetch a single flow with its full graph."""
    return _get_flow_or_404(db, flow_id)


@router.put("/{flow_id}", response_model=FlowOut)
def update_flow(flow_id: str, body: FlowUpdate, db: DbSession) -> Flow:
    """Partially update a flow. The resulting graph is re-validated."""
    flow = _get_flow_or_404(db, flow_id)
    if body.name is not None:
        flow.name = body.name
    if body.description is not None:
        flow.description = body.description

    if body.nodes is not None or body.edges is not None:
        current = FlowOut.model_validate(flow)
        nodes = body.nodes if body.nodes is not None else current.nodes
        edges = body.edges if body.edges is not None else current.edges
        try:
            validate_graph(nodes, edges)
        except ValueError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
        flow.nodes = _dump(nodes)
        flow.edges = _dump(edges)

    db.commit()
    db.refresh(flow)
    return flow


@router.delete("/{flow_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_flow(flow_id: str, db: DbSession) -> None:
    """Delete a flow and all of its runs."""
    flow = _get_flow_or_404(db, flow_id)
    db.delete(flow)
    db.commit()


@router.post("/{flow_id}/duplicate", response_model=FlowOut, status_code=status.HTTP_201_CREATED)
def duplicate_flow(flow_id: str, db: DbSession) -> Flow:
    """Clone a flow (graph only, no runs) under the name ``<name> (copy)``."""
    src = _get_flow_or_404(db, flow_id)
    count = db.scalar(
        select(func.count()).select_from(Flow).where(Flow.name.like(f"{src.name} (copy%"))
    )
    suffix = " (copy)" if not count else f" (copy {count + 1})"
    clone = Flow(
        name=f"{src.name}{suffix}",
        description=src.description,
        nodes=list(src.nodes or []),
        edges=list(src.edges or []),
    )
    db.add(clone)
    db.commit()
    db.refresh(clone)
    return clone
