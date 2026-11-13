"""Pydantic schemas for the API and for validating flow graphs."""

from __future__ import annotations

import enum
import re
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    field_validator,
    model_validator,
)

from app.models import (
    FlowVisibility,
    KeyProvider,
    ModelProvider,
    RunStatus,
    StepRunStatus,
    TriggerType,
)

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
    custom = "custom"
    shell = "shell"
    condition = "condition"
    data_transform = "data_transform"
    http_request = "http_request"
    loop = "loop"
    merge = "merge"
    template = "template"
    output = "output"
    approval = "approval"


LLM_TYPES = frozenset({"plan", "code", "test", "review", "custom"})
#: Node types whose outgoing edges carry a handle (``true/false`` or ``body/done``).
BRANCHING_HANDLES: dict[str, tuple[str, str]] = {
    "condition": ("true", "false"),
    "loop": ("body", "done"),
}


ToolName = str
#: Default tool set for new steps (the classic code tools).
ALL_TOOLS: tuple[str, ...] = ("read_file", "write_file", "run_shell", "list_files", "git_diff")


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
    # LLM to use for this step; ``None`` = the user's default model.
    model_id: str | None = None
    # Model to switch to when the primary provider fails or is rate limited.
    fallback_model_id: str | None = None
    # Skills whose SKILL.md is added to the system prompt and whose files are mounted.
    skill_ids: list[str] = Field(default_factory=list)
    # ---- data_transform
    language: str | None = None  # python | javascript
    code: str | None = None
    # ---- http_request
    url: str | None = None
    method: str | None = None
    headers: dict[str, str] | None = None
    body: str | None = None
    # ---- loop: variable path / JSON / comma list that yields the items
    items_expression: str | None = None
    # ---- merge
    merge_steps: list[str] | None = None
    merge_mode: str | None = None  # concat | json
    # ---- template (Jinja2); output_path also used by output(kind=file)
    template: str | None = None
    output_path: str | None = None
    # ---- output
    output_kind: str | None = None  # text | json | file
    output_source: str | None = None
    deliver_tool: str | None = None
    deliver_args: dict[str, Any] | None = None
    # ---- approval: how long to wait for a decision (bounded by the run timeout)
    approval_timeout_seconds: int | None = None

    @property
    def is_llm(self) -> bool:
        return self.type.value in LLM_TYPES

    @field_validator("tools_allowed")
    @classmethod
    def _validate_tools(cls, v: list[str]) -> list[str]:
        from app.tools import registry

        unknown = [t for t in v if not registry.is_known(t)]
        if unknown:
            raise ValueError(f"unknown tools: {', '.join(unknown)}")
        return list(dict.fromkeys(v))

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


class GraphReport(BaseModel):
    """Outcome of :func:`analyze_graph`: errors block saving, warnings do not."""

    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _find_unguarded_cycle(nodes: list[Node], edges: list[Edge]) -> list[str] | None:
    """Return a cycle made only of edges that don't leave a condition node, if any.

    Loops that pass through a ``condition`` node are guarded (the condition can
    exit the loop) and are allowed; ``on_fail: goto`` jumps are bounded by
    ``retry_limit`` and are not edges at all.
    """
    cond = {n.id for n in nodes if n.data.type.value in BRANCHING_HANDLES}
    adj: dict[str, list[str]] = {n.id: [] for n in nodes}
    for e in edges:
        if e.source in adj and e.source not in cond:
            adj[e.source].append(e.target)
    WHITE, GREY, BLACK = 0, 1, 2
    colour = dict.fromkeys(adj, WHITE)
    stack: list[str] = []

    def dfs(u: str) -> list[str] | None:
        colour[u] = GREY
        stack.append(u)
        for v in adj.get(u, ()):
            if colour.get(v) == GREY:
                return stack[stack.index(v) :] + [v]
            if colour.get(v) == WHITE:
                found = dfs(v)
                if found:
                    return found
        stack.pop()
        colour[u] = BLACK
        return None

    for n in adj:
        if colour[n] == WHITE:
            found = dfs(n)
            if found:
                return found
    return None


