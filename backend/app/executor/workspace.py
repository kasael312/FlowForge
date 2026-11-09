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


class WorkspaceError(Exception):
    """Raised when a repository cannot be prepared."""


class Workspace(Protocol):
    """Prepares and cleans up the host directory mounted into the sandbox."""

    async def prepare(self, run_id: str, repo_url: str, branch: str) -> tuple[str, str]:
        """Clone and return ``(container_visible_path, host_bind_path)``."""

    async def cleanup(self, run_id: str) -> None:
        """Remove the clone."""


def validate_repo_ref(repo_url: str, branch: str) -> None:
    """Reject URLs/branches that could be interpreted as git options."""
    if not _URL_RE.match(repo_url.strip()):
        raise WorkspaceError("repo_url must be an https://, ssh:// or git@ URL")
    if not _BRANCH_RE.match(branch) or branch.startswith("-"):
        raise WorkspaceError("invalid branch name")


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
        proc = await asyncio.create_subprocess_exec(
            "git",
            "clone",
            "--depth",
            "1",
            "--branch",
            branch,
            "--",
            repo_url,
            str(local),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
        _, err = await asyncio.wait_for(proc.communicate(), timeout=600)
        if proc.returncode != 0:
            raise WorkspaceError(f"git clone failed: {err.decode(errors='replace').strip()}")
        _chown_tree(local)
        return str(local), str(host)

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
