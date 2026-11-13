"""Dashboard numbers for the authenticated user."""

from __future__ import annotations

from datetime import UTC, datetime, time
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import CurrentUser
from app.db import get_db
from app.models import Flow, Run, RunStatus, StepRun
from app.schemas import ModelCost, RunOut, Stats

router = APIRouter(tags=["stats"])
DbSession = Annotated[Session, Depends(get_db)]
FINISHED = (RunStatus.success, RunStatus.failed, RunStatus.cancelled)


@router.get("/stats", response_model=Stats)
def get_stats(user: CurrentUser, db: DbSession) -> Stats:
    """Flow count, runs started today (UTC), success rate and the 5 latest runs."""
    today_start = datetime.combine(datetime.now(UTC).date(), time.min, tzinfo=UTC)
    flows = db.scalar(select(func.count()).select_from(Flow).where(Flow.user_id == user.id)) or 0
    runs_total = db.scalar(select(func.count()).select_from(Run).where(Run.user_id == user.id)) or 0
    runs_today = (
        db.scalar(
            select(func.count())
            .select_from(Run)
            .where(Run.user_id == user.id, Run.created_at >= today_start)
        )
        or 0
    )
    finished = (
        db.scalar(
            select(func.count())
            .select_from(Run)
            .where(Run.user_id == user.id, Run.status.in_(FINISHED))
        )
        or 0
    )
    succeeded = (
        db.scalar(
            select(func.count())
            .select_from(Run)
            .where(Run.user_id == user.id, Run.status == RunStatus.success)
        )
        or 0
    )
    active = (
        db.scalar(
            select(func.count())
            .select_from(Run)
            .where(Run.user_id == user.id, Run.status.in_((RunStatus.queued, RunStatus.running)))
        )
        or 0
    )
    cost_rows = db.execute(
        select(
            StepRun.model,
            func.coalesce(func.sum(StepRun.cost_usd), 0.0),
            func.coalesce(func.sum(StepRun.input_tokens), 0),
            func.coalesce(func.sum(StepRun.output_tokens), 0),
            func.count(),
        )
        .join(Run, Run.id == StepRun.run_id)
        .where(Run.user_id == user.id, StepRun.model.is_not(None))
        .group_by(StepRun.model)
        .order_by(func.coalesce(func.sum(StepRun.cost_usd), 0.0).desc())
    ).all()
    cost_by_model = [
        ModelCost(
            model=str(m), cost_usd=float(c), input_tokens=int(i), output_tokens=int(o), steps=int(n)
        )
        for m, c, i, o, n in cost_rows
    ]
    recent = db.scalars(
        select(Run).where(Run.user_id == user.id).order_by(Run.created_at.desc()).limit(5)
    ).all()
    return Stats(
        flows=flows,
        runs_total=runs_total,
        runs_today=runs_today,
        runs_active=active,
        success_rate=(succeeded / finished) if finished else None,
        recent_runs=[RunOut.model_validate(r) for r in recent],
        cost_by_model=cost_by_model,
        total_cost_usd=round(sum(c.cost_usd for c in cost_by_model), 6),
    )
