"""Workspaces, membership and onboarding."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import CurrentUser
from app.billing import service as billing
from app.db import get_db
from app.industry import packs
from app.models import User, Workspace, WorkspaceMember
from app.schemas import (
    MemberCreate,
    MemberOut,
    MemberUpdate,
    OnboardingAnswers,
    WorkspaceCreate,
    WorkspaceOut,
    WorkspaceUpdate,
)
from app.workspace import (
    ROLES,
    CurrentWorkspace,
    OwnerWorkspace,
    WorkspaceContext,
    memberships,
)

router = APIRouter(tags=["workspaces"])
DbSession = Annotated[Session, Depends(get_db)]


def _branches(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalise branches: keep ids, assign new ones, drop blanks."""
    out = []
    for b in raw:
        name = str(b.get("name") or "").strip()
        if not name:
            continue
        out.append(
            {
                "id": str(b.get("id") or uuid.uuid4().hex[:8]),
                "name": name[:100],
                "address": str(b.get("address") or "")[:300],
                "phone": str(b.get("phone") or "")[:30],
            }
        )
    return out


def _out(ws: Workspace, member: WorkspaceMember) -> WorkspaceOut:
    return WorkspaceOut(
        id=ws.id,
        name=ws.name,
        industry=ws.industry,
        industry_label=packs.get(ws.industry).label if ws.industry in packs else ws.industry,
        timezone=ws.timezone,
        branches=list(ws.branches or []),
        onboarding=dict(ws.onboarding or {}),
        role=member.role,
        created_at=ws.created_at,
    )


def _create(
    db: Session, user: User, body: WorkspaceCreate, onboarding: dict[str, Any]
) -> WorkspaceContext:
    if body.industry not in packs:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, f"unknown industry {body.industry!r}"
        )
    pack = packs.get(body.industry)
    ws = Workspace(
        name=body.name.strip(),
        industry=body.industry,
        timezone=body.timezone or pack.timezone,
        branches=_branches([b.model_dump() for b in body.branches])
        or [{"id": uuid.uuid4().hex[:8], "name": "Chi nhánh 1", "address": "", "phone": ""}],
        onboarding=onboarding,
    )
    db.add(ws)
    db.flush()
    member = WorkspaceMember(workspace_id=ws.id, user_id=user.id, role="owner")
    db.add(member)
    user.current_workspace_id = ws.id
    db.flush()
    pack.on_onboarded(db, ws, {**onboarding, "owner_user_id": user.id})
    db.commit()
    db.refresh(ws)
    db.refresh(member)
    return WorkspaceContext(workspace=ws, member=member)


# ---------------------------------------------------------------- onboarding


@router.get("/onboarding/questions")
def onboarding_questions(_user: CurrentUser) -> dict[str, Any]:
    """The questions asked right after sign-up (industry first, then the common ones)."""
    return {
        "questions": packs.questions(),
        "industries": [
            {"key": p.key, "label": p.label, "description": p.description} for p in packs.all()
        ],
    }


@router.post("/onboarding", response_model=WorkspaceOut, status_code=status.HTTP_201_CREATED)
def onboard(body: OnboardingAnswers, user: CurrentUser, db: DbSession) -> WorkspaceOut:
    """Create the user's workspace from the onboarding answers and make them owner."""
    count = max(1, min(body.branch_count, 50))
    branches = [
        {
            "id": uuid.uuid4().hex[:8],
            "name": f"Chi nhánh {i + 1}" if count > 1 else "Chi nhánh chính",
            "address": "",
            "phone": "",
        }
        for i in range(count)
    ]
    answers = {
        "industry": body.industry,
        "branch_count": count,
        "main_channel": body.main_channel,
        "current_software": body.current_software.strip(),
    }
    create = WorkspaceCreate(
        name=body.name, industry=body.industry, timezone=body.timezone, branches=branches
    )
    ctx = _create(db, user, create, answers)
    return _out(ctx.workspace, ctx.member)


# ---------------------------------------------------------------- workspaces


@router.get("/workspaces", response_model=list[WorkspaceOut])
def list_workspaces(user: CurrentUser, db: DbSession) -> list[WorkspaceOut]:
    """Every workspace I belong to, with my role."""
    out = []
    for m in memberships(db, user.id):
        ws = db.get(Workspace, m.workspace_id)
        if ws is not None:
            out.append(_out(ws, m))
    return out


@router.post("/workspaces", response_model=WorkspaceOut, status_code=status.HTTP_201_CREATED)
def create_workspace(body: WorkspaceCreate, user: CurrentUser, db: DbSession) -> WorkspaceOut:
    ctx = _create(db, user, body, {})
    return _out(ctx.workspace, ctx.member)


@router.get("/workspaces/current", response_model=WorkspaceOut)
def current_workspace(ctx: CurrentWorkspace) -> WorkspaceOut:
    return _out(ctx.workspace, ctx.member)


