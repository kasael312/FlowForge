"""Tools page API: registry listing with readiness, per-tool test, MCP servers."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import CurrentUser
from app.config import get_settings
from app.db import SessionLocal, get_db
from app.models import McpServer, User
from app.net import UnsafeUrl, check_public_url_sync
from app.schemas import KeyTestResult, McpServerCreate, McpServerOut, McpServerUpdate, ToolInfo
from app.services import mcp as mcp_service
from app.services.toolctx import build_tool_context
from app.tools import ToolError, registry

router = APIRouter(tags=["tools"])
DbSession = Annotated[Session, Depends(get_db)]


def _ctx(user_id: str):
    return build_tool_context(SessionLocal, get_settings(), user_id)


def _server_out(s: McpServer) -> McpServerOut:
    return McpServerOut(
        id=s.id,
        name=s.name,
        url=s.url,
        command=s.command,
        args=list(s.args or []),
        env_keys=sorted(mcp_service._secret_json(s.env_encrypted)),
        header_keys=sorted(mcp_service._secret_json(s.headers_encrypted)),
        enabled=bool(s.enabled),
        last_error=s.last_error,
        tool_count=len(s.tools_cache or []),
        created_at=s.created_at,
    )


def _owned(db: Session, user_id: str, server_id: str) -> McpServer:
    s = db.get(McpServer, server_id)
    if s is None or s.user_id != user_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "mcp server not found")
    return s


@router.get("/tools", response_model=list[ToolInfo])
def list_tools(user: CurrentUser, db: DbSession) -> list[ToolInfo]:
    """Built-in tools plus the user's MCP tools, each with a readiness status."""
    ctx = _ctx(user.id)
    rows = [ToolInfo(**r) for r in registry.describe(ctx)]
    servers = db.scalars(select(McpServer).where(McpServer.user_id == user.id)).all()
    for srv in servers:
        for spec in srv.tools_cache or []:
            tool = mcp_service.McpTool(srv, spec)
            rows.append(
                ToolInfo(
                    name=tool.name,
                    description=tool.description,
                    category="mcp",
                    input_schema=tool.input_schema,
                    required_credentials=[],
                    missing_credentials=[],
                    needs_sandbox=False,
                    status="offline" if (srv.last_error or not srv.enabled) else "ready",
                    server_id=srv.id,
                )
            )
    return rows


@router.post("/tools/{name}/test", response_model=KeyTestResult)
async def test_tool(name: str, user: CurrentUser, db: DbSession) -> KeyTestResult:
    """Run the tool's self-test (credentials, cheap request)."""
    extra = mcp_service.user_mcp_tools(db, user.id)
    try:
        tool = registry.get(name, extra)
        message = await tool.self_test(_ctx(user.id))
        return KeyTestResult(ok=True, message=message)
    except ToolError as exc:
        return KeyTestResult(ok=False, message=str(exc))
    except Exception as exc:  # noqa: BLE001 - surface provider errors as a failed test
        return KeyTestResult(ok=False, message=f"{exc.__class__.__name__}: {str(exc)[:200]}")


# ---------------------------------------------------------------- MCP servers


@router.get("/mcp-servers", response_model=list[McpServerOut])
def list_servers(user: CurrentUser, db: DbSession) -> list[McpServerOut]:
    rows = db.scalars(
        select(McpServer).where(McpServer.user_id == user.id).order_by(McpServer.created_at)
    ).all()
    return [_server_out(s) for s in rows]


def _check_transport(user: User, url: str | None, command: str | None) -> None:
    """stdio servers run on the backend host (admin only); URLs must be public for non-admins."""
    settings = get_settings()
    if command and settings.mcp_stdio_admin_only and not user.is_admin:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "command (stdio) MCP servers run on the server host and are admin-only; use an HTTP URL",
        )
    if url and not user.is_admin:
        try:
            check_public_url_sync(url)
        except UnsafeUrl as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"url: {exc}") from exc


@router.post("/mcp-servers", response_model=McpServerOut, status_code=status.HTTP_201_CREATED)
async def create_server(body: McpServerCreate, user: CurrentUser, db: DbSession) -> McpServerOut:
    """Register a server and fetch its tool list right away."""
    _check_transport(user, body.url, body.command)
    s = McpServer(
        user_id=user.id,
        name=body.name.strip(),
        url=body.url,
        command=body.command,
        args=list(body.args),
        env_encrypted=mcp_service.encode_secret_json(body.env),
        headers_encrypted=mcp_service.encode_secret_json(body.headers),
        enabled=body.enabled,
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    await mcp_service.refresh_server(db, s)
    return _server_out(s)


@router.put("/mcp-servers/{server_id}", response_model=McpServerOut)
def update_server(
    server_id: str, body: McpServerUpdate, user: CurrentUser, db: DbSession
) -> McpServerOut:
    s = _owned(db, user.id, server_id)
    _check_transport(
        user,
        body.url if body.url is not None else s.url,
        body.command if body.command is not None else s.command,
    )
    if body.name is not None:
        s.name = body.name.strip()
    if "url" in body.model_fields_set:
        s.url = body.url
    if "command" in body.model_fields_set:
        s.command = body.command
    if body.args is not None:
        s.args = list(body.args)
    if body.env is not None:
        s.env_encrypted = mcp_service.encode_secret_json(body.env)
    if body.headers is not None:
        s.headers_encrypted = mcp_service.encode_secret_json(body.headers)
    if body.enabled is not None:
        s.enabled = body.enabled
    if bool(s.url) == bool(s.command):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "exactly one of url or command is required"
        )
    db.commit()
    db.refresh(s)
    return _server_out(s)


@router.delete("/mcp-servers/{server_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_server(server_id: str, user: CurrentUser, db: DbSession) -> None:
    db.delete(_owned(db, user.id, server_id))
    db.commit()


@router.post("/mcp-servers/{server_id}/refresh", response_model=McpServerOut)
async def refresh(server_id: str, user: CurrentUser, db: DbSession) -> McpServerOut:
    """Reconnect and refresh the cached tool list."""
    s = _owned(db, user.id, server_id)
    await mcp_service.refresh_server(db, s)
    return _server_out(s)
