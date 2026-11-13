"""Sandbox abstraction and the Docker implementation.

A sandbox is a long-lived container with the cloned repository mounted at
``/repo``. Every tool call becomes a ``docker exec`` of an argv list, never a
shell string, so the only commands that run are the ones the executor builds.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from dataclasses import dataclass, field
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


NETWORK_ORDER = {"none": 0, "allowlist": 1, "bridge": 2}


@dataclass(slots=True)
class SandboxOptions:
    """Per-run resource limits and network policy."""

    cpus: float = 2.0
    memory_mb: int = 2048
    #: Whole-run wall clock limit enforced by the executor.
    timeout_seconds: int = 3600
    #: ``none`` (no egress), ``allowlist`` (proxy sidecar) or ``bridge`` (open).
    network: str = "none"
    allowed_hosts: list[str] = field(default_factory=list)
    pids_limit: int = 512
    #: Highest network mode allowed (only meaningful on the *caps* instance).
    max_network: str = "bridge"
    #: False = lightweight run: no workspace, no container (industry flows, pure LLM/HTTP/template graphs).
    enabled: bool = True

    @classmethod
    def from_dict(cls, d: dict | None, *, caps: SandboxOptions | None = None) -> SandboxOptions:
        """Build from a JSON dict, clamped to ``caps`` (server maximums)."""
        d = d or {}
        caps = caps or cls()
        net = str(d.get("network") or caps.network)
        if net not in NETWORK_ORDER:
            net = "none"
        if NETWORK_ORDER[net] > NETWORK_ORDER.get(caps.max_network, 2):
            net = caps.max_network  # server ceiling wins over the flow's request
        hosts = [str(h).strip().lower() for h in (d.get("allowed_hosts") or []) if str(h).strip()]
        return cls(
            enabled=bool(d.get("enabled", True)),
            cpus=max(0.25, min(float(d.get("cpus") or caps.cpus), caps.cpus)),
            memory_mb=max(128, min(int(d.get("memory_mb") or caps.memory_mb), caps.memory_mb)),
            timeout_seconds=max(
                30, min(int(d.get("timeout_seconds") or caps.timeout_seconds), caps.timeout_seconds)
            ),
            network=net,
            allowed_hosts=hosts[:50],
            pids_limit=caps.pids_limit,
        )

    def to_dict(self) -> dict:
        return {
            "cpus": self.cpus,
            "memory_mb": self.memory_mb,
            "timeout_seconds": self.timeout_seconds,
            "network": self.network,
            "allowed_hosts": list(self.allowed_hosts),
        }


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

    async def read_bytes(self, path: str) -> bytes:
        """Raw contents of a file inside the sandbox."""

    async def write_bytes(self, path: str, data: bytes) -> None:
        """Write raw bytes to a file inside the sandbox (parents created)."""


class SandboxFactory(Protocol):
    """Creates a sandbox for a given run, host repo path and options."""

    def __call__(
        self, run_id: str, host_repo_path: str, options: SandboxOptions | None = None
    ) -> Sandbox: ...


async def _run_raw(
    argv: list[str], *, stdin: bytes | None, timeout: float
) -> tuple[int, bytes, bytes]:
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(stdin), timeout=timeout)
    except (TimeoutError, asyncio.CancelledError):
        proc.kill()
        await proc.wait()
        raise
    return proc.returncode or 0, out, err


async def _run(argv: list[str], *, stdin: str | None, timeout: float) -> ExecResult:
    code, out, err = await _run_raw(
        argv, stdin=stdin.encode() if stdin is not None else None, timeout=timeout
    )
    return ExecResult(code, out.decode(errors="replace"), err.decode(errors="replace"))


class DockerSandbox:
    """Sandbox backed by a Docker container started from ``image``.

    Network modes:

    * ``none``: ``--network none``.
    * ``bridge``: default Docker bridge (open egress).
    * ``allowlist``: a private internal network (no egress) plus a proxy sidecar
      (same image, ``/opt/flowforge/proxy.py``) that is also attached to the
      bridge; the sandbox gets ``HTTP_PROXY``/``HTTPS_PROXY`` pointing at it, so
      only hosts in ``allowed_hosts`` are reachable.
    """

    PROXY_PORT = 3128

    def __init__(
        self,
        run_id: str,
        host_repo_path: str,
        *,
        image: str,
        options: SandboxOptions | None = None,
        network: str | None = None,
    ) -> None:
        self.name = f"flowforge-run-{run_id[:12]}"
        self.host_repo_path = host_repo_path
        self.image = image
        self.options = options or SandboxOptions(network=network or "none")
        if network is not None and options is None:
            self.options.network = network
        self.net_name = f"flowforge-net-{run_id[:12]}"
        self.proxy_name = f"flowforge-proxy-{run_id[:12]}"
        #: Per-run credential the sandbox must present to the egress proxy.
        self.proxy_token = secrets.token_urlsafe(18)

    @property
    def network(self) -> str:
        return self.options.network

    def run_argv(self) -> list[str]:
        """``docker run`` arguments for the sandbox container."""
        o = self.options
        argv = [
            "docker", "run", "-d", "--name", self.name,
            "--memory", f"{o.memory_mb}m", "--cpus", str(o.cpus), "--pids-limit", str(o.pids_limit),
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "-v", f"{self.host_repo_path}:{REPO_ROOT}",
        ]  # fmt: skip
        if o.network == "allowlist":
            proxy = f"http://ff:{self.proxy_token}@{self.proxy_name}:{self.PROXY_PORT}"
            argv += ["--network", self.net_name]
            for var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
                argv += ["-e", f"{var}={proxy}"]
            argv += ["-e", "NO_PROXY=localhost,127.0.0.1", "-e", "FF_NETWORK=allowlist"]
        elif o.network == "bridge":
            argv += ["--network", "bridge", "-e", "FF_NETWORK=bridge"]
        else:
            argv += ["--network", "none", "-e", "FF_NETWORK=none"]
        argv.append(self.image)
        return argv

    def proxy_argv(self) -> list[str]:
        """``docker run`` arguments for the allowlist proxy sidecar."""
        return [
            "docker", "run", "-d", "--name", self.proxy_name,
            "--network", self.net_name,
            "--memory", "256m", "--cpus", "0.5", "--pids-limit", "64",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "-e", f"FF_ALLOWED_HOSTS={','.join(self.options.allowed_hosts)}",
            "-e", f"FF_PROXY_PORT={self.PROXY_PORT}",
            "-e", f"FF_PROXY_TOKEN={self.proxy_token}",
            self.image, "python3", "/opt/flowforge/proxy.py",
        ]  # fmt: skip

    async def start(self) -> None:
        """Create the network/proxy when needed, then ``docker run`` the sandbox."""
        if self.options.network == "allowlist":
            res = await _run(
                ["docker", "network", "create", "--internal", self.net_name], stdin=None, timeout=60
            )
            if not res.ok and "already exists" not in res.stderr:
                raise RuntimeError(f"failed to create sandbox network: {res.stderr.strip()}")
            res = await _run(self.proxy_argv(), stdin=None, timeout=120)
            if not res.ok:
                await self.stop()
                raise RuntimeError(f"failed to start egress proxy: {res.stderr.strip()}")
            res = await _run(
                ["docker", "network", "connect", "bridge", self.proxy_name], stdin=None, timeout=60
            )
            if not res.ok:
                await self.stop()
                raise RuntimeError(f"failed to attach proxy to bridge: {res.stderr.strip()}")
        res = await _run(self.run_argv(), stdin=None, timeout=120)
        if not res.ok:
            await self.stop()
            raise RuntimeError(f"failed to start sandbox: {res.stderr.strip()}")
        log.info("sandbox %s started (network=%s)", self.name, self.options.network)

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

    async def read_bytes(self, path: str) -> bytes:
        """``docker exec cat`` without decoding."""
        code, out, err = await _run_raw(
            ["docker", "exec", self.name, "cat", "--", path], stdin=None, timeout=120
        )
        if code != 0:
            raise FileNotFoundError(err.decode(errors="replace").strip() or path)
        return out

    async def write_bytes(self, path: str, data: bytes) -> None:
        """``docker exec -i tee`` with raw stdin."""
        parent = path.rsplit("/", 1)[0] if "/" in path else "."
        await _run(
            ["docker", "exec", self.name, "mkdir", "-p", "--", parent], stdin=None, timeout=30
        )
        code, _, err = await _run_raw(
            ["docker", "exec", "-i", self.name, "tee", "--", path], stdin=data, timeout=120
        )
        if code != 0:
            raise OSError(err.decode(errors="replace").strip() or f"cannot write {path}")

    async def stop(self) -> None:
        """Force-remove the container(s) and network; errors are logged, not raised."""
        for argv in (
            ["docker", "rm", "-f", self.name],
            *(
                [
                    ["docker", "rm", "-f", self.proxy_name],
                    ["docker", "network", "rm", self.net_name],
                ]
                if self.options.network == "allowlist"
                else []
            ),
        ):
            res = await _run(argv, stdin=None, timeout=60)
            if not res.ok and "No such" not in res.stderr and "not found" not in res.stderr:
                log.warning("cleanup %s failed: %s", " ".join(argv[1:3]), res.stderr.strip())


def docker_sandbox_factory(image: str, default_network: str) -> SandboxFactory:
    """Build a :class:`SandboxFactory` bound to settings."""

    def factory(run_id: str, host_repo_path: str, options: SandboxOptions | None = None) -> Sandbox:
        opts = options or SandboxOptions(network=default_network)
        return DockerSandbox(run_id, host_repo_path, image=image, options=opts)

    return factory
