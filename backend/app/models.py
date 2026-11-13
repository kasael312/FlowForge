"""ORM models: Flow, Run, StepRun."""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
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
    #: Paused at an ``approval`` step until a person decides.
    waiting = "waiting"
    success = "success"
    failed = "failed"
    cancelled = "cancelled"


class FlowVisibility(str, enum.Enum):
    """Who can read a flow."""

    private = "private"
    shared_link = "shared_link"


class KeyProvider(str, enum.Enum):
    """Third-party services a stored API key can belong to."""

    anthropic = "anthropic"
    openai = "openai"
    github = "github"
    #: Any OpenAI-compatible host (Groq, DeepSeek, OpenRouter, ...).
    other = "other"
    # ---- tool credentials
    tavily = "tavily"
    smtp = "smtp"
    slack = "slack"
    telegram = "telegram"
    notion = "notion"
    google = "google"
    database = "database"
    imap = "imap"


class TriggerType(str, enum.Enum):
    """How a run gets started."""

    manual = "manual"
    schedule = "schedule"
    webhook = "webhook"
    email = "email"
    file_watch = "file_watch"
    github = "github"


class ModelProvider(str, enum.Enum):
    """LLM provider families known to ``app.llm``."""

    anthropic = "anthropic"
    openai = "openai"
    ollama = "ollama"
    openai_compatible = "openai_compatible"


class StepRunStatus(str, enum.Enum):
    """Lifecycle of a single step within a Run."""

    pending = "pending"
    running = "running"
    waiting = "waiting"
    success = "success"
    failed = "failed"
    skipped = "skipped"


class User(Base):
    """An account. Every Flow, Run and ApiKey belongs to exactly one user."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(100), nullable=False)
    #: The first account becomes admin: may edit the global pricing table and use stdio MCP servers.
    is_admin: Mapped[bool | None] = mapped_column(Boolean, nullable=True, default=False)
    #: Bumped on logout / password change; tokens carrying an older value are rejected.
    token_version: Mapped[int | None] = mapped_column(Integer, nullable=True, default=0)
    #: Active workspace (one of the user's memberships); NULL until onboarding.
    current_workspace_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    flows: Mapped[list[Flow]] = relationship(back_populates="owner", cascade="all, delete-orphan")
    api_keys: Mapped[list[ApiKey]] = relationship(
        back_populates="owner", cascade="all, delete-orphan"
    )


class Model(Base):
    """A user's configured LLM: provider + model name + credentials + params."""

    __tablename__ = "models"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    provider: Mapped[ModelProvider] = mapped_column(Enum(ModelProvider, native_enum=False))
    model_name: Mapped[str] = mapped_column(String(200), nullable=False)
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    base_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    api_key_id: Mapped[str | None] = mapped_column(
        ForeignKey("api_keys.id", ondelete="SET NULL"), nullable=True
    )
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    api_key: Mapped[ApiKey | None] = relationship()


