"""Tool plugin interface.

A tool is a class with a ``name``, ``description``, JSON ``input_schema``, a
``category`` and the credential providers it needs; ``run()`` receives the
validated arguments and a :class:`ToolContext`. The registry turns tools into
canonical schema dicts that every LLM provider understands (see ``app.llm``).

Writing a new tool::

    class Shout(Tool):
        name = "shout"
        description = "Upper-case some text"
        category = ToolCategory.code
        input_schema = {"type": "object", "properties": {"text": {"type": "string"}},
                        "required": ["text"], "additionalProperties": False}

        async def run(self, args, ctx):
            return args["text"].upper()

    registry.register(Shout())
"""

from __future__ import annotations

import enum
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, ClassVar

import httpx

from app.executor.sandbox import Sandbox
from app.net import guarded_client

MAX_OUTPUT = 20_000


class ToolError(Exception):
    """Raised when a tool call is rejected or fails; returned to the model as ``is_error``."""


class ToolCategory(str, enum.Enum):
    """Grouping shown in the UI."""

    code = "code"
    web = "web"
    files = "files"
    data = "data"
    communication = "communication"
    integrations = "integrations"
    media = "media"
    industry = "industry"
    mcp = "mcp"


@dataclass(slots=True)
class ToolContext:
    """What a tool may touch while running."""

    #: Sandbox of the current run (``None`` when trying a step or testing a tool).
    sandbox: Sandbox | None = None
    #: ``provider -> decrypted secret`` for the current user (stored keys + env).
    credential: Callable[[str], str | None] = lambda _p: None
    user_id: str | None = None
    shell_whitelist: frozenset[str] = frozenset()
    #: Shared HTTP client (tests inject a mock transport).
    http: httpx.AsyncClient | None = None
    #: Free-form per-run settings (e.g. network allowlist later).
    options: dict[str, Any] = field(default_factory=dict)
    #: Industry runs: the workspace the flow belongs to, its card config, and dry-run mode.
    workspace_id: str | None = None
    flow_config: dict[str, Any] = field(default_factory=dict)
    dry_run: bool = False

    def need(self, provider: str, hint: str = "") -> str:
        """Credential for ``provider`` or a ToolError telling the user what to add."""
        value = self.credential(provider)
        if not value:
            raise ToolError(
                f"missing credential {provider!r}: add it under Settings → API keys"
                + (f" ({hint})" if hint else "")
            )
        return value

    def sandbox_required(self) -> Sandbox:
        if self.sandbox is None:
            raise ToolError("this tool needs a running sandbox (start a real run)")
        return self.sandbox

    def client(self) -> httpx.AsyncClient:
        if self.http is None:
            self.http = guarded_client(timeout=60.0, follow_redirects=True)
        return self.http


class Tool(ABC):
    """Base class for all tools."""

    name: str = ""
    description: str = ""
    category: ToolCategory = ToolCategory.code
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }
    #: Credential providers (``KeyProvider`` values) the tool needs.
    required_credentials: tuple[str, ...] = ()
    #: Whether the tool only works inside a run (needs the sandbox).
    needs_sandbox: bool = False

    @abstractmethod
    async def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        """Execute. Return text for the model; raise :class:`ToolError` on failure."""

    async def self_test(self, ctx: ToolContext) -> str:
        """Cheap check used by the Tools page. Default: credentials present."""
        for prov in self.required_credentials:
            ctx.need(prov)
        return "credentials present" if self.required_credentials else "ready"

    def definition(self) -> dict[str, Any]:
        """Canonical schema dict (Anthropic-shaped; providers convert)."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }

    def missing_credentials(self, ctx: ToolContext) -> list[str]:
        return [p for p in self.required_credentials if not ctx.credential(p)]


def truncate(text: str, limit: int = MAX_OUTPUT) -> str:
    """Cap tool output so it fits in the model context."""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [truncated {len(text) - limit} chars]"


def schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    """Shorthand for a strict object schema."""
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }
