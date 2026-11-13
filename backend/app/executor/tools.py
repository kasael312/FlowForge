"""Dispatch of tool calls to the plugin registry (compatibility façade).

The executor and the try-step service talk to :class:`ToolExecutor`; the
actual tools live in :mod:`app.tools`.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from app.executor.sandbox import Sandbox
from app.tools import (
    MCP_PREFIX,
    Tool,
    ToolContext,
    ToolError,
    parse_whitelisted_command,
    registry,
    safe_repo_path,
)

__all__ = [
    "MAX_OUTPUT",
    "ToolError",
    "ToolExecutor",
    "parse_whitelisted_command",
    "safe_repo_path",
    "tool_definitions",
]

MAX_OUTPUT = 20_000


def tool_definitions(
    allowed: Iterable[str], extra: dict[str, Tool] | None = None
) -> list[dict[str, Any]]:
    """Canonical tool definitions for the given names (order preserved)."""
    return registry.definitions(allowed, extra)


class ToolExecutor:
    """Runs tool calls for one step with a shared :class:`ToolContext`."""

    def __init__(
        self,
        sandbox: Sandbox | None,
        shell_whitelist: Iterable[str],
        *,
        ctx: ToolContext | None = None,
        extra_tools: dict[str, Tool] | None = None,
    ) -> None:
        self.ctx = ctx or ToolContext()
        self.ctx.sandbox = sandbox
        self.ctx.shell_whitelist = frozenset(shell_whitelist)
        self.extra_tools = extra_tools or {}

    @property
    def sandbox(self) -> Sandbox | None:
        return self.ctx.sandbox

    def definitions(self, allowed: Iterable[str]) -> list[dict[str, Any]]:
        return registry.definitions(allowed, self.extra_tools)

    async def call(self, name: str, args: dict[str, Any]) -> str:
        """Execute ``name`` with ``args``. Raises :class:`ToolError` on failure."""
        tool = registry.get(name, self.extra_tools)
        if name.startswith(MCP_PREFIX) and name not in self.extra_tools:
            raise ToolError(f"MCP tool {name!r} is not available (server disabled or offline)")
        if not isinstance(args, dict):
            raise ToolError("tool arguments must be an object")
        try:
            return await tool.run(args, self.ctx)
        except ToolError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise ToolError(f"invalid arguments: {exc}") from exc
        except FileNotFoundError as exc:
            raise ToolError(f"file not found: {exc}") from exc