def loop_return_edges(nodes: list[Node], edges: list[Edge]) -> set[str]:
    """Ids of edges that close a loop body (last body step -> its loop node).

    They are not "incoming" edges for start-node detection.
    """
    by_type = {n.id: n.data.type for n in nodes}
    out: dict[str, list[Edge]] = {}
    for e in edges:
        out.setdefault(e.source, []).append(e)
    result: set[str] = set()
    for loop_id, kind in by_type.items():
        if kind is not StepType.loop:
            continue
        body = [e for e in out.get(loop_id, []) if e.sourceHandle == "body"]
        if not body:
            continue
        seen: set[str] = set()
        frontier = [body[0].target]
        while frontier:
            cur = frontier.pop()
            if cur in seen or cur == loop_id:
                continue
            seen.add(cur)
            for e in out.get(cur, []):
                if e.target == loop_id:
                    result.add(e.id)
                else:
                    frontier.append(e.target)
    return result


def start_nodes(nodes: list[Node], edges: list[Edge]) -> list[str]:
    """Ids of nodes without a real incoming edge (loop return edges ignored)."""
    returns = loop_return_edges(nodes, edges)
    targets = {e.target for e in edges if e.id not in returns}
    return [n.id for n in nodes if n.id not in targets]


def analyze_graph(nodes: list[Node], edges: list[Edge]) -> GraphReport:
    """Structural checks. Errors: duplicate ids, dangling edges, bad goto, bad
    condition handles/expressions, no single start node, unguarded cycles.
    Warnings: unconnected nodes, unreachable nodes, condition without both branches."""
    from app.executor.conditions import ConditionError, validate_expression

    rep = GraphReport()
    ids = [n.id for n in nodes]
    if len(ids) != len(set(ids)):
        rep.errors.append("duplicate node ids")
        return rep
    id_set = set(ids)
    for e in edges:
        if e.source not in id_set or e.target not in id_set:
            rep.errors.append(f"edge {e.id!r} references unknown node")
    if rep.errors:
        return rep

    for n in nodes:
        target = n.data.goto_target
        if target is not None and target not in id_set:
            rep.errors.append(f"node {n.id!r}: on_fail goto target {target!r} not found")
        if target == n.id:
            rep.errors.append(f"node {n.id!r}: on_fail goto cannot target itself")
        if n.data.type.value in BRANCHING_HANDLES:
            a, b = BRANCHING_HANDLES[n.data.type.value]
            handles = [e.sourceHandle for e in edges if e.source == n.id]
            if not set(handles) <= {a, b}:
                rep.errors.append(
                    f"{n.data.type.value} {n.id!r}: outgoing edges must use handle {a}/{b}"
                )
            if n.data.type is StepType.condition:
                try:
                    validate_expression(n.data.expression or "")
                except ConditionError as exc:
                    rep.errors.append(f"condition {n.id!r}: {exc}")
                if len(set(handles)) < 2:
                    rep.warnings.append(f"condition {n.id!r}: only one branch is connected")
            else:
                if not (n.data.items_expression or "").strip():
                    rep.errors.append(f"loop {n.id!r}: items expression is empty")
                if "body" not in handles:
                    rep.warnings.append(f"loop {n.id!r}: no body branch connected")
        if n.data.type is StepType.shell and not (n.data.command or "").strip():
            rep.errors.append(f"shell step {n.id!r}: command is empty")
        if n.data.type is StepType.data_transform and not (n.data.code or "").strip():
            rep.errors.append(f"data_transform {n.id!r}: code is empty")
        if n.data.type is StepType.http_request and not (n.data.url or "").strip():
            rep.errors.append(f"http_request {n.id!r}: url is empty")
        if n.data.type is StepType.template and not (n.data.template or "").strip():
            rep.errors.append(f"template {n.id!r}: template is empty")
        if n.data.type is StepType.output and (n.data.output_kind or "text") not in (
            "text",
            "json",
            "file",
        ):
            rep.errors.append(f"output {n.id!r}: kind must be text, json or file")
        if n.data.type is StepType.custom and not n.data.system_prompt.strip():
            rep.warnings.append(f"custom step {n.id!r}: system prompt is empty")

    if nodes:
        targets = {e.target for e in edges}
        starts = start_nodes(nodes, edges)
        if len(starts) != 1:
            rep.errors.append(
                f"flow must have exactly one start node (no incoming edge), found {len(starts)}: {', '.join(starts) or '-'}"
            )
        cycle = _find_unguarded_cycle(nodes, edges)
        if cycle:
            rep.errors.append(
                "cycle without a condition step: "
                + " -> ".join(cycle)
                + " (route loops through a condition, or use on_fail goto)"
            )
        if len(nodes) > 1:
            connected = {e.source for e in edges} | targets
            for n in nodes:
                if n.id not in connected:
                    rep.warnings.append(f"node {n.id!r} is not connected to anything")
        if len(starts) == 1:
            reach = {starts[0]}
            frontier = [starts[0]]
            adj: dict[str, list[str]] = {}
            for e in edges:
                adj.setdefault(e.source, []).append(e.target)
            while frontier:
                cur = frontier.pop()
                for nxt in adj.get(cur, ()):
                    if nxt not in reach:
                        reach.add(nxt)
                        frontier.append(nxt)
            for n in nodes:
                if n.id not in reach and n.data.type is not StepType.condition:
                    gotos = {m.data.goto_target for m in nodes}
                    if n.id not in gotos:
                        rep.warnings.append(f"node {n.id!r} is unreachable from the start node")
    return rep


def validate_graph(nodes: list[Node], edges: list[Edge]) -> None:
    """Raise ``ValueError`` with all blocking problems of the graph."""
    rep = analyze_graph(nodes, edges)
    if rep.errors:
        raise ValueError("; ".join(rep.errors))


# ---------- Flow ----------


InputType = Literal["text", "textarea", "number", "url", "select", "file", "boolean"]
_FIELD_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


class InputField(BaseModel):
    """One field of a flow's run form; its value is ``{{input.<name>}}`` at run time."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=50)
    label: str = ""
    type: InputType = "text"
    required: bool = False
    default: str | float | bool | None = None
    options: list[str] = Field(default_factory=list)
    placeholder: str = ""
    description: str = ""

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        if not _FIELD_NAME_RE.match(v):
            raise ValueError("field name must be an identifier (letters, digits, underscore)")
        if v in ("task", "repo", "branch", "prev_output", "step_id", "payload"):
            raise ValueError(f"{v!r} is reserved")
        return v

    @model_validator(mode="after")
    def _options(self) -> InputField:
        if self.type == "select" and not self.options:
            raise ValueError(f"select field {self.name!r} needs options")
        return self


def validate_input_schema(fields: list[InputField]) -> None:
    names = [f.name for f in fields]
    if len(names) != len(set(names)):
        raise ValueError("duplicate input field names")


def coerce_inputs(fields: list[InputField], values: dict[str, Any] | None) -> dict[str, Any]:
    """Validate submitted values against the schema; returns the cleaned dict."""
    values = dict(values or {})
    out: dict[str, Any] = {}
    for f in fields:
        v = values.pop(f.name, None)
        if v in (None, "") and f.default not in (None, ""):
            v = f.default
        if v in (None, "", []):
            if f.required:
                raise ValueError(f"input {f.name!r} is required")
            out[f.name] = None
            continue
        if f.type == "number":
            try:
                out[f.name] = float(v) if "." in str(v) else int(v)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"input {f.name!r} must be a number") from exc
        elif f.type == "boolean":
            out[f.name] = v if isinstance(v, bool) else str(v).lower() in ("1", "true", "yes", "on")
        elif f.type == "select":
            if str(v) not in f.options:
                raise ValueError(f"input {f.name!r} must be one of {', '.join(f.options)}")
            out[f.name] = str(v)
        elif f.type == "url":
            if not str(v).startswith(("http://", "https://")):
                raise ValueError(f"input {f.name!r} must be a URL")
            out[f.name] = str(v)
        elif f.type == "file":
            if not isinstance(v, dict) or not v.get("upload_id"):
                raise ValueError(f"input {f.name!r} must reference an upload")
            out[f.name] = {"upload_id": str(v["upload_id"]), "name": str(v.get("name") or "file")}
        else:
            out[f.name] = str(v)
    # extra keys (e.g. webhook payload fields) are kept as-is
    out.update(values)
    return out


class SandboxSettings(BaseModel):
    """Per-flow (or per-run) sandbox limits. Values above the server caps are clamped."""

    model_config = ConfigDict(extra="forbid")

    cpus: float | None = Field(default=None, ge=0.25, le=64)
    memory_mb: int | None = Field(default=None, ge=128, le=262_144)
    timeout_seconds: int | None = Field(default=None, ge=30, le=86_400)
    network: Literal["none", "allowlist", "bridge"] | None = None
    allowed_hosts: list[str] = Field(default_factory=list)
    #: False = run without a workspace/container (only LLM, HTTP, template, output, approval steps).
    enabled: bool | None = None

    @field_validator("allowed_hosts")
    @classmethod
    def _hosts(cls, v: list[str]) -> list[str]:
        out = []
        for h in v:
            h = h.strip().lower()
            if not h:
                continue
            if not re.match(r"^(\*\.)?[a-z0-9.-]+$", h) or re.fullmatch(r"[0-9.]+", h.lstrip("*.")):
                raise ValueError(
                    f"invalid host pattern {h!r} (use example.com or *.example.com; no IPs, no *)"
                )
            if h == "localhost" or h.endswith((".localhost", ".internal")):
                raise ValueError(f"host pattern {h!r} is not allowed")
            out.append(h)
        return out


class FlowBase(BaseModel):
    """Fields shared by create/update."""

    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    nodes: list[Node] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)
    input_schema: list[InputField] = Field(default_factory=list)
    sandbox: SandboxSettings | None = None

    @model_validator(mode="after")
    def _check_inputs(self) -> FlowBase:
        validate_input_schema(self.input_schema)
        return self

    @model_validator(mode="after")
    def _check_graph(self) -> FlowBase:
        validate_graph(self.nodes, self.edges)
        return self


class FlowCreate(FlowBase):
    """Body for ``POST /flows``."""


class GraphInput(BaseModel):
    """Body for ``POST /flows/validate``: a graph that may be invalid."""

    name: str = ""
    description: str = ""
    nodes: list[Node] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)


class FlowUpdate(BaseModel):
    """Body for ``PUT /flows/{id}``; all fields optional."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    nodes: list[Node] | None = None
    edges: list[Edge] | None = None
    input_schema: list[InputField] | None = None
    sandbox: SandboxSettings | None = None
    visibility: FlowVisibility | None = None

    @model_validator(mode="after")
    def _check_inputs(self) -> FlowUpdate:
        if self.input_schema is not None:
            validate_input_schema(self.input_schema)
        return self


class FlowOut(FlowBase):
    """Flow as returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    #: Stored as a plain dict (only the keys the user set).
    sandbox: dict[str, Any] | None = None  # type: ignore[assignment]
    visibility: FlowVisibility = FlowVisibility.private
    share_token: str | None = None
    workspace_id: str | None = None
    industry_pack: str | None = None
    flow_key: str | None = None
    config: dict[str, Any] | None = None
    enabled: bool | None = None
    created_at: UtcDateTime
    updated_at: UtcDateTime

    @field_validator("visibility", mode="before")
    @classmethod
    def _default_visibility(cls, v: object) -> object:
        return v if v is not None else FlowVisibility.private

    @field_validator("input_schema", mode="before")
    @classmethod
    def _default_schema(cls, v: object) -> object:
        return v if v is not None else []


class SharedFlowOut(BaseModel):
    """Read-only public view of a shared flow (no owner information)."""

    name: str
    description: str
    nodes: list[Node]
    edges: list[Edge]
    input_schema: list[InputField] = Field(default_factory=list)
    updated_at: UtcDateTime

    @field_validator("input_schema", mode="before")
    @classmethod
    def _default_schema(cls, v: object) -> object:
        return v if v is not None else []


class FlowSummary(BaseModel):
    """Lightweight flow row for list views."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: str
    node_count: int
    industry_pack: str | None = None
    flow_key: str | None = None
    created_at: UtcDateTime
    updated_at: UtcDateTime


