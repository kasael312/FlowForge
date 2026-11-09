"""Tools exposed to the model. All of them operate strictly inside ``/repo``.

Adding a tool: add a spec to :data:`TOOL_SPECS`, a handler method on
:class:`ToolExecutor` named ``tool_<name>``, and the literal to
``app.schemas.ToolName``.
"""

from __future__ import annotations

import posixpath
import shlex
from collections.abc import Iterable
from typing import Any

from app.executor.sandbox import REPO_ROOT, Sandbox

MAX_OUTPUT = 20_000
_SHELL_META = set(";&|<>`$(){}\n\\")


class ToolError(Exception):
    """Raised when a tool call is rejected or fails; returned to the model as is_error."""


def safe_repo_path(path: str) -> str:
    """Resolve a user-supplied path to an absolute path under ``/repo``.

    Raises:
        ToolError: if the path escapes the repository.
    """
    if not path or path.strip() == "":
        raise ToolError("path must not be empty")
    p = path.strip()
    if p.startswith(REPO_ROOT + "/") or p == REPO_ROOT:
        p = p[len(REPO_ROOT) :].lstrip("/") or "."
    if p.startswith("/"):
        raise ToolError(f"absolute paths outside {REPO_ROOT} are not allowed: {path!r}")
    norm = posixpath.normpath(p)
    if norm == ".." or norm.startswith("../"):
        raise ToolError(f"path escapes repository: {path!r}")
    return REPO_ROOT if norm == "." else posixpath.join(REPO_ROOT, norm)


def parse_whitelisted_command(command: str, whitelist: Iterable[str]) -> list[str]:
    """Split a command line into argv and enforce the executable whitelist.

    Shell metacharacters are rejected outright so that nothing is ever
    interpreted by a shell: the argv is passed verbatim to ``docker exec``.
    """
    if not command or not command.strip():
        raise ToolError("command must not be empty")
    bad = sorted(set(command) & _SHELL_META)
    if bad:
        raise ToolError(f"shell metacharacters are not allowed: {''.join(bad)!r}")
    try:
        argv = shlex.split(command)
    except ValueError as exc:
        raise ToolError(f"cannot parse command: {exc}") from exc
    if not argv:
        raise ToolError("command must not be empty")
    exe = posixpath.basename(argv[0])
    allowed = set(whitelist)
    if exe not in allowed:
        raise ToolError(
            f"command {exe!r} is not whitelisted. Allowed: {', '.join(sorted(allowed))}"
        )
    argv[0] = exe
    return argv


TOOL_SPECS: dict[str, dict[str, Any]] = {
    "read_file": {
        "name": "read_file",
        "description": "Read a UTF-8 text file from the repository. Paths are relative to the repo root.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Relative file path"}},
            "required": ["path"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    "write_file": {
        "name": "write_file",
        "description": "Create or overwrite a text file in the repository, creating parent directories.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative file path"},
                "content": {"type": "string", "description": "Full new file content"},
            },
            "required": ["path", "content"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    "list_files": {
        "name": "list_files",
        "description": "List files under a directory (recursive, excluding .git and node_modules).",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative directory path, default '.'"},
                "max_results": {"type": "integer", "description": "Default 500"},
            },
            "required": [],
            "additionalProperties": False,
        },
    },
    "run_shell": {
        "name": "run_shell",
        "description": (
            "Run a single whitelisted command in the repository root. No pipes, redirects, "
            "variables or chaining: just an executable and its arguments."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "e.g. 'pytest -q tests/'"},
                "timeout": {"type": "integer", "description": "Seconds, default 300"},
            },
            "required": ["command"],
            "additionalProperties": False,
        },
    },
    "git_diff": {
        "name": "git_diff",
        "description": "Show the current uncommitted changes (including new files) as a unified diff.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
}


def tool_definitions(allowed: Iterable[str]) -> list[dict[str, Any]]:
    """API tool definitions for the given allowed tool names (order preserved)."""
    return [TOOL_SPECS[name] for name in allowed if name in TOOL_SPECS]


class ToolExecutor:
    """Dispatches tool calls to a :class:`Sandbox` with safety checks."""

    def __init__(self, sandbox: Sandbox, shell_whitelist: Iterable[str]) -> None:
        self.sandbox = sandbox
        self.shell_whitelist = frozenset(shell_whitelist)

    async def call(self, name: str, args: dict[str, Any]) -> str:
        """Execute ``name`` with ``args``. Raises :class:`ToolError` on failure."""
        handler = getattr(self, f"tool_{name}", None)
        if handler is None or name not in TOOL_SPECS:
            raise ToolError(f"unknown tool {name!r}")
        return await handler(**args)

    async def tool_read_file(self, path: str) -> str:
        full = safe_repo_path(path)
        res = await self.sandbox.exec(["cat", "--", full], timeout=30)
        if not res.ok:
            raise ToolError(res.stderr.strip() or f"cannot read {path}")
        return _truncate(res.stdout)

    async def tool_write_file(self, path: str, content: str) -> str:
        full = safe_repo_path(path)
        if full == REPO_ROOT:
            raise ToolError("cannot write to the repository root itself")
        parent = posixpath.dirname(full)
        mk = await self.sandbox.exec(["mkdir", "-p", "--", parent], timeout=30)
        if not mk.ok:
            raise ToolError(mk.stderr.strip())
        # `tee` writes stdin to the file without involving a shell.
        res = await self.sandbox.exec(["tee", "--", full], stdin=content, timeout=30)
        if not res.ok:
            raise ToolError(res.stderr.strip() or f"cannot write {path}")
        return f"wrote {len(content)} chars to {posixpath.relpath(full, REPO_ROOT)}"

    async def tool_list_files(self, path: str = ".", max_results: int = 500) -> str:
        full = safe_repo_path(path or ".")
        max_results = max(1, min(int(max_results), 5000))
        argv = [
            "find",
            full,
            "(",
            "-name",
            ".git",
            "-o",
            "-name",
            "node_modules",
            "-o",
            "-name",
            "__pycache__",
            ")",
            "-prune",
            "-o",
            "-type",
            "f",
            "-print",
        ]
        res = await self.sandbox.exec(argv, timeout=60)
        if not res.ok:
            raise ToolError(res.stderr.strip() or f"cannot list {path}")
        lines = sorted(res.stdout.splitlines())
        rel = [posixpath.relpath(line, REPO_ROOT) for line in lines]
        out = "\n".join(rel[:max_results])
        if len(rel) > max_results:
            out += f"\n... ({len(rel) - max_results} more)"
        return out or "(no files)"

    async def tool_run_shell(self, command: str, timeout: int = 300) -> str:
        argv = parse_whitelisted_command(command, self.shell_whitelist)
        timeout = max(1, min(int(timeout), 1800))
        res = await self.sandbox.exec(argv, timeout=timeout)
        body = res.text(MAX_OUTPUT)
        return f"exit_code: {res.exit_code}\n{body}"

    async def tool_git_diff(self) -> str:
        # intent-to-add so that new files appear in the diff
        await self.sandbox.exec(["git", "add", "-N", "."], timeout=60)
        res = await self.sandbox.exec(["git", "diff", "HEAD"], timeout=60)
        if not res.ok:
            raise ToolError(res.stderr.strip() or "git diff failed")
        return _truncate(res.stdout) or "(no changes)"


def _truncate(text: str, limit: int = MAX_OUTPUT) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [truncated {len(text) - limit} chars]"
