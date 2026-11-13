"""MCP client: expose tools of user-configured servers as registry tools.

Each call opens a short-lived session (initialize → tools/call) so servers can
be stdio commands or Streamable-HTTP endpoints without keeping processes
around. ``tools/list`` results are cached on the ``McpServer`` row so flows can
be validated and listed without touching the server.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

import httpx
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.crypto import DecryptError, decrypt, encrypt
from app.models import McpServer
from app.tools import MCP_PREFIX, Tool, ToolCategory, ToolContext, ToolError, truncate

log = logging.getLogger(__name__)

#: Tests replace this with a factory yielding in-memory (read, write) streams.
transport_override: Callable[[McpServer], Any] | None = None


def tool_name(server_id: str, name: str) -> str:
    return f"{MCP_PREFIX}{server_id}:{name}"


def split_tool_name(full: str) -> tuple[str, str]:
    """``mcp:<server_id>:<tool>`` -> (server_id, tool)."""
    if not full.startswith(MCP_PREFIX):
        raise ToolError(f"not an MCP tool: {full}")
    rest = full[len(MCP_PREFIX) :]
    server_id, _, name = rest.partition(":")
    if not server_id or not name:
        raise ToolError(f"malformed MCP tool name: {full}")
    return server_id, name


def _secret_json(blob: str | None) -> dict[str, str]:
    if not blob:
        return {}
    try:
        return dict(json.loads(decrypt(blob)))
    except (DecryptError, ValueError):
        log.warning("cannot decrypt MCP server secrets")
        return {}


def encode_secret_json(data: dict[str, str]) -> str | None:
    return encrypt(json.dumps(data)) if data else None


@asynccontextmanager
async def connect(server: McpServer) -> AsyncIterator[ClientSession]:
    """Open an initialised session to ``server``."""
    if transport_override is not None:
        async with (
            transport_override(server) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            yield session
        return
    if server.url:
        headers = _secret_json(server.headers_encrypted)
        async with (
            httpx.AsyncClient(headers=headers, timeout=60.0) as http,
            streamable_http_client(server.url, http_client=http) as (read, write, *_),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            yield session
        return
    if server.command:
        params = StdioServerParameters(
            command=server.command,
            args=list(server.args or []),
            env=_secret_json(server.env_encrypted) or None,
        )
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            yield session
        return
    raise ToolError("MCP server has neither url nor command")


async def list_tools(server: McpServer) -> list[dict[str, Any]]:
    """``tools/list`` as plain dicts (name, description, input_schema)."""
    async with connect(server) as session:
        result = await session.list_tools()
    return [
        {
            "name": t.name,
            "description": t.description or "",
            "input_schema": t.input_schema or {"type": "object", "properties": {}},
        }
        for t in result.tools
    ]


def _result_text(result: Any) -> str:
    parts: list[str] = []
    for block in getattr(result, "content", None) or []:
        if getattr(block, "type", None) == "text":
            parts.append(block.text)
        else:
            parts.append(f"[{getattr(block, 'type', 'content')}]")
    sc = getattr(result, "structured_content", None)
    if sc and not parts:
        parts.append(json.dumps(sc, ensure_ascii=False))
    return "\n".join(parts)


async def call_tool(server: McpServer, name: str, args: dict[str, Any]) -> str:
    """``tools/call`` and flatten the result to text."""
    async with connect(server) as session:
        result = await session.call_tool(name, args)
    text = _result_text(result)
    if getattr(result, "is_error", False):
        raise ToolError(text or "MCP tool reported an error")
    return truncate(text)


class McpTool(Tool):
    """Registry wrapper around one tool of one server."""

    category = ToolCategory.mcp

    def __init__(self, server: McpServer, spec: dict[str, Any]) -> None:
        self.server = server
        self.remote_name = spec["name"]
        self.name = tool_name(server.id, spec["name"])
        self.description = f"[{server.name}] {spec.get('description', '')}".strip()
        self.input_schema = spec.get("input_schema") or {"type": "object", "properties": {}}

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        try:
            return await call_tool(self.server, self.remote_name, args)
        except ToolError:
            raise
        except Exception as exc:
            raise ToolError(
                f"MCP {self.server.name}: {exc.__class__.__name__}: {str(exc)[:200]}"
            ) from exc

    async def self_test(self, ctx: ToolContext) -> str:
        tools = await list_tools(self.server)
        if not any(t["name"] == self.remote_name for t in tools):
            raise ToolError("tool no longer offered by the server")
        return f"server reachable, {len(tools)} tools"


async def refresh_server(db: Session, server: McpServer) -> list[dict[str, Any]]:
    """Reconnect, update the cached tool list and ``last_error``."""
    try:
        tools = await list_tools(server)
        server.tools_cache = tools
        server.last_error = None
    except Exception as exc:  # noqa: BLE001
        server.last_error = f"{exc.__class__.__name__}: {str(exc)[:300]}"
        tools = list(server.tools_cache or [])
    db.add(server)
    db.commit()
    return tools


def user_mcp_tools(db: Session, user_id: str | None) -> dict[str, Tool]:
    """Tools of the user's enabled servers, from the cache (no network)."""
    if not user_id:
        return {}
    rows = db.scalars(
        select(McpServer).where(McpServer.user_id == user_id, McpServer.enabled.is_(True))
    ).all()
    out: dict[str, Tool] = {}
    for srv in rows:
        for spec in srv.tools_cache or []:
            tool = McpTool(srv, spec)
            out[tool.name] = tool
    return out


def mcp_tools_factory(
    session_factory: sessionmaker[Session],
) -> Callable[[str | None], dict[str, Tool]]:
    def _load(user_id: str | None) -> dict[str, Tool]:
        with session_factory() as db:
            tools = user_mcp_tools(db, user_id)
            db.expunge_all()
            return tools

    return _load
