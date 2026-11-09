"""Run lifecycle: create, launch in the background, cancel."""

from __future__ import annotations

import asyncio
import logging

from anthropic import AsyncAnthropic
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import SessionLocal
from app.executor.agent import AgentConfig
from app.executor.engine import ExecutorDeps, FlowExecutor
from app.executor.events import bus
from app.executor.sandbox import docker_sandbox_factory
from app.executor.workspace import GitWorkspace, validate_repo_ref
from app.models import Flow, Run, RunStatus
from app.schemas import RunCreate

log = logging.getLogger(__name__)


class RunManager:
    """Owns the background tasks of in-flight runs."""

    def __init__(self, deps: ExecutorDeps | None = None, history_ttl: float = 600.0) -> None:
        self._deps = deps
        self.history_ttl = history_ttl
        self._executors: dict[str, FlowExecutor] = {}
        self._tasks: dict[str, asyncio.Task[RunStatus]] = {}

    @property
    def deps(self) -> ExecutorDeps:
        """Lazily build production dependencies from settings."""
        if self._deps is None:
            self._deps = build_deps(get_settings())
        return self._deps

    def create_run(self, db: Session, flow: Flow, body: RunCreate) -> Run:
        """Persist a queued run (validates repo/branch first)."""
        validate_repo_ref(body.repo_url, body.branch)
        run = Run(
            flow_id=flow.id,
            repo_url=body.repo_url.strip(),
            branch=body.branch,
            task_input=body.task,
            status=RunStatus.queued,
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        return run

    def launch(self, run_id: str) -> asyncio.Task[RunStatus]:
        """Start executing a run in the background."""
        executor = FlowExecutor(self.deps, run_id)
        task = asyncio.create_task(executor.execute(), name=f"run-{run_id}")
        self._executors[run_id] = executor
        self._tasks[run_id] = task
        task.add_done_callback(lambda _t: self._done(run_id))
        return task

    def _done(self, run_id: str) -> None:
        self._executors.pop(run_id, None)
        task = self._tasks.pop(run_id, None)
        if task and not task.cancelled() and task.exception():
            log.error("run %s failed: %r", run_id, task.exception())
        # Keep the live log buffer around briefly for late WebSocket clients,
        # then evict it; afterwards logs are rebuilt from the database.
        asyncio.get_running_loop().call_later(self.history_ttl, self.deps.bus.forget, run_id)

    def is_active(self, run_id: str) -> bool:
        """Whether the run is currently executing in this process."""
        return run_id in self._tasks

    def cancel(self, run_id: str) -> bool:
        """Request cancellation; returns False if the run isn't active here."""
        executor = self._executors.get(run_id)
        task = self._tasks.get(run_id)
        if executor is None or task is None:
            return False
        executor.cancel.set()
        task.cancel()
        return True

    async def shutdown(self) -> None:
        """Cancel every active run (application shutdown)."""
        for run_id in list(self._tasks):
            self.cancel(run_id)
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)


def build_deps(settings: Settings) -> ExecutorDeps:
    """Production dependency wiring."""
    return ExecutorDeps(
        client=AsyncAnthropic(api_key=settings.anthropic_api_key or None),
        agent_config=AgentConfig(
            model=settings.anthropic_model,
            use_fallbacks=settings.anthropic_fallbacks,
            effort=settings.anthropic_effort or None,
        ),
        sandbox_factory=docker_sandbox_factory(settings.sandbox_image, settings.sandbox_network),
        workspace=GitWorkspace(settings.workspace_dir, settings.host_workspace_dir),
        session_factory=SessionLocal,
        bus=bus,
        shell_whitelist=settings.shell_whitelist_set,
        max_steps_per_run=settings.max_steps_per_run,
    )


run_manager = RunManager()
"""Process-wide manager used by the API."""