@router.put("/workspaces/current", response_model=WorkspaceOut)
def update_workspace(body: WorkspaceUpdate, ctx: OwnerWorkspace, db: DbSession) -> WorkspaceOut:
    ws = ctx.workspace
    if body.name is not None:
        ws.name = body.name.strip()
    if body.industry is not None:
        if body.industry not in packs:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, f"unknown industry {body.industry!r}"
            )
        ws.industry = body.industry
    if body.timezone is not None:
        ws.timezone = body.timezone
    if body.branches is not None:
        branches = _branches([b.model_dump() for b in body.branches])
        if not branches:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "a workspace needs at least one branch"
            )
        limit = billing.effective_plan(billing.get_subscription(db, ws)).branches
        if len(branches) > limit and len(branches) > len(ws.branches or []):
            raise HTTPException(
                status.HTTP_402_PAYMENT_REQUIRED,
                f"Gói hiện tại cho tối đa {limit} chi nhánh. Nâng gói ở mục Gói & thanh toán để thêm.",
            )
        ws.branches = branches
    db.commit()
    db.refresh(ws)
    return _out(ws, ctx.member)


@router.post("/workspaces/{workspace_id}/switch", response_model=WorkspaceOut)
def switch_workspace(workspace_id: str, user: CurrentUser, db: DbSession) -> WorkspaceOut:
    """Make one of my workspaces the active one."""
    member = db.scalar(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id, WorkspaceMember.user_id == user.id
        )
    )
    ws = db.get(Workspace, workspace_id) if member else None
    if member is None or ws is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "workspace not found")
    user.current_workspace_id = ws.id
    db.commit()
    return _out(ws, member)


@router.delete("/workspaces/current", status_code=status.HTTP_204_NO_CONTENT)
def delete_workspace(ctx: OwnerWorkspace, user: CurrentUser, db: DbSession) -> None:
    """Delete the active workspace and everything in it (owner only)."""
    db.delete(ctx.workspace)
    user.current_workspace_id = None
    db.commit()


# ---------------------------------------------------------------- members


def _member_out(db: Session, m: WorkspaceMember) -> MemberOut:
    u = db.get(User, m.user_id)
    return MemberOut(
        id=m.id,
        user_id=m.user_id,
        email=u.email if u else "?",
        role=m.role,
        created_at=m.created_at,
    )


@router.get("/workspaces/current/members", response_model=list[MemberOut])
def list_members(ctx: CurrentWorkspace, db: DbSession) -> list[MemberOut]:
    rows = db.scalars(
        select(WorkspaceMember)
        .where(WorkspaceMember.workspace_id == ctx.id)
        .order_by(WorkspaceMember.created_at, WorkspaceMember.id)
    ).all()
    return [_member_out(db, m) for m in rows]


@router.post(
    "/workspaces/current/members", response_model=MemberOut, status_code=status.HTTP_201_CREATED
)
def add_member(body: MemberCreate, ctx: OwnerWorkspace, db: DbSession) -> MemberOut:
    """Add an existing account to the workspace by email."""
    if body.role not in ROLES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "role must be owner or staff")
    u = db.scalar(select(User).where(User.email == body.email.lower().strip()))
    if u is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "no account with that email; ask them to register first"
        )
    exists = db.scalar(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == ctx.id, WorkspaceMember.user_id == u.id
        )
    )
    if exists is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "already a member")
    m = WorkspaceMember(workspace_id=ctx.id, user_id=u.id, role=body.role)
    db.add(m)
    if u.current_workspace_id is None:
        u.current_workspace_id = ctx.id
    db.commit()
    db.refresh(m)
    return _member_out(db, m)


def _owned_member(db: Session, ctx: WorkspaceContext, member_id: str) -> WorkspaceMember:
    m = db.get(WorkspaceMember, member_id)
    if m is None or m.workspace_id != ctx.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "member not found")
    return m


def _owner_count(db: Session, workspace_id: str) -> int:
    return len(
        db.scalars(
            select(WorkspaceMember.id).where(
                WorkspaceMember.workspace_id == workspace_id, WorkspaceMember.role == "owner"
            )
        ).all()
    )


@router.put("/workspaces/current/members/{member_id}", response_model=MemberOut)
def update_member(
    member_id: str, body: MemberUpdate, ctx: OwnerWorkspace, db: DbSession
) -> MemberOut:
    m = _owned_member(db, ctx, member_id)
    if body.role not in ROLES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "role must be owner or staff")
    if m.role == "owner" and body.role != "owner" and _owner_count(db, ctx.id) <= 1:
        raise HTTPException(status.HTTP_409_CONFLICT, "a workspace needs at least one owner")
    m.role = body.role
    db.commit()
    db.refresh(m)
    return _member_out(db, m)


@router.delete("/workspaces/current/members/{member_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_member(member_id: str, ctx: OwnerWorkspace, db: DbSession) -> None:
    m = _owned_member(db, ctx, member_id)
    if m.role == "owner" and _owner_count(db, ctx.id) <= 1:
        raise HTTPException(status.HTTP_409_CONFLICT, "a workspace needs at least one owner")
    user = db.get(User, m.user_id)
    db.delete(m)
    if user is not None and user.current_workspace_id == ctx.id:
        user.current_workspace_id = None
    db.commit()
