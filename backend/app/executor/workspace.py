"""Cloning repositories into per-run workspaces on the host."""

from __future__ import annotations

import asyncio
import os
import re
import shutil
from pathlib import Path
from typing import Protocol

_URL_RE = re.compile(r"^(https://[\w.\-]+/[\w.\-/]+|git@[\w.\-]+:[\w.\-/]+|ssh://[\w.\-@:/]+)$")
_BRANCH_RE = re.compile(r"^[\w.\-/]+$")
SANDBOX_UID = 1000
_GIT_IDENTITY = ["-c", "user.name=FlowForge", "-c", "user.email=flowforge@localhost"]


class WorkspaceError(Exception):
    """Raised when a repository cannot be prepared."""


class Workspace(Protocol):
    """Prepares and cleans up the host directory mounted into the sandbox."""

    async def prepare(self, run_id: str, repo_url: str, branch: str) -> tuple[str, str]:
        """Clone and return ``(container_visible_path, host_bind_path)``."""

    async def cleanup(self, run_id: str) -> None:
        """Remove the clone."""


def validate_repo_ref(repo_url: str, branch: str) -> None:
    """Reject URLs/branches that could be interpreted as git options.

    An empty ``repo_url`` is allowed and means "no repository" (an empty git
    workspace is created instead).
    """
    if repo_url.strip() and not _URL_RE.match(repo_url.strip()):
        raise WorkspaceError("repo_url must be an https://, ssh:// or git@ URL")
    if not _BRANCH_RE.match(branch) or branch.startswith("-"):
        raise WorkspaceError("invalid branch name")


async def _git(argv: list[str], *, cwd: Path | None = None, timeout: float = 60) -> None:
    proc = await asyncio.create_subprocess_exec(
        "git",
        *argv,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )
    _, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    if proc.returncode != 0:
        raise WorkspaceError(f"git {argv[0]} failed: {err.decode(errors='replace').strip()}")


class GitWorkspace:
    """Clones with ``git clone --depth 1`` into ``<root>/<run_id>``.

    ``host_root`` is the same directory as seen by the Docker daemon. It differs
    from ``root`` when the backend itself runs inside a container.
    """

    def __init__(self, root: str, host_root: str | None = None) -> None:
        self.root = Path(root).resolve()
        self.host_root = Path(host_root) if host_root else self.root

    def _paths(self, run_id: str) -> tuple[Path, Path]:
        return self.root / run_id, self.host_root / run_id

    async def prepare(self, run_id: str, repo_url: str, branch: str) -> tuple[str, str]:
        validate_repo_ref(repo_url, branch)
        local, host = self._paths(run_id)
        self.root.mkdir(parents=True, exist_ok=True)
        if local.exists():
            shutil.rmtree(local)
        if repo_url.strip():
            await _git(
                ["clone", "--depth", "1", "--branch", branch, "--", repo_url.strip(), str(local)],
                timeout=600,
            )
        else:
            await self._init_empty(local)
        _chown_tree(local)
        return str(local), str(host)

    async def _init_empty(self, local: Path) -> None:
        """Workspace for flows that don't operate on a repository."""
        local.mkdir(parents=True, exist_ok=True)
        (local / "input").mkdir(exist_ok=True)
        (local / "output").mkdir(exist_ok=True)
        (local / "README.md").write_text(
            "# FlowForge workspace\n\nInputs are under input/, write results to output/.\n"
        )
        await _git(["init", "-q", "-b", "main"], cwd=local)
        await _git([*_GIT_IDENTITY, "add", "-A"], cwd=local)
        await _git([*_GIT_IDENTITY, "commit", "-qm", "workspace"], cwd=local)

    async def cleanup(self, run_id: str) -> None:
        local, _ = self._paths(run_id)
        await asyncio.to_thread(shutil.rmtree, local, True)


def _chown_tree(path: Path) -> None:
    """Hand the clone to the sandbox's unprivileged uid when we are root."""
    if os.geteuid() != 0:
        return
    for dirpath, dirnames, filenames in os.walk(path):
        os.chown(dirpath, SANDBOX_UID, SANDBOX_UID)
        for name in filenames:
            os.lchown(os.path.join(dirpath, name), SANDBOX_UID, SANDBOX_UID)
