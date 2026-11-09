"""ORM models: Flow, Run, StepRun."""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, Enum, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def _uuid() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    """Timezone-aware UTC now."""
    return datetime.now(UTC)


class RunStatus(str, enum.Enum):
    """Lifecycle of a Run."""

    queued = "queued"
    running = "running"
    success = "success"
    failed = "failed"
    cancelled = "cancelled"


class StepRunStatus(str, enum.Enum):
    """Lifecycle of a single step within a Run."""

    pending = "pending"
    running = "running"
    success = "success"
    failed = "failed"
    skipped = "skipped"


class Flow(Base):
    """A pipeline definition: a graph of steps stored as React Flow nodes/edges."""

    __tablename__ = "flows"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    nodes: Mapped[list[dict]] = mapped_column(JSON, default=list)
    edges: Mapped[list[dict]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    runs: Mapped[list[Run]] = relationship(back_populates="flow", cascade="all, delete-orphan")


class Run(Base):
    """One execution of a Flow against a repository."""

    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    flow_id: Mapped[str] = mapped_column(ForeignKey("flows.id", ondelete="CASCADE"), index=True)
    repo_url: Mapped[str] = mapped_column(String(500), nullable=False)
    branch: Mapped[str] = mapped_column(String(200), default="main")
    task_input: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[RunStatus] = mapped_column(
        Enum(RunStatus, native_enum=False), default=RunStatus.queued, index=True
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    cost_tokens: Mapped[int] = mapped_column(Integer, default=0)
    work_branch: Mapped[str | None] = mapped_column(String(200), nullable=True)
    final_diff: Mapped[str | None] = mapped_column(Text, nullable=True)
    pr_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    flow: Mapped[Flow] = relationship(back_populates="runs")
    step_runs: Mapped[list[StepRun]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="StepRun.started_at"
    )


class StepRun(Base):
    """Execution record of a single step (node) inside a Run."""

    __tablename__ = "step_runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    step_id: Mapped[str] = mapped_column(String(100), nullable=False)
    step_type: Mapped[str] = mapped_column(String(32), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[StepRunStatus] = mapped_column(
        Enum(StepRunStatus, native_enum=False), default=StepRunStatus.pending
    )
    messages: Mapped[list[dict]] = mapped_column(JSON, default=list)
    tool_calls: Mapped[list[dict]] = mapped_column(JSON, default=list)
    output: Mapped[str] = mapped_column(Text, default="")
    duration: Mapped[float] = mapped_column(Float, default=0.0)
    tokens: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    run: Mapped[Run] = relationship(back_populates="step_runs")
