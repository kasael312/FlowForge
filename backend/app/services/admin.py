"""Admin bookkeeping: the oldest account is the operator when none is flagged yet."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import User


def ensure_admin(db: Session) -> None:
    """Make the earliest user admin when the DB predates the ``is_admin`` column."""
    if db.scalar(select(User.id).where(User.is_admin.is_(True))) is not None:
        return
    first = db.scalar(select(User).order_by(User.created_at, User.id))
    if first is not None:
        first.is_admin = True
        db.commit()
