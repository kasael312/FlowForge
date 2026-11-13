"""Workspace resolution for request handlers.

Every industry resource belongs to a workspace. A user may be a member of several
workspaces; ``users.current_workspace_id`` selects the active one (defaults to the
first membership). ``CurrentWorkspace`` resolves it or fails with 404, and
``OwnerWorkspace`` additionally requires the ``owner`` role.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import CurrentUser
from app.db import get_db
from app.models import User, Workspace, WorkspaceMember

ROLES = ("owner", "staff")


@dataclass
class WorkspaceContext:
    workspace: Workspace
    member: WorkspaceMember

    @property
    def id(self) -> str:
        return self.workspace.id

    @property
    def role(self) -> str:
        return self.member.role

    @property
    def is_owner(self) -> bool:
        return self.member.role == "owner"


def memberships(db: Session, user_id: str) -> list[WorkspaceMember]:
    return list(
        db.scalars(
            select(WorkspaceMember)
            .where(WorkspaceMember.user_id == user_id)
            .order_by(WorkspaceMember.created_at, WorkspaceMember.id)
        ).all()
    )


def resolve_workspace(db: Session, user: User) -> WorkspaceContext | None:
    """The user's active workspace (current_workspace_id, else the first membership)."""
    rows = memberships(db, user.id)
    if not rows:
        return None
    member = next((m for m in rows if m.workspace_id == user.current_workspace_id), rows[0])
    if user.current_workspace_id != member.workspace_id:
        user.current_workspace_id = member.workspace_id
        db.commit()
    ws = db.get(Workspace, member.workspace_id)
    if ws is None:  # pragma: no cover - FK cascade keeps this consistent
        return None
    return WorkspaceContext(workspace=ws, member=member)


def get_current_workspace(
    user: CurrentUser, db: Annotated[Session, Depends(get_db)]
) -> WorkspaceContext:
    ctx = resolve_workspace(db, user)
    if ctx is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "no workspace yet; complete onboarding first"
        )
    return ctx


def get_owner_workspace(
    ctx: Annotated[WorkspaceContext, Depends(get_current_workspace)],
) -> WorkspaceContext:
    if not ctx.is_owner:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "owner role required")
    return ctx


CurrentWorkspace = Annotated[WorkspaceContext, Depends(get_current_workspace)]
OwnerWorkspace = Annotated[WorkspaceContext, Depends(get_owner_workspace)]
