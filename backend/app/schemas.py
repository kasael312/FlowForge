"""Pydantic schemas for the API and for validating flow graphs."""

from __future__ import annotations

import enum
import re
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models import RunStatus, StepRunStatus

_GOTO_RE = re.compile(r"^goto:(?P<target>[\w\-]+)$")


def _ensure_utc(v: datetime) -> datetime:
    """SQLite drops tzinfo; treat naive datetimes as UTC."""
    return v if v.tzinfo is not None else v.replace(tzinfo=UTC)


UtcDateTime = Annotated[datetime, AfterValidator(_ensure_utc)]


class StepType(str, enum.Enum):
    """Kinds of steps the executor knows how to run."""

    plan = "plan"
    code = "code"
    test = "test"
    review = "review"
    shell = "shell"
    condition = "condition"


ToolName = Literal["read_file", "write_file", "run_shell", "list_files", "git_diff"]
ALL_TOOLS: tuple[ToolName, ...] = ("read_file", "write_file", "run_shell", "list_files", "git_diff")


class StepData(BaseModel):
    """Properties of a step, stored in ``node.data``."""

    model_config = ConfigDict(extra="allow")

    label: str = ""
    type: StepType = StepType.code
    system_prompt: str = ""
    tools_allowed: list[ToolName] = Field(default_factory=lambda: list(ALL_TOOLS))
    max_turns: int = Field(default=20, ge=1, le=200)
    on_fail: str = "stop"
    retry_limit: int = Field(default=1, ge=0, le=10)
    # For ``shell`` steps: the command to run (no LLM involved).
    command: str | None = None
    # For ``condition`` steps: python-like expression evaluated against the
    # previous step result, e.g. ``prev.success`` or ``'FAIL' not in prev.output``.
    expression: str | None = None

    @field_validator("on_fail")
    @classmethod
    def _validate_on_fail(cls, v: str) -> str:
        if v in ("retry", "stop") or _GOTO_RE.match(v):
            return v
        raise ValueError("on_fail must be 'retry', 'stop' or 'goto:<step_id>'")

    @property
    def goto_target(self) -> str | None:
        """Step id referenced by an ``on_fail = goto:<id>``, if any."""
        m = _GOTO_RE.match(self.on_fail)
        return m.group("target") if m else None


class Position(BaseModel):
    """Canvas coordinates of a node."""

    x: float = 0
    y: float = 0


class Node(BaseModel):
    """A React Flow node carrying a :class:`StepData` payload."""

    model_config = ConfigDict(extra="allow")

    id: str = Field(min_length=1, max_length=100)
    type: str = "step"
    position: Position = Field(default_factory=Position)
    data: StepData = Field(default_factory=StepData)


class Edge(BaseModel):
    """A React Flow edge. ``sourceHandle`` on condition nodes is 'true' or 'false'."""

    model_config = ConfigDict(extra="allow")

    id: str = Field(min_length=1, max_length=200)
    source: str
    target: str
    sourceHandle: str | None = None
    label: str | None = None


def validate_graph(nodes: list[Node], edges: list[Edge]) -> None:
    """Check structural integrity of a flow graph.

    Raises:
        ValueError: duplicate node ids, dangling edges, bad goto targets, or
            condition nodes whose outgoing edges aren't labelled true/false.
    """
    ids = [n.id for n in nodes]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate node ids")
    id_set = set(ids)
    for e in edges:
        if e.source not in id_set or e.target not in id_set:
            raise ValueError(f"edge {e.id!r} references unknown node")
    for n in nodes:
        target = n.data.goto_target
        if target is not None and target not in id_set:
            raise ValueError(f"node {n.id!r}: on_fail goto target {target!r} not found")
        if n.data.type is StepType.condition:
            handles = {e.sourceHandle for e in edges if e.source == n.id}
            if not handles <= {"true", "false"}:
                raise ValueError(
                    f"condition node {n.id!r}: outgoing edges must use sourceHandle true/false"
                )


# ---------- Flow ----------


class FlowBase(BaseModel):
    """Fields shared by create/update."""

    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    nodes: list[Node] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_graph(self) -> FlowBase:
        validate_graph(self.nodes, self.edges)
        return self


class FlowCreate(FlowBase):
    """Body for ``POST /flows``."""


class FlowUpdate(BaseModel):
    """Body for ``PUT /flows/{id}``; all fields optional."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    nodes: list[Node] | None = None
    edges: list[Edge] | None = None


class FlowOut(FlowBase):
    """Flow as returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    created_at: UtcDateTime
    updated_at: UtcDateTime


class FlowSummary(BaseModel):
    """Lightweight flow row for list views."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: str
    node_count: int
    created_at: UtcDateTime
    updated_at: UtcDateTime


# ---------- Run ----------


class RunCreate(BaseModel):
    """Body for ``POST /flows/{id}/run``."""

    repo_url: str = Field(min_length=1, max_length=500)
    branch: str = Field(default="main", min_length=1, max_length=200)
    task: str = ""


class RunOut(BaseModel):
    """Run as returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    flow_id: str
    repo_url: str
    branch: str
    task_input: str
    status: RunStatus
    error: str | None
    started_at: UtcDateTime | None
    finished_at: UtcDateTime | None
    created_at: UtcDateTime
    cost_tokens: int
    work_branch: str | None
    pr_url: str | None


class RunDetail(RunOut):
    """Run plus its final diff."""

    final_diff: str | None


class StepRunOut(BaseModel):
    """StepRun as returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    run_id: str
    step_id: str
    step_type: str
    attempt: int
    status: StepRunStatus
    messages: list[dict[str, Any]]
    tool_calls: list[dict[str, Any]]
    output: str
    duration: float
    tokens: int
    started_at: UtcDateTime
    finished_at: UtcDateTime | None
