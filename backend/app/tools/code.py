"""Code tools: repository access inside the sandbox (the original five)."""

from __future__ import annotations

import posixpath
import shlex
from collections.abc import Iterable
from typing import Any

from app.executor.sandbox import REPO_ROOT
from app.tools.base import Tool, ToolCategory, ToolContext, ToolError, schema, truncate

_SHELL_META = set(";&|<>`$(){}\n\\")


def safe_repo_path(path: str) -> str:
    """Resolve a user-supplied path to an absolute path under ``/repo``."""
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
    """Split a command line into argv and enforce the executable whitelist."""
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


class ReadFile(Tool):
    name = "read_file"
    description = "Read a UTF-8 text file from the repository. Paths are relative to the repo root."
    category = ToolCategory.code
    needs_sandbox = True
    input_schema = schema(
        {"path": {"type": "string", "description": "Relative file path"}}, ["path"]
    )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        full = safe_repo_path(args["path"])
        res = await ctx.sandbox_required().exec(["cat", "--", full], timeout=30)
        if not res.ok:
            raise ToolError(res.stderr.strip() or f"cannot read {args['path']}")
        return truncate(res.stdout)


class WriteFile(Tool):
    name = "write_file"
    description = "Create or overwrite a text file in the repository, creating parent directories."
    category = ToolCategory.code
    needs_sandbox = True
    input_schema = schema(
        {
            "path": {"type": "string", "description": "Relative file path"},
            "content": {"type": "string", "description": "Full new file content"},
        },
        ["path", "content"],
    )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        sb = ctx.sandbox_required()
        full = safe_repo_path(args["path"])
        if full == REPO_ROOT:
            raise ToolError("cannot write to the repository root itself")
        mk = await sb.exec(["mkdir", "-p", "--", posixpath.dirname(full)], timeout=30)
        if not mk.ok:
            raise ToolError(mk.stderr.strip())
        res = await sb.exec(["tee", "--", full], stdin=args["content"], timeout=30)
        if not res.ok:
            raise ToolError(res.stderr.strip() or f"cannot write {args['path']}")
        return f"wrote {len(args['content'])} chars to {posixpath.relpath(full, REPO_ROOT)}"


class ListFiles(Tool):
    name = "list_files"
    description = "List files under a directory (recursive, excluding .git and node_modules)."
    category = ToolCategory.code
    needs_sandbox = True
    input_schema = schema(
        {
            "path": {"type": "string", "description": "Relative directory path, default '.'"},
            "max_results": {"type": "integer", "description": "Default 500"},
        }
    )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        full = safe_repo_path(args.get("path") or ".")
        max_results = max(1, min(int(args.get("max_results") or 500), 5000))
        argv = [
            "find", full,
            "(", "-name", ".git", "-o", "-name", "node_modules", "-o", "-name", "__pycache__", ")",
            "-prune", "-o", "-type", "f", "-print",
        ]  # fmt: skip
        res = await ctx.sandbox_required().exec(argv, timeout=60)
        if not res.ok:
            raise ToolError(res.stderr.strip() or f"cannot list {args.get('path')}")
        rel = sorted(posixpath.relpath(line, REPO_ROOT) for line in res.stdout.splitlines())
        out = "\n".join(rel[:max_results])
        if len(rel) > max_results:
            out += f"\n... ({len(rel) - max_results} more)"
        return out or "(no files)"


class RunShell(Tool):
    name = "run_shell"
    description = (
        "Run a single whitelisted command in the repository root. No pipes, redirects, "
        "variables or chaining: just an executable and its arguments."
    )
    category = ToolCategory.code
    needs_sandbox = True
    input_schema = schema(
        {
            "command": {"type": "string", "description": "e.g. 'pytest -q tests/'"},
            "timeout": {"type": "integer", "description": "Seconds, default 300"},
        },
        ["command"],
    )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        argv = parse_whitelisted_command(args["command"], ctx.shell_whitelist)
        timeout = max(1, min(int(args.get("timeout") or 300), 1800))
        res = await ctx.sandbox_required().exec(argv, timeout=timeout)
        return f"exit_code: {res.exit_code}\n{res.text()}"


class GitDiff(Tool):
    name = "git_diff"
    description = "Show the current uncommitted changes (including new files) as a unified diff."
    category = ToolCategory.code
    needs_sandbox = True
    input_schema = schema({})

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        sb = ctx.sandbox_required()
        await sb.exec(["git", "add", "-N", "."], timeout=60)
        res = await sb.exec(["git", "diff", "HEAD"], timeout=60)
        if not res.ok:
            raise ToolError(res.stderr.strip() or "git diff failed")
        return truncate(res.stdout) or "(no changes)"


CODE_TOOLS: list[Tool] = [ReadFile(), WriteFile(), ListFiles(), RunShell(), GitDiff()]
