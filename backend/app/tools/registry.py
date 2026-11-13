"""Registry of built-in tools plus per-user dynamic (MCP) tools."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from app.tools.base import Tool, ToolCategory, ToolContext, ToolError

MCP_PREFIX = "mcp:"


class ToolRegistry:
    """Name → tool. Built-ins are global; MCP tools are supplied per request."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool, *, replace: bool = False) -> Tool:
        if not tool.name:
            raise ValueError("tool needs a name")
        if tool.name in self._tools and not replace:
            raise ValueError(f"tool {tool.name!r} already registered")
        self._tools[tool.name] = tool
        return tool

    def get(self, name: str, extra: dict[str, Tool] | None = None) -> Tool:
        tool = (extra or {}).get(name) or self._tools.get(name)
        if tool is None:
            raise ToolError(f"unknown tool {name!r}")
        return tool

    def names(self) -> list[str]:
        return list(self._tools)

    def all(self) -> list[Tool]:
        return list(self._tools.values())

    def by_category(self) -> dict[ToolCategory, list[Tool]]:
        out: dict[ToolCategory, list[Tool]] = {c: [] for c in ToolCategory}
        for t in self._tools.values():
            out[t.category].append(t)
        return out

    def definitions(
        self, names: Iterable[str], extra: dict[str, Tool] | None = None
    ) -> list[dict[str, Any]]:
        """Canonical schema dicts for ``names`` (unknown names are skipped)."""
        out = []
        for n in names:
            tool = (extra or {}).get(n) or self._tools.get(n)
            if tool is not None:
                out.append(tool.definition())
        return out

    def is_known(self, name: str) -> bool:
        """Built-in, or an MCP tool reference (validated when the run starts)."""
        return name in self._tools or name.startswith(MCP_PREFIX)

    def describe(
        self, ctx: ToolContext, extra: dict[str, Tool] | None = None
    ) -> list[dict[str, Any]]:
        """UI listing with readiness status."""
        rows = []
        for t in [*self._tools.values(), *(extra or {}).values()]:
            missing = t.missing_credentials(ctx)
            rows.append(
                {
                    "name": t.name,
                    "description": t.description,
                    "category": t.category.value,
                    "input_schema": t.input_schema,
                    "required_credentials": list(t.required_credentials),
                    "missing_credentials": missing,
                    "needs_sandbox": t.needs_sandbox,
                    "status": "missing_credentials" if missing else "ready",
                }
            )
        return rows


registry = ToolRegistry()
"""Process-wide registry; populated by :mod:`app.tools` on import."""