class Rule(Base):
    """A constraint prepended to LLM step prompts. Scope: global / flow / step."""

    __tablename__ = "rules"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: Built-in origin (``no-secrets`` …) so seeding is idempotent per user; NULL for own rules.
    key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    content: Mapped[str] = mapped_column(Text, default="")
    scope: Mapped[str] = mapped_column(String(16), default="global", index=True)
    flow_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("flows.id", ondelete="CASCADE"), nullable=True, index=True
    )
    step_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    priority: Mapped[int] = mapped_column(Integer, default=100)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class Trigger(Base):
    """Something that starts runs of a flow automatically."""

    __tablename__ = "triggers"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    flow_id: Mapped[str] = mapped_column(ForeignKey("flows.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    type: Mapped[TriggerType] = mapped_column(Enum(TriggerType, native_enum=False))
    name: Mapped[str] = mapped_column(String(100), default="")
    #: Type-specific settings (cron, folder, poll interval, run defaults...).
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    #: Webhook / GitHub shared secret (HMAC). Stored encrypted.
    secret_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_fired: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Poller bookkeeping (seen email uids / files), kept out of ``config``.
    state: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Upload(Base):
    """A file uploaded for a run input (or dropped into a watched folder)."""

    __tablename__ = "uploads"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    size: Mapped[int] = mapped_column(Integer, default=0)
    content_type: Mapped[str] = mapped_column(String(100), default="application/octet-stream")
    #: Path on disk relative to ``UPLOADS_DIR``.
    path: Mapped[str] = mapped_column(String(500), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Skill(Base):
    """Reusable instruction package (SKILL.md + reference files) attached to LLM steps.

    ``user_id`` is NULL for built-in skills, which are read-only.
    """

    __tablename__ = "skills"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    key: Mapped[str | None] = mapped_column(String(100), nullable=True, unique=True)
    user_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    #: Body of SKILL.md (without the front matter).
    content: Mapped[str] = mapped_column(Text, default="")
    tools_required: Mapped[list] = mapped_column(JSON, default=list)
    #: ``skill`` (how to do a task) or ``persona`` (who the agent is); NULL = skill.
    kind: Mapped[str | None] = mapped_column(String(16), nullable=True, default="skill")
    #: ``[{path, size, content_type, data(base64)}]``
    files: Mapped[list] = mapped_column(JSON, default=list)
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class McpServer(Base):
    """A user-configured MCP server whose tools join the registry."""

    __tablename__ = "mcp_servers"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    command: Mapped[str | None] = mapped_column(String(500), nullable=True)
    args: Mapped[list] = mapped_column(JSON, default=list)
    #: Encrypted JSON of env vars / headers (may hold secrets).
    env_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    headers_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    #: Cached ``tools/list`` result (name, description, inputSchema).
    tools_cache: Mapped[list] = mapped_column(JSON, default=list)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Workspace(Base):
    """One business (gym / spa / ...). Every industry record carries a ``workspace_id``."""

    __tablename__ = "workspaces"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    #: Industry pack key: gym | spa | both (see ``app.industry``).
    industry: Mapped[str] = mapped_column(String(32), nullable=False, default="gym")
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Ho_Chi_Minh")
    #: ``[{id, name, address, phone}]``
    branches: Mapped[list] = mapped_column(JSON, default=list)
    #: Onboarding answers (industry, branch_count, main_channel, current_software).
    onboarding: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    members: Mapped[list[WorkspaceMember]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan"
    )


class WorkspaceMember(Base):
    """A user's role inside a workspace: owner | staff."""

    __tablename__ = "workspace_members"
    __table_args__ = (UniqueConstraint("workspace_id", "user_id", name="uq_workspace_user"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(16), default="staff")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    workspace: Mapped[Workspace] = relationship(back_populates="members")


class ApiKey(Base):
    """A user's credential for a provider. The value is stored encrypted."""

    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    provider: Mapped[KeyProvider] = mapped_column(Enum(KeyProvider, native_enum=False), index=True)
    label: Mapped[str] = mapped_column(String(100), default="")
    encrypted_value: Mapped[str] = mapped_column(Text, nullable=False)
    last4: Mapped[str] = mapped_column(String(4), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    owner: Mapped[User] = relationship(back_populates="api_keys")


class Flow(Base):
    """A pipeline definition: a graph of steps stored as React Flow nodes/edges."""

    __tablename__ = "flows"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    nodes: Mapped[list[dict]] = mapped_column(JSON, default=list)
    edges: Mapped[list[dict]] = mapped_column(JSON, default=list)
    #: Form definition shown on the Run page (list of InputField dicts).
    input_schema: Mapped[list | None] = mapped_column(JSON, nullable=True, default=list)
    #: Resource limits / network policy (SandboxOptions dict); NULL = server defaults.
    sandbox: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    visibility: Mapped[FlowVisibility | None] = mapped_column(
        Enum(FlowVisibility, native_enum=False), default=FlowVisibility.private, nullable=True
    )
    share_token: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    # ---- industry flows (cards in "Việc tự động"); NULL for ordinary flows
    workspace_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=True, index=True
    )
    industry_pack: Mapped[str | None] = mapped_column(String(32), nullable=True)
    flow_key: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    #: Simple owner-facing fields rendered as a form: ``[{key, label, type, options, default, help}]``.
    config_schema: Mapped[list | None] = mapped_column(JSON, nullable=True)
    #: Current values for ``config_schema``.
    config: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    enabled: Mapped[bool | None] = mapped_column(Boolean, nullable=True, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    runs: Mapped[list[Run]] = relationship(back_populates="flow", cascade="all, delete-orphan")
    rules: Mapped[list[Rule]] = relationship(cascade="all, delete-orphan")
    owner: Mapped[User | None] = relationship(back_populates="flows")


class FlowTemplate(Base):
    """A reusable graph. ``user_id`` is NULL for built-in templates."""

    __tablename__ = "flow_templates"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    #: Stable identifier for built-ins (used by the seeder); NULL for user templates.
    key: Mapped[str | None] = mapped_column(String(50), unique=True, nullable=True)
    user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    nodes: Mapped[list[dict]] = mapped_column(JSON, default=list)
    edges: Mapped[list[dict]] = mapped_column(JSON, default=list)
    input_schema: Mapped[list | None] = mapped_column(JSON, nullable=True, default=list)
    sandbox: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    #: ``{node_id: [skill names]}`` resolved against the user's skills on use.
    skills: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    #: Flow/step rule specs created for the new flow on use.
    rules: Mapped[list | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Run(Base):
    """One execution of a Flow against a repository."""

    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    flow_id: Mapped[str] = mapped_column(ForeignKey("flows.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=True
    )
    #: Empty string = no repository (an empty git workspace is created).
    repo_url: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    branch: Mapped[str] = mapped_column(String(200), default="main")
    task_input: Mapped[str] = mapped_column(Text, default="")
    #: Values of the flow's input form, available as ``{{input.<name>}}``.
    inputs: Mapped[dict | None] = mapped_column(JSON, nullable=True, default=dict)
    trigger_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    #: Results declared by ``output`` nodes.
    outputs: Mapped[list | None] = mapped_column(JSON, nullable=True, default=list)
    #: Effective sandbox options used for this run.
    sandbox: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    artifacts: Mapped[list[Artifact]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    status: Mapped[RunStatus] = mapped_column(
        Enum(RunStatus, native_enum=False), default=RunStatus.queued, index=True
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    cost_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True, default=0.0)
    work_branch: Mapped[str | None] = mapped_column(String(200), nullable=True)
    final_diff: Mapped[str | None] = mapped_column(Text, nullable=True)
    pr_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    flow: Mapped[Flow] = relationship(back_populates="runs")
    step_runs: Mapped[list[StepRun]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="StepRun.started_at"
    )


class Artifact(Base):
    """A file produced under ``/repo/output`` and kept after the run."""

    __tablename__ = "artifacts"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    #: Path relative to /repo/output, e.g. ``charts/sales.png``.
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    size: Mapped[int] = mapped_column(Integer, default=0)
    content_type: Mapped[str] = mapped_column(String(100), default="application/octet-stream")
    #: Path on disk relative to ``ARTIFACTS_DIR``.
    path: Mapped[str] = mapped_column(String(600), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    run: Mapped[Run] = relationship(back_populates="artifacts")


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
    #: ``provider/model`` actually used (after fallback), NULL for shell/condition.
    model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True, default=0)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True, default=0)
    #: Estimated USD cost, NULL when the model is not in pricing.json.
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    #: ``[{name, kind}]`` of the personas/skills applied to this step.
    skills: Mapped[list | None] = mapped_column(JSON, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    run: Mapped[Run] = relationship(back_populates="step_runs")


from app.billing import models as _billing_models  # noqa: F401
from app.biz import automation_models as _automation_models  # noqa: F401
from app.biz import models as _biz_models  # noqa: F401  (register industry tables)
from app.connectors import models as _connector_models  # noqa: F401
from app.kb import models as _kb_models  # noqa: F401
