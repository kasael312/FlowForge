"""CRUD endpoints for flows. Every flow belongs to the authenticated user."""

import secrets
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import CurrentUser
from app.db import get_db
from app.models import Flow, FlowVisibility, User
from app.schemas import (
    FlowCreate,
    FlowImportResult,
    FlowOut,
    FlowSummary,
    FlowUpdate,
    GraphInput,
    GraphReport,
    PromptSection,
    ResolvedPrompt,
    StepTryRequest,
    StepTryResult,
    analyze_graph,
    validate_graph,
)

router = APIRouter(prefix="/flows", tags=["flows"])
DbSession = Annotated[Session, Depends(get_db)]


def get_owned_flow(db: Session, user: User, flow_id: str) -> Flow:
    """Fetch a flow owned by ``user`` or raise 404 (never reveal other users' ids)."""
    flow = db.get(Flow, flow_id)
    if flow is None or flow.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "flow not found")
    return flow


def _dump(items: list) -> list[dict]:
    return [i.model_dump(mode="json") for i in items]


@router.get("", response_model=list[FlowSummary])
def list_flows(user: CurrentUser, db: DbSession) -> list[FlowSummary]:
    """List the user's flows, newest first."""
    rows = db.scalars(
        select(Flow).where(Flow.user_id == user.id).order_by(Flow.updated_at.desc())
    ).all()
    return [
        FlowSummary(
            id=f.id,
            name=f.name,
            description=f.description,
            node_count=len(f.nodes or []),
            industry_pack=f.industry_pack,
            flow_key=f.flow_key,
            created_at=f.created_at,
            updated_at=f.updated_at,
        )
        for f in rows
    ]


@router.post("/validate", response_model=GraphReport)
def validate_flow(body: GraphInput, _user: CurrentUser) -> GraphReport:
    """Report errors and warnings for a graph without saving it.

    Validation errors inside the body itself (bad on_fail, unknown tool, ...)
    surface as 422 like everywhere else.
    """
    return analyze_graph(body.nodes, body.edges)


@router.post("", response_model=FlowOut, status_code=status.HTTP_201_CREATED)
def create_flow(body: FlowCreate, user: CurrentUser, db: DbSession) -> Flow:
    """Create a flow from a validated graph."""
    flow = Flow(
        user_id=user.id,
        name=body.name,
        description=body.description,
        nodes=_dump(body.nodes),
        edges=_dump(body.edges),
        input_schema=_dump(body.input_schema),
        sandbox=body.sandbox.model_dump(exclude_none=True) if body.sandbox else None,
    )
    db.add(flow)
    db.commit()
    db.refresh(flow)
    return flow


@router.get("/{flow_id}", response_model=FlowOut)
def get_flow(flow_id: str, user: CurrentUser, db: DbSession) -> Flow:
    """Fetch a single flow with its full graph."""
    return get_owned_flow(db, user, flow_id)


@router.put("/{flow_id}", response_model=FlowOut)
def update_flow(flow_id: str, body: FlowUpdate, user: CurrentUser, db: DbSession) -> Flow:
    """Partially update a flow. The resulting graph is re-validated."""
    flow = get_owned_flow(db, user, flow_id)
    if body.name is not None:
        flow.name = body.name
    if body.description is not None:
        flow.description = body.description
    if body.input_schema is not None:
        flow.input_schema = [f.model_dump(mode="json") for f in body.input_schema]
    if body.sandbox is not None:
        flow.sandbox = body.sandbox.model_dump(exclude_none=True)
    if body.visibility is not None:
        flow.visibility = body.visibility
        if body.visibility is FlowVisibility.shared_link and not flow.share_token:
            flow.share_token = secrets.token_urlsafe(24)

    if body.nodes is not None or body.edges is not None:
        current = FlowOut.model_validate(flow)
        nodes = body.nodes if body.nodes is not None else current.nodes
        edges = body.edges if body.edges is not None else current.edges
        try:
            validate_graph(nodes, edges)
        except ValueError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
        flow.nodes = _dump(nodes)
        flow.edges = _dump(edges)

    db.commit()
    db.refresh(flow)
    return flow