# ---------- Run ----------


class RunCreate(BaseModel):
    """Body for ``POST /flows/{id}/run``. ``repo_url`` may be empty (no repository)."""

    repo_url: str = Field(default="", max_length=500)
    branch: str = Field(default="main", min_length=1, max_length=200)
    task: str = ""
    inputs: dict[str, Any] = Field(default_factory=dict)
    #: Optional override of the flow's sandbox settings for this run.
    sandbox: SandboxSettings | None = None


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
    cost_usd: float | None = 0.0
    work_branch: str | None
    pr_url: str | None
    inputs: dict[str, Any] | None = None
    trigger_id: str | None = None
    outputs: list[dict[str, Any]] | None = None
    sandbox: dict[str, Any] | None = None


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
    model: str | None = None
    input_tokens: int | None = 0
    output_tokens: int | None = 0
    cost_usd: float | None = None
    skills: list[dict[str, Any]] | None = None
    started_at: UtcDateTime
    finished_at: UtcDateTime | None


# ---------- Auth ----------


class Credentials(BaseModel):
    """Body for register/login."""

    email: EmailStr
    password: str = Field(min_length=8, max_length=72)


class UserOut(BaseModel):
    """Public view of a user."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    is_admin: bool = False
    current_workspace_id: str | None = None
    created_at: UtcDateTime

    @field_validator("is_admin", mode="before")
    @classmethod
    def _none_is_false(cls, v: object) -> bool:
        return bool(v)


# ---------- API keys ----------


class ApiKeyCreate(BaseModel):
    """Body for ``POST /keys``. The value is encrypted at rest and never returned."""

    provider: KeyProvider
    label: str = Field(default="", max_length=100)
    value: str = Field(min_length=8, max_length=4096)

    @field_validator("value")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if any(ch.isspace() for ch in v):
            raise ValueError("key must not contain whitespace")
        return v


class ApiKeyOut(BaseModel):
    """Public view of a stored key: only the last 4 characters are exposed."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    provider: KeyProvider
    label: str
    last4: str
    created_at: UtcDateTime


class ApiKeyTest(BaseModel):
    """Body for ``POST /keys/test`` (test a value before saving)."""

    provider: KeyProvider
    value: str = Field(min_length=8, max_length=4096)


class KeyTestResult(BaseModel):
    """Outcome of a key test."""

    ok: bool
    message: str


# ---------- Dashboard / profile ----------


class ModelCost(BaseModel):
    model: str
    cost_usd: float
    input_tokens: int
    output_tokens: int
    steps: int


class Stats(BaseModel):
    """Numbers shown on the dashboard."""

    flows: int
    runs_total: int
    runs_today: int
    runs_active: int
    #: ``None`` until at least one run has finished.
    success_rate: float | None
    recent_runs: list[RunOut]
    #: Spend per model over all LLM steps, most expensive first.
    cost_by_model: list[ModelCost] = Field(default_factory=list)
    total_cost_usd: float = 0.0


