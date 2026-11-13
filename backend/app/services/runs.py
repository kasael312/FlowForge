"""Run lifecycle: create, launch in the background, cancel."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import SessionLocal
from app.executor.engine import ExecutorDeps, FlowExecutor
from app.executor.events import bus
from app.executor.sandbox import SandboxOptions, docker_sandbox_factory
from app.executor.workspace import GitWorkspace, WorkspaceError, validate_repo_ref
from app.models import Flow, Run, RunStatus
from app.schemas import RunCreate
from app.services.artifacts import ArtifactStore
from app.services.mcp import mcp_tools_factory
from app.services.models import make_resolver
from app.services.toolctx import build_tool_context

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

    def create_run(self, db: Session, flow: Flow, body: RunCreate, *, user_id: str) -> Run:
        """Persist a queued run owned by ``user_id``.

        Validates the repository reference (empty = no repository) and the
        submitted inputs against the flow's input schema.
        """
        from app.schemas import InputField, coerce_inputs

        validate_repo_ref(body.repo_url, body.branch)
        fields = [InputField.model_validate(f) for f in (flow.input_schema or [])]
        try:
            inputs = coerce_inputs(fields, body.inputs)
        except ValueError as exc:
            raise WorkspaceError(str(exc)) from exc
        sandbox = dict(flow.sandbox or {})
        if body.sandbox is not None:
            sandbox.update(body.sandbox.model_dump(exclude_none=True))
        run = Run(
            flow_id=flow.id,
            user_id=user_id,
            repo_url=body.repo_url.strip(),
            branch=body.branch,
            task_input=body.task,
            inputs=inputs,
            sandbox=sandbox or None,
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

    def approve(self, run_id: str, decision: str, comment: str = "") -> bool:
        """Resolve a pending approval of an active run; False when none is pending."""
        executor = self._executors.get(run_id)
        return bool(executor and executor.resolve_approval(decision, comment))

    def pending_approval(self, run_id: str) -> str | None:
        executor = self._executors.get(run_id)
        return executor.pending_approval if executor else None

    async def shutdown(self) -> None:
        """Cancel every active run (application shutdown)."""
        for run_id in list(self._tasks):
            self.cancel(run_id)
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)


def build_deps(settings: Settings) -> ExecutorDeps:
    """Production dependency wiring."""
    return ExecutorDeps(
        provider_resolver=make_resolver(SessionLocal, settings),
        sandbox_factory=docker_sandbox_factory(settings.sandbox_image, settings.sandbox_network),
        workspace=GitWorkspace(settings.workspace_dir, settings.host_workspace_dir),
        session_factory=SessionLocal,
        bus=bus,
        shell_whitelist=settings.shell_whitelist_set,
        max_steps_per_run=settings.max_steps_per_run,
        tool_context_factory=lambda uid: build_tool_context(SessionLocal, settings, uid),
        mcp_tools_factory=mcp_tools_factory(SessionLocal),
        upload_reader=lambda uid, upload_id: _read_upload(uid, upload_id),
        artifact_store=ArtifactStore(SessionLocal, settings),
        skill_loader=_load_skills,
        rule_loader=_load_rules,
        sandbox_caps=SandboxOptions(
            cpus=settings.sandbox_max_cpus,
            memory_mb=settings.sandbox_max_memory_mb,
            timeout_seconds=settings.sandbox_max_timeout_seconds,
            network=settings.sandbox_network,
            max_network=settings.sandbox_max_network,
        ),
    )


def _load_skills(user_id: str | None, ids: list[str]) -> tuple[list[Any], list[str]]:
    from app.services.skills import load_bundles, materialize, resolve_skills

    with SessionLocal() as db:
        skills, _ = resolve_skills(db, user_id, ids)
        for s in skills:  # on-disk cache (data/skills/<id>/) for inspection and debugging
            materialize(s, get_settings().skills_dir)
        return load_bundles(db, user_id, ids)


def _load_rules(user_id: str | None, flow_id: str | None, step_id: str) -> str:
    from app.services.rules import load_rules_block

    with SessionLocal() as db:
        return load_rules_block(db, user_id, flow_id, step_id)


def _read_upload(user_id: str | None, upload_id: str) -> tuple[str, bytes]:
    from app.services.uploads import read_upload

    with SessionLocal() as db:
        return read_upload(db, get_settings(), user_id, upload_id)


run_manager = RunManager()
"""Process-wide manager used by the API."""
