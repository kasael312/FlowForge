"""Sandbox abstraction and the Docker implementation.

A sandbox is a long-lived container with the cloned repository mounted at
``/repo``. Every tool call becomes a ``docker exec`` of an argv list, never a
shell string, so the only commands that run are the ones the executor builds.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Protocol

log = logging.getLogger(__name__)

REPO_ROOT = "/repo"
DEFAULT_TIMEOUT = 300


@dataclass(slots=True)
class ExecResult:
    """Outcome of a command executed in the sandbox."""

    exit_code: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        """True when the command exited with status 0."""
        return self.exit_code == 0

    def text(self, limit: int = 20_000) -> str:
        """Combined stdout/stderr, truncated for prompting."""
        out = self.stdout
        if self.stderr:
            out += ("\n" if out else "") + "[stderr]\n" + self.stderr
        if len(out) > limit:
            out = out[:limit] + f"\n... [truncated {len(out) - limit} chars]"
        return out


class Sandbox(Protocol):
    """Minimal interface the tools need."""

    async def start(self) -> None:
        """Create the container."""

    async def exec(
        self,
        argv: list[str],
        *,
        stdin: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        cwd: str = REPO_ROOT,
    ) -> ExecResult:
        """Run ``argv`` inside the sandbox."""

    async def stop(self) -> None:
        """Destroy the container."""


class SandboxFactory(Protocol):
    """Creates a sandbox for a given run and host repo path."""

    def __call__(self, run_id: str, host_repo_path: str) -> Sandbox: ...


async def _run(argv: list[str], *, stdin: str | None, timeout: float) -> ExecResult:
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(
            proc.communicate(stdin.encode() if stdin is not None else None), timeout=timeout
        )
    except (TimeoutError, asyncio.CancelledError):
        proc.kill()
        await proc.wait()
        raise
    return ExecResult(
        proc.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace")
    )


class DockerSandbox:
    """Sandbox backed by a Docker container started from ``image``."""

    def __init__(
        self,
        run_id: str,
        host_repo_path: str,
        *,
        image: str,
        network: str = "none",
        memory: str = "2g",
        cpus: str = "2",
        pids_limit: int = 512,
    ) -> None:
        self.name = f"flowforge-run-{run_id[:12]}"
        self.host_repo_path = host_repo_path
        self.image = image
        self.network = network
        self.memory = memory
        self.cpus = cpus
        self.pids_limit = pids_limit

    async def start(self) -> None:
        """``docker run`` a detached container with the repo bind-mounted."""
        argv = [
            "docker",
            "run",
            "-d",
            "--name",
            self.name,
            "--network",
            self.network,
            "--memory",
            self.memory,
            "--cpus",
            self.cpus,
            "--pids-limit",
            str(self.pids_limit),
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "-v",
            f"{self.host_repo_path}:{REPO_ROOT}",
            self.image,
        ]
        res = await _run(argv, stdin=None, timeout=120)
        if not res.ok:
            raise RuntimeError(f"failed to start sandbox: {res.stderr.strip()}")
        log.info("sandbox %s started", self.name)

    async def exec(
        self,
        argv: list[str],
        *,
        stdin: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        cwd: str = REPO_ROOT,
    ) -> ExecResult:
        """``docker exec`` an argv list inside the container."""
        cmd = ["docker", "exec", "-w", cwd]
        if stdin is not None:
            cmd.append("-i")
        cmd += [self.name, *argv]
        try:
            return await _run(cmd, stdin=stdin, timeout=timeout)
        except TimeoutError:
            return ExecResult(124, "", f"command timed out after {timeout:.0f}s")

    async def stop(self) -> None:
        """Force-remove the container; errors are logged, not raised."""
        res = await _run(["docker", "rm", "-f", self.name], stdin=None, timeout=60)
        if not res.ok:
            log.warning("failed to remove sandbox %s: %s", self.name, res.stderr.strip())


def docker_sandbox_factory(image: str, network: str) -> SandboxFactory:
    """Build a :class:`SandboxFactory` bound to settings."""

    def factory(run_id: str, host_repo_path: str) -> Sandbox:
        return DockerSandbox(run_id, host_repo_path, image=image, network=network)

    return factory