class PasswordChange(BaseModel):
    """Body for ``POST /auth/password``."""

    current_password: str = Field(min_length=1, max_length=72)
    new_password: str = Field(min_length=8, max_length=72)


# ---------- Templates ----------


class TemplateOut(BaseModel):
    """A template as listed in the API."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    key: str | None
    name: str
    description: str
    builtin: bool
    node_count: int
    nodes: list[Node]
    edges: list[Edge]
    input_schema: list[InputField] = Field(default_factory=list)
    sandbox: dict[str, Any] | None = None
    #: ``{node_id: [skill names]}`` attached to steps.
    skills: dict[str, list[str]] = Field(default_factory=dict)
    rules: list[dict[str, Any]] = Field(default_factory=list)
    created_at: UtcDateTime


class TemplateCreate(BaseModel):
    """Body for ``POST /templates``: save one of my flows as a template."""

    flow_id: str
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None


# ---------- Try a single step ----------


class StepTryRequest(BaseModel):
    """Simulated inputs for trying a step. ``node`` lets the editor try unsaved edits."""

    node: Node | None = None
    task: str = ""
    prev_output: str = ""
    prev_success: bool = True
    prev_exit_code: int | None = 0
    repo: str = "https://github.com/example/repo"
    branch: str = "main"
    inputs: dict[str, Any] = Field(default_factory=dict)


class StepTryResult(BaseModel):
    """What a dry run of one step produced."""

    step_id: str
    type: StepType
    success: bool
    output: str
    error: str | None = None
    tokens: int = 0
    system_prompt: str = ""
    user_message: str = ""


# ---------- Models (LLM configurations) ----------


class ModelParamsIn(BaseModel):
    """Sampling parameters stored on a model."""

    model_config = ConfigDict(extra="forbid")

    temperature: float | None = Field(default=None, ge=0, le=2)
    max_tokens: int | None = Field(default=None, ge=1, le=200_000)
    effort: Literal["low", "medium", "high", "xhigh", "max"] | None = None


class ModelCreate(BaseModel):
    """Body for ``POST /models``."""

    provider: ModelProvider
    model_name: str = Field(min_length=1, max_length=200)
    label: str = Field(min_length=1, max_length=100)
    base_url: str | None = Field(default=None, max_length=500)
    api_key_id: str | None = None
    params: ModelParamsIn = Field(default_factory=ModelParamsIn)
    is_default: bool = False

    @field_validator("base_url")
    @classmethod
    def _url(cls, v: str | None) -> str | None:
        if v is None or not v.strip():
            return None
        v = v.strip().rstrip("/")
        if not v.startswith(("http://", "https://")):
            raise ValueError("base_url must start with http:// or https://")
        return v


class ModelUpdate(BaseModel):
    """Body for ``PUT /models/{id}``; all optional."""

    model_name: str | None = Field(default=None, min_length=1, max_length=200)
    label: str | None = Field(default=None, min_length=1, max_length=100)
    base_url: str | None = Field(default=None, max_length=500)
    api_key_id: str | None = None
    params: ModelParamsIn | None = None
    is_default: bool | None = None


class ModelOut(BaseModel):
    """A configured model (never includes the key value)."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    provider: ModelProvider
    model_name: str
    label: str
    base_url: str | None
    api_key_id: str | None
    api_key_last4: str | None = None
    params: dict[str, Any]
    is_default: bool
    created_at: UtcDateTime
    #: ``provider/model_name`` key used for pricing.
    pricing_key: str = ""
    price: dict[str, float] | None = None


class PricingTable(BaseModel):
    """``{"provider/model": {"input": usd_per_1M, "output": usd_per_1M}}``."""

    prices: dict[str, dict[str, float]]


# ---------- Tools / MCP ----------


