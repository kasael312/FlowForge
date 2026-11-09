"""Graph traversal: runs a Flow's steps in order, handling conditions and failures.

Traversal starts at the unique node without incoming edges and follows outgoing
edges. ``condition`` nodes evaluate an expression over the previous step and
pick the edge whose ``sourceHandle`` is ``"true"`` or ``"false"``. A failed step
consults its ``on_fail`` policy: ``retry`` re-runs it, ``goto:<id>`` jumps, and
``stop`` fails the run. ``retry_limit`` bounds both retries and gotos per step.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from anthropic import AsyncAnthropic
from sqlalchemy.orm import Session, sessionmaker

from app.executor.agent import AgentConfig, run_agent
from app.executor.conditions import ConditionError, StepResultView, evaluate
from app.executor.events import RunEventBus
from app.executor.prompts import system_prompt_for
from app.executor.sandbox import Sandbox, SandboxFactory
from app.executor.tools import ToolError, ToolExecutor, parse_whitelisted_command
from app.executor.workspace import Workspace, WorkspaceError
from app.models import Run, RunStatus, StepRun, StepRunStatus
from app.schemas import Edge, Node, StepType

log = logging.getLogger(__name__)

GIT_EXCLUDES = "\n__pycache__/\n*.pyc\n.pytest_cache/\nnode_modules/\n.venv/\ndist/\n"


class FlowGraphError(Exception):
    """The flow graph cannot be executed as defined."""


@dataclass(slots=True)
class ExecutorDeps:
    """Everything the engine needs, injectable for tests."""

    client: AsyncAnthropic
    agent_config: AgentConfig
    sandbox_factory: SandboxFactory
    workspace: Workspace
    session_factory: sessionmaker[Session]
    bus: RunEventBus
    shell_whitelist: frozenset[str]
    max_steps_per_run: int = 50


@dataclass(slots=True)
class StepOutcome:
    """Result of executing one node once."""

    success: bool
    output: str
    exit_code: int | None = None
    error: str | None = None


def find_start(nodes: list[Node], edges: list[Edge]) -> Node:
    """The single node with no incoming edges."""
    targets = {e.target for e in edges}
    starts = [n for n in nodes if n.id not in targets]
    if not nodes:
        raise FlowGraphError("flow has no steps")
    if len(starts) != 1:
        raise FlowGraphError(f"flow must have exactly one start node, found {len(starts)}")
    return starts[0]


class FlowExecutor:
    """Executes a single run. One instance per run."""

    def __init__(self, deps: ExecutorDeps, run_id: str) -> None:
        self.deps = deps
        self.run_id = run_id
        self.cancel = asyncio.Event()
        self._sandbox: Sandbox | None = None

    # ---- persistence helpers -------------------------------------------------

    def _session(self) -> Session:
        return self.deps.session_factory()

    def _set_run(self, **fields: Any) -> None:
        with self._session() as db:
            run = db.get(Run, self.run_id)
            if run is None:
                return
            for k, v in fields.items():
                setattr(run, k, v)
            db.commit()

    def _log(self, event: str, **data: Any) -> None:
        self.deps.bus.publish(self.run_id, event, **data)

    # ---- entry point -----------------------------------------------------------

    async def execute(self) -> RunStatus:
        """Run the flow to completion and return the final status."""
        with self._session() as db:
            run = db.get(Run, self.run_id)
            if run is None:
                raise FlowGraphError(f"run {self.run_id} not found")
            flow = run.flow
            nodes = [Node.model_validate(n) for n in flow.nodes]
            edges = [Edge.model_validate(e) for e in flow.edges]
            repo_url, branch, task = run.repo_url, run.branch, run.task_input

        self._set_run(status=RunStatus.running, started_at=datetime.now(UTC))
        self._log("run_status", status=RunStatus.running.value)
        final = RunStatus.failed
        error: str | None = None
        try:
            start = find_start(nodes, edges)
            _, host_path = await self.deps.workspace.prepare(self.run_id, repo_url, branch)
            self._log("log", message=f"cloned {repo_url}@{branch}")
            self._sandbox = self.deps.sandbox_factory(self.run_id, host_path)
            await self._sandbox.start()
            await self._git_setup()
            ok = await self._traverse(start, nodes, edges, task)
            final = RunStatus.success if ok else RunStatus.failed
            if not ok:
                error = "a step failed"
            await self._capture_diff()
        except asyncio.CancelledError:
            final, error = RunStatus.cancelled, "cancelled by user"
        except (FlowGraphError, WorkspaceError, RuntimeError) as exc:
            final, error = RunStatus.failed, str(exc)
            self._log("log", level="error", message=str(exc))
        except Exception as exc:  # pragma: no cover - unexpected
            log.exception("run %s crashed", self.run_id)
            final, error = RunStatus.failed, f"internal error: {exc}"
        finally:
            await self._teardown()
            with self._session() as db:
                run = db.get(Run, self.run_id)
                if run is not None:
                    run.status = final
                    run.error = error
                    run.finished_at = datetime.now(UTC)
                    run.cost_tokens = sum(s.tokens for s in run.step_runs)
                    db.commit()
            self._log("run_status", status=final.value, error=error)
            self.deps.bus.close(self.run_id)
        return final

    async def _teardown(self) -> None:
        if self._sandbox is not None:
            try:
                await asyncio.shield(self._sandbox.stop())
            except Exception:  # pragma: no cover
                log.exception("sandbox stop failed")
            self._sandbox = None

    async def _git_setup(self) -> None:
        assert self._sandbox is not None
        wb = f"flowforge/{self.run_id[:8]}"
        for argv in (
            ["git", "config", "user.email", "flowforge@localhost"],
            ["git", "config", "user.name", "FlowForge"],
            ["git", "checkout", "-b", wb],
        ):
            res = await self._sandbox.exec(argv, timeout=30)
            if not res.ok:
                raise RuntimeError(f"git setup failed: {res.stderr.strip()}")
        # Keep build junk out of diffs without touching the repo's own .gitignore.
        await self._sandbox.exec(["tee", "-a", ".git/info/exclude"], stdin=GIT_EXCLUDES, timeout=30)
        self._set_run(work_branch=wb)

    async def _capture_diff(self) -> None:
        if self._sandbox is None:
            return
        await self._sandbox.exec(["git", "add", "-N", "."], timeout=60)
        res = await self._sandbox.exec(["git", "diff", "HEAD"], timeout=60)
        if res.ok:
            self._set_run(final_diff=res.stdout)

    # ---- traversal -------------------------------------------------------------

    async def _traverse(self, start: Node, nodes: list[Node], edges: list[Edge], task: str) -> bool:
        by_id = {n.id: n for n in nodes}
        out_edges: dict[str, list[Edge]] = {n.id: [] for n in nodes}
        for e in edges:
            out_edges[e.source].append(e)

        attempts: dict[str, int] = {}
        current: Node | None = start
        prev: StepResultView | None = None
        executed = 0

        while current is not None:
            if self.cancel.is_set():
                raise asyncio.CancelledError
            executed += 1
            if executed > self.deps.max_steps_per_run:
                raise FlowGraphError(f"exceeded {self.deps.max_steps_per_run} step executions")

            attempts[current.id] = attempts.get(current.id, 0) + 1
            outcome = await self._run_step(current, attempts[current.id], task, prev)
            prev = StepResultView(
                step_id=current.id,
                type=current.data.type.value,
                success=outcome.success,
                output=outcome.output,
                exit_code=outcome.exit_code,
            )

            if current.data.type is StepType.condition:
                handle = "true" if outcome.success else "false"
                nxt = [e for e in out_edges[current.id] if e.sourceHandle == handle]
                current = by_id[nxt[0].target] if nxt else None
                continue

            if outcome.success:
                nxt = out_edges[current.id]
                current = by_id[nxt[0].target] if nxt else None
                continue

            # failure handling
            policy = current.data.on_fail
            budget_left = attempts[current.id] <= current.data.retry_limit
            if policy == "retry" and budget_left:
                self._log(
                    "log",
                    step_id=current.id,
                    message=f"retrying ({attempts[current.id]}/{current.data.retry_limit})",
                )
                continue
            target = current.data.goto_target
            if target is not None and budget_left:
                self._log("log", step_id=current.id, message=f"on_fail -> goto {target}")
                current = by_id[target]
                continue
            return False
        return True

    # ---- single step -----------------------------------------------------------

    async def _run_step(
        self, node: Node, attempt: int, task: str, prev: StepResultView | None
    ) -> StepOutcome:
        data = node.data
        with self._session() as db:
            sr = StepRun(
                run_id=self.run_id,
                step_id=node.id,
                step_type=data.type.value,
                attempt=attempt,
                status=StepRunStatus.running,
            )
            db.add(sr)
            db.commit()
            step_run_id = sr.id
        self._log(
            "step_start",
            step_id=node.id,
            step_run_id=step_run_id,
            type=data.type.value,
            attempt=attempt,
            label=data.label,
        )
        started = time.monotonic()

        messages: list[dict[str, Any]] = []
        tool_calls: list[dict[str, Any]] = []
        tokens = 0
        try:
            if data.type is StepType.condition:
                outcome = self._run_condition(data.expression, prev)
            elif data.type is StepType.shell:
                outcome = await self._run_shell(data.command or "")
            else:
                result = await run_agent(
                    self.deps.client,
                    self.deps.agent_config,
                    system_prompt=system_prompt_for(data.type, data.system_prompt),
                    user_message=_user_message(task, prev),
                    tools_allowed=list(data.tools_allowed),
                    max_turns=data.max_turns,
                    executor=ToolExecutor(self._sandbox_required(), self.deps.shell_whitelist),
                    log=lambda ev, **d: self._log(
                        ev, step_id=node.id, step_run_id=step_run_id, **d
                    ),
                    cancel=self.cancel,
                )
                messages, tool_calls, tokens = result.messages, result.tool_calls, result.tokens
                outcome = StepOutcome(result.success, result.output, error=result.error)
        except asyncio.CancelledError:
            self._finish_step(
                step_run_id,
                StepRunStatus.failed,
                "cancelled",
                started,
                messages,
                tool_calls,
                tokens,
            )
            raise
        except Exception as exc:
            log.exception("step %s crashed", node.id)
            outcome = StepOutcome(False, "", error=f"{type(exc).__name__}: {exc}")

        status = StepRunStatus.success if outcome.success else StepRunStatus.failed
        output = (
            outcome.output
            if outcome.error is None
            else f"{outcome.output}\n[error] {outcome.error}".strip()
        )
        duration = self._finish_step(
            step_run_id, status, output, started, messages, tool_calls, tokens
        )
        self._log(
            "step_end",
            step_id=node.id,
            step_run_id=step_run_id,
            status=status.value,
            duration=duration,
            error=outcome.error,
            output=outcome.output[-2000:],
        )
        return outcome

    def _finish_step(
        self,
        step_run_id: str,
        status: StepRunStatus,
        output: str,
        started: float,
        messages: list[dict[str, Any]],
        tool_calls: list[dict[str, Any]],
        tokens: int,
    ) -> float:
        duration = round(time.monotonic() - started, 3)
        with self._session() as db:
            sr = db.get(StepRun, step_run_id)
            if sr is not None:
                sr.status = status
                sr.output = output
                sr.messages = messages
                sr.tool_calls = tool_calls
                sr.tokens = tokens
                sr.duration = duration
                sr.finished_at = datetime.now(UTC)
                db.commit()
        return duration

    def _sandbox_required(self) -> Sandbox:
        if self._sandbox is None:
            raise RuntimeError("sandbox not started")
        return self._sandbox

    def _run_condition(self, expression: str | None, prev: StepResultView | None) -> StepOutcome:
        if prev is None:
            return StepOutcome(False, "", error="condition has no previous step")
        try:
            value = evaluate(expression or "", prev)
        except ConditionError as exc:
            return StepOutcome(False, "", error=str(exc))
        return StepOutcome(value, f"{expression or 'prev.success'} -> {value}")

    async def _run_shell(self, command: str) -> StepOutcome:
        try:
            argv = parse_whitelisted_command(command, self.deps.shell_whitelist)
        except ToolError as exc:
            return StepOutcome(False, "", error=str(exc))
        self._log("tool_call", tool="run_shell", input={"command": command})
        res = await self._sandbox_required().exec(argv)
        text = res.text()
        self._log(
            "tool_result",
            tool="run_shell",
            output=text,
            is_error=not res.ok,
            exit_code=res.exit_code,
        )
        return StepOutcome(
            res.ok,
            text,
            exit_code=res.exit_code,
            error=None if res.ok else f"exit code {res.exit_code}",
        )


def _user_message(task: str, prev: StepResultView | None) -> str:
    parts = [f"## Task\n{task.strip() or '(no task description given)'}"]
    if prev is not None:
        status = "succeeded" if prev.success else "FAILED"
        parts.append(
            f"## Previous step `{prev.step_id}` ({prev.type}) {status}\n{prev.output.strip() or '(no output)'}"
        )
    return "\n\n".join(parts)