@router.delete("/{flow_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_flow(flow_id: str, user: CurrentUser, db: DbSession) -> None:
    """Delete a flow and all of its runs."""
    flow = get_owned_flow(db, user, flow_id)
    db.delete(flow)
    db.commit()


@router.post("/{flow_id}/duplicate", response_model=FlowOut, status_code=status.HTTP_201_CREATED)
def duplicate_flow(flow_id: str, user: CurrentUser, db: DbSession) -> Flow:
    """Clone a flow (graph only, no runs) under the name ``<name> (copy)``."""
    src = get_owned_flow(db, user, flow_id)
    count = db.scalar(
        select(func.count())
        .select_from(Flow)
        .where(Flow.user_id == user.id, Flow.name.like(f"{src.name} (copy%"))
    )
    suffix = " (copy)" if not count else f" (copy {count + 1})"
    clone = Flow(
        user_id=user.id,
        name=f"{src.name}{suffix}",
        description=src.description,
        nodes=list(src.nodes or []),
        edges=list(src.edges or []),
        input_schema=list(src.input_schema or []),
        sandbox=dict(src.sandbox or {}) or None,
    )
    db.add(clone)
    db.flush()
    from app.services.flowio import create_flow_rules, flow_rules_spec

    create_flow_rules(db, user.id, clone.id, flow_rules_spec(db, user.id, src.id))
    db.commit()
    db.refresh(clone)
    return clone


@router.get("/{flow_id}/export")
def export_flow(flow_id: str, user: CurrentUser, db: DbSession) -> dict[str, Any]:
    """Portable JSON: graph, inputs, sandbox, referenced models (no keys), skills and rules."""
    from app.services.flowio import export_flow as _export

    return _export(db, user, get_owned_flow(db, user, flow_id))


@router.post("/import", response_model=FlowImportResult, status_code=status.HTTP_201_CREATED)
def import_flow(doc: dict[str, Any], user: CurrentUser, db: DbSession) -> FlowImportResult:
    """Create a new flow from an exported document (models by label, skills by name, rules)."""
    from app.services.flowio import FlowImportError, apply_import

    try:
        flow, warnings = apply_import(db, user, doc)
    except (FlowImportError, ValueError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    return FlowImportResult(flow=FlowOut.model_validate(flow), warnings=warnings)


@router.post("/{flow_id}/import", response_model=FlowImportResult)
def import_into_flow(
    flow_id: str, doc: dict[str, Any], user: CurrentUser, db: DbSession
) -> FlowImportResult:
    """Replace an existing flow's graph, inputs, sandbox and rules with a document."""
    from app.services.flowio import FlowImportError, apply_import

    target = get_owned_flow(db, user, flow_id)
    try:
        flow, warnings = apply_import(db, user, doc, into=target)
    except (FlowImportError, ValueError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    return FlowImportResult(flow=FlowOut.model_validate(flow), warnings=warnings)


@router.post("/{flow_id}/steps/{step_id}/try", response_model=StepTryResult)
async def try_step(
    flow_id: str, step_id: str, body: StepTryRequest, user: CurrentUser, db: DbSession
) -> StepTryResult:
    """Dry-run one step with simulated inputs (tools disabled, one model turn)."""
    from app.services.runs import run_manager
    from app.services.trystep import try_step as _try

    flow = get_owned_flow(db, user, flow_id)
    current = FlowOut.model_validate(flow)
    node = (
        body.node
        if body.node is not None
        else next((n for n in current.nodes if n.id == step_id), None)
    )
    if node is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "step not found")
    from app.services.models import ModelConfigError
    from app.services.rules import load_rules_block
    from app.services.skills import load_bundles, skills_prompt_block

    bundles, _ = load_bundles(db, user.id, node.data.skill_ids)
    skills = skills_prompt_block(bundles)
    rules = load_rules_block(db, user.id, flow.id, node.id)
    try:
        provider = run_manager.deps.provider_resolver(user.id, node.data.model_id)
    except ModelConfigError as exc:
        return await _try(node, body, None, str(exc), skills=skills, rules=rules)
    return await _try(node, body, provider, skills=skills, rules=rules)


@router.get("/{flow_id}/steps/{step_id}/resolved-prompt", response_model=ResolvedPrompt)
def resolved_prompt(flow_id: str, step_id: str, user: CurrentUser, db: DbSession) -> ResolvedPrompt:
    """The system prompt exactly as it will be composed: base → rules → persona → skills → step."""
    from app.executor.prompts import BASE_RULES, DEFAULT_PROMPTS, system_prompt_for
    from app.services.rules import resolve_rules, rules_prompt_block
    from app.services.skills import load_bundles, skills_prompt_block

    flow = get_owned_flow(db, user, flow_id)
    current = FlowOut.model_validate(flow)
    node = next((n for n in current.nodes if n.id == step_id), None)
    if node is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "step not found")
    rules = resolve_rules(db, user.id, flow.id, node.id)
    bundles, _ = load_bundles(db, user.id, node.data.skill_ids)
    bundles.sort(key=lambda b: 0 if b.kind == "persona" else 1)  # same order as the prompt
    sections = [PromptSection(kind="base", name="Base rules", text=BASE_RULES)]
    sections += [
        PromptSection(kind="rule", name=f"{r.name} ({r.scope})", text=r.content) for r in rules
    ]
    sections += [
        PromptSection(
            kind="persona" if b.kind == "persona" else "skill", name=b.name, text=b.content
        )
        for b in bundles
    ]
    body = node.data.system_prompt.strip() or DEFAULT_PROMPTS.get(node.data.type, "")
    sections.append(
        PromptSection(kind="step", name=f"Step prompt ({node.data.type.value})", text=body)
    )
    return ResolvedPrompt(
        step_id=node.id,
        system_prompt=system_prompt_for(
            node.data.type,
            node.data.system_prompt,
            skills=skills_prompt_block(bundles),
            rules=rules_prompt_block(rules),
        ),
        sections=sections,
    )