class ToolInfo(BaseModel):
    """A registry entry as shown on the Tools page."""

    name: str
    description: str
    category: str
    input_schema: dict[str, Any]
    required_credentials: list[str]
    missing_credentials: list[str]
    needs_sandbox: bool
    status: Literal["ready", "missing_credentials", "offline"]
    #: Set for MCP tools: the server they come from.
    server_id: str | None = None


class McpServerCreate(BaseModel):
    """Body for ``POST /mcp-servers``: one of ``url`` or ``command``."""

    name: str = Field(min_length=1, max_length=100)
    url: str | None = Field(default=None, max_length=500)
    command: str | None = Field(default=None, max_length=500)
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)
    enabled: bool = True

    @model_validator(mode="after")
    def _one_transport(self) -> McpServerCreate:
        if bool(self.url) == bool(self.command):
            raise ValueError("provide exactly one of url (HTTP) or command (stdio)")
        if self.url and not self.url.startswith(("http://", "https://")):
            raise ValueError("url must start with http:// or https://")
        return self


class McpServerUpdate(BaseModel):
    """Body for ``PUT /mcp-servers/{id}``."""

    name: str | None = Field(default=None, min_length=1, max_length=100)
    url: str | None = None
    command: str | None = None
    args: list[str] | None = None
    env: dict[str, str] | None = None
    headers: dict[str, str] | None = None
    enabled: bool | None = None


class McpServerOut(BaseModel):
    """An MCP server configuration (env/headers values are masked)."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    url: str | None
    command: str | None
    args: list[str]
    env_keys: list[str] = Field(default_factory=list)
    header_keys: list[str] = Field(default_factory=list)
    enabled: bool
    last_error: str | None = None
    tool_count: int = 0
    created_at: UtcDateTime


# ---------- Triggers / uploads ----------


class TriggerCreate(BaseModel):
    """Body for ``POST /flows/{id}/triggers``."""

    type: TriggerType
    name: str = Field(default="", max_length=100)
    config: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class TriggerUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=100)
    config: dict[str, Any] | None = None
    enabled: bool | None = None
    #: ``true`` rotates the webhook secret.
    rotate_secret: bool = False


class TriggerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    flow_id: str
    type: TriggerType
    name: str
    config: dict[str, Any]
    enabled: bool
    last_fired: UtcDateTime | None
    last_error: str | None
    created_at: UtcDateTime
    #: Only for webhook/github: URL, header name, secret and a curl example.
    webhook: dict[str, Any] | None = None
    #: Only for file_watch: the folder to drop files into.
    watch_folder: str | None = None


class UploadOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    size: int
    content_type: str
    created_at: UtcDateTime


class ArtifactOut(BaseModel):
    """A kept output file of a run."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    run_id: str
    name: str
    size: int
    content_type: str
    created_at: UtcDateTime


class ApprovalDecision(BaseModel):
    """Body for ``POST /runs/{id}/approve``."""

    decision: Literal["approve", "reject"]
    comment: str = Field(default="", max_length=4000)


# ---------------------------------------------------------------- skills


class SkillFileOut(BaseModel):
    path: str
    size: int
    content_type: str = ""


SkillKind = Literal["skill", "persona"]


class SkillCreate(BaseModel):
    name: str = Field(min_length=2, max_length=64)
    kind: SkillKind = "skill"
    description: str = Field(default="", max_length=2000)
    content: str = Field(default="", max_length=200_000)
    tools_required: list[str] = Field(default_factory=list, max_length=50)


class SkillUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=64)
    kind: SkillKind | None = None
    description: str | None = Field(default=None, max_length=2000)
    content: str | None = Field(default=None, max_length=200_000)
    tools_required: list[str] | None = Field(default=None, max_length=50)


class SkillOut(BaseModel):
    id: str
    name: str
    kind: SkillKind
    description: str
    content: str
    tools_required: list[str]
    files: list[SkillFileOut]
    builtin: bool
    #: Rendered SKILL.md (front matter + content).
    skill_md: str
    created_at: UtcDateTime
    updated_at: UtcDateTime


# ---------------------------------------------------------------- rules

RuleScope = Literal["global", "flow", "step"]


class RuleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    content: str = Field(default="", max_length=20_000)
    scope: RuleScope = "global"
    flow_id: str | None = None
    step_id: str | None = None
    priority: int = Field(default=100, ge=0, le=10_000)
    enabled: bool = True


class RuleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    content: str | None = Field(default=None, max_length=20_000)
    scope: RuleScope | None = None
    flow_id: str | None = None
    step_id: str | None = None
    priority: int | None = Field(default=None, ge=0, le=10_000)
    enabled: bool | None = None


class RuleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    key: str | None
    name: str
    content: str
    scope: RuleScope
    flow_id: str | None
    step_id: str | None
    priority: int
    enabled: bool
    created_at: UtcDateTime
    updated_at: UtcDateTime


class PromptSection(BaseModel):
    """One block of the composed system prompt, in order."""

    kind: Literal["base", "rule", "persona", "skill", "step"]
    name: str
    text: str


class ResolvedPrompt(BaseModel):
    """``GET /flows/{id}/steps/{step_id}/resolved-prompt``: the full prompt and its parts."""

    step_id: str
    system_prompt: str
    sections: list[PromptSection]


class FlowImportResult(BaseModel):
    """``POST /flows/import``: the created/updated flow plus mapping warnings."""

    flow: FlowOut
    warnings: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------- workspaces


class BranchIn(BaseModel):
    id: str | None = None
    name: str = Field(min_length=1, max_length=100)
    address: str = Field(default="", max_length=300)
    phone: str = Field(default="", max_length=30)


class WorkspaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    industry: str = Field(min_length=1, max_length=32)
    timezone: str | None = Field(default=None, max_length=64)
    branches: list[BranchIn] = Field(default_factory=list, max_length=50)


class WorkspaceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    industry: str | None = Field(default=None, min_length=1, max_length=32)
    timezone: str | None = Field(default=None, max_length=64)
    branches: list[BranchIn] | None = Field(default=None, max_length=50)


class WorkspaceOut(BaseModel):
    id: str
    name: str
    industry: str
    industry_label: str
    timezone: str
    branches: list[dict[str, Any]]
    onboarding: dict[str, Any]
    #: My role in this workspace.
    role: str
    created_at: UtcDateTime


class OnboardingAnswers(BaseModel):
    """The 4 questions asked after sign-up (+ a workspace name)."""

    name: str = Field(min_length=1, max_length=200)
    industry: str = Field(min_length=1, max_length=32)
    branch_count: int = Field(default=1, ge=1, le=50)
    main_channel: Literal["zalo", "facebook", "both"] = "zalo"
    current_software: str = Field(default="", max_length=200)
    timezone: str | None = Field(default=None, max_length=64)


class MemberCreate(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    role: str = "staff"


class MemberUpdate(BaseModel):
    role: str


class MemberOut(BaseModel):
    id: str
    user_id: str
    email: str
    role: str
    created_at: UtcDateTime


# ---------------------------------------------------------------- connectors


class ConnectorUpsert(BaseModel):
    enabled: bool | None = None
    config: dict[str, Any] | None = None
    #: Empty string keeps the stored secret; null removes it.
    secrets: dict[str, str | None] | None = None
    rotate_webhook_token: bool = False


class ConnectorOut(BaseModel):
    id: str
    kind: str
    enabled: bool
    config: dict[str, Any]
    #: Names of secrets that are stored (values never leave the server).
    secret_keys: list[str]
    webhook_url: str | None
    verify_token: str | None
    status: str
    last_checked_at: UtcDateTime | None
    last_error: str | None
    last_event_at: UtcDateTime | None
    updated_at: UtcDateTime
    test_message: str | None = None
    test_details: dict[str, Any] | None = None
