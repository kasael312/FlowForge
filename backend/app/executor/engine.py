"""Graph traversal: runs a Flow's steps in order, handling conditions and failures.

Traversal starts at the unique node without incoming edges and follows outgoing
edges. ``condition`` nodes evaluate an expression over the previous step and
pick the edge whose ``sourceHandle`` is ``"true"`` or ``"false"``. A failed step
consults its ``on_fail`` policy: ``retry`` re-runs it, ``goto:<id>`` jumps, and
``stop`` fails the run. ``retry_limit`` bounds both retries and gotos per step.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from app.executor.agent import AgentResult, run_agent
from app.executor.conditions import ConditionError, StepResultView, evaluate
from app.executor.events import RunEventBus
from app.executor.nodes import (
    NodeEnv,
    NodeResult,
    parse_json,
    resolve_items,
    run_data_transform,
    run_http_request,
    run_merge,
    run_output,
    run_template,
)
from app.executor.prompts import system_prompt_for
from app.executor.sandbox import Sandbox, SandboxFactory, SandboxOptions
from app.executor.tools import ToolError, ToolExecutor, parse_whitelisted_command
from app.executor.variables import render
from app.executor.workspace import Workspace, WorkspaceError
from app.llm.base import LLMProvider, ProviderError
from app.llm.pricing import estimate_cost
from app.models import Run, RunStatus, StepRun, StepRunStatus
from app.schemas import Edge, Node, StepType
from app.services.models import ModelConfigError, ProviderResolver
from app.services.skills import SKILLS_MOUNT, SkillBundle, skills_prompt_block
from app.tools import Tool, ToolContext

log = logging.getLogger(__name__)

GIT_EXCLUDES = "\n__pycache__/\n*.pyc\n.pytest_cache/\nnode_modules/\n.venv/\ndist/\n"


class FlowGraphError(Exception):
    """The flow graph cannot be executed as defined."""


@dataclass(slots=True)
class ExecutorDeps:
    """Everything the engine needs, injectable for tests."""

    #: ``(user_id, model_id) -> LLMProvider``; raises ModelConfigError.
    provider_resolver: ProviderResolver
    sandbox_factory: SandboxFactory
    workspace: Workspace
    session_factory: sessionmaker[Session]
    bus: RunEventBus
    shell_whitelist: frozenset[str]
    max_steps_per_run: int = 50
    #: ``user_id -> ToolContext`` (credentials for tools). Optional.
    tool_context_factory: Callable[[str | None], ToolContext] | None = None
    #: ``user_id -> {name: Tool}`` of the user's MCP tools. Optional.
    mcp_tools_factory: Callable[[str | None], dict[str, Tool]] | None = None
    #: ``(user_id, upload_id) -> (filename, bytes)`` for ``file`` inputs. Optional.
    upload_reader: Callable[[str | None, str], tuple[str, bytes]] | None = None
    #: Copies ``/repo/output`` files out of the sandbox after the run. Optional.
    artifact_store: Any | None = None
    #: Server caps applied to per-run sandbox options.
    sandbox_caps: SandboxOptions | None = None
    #: ``(user_id, skill_ids) -> (bundles, missing_ids)``; None disables skills.
    skill_loader: Callable[[str | None, list[str]], tuple[list[SkillBundle], list[str]]] | None = (
        None
    )
    #: ``(user_id, flow_id, step_id) -> rules prompt block``; None disables rules.
    rule_loader: Callable[[str | None, str | None, str], str] | None = None


@dataclass(slots=True)
class StepOutcome:
    """Result of executing one node once."""

    success: bool
    output: str
    exit_code: int | None = None
    error: str | None = None
    #: Parsed JSON of the output (for ``{{steps.<id>.data...}}``), when applicable.
    data: Any = None


def find_start(nodes: list[Node], edges: list[Edge]) -> Node:
    """The single node with no incoming edges."""
    from app.schemas import start_nodes

    start_ids = set(start_nodes(nodes, edges))
    starts = [n for n in nodes if n.id in start_ids]
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
        self._providers: dict[str | None, LLMProvider] = {}
        self._user_id: str | None = None
        self._flow_id: str | None = None
        self._options: SandboxOptions | None = None
        self._tool_ctx: ToolContext | None = None
        self._mcp_tools: dict[str, Tool] = {}
        self._outputs: list[dict[str, Any]] = []
        self._executed = 0
        #: Pending approval: (step_run_id, future resolved with (decision, comment)).
        self._approval: tuple[str, asyncio.Future[tuple[str, str]]] | None = None
        #: Skill bundles by id, loaded once per run and mounted under /skills.
        self._skills: dict[str, SkillBundle] = {}
        self._attempts: dict[str, int] = {}
        self._context: dict[str, str] = {}

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
            user_id = run.user_id
            flow_id = flow.id
            inputs: dict[str, Any] = dict(run.inputs or {})
            options = SandboxOptions.from_dict(run.sandbox, caps=self.deps.sandbox_caps)
            workspace_id = getattr(flow, "workspace_id", None)
            flow_config = dict(getattr(flow, "config", None) or {})
        self._user_id = user_id
        self._flow_id = flow_id
        self._options = options
        self._context = {
            "task": task,
            "repo": repo_url,
            "branch": branch,
            "input": inputs,
            "steps": {},
            "items": {},
            "prev": {},
        }
        self._tool_ctx = (
            self.deps.tool_context_factory(user_id) if self.deps.tool_context_factory else None
        )
        if self._tool_ctx is not None:
            self._tool_ctx.workspace_id = workspace_id
            self._tool_ctx.flow_config = flow_config
            self._tool_ctx.dry_run = bool(inputs.get("_dry_run"))
        self._mcp_tools = (
            self.deps.mcp_tools_factory(user_id) if self.deps.mcp_tools_factory else {}
        )

        self._set_run(status=RunStatus.running, started_at=datetime.now(UTC))
        self._log("run_status", status=RunStatus.running.value)
        final = RunStatus.failed
        error: str | None = None
        try:
            start = find_start(nodes, edges)
            # Resolve every LLM step's model up front so a misconfigured model
            # fails the run before anything is cloned.
            for n in nodes:
                if n.data.is_llm:
                    self._provider(n.data.model_id)
                    if n.data.fallback_model_id:
                        self._provider(n.data.fallback_model_id)
            if options.enabled:
                _, host_path = await self.deps.workspace.prepare(self.run_id, repo_url, branch)
                self._log(
                    "log",
                    message=f"cloned {repo_url}@{branch}"
                    if repo_url
                    else "created empty workspace",
                )
                self._sandbox = self.deps.sandbox_factory(self.run_id, host_path, options)
                await self._sandbox.start()
                self._log(
                    "log",
                    message=f"sandbox: {options.cpus} cpu, {options.memory_mb} MB, network={options.network}"
                    + (
                        f" ({', '.join(options.allowed_hosts)})"
                        if options.network == "allowlist"
                        else ""
                    )
                    + f", timeout {options.timeout_seconds}s",
                )
                await self._git_setup()
                await self._stage_inputs(inputs)
                await self._mount_skills(nodes, user_id)
            else:
                self._log("log", message="lightweight run: no workspace or sandbox")
            try:
                ok = await asyncio.wait_for(
                    self._traverse(start, nodes, edges, task), timeout=options.timeout_seconds
                )
            except TimeoutError:
                ok = False
                error = f"run exceeded the time limit of {options.timeout_seconds}s"
                self._log("log", level="error", message=error)
            final = RunStatus.success if ok else RunStatus.failed
            if not ok and error is None:
                error = "a step failed"
            await self._capture_diff()
            await self._collect_artifacts()
        except asyncio.CancelledError:
            final, error = RunStatus.cancelled, "cancelled by user"
        except (FlowGraphError, WorkspaceError, ModelConfigError, RuntimeError) as exc:
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
                    run.cost_usd = round(sum(s.cost_usd or 0.0 for s in run.step_runs), 6)
                    run.outputs = self._outputs
                    db.commit()
                    if final is RunStatus.failed:
                        from app.biz import alerts

                        try:
                            alerts.run_failed(db, run)
                        except Exception:
                            log.warning("could not record run alert", exc_info=True)
            self._log("run_status", status=final.value, error=error)
            self.deps.bus.close(self.run_id)
        return final

    def _provider(self, model_id: str | None) -> LLMProvider:
        """Provider for a step's ``model_id`` (``None`` = user default), cached per run."""
        if model_id not in self._providers:
            self._providers[model_id] = self.deps.provider_resolver(self._user_id, model_id)
        return self._providers[model_id]

    async def _teardown(self) -> None:
        if self._sandbox is not None:
            try:
                await asyncio.shield(self._sandbox.stop())
            except Exception:  # pragma: no cover
                log.exception("sandbox stop failed")
            self._sandbox = None
        # The clone is no longer needed: diff and artifacts were captured already.
        if self._options is not None and not self._options.enabled:
            return
        try:
            await asyncio.shield(self.deps.workspace.cleanup(self.run_id))
        except Exception:  # pragma: no cover
            log.exception("workspace cleanup failed")

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

    async def _stage_inputs(self, inputs: dict[str, Any]) -> None:
        """Copy ``file`` inputs into ``/repo/input/<name>`` and record their paths."""
        if self._sandbox is None or not inputs:
            return
        for key, value in list(inputs.items()):
            if not (isinstance(value, dict) and value.get("upload_id")):
                continue
            if self.deps.upload_reader is None:
                raise FlowGraphError("file inputs are not supported in this configuration")
            name, data = self.deps.upload_reader(self._user_id, str(value["upload_id"]))
            path = f"/repo/input/{name}"
            await self._sandbox.write_bytes(path, data)
            inputs[key] = {**value, "name": name, "path": f"input/{name}", "size": len(data)}
            self._log("log", message=f"staged input file {key} -> input/{name} ({len(data)} bytes)")
        self._context["input"] = inputs

    async def _collect_artifacts(self) -> None:
        """Keep files from ``/repo/output`` so they can be downloaded later."""
        if self._sandbox is None or self.deps.artifact_store is None:
            return
        try:
            kept = await self.deps.artifact_store.collect(self.run_id, self._sandbox)
        except Exception as exc:
            log.exception("artifact collection failed")
            self._log("log", level="warning", message=f"artifact collection failed: {exc}")
            return
        if kept:
            self._log("artifacts", artifacts=kept)
            self._log("log", message=f"kept {len(kept)} artifact(s) from output/")

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
        self._graph = (by_id, out_edges)
        return await self._run_sequence(start, task, stop_at=frozenset())

    async def _run_sequence(
        self, start: Node | None, task: str, *, stop_at: frozenset[str]
    ) -> bool:
        """Run nodes from ``start`` following edges until the branch ends.

        ``stop_at`` holds ids where the walk must stop without executing (the
        loop node that owns the current body branch).
        """
        by_id, out_edges = self._graph
        current = start
        while current is not None:
            if self.cancel.is_set():
                raise asyncio.CancelledError
            if current.id in stop_at:
                return True
            self._executed += 1
            if self._executed > self.deps.max_steps_per_run:
                raise FlowGraphError(f"exceeded {self.deps.max_steps_per_run} step executions")

            self._attempts[current.id] = self._attempts.get(current.id, 0) + 1
            attempt = self._attempts[current.id]
            if current.data.type is StepType.loop:
                outcome = await self._run_loop(current, attempt, task)
            else:
                outcome = await self._run_step(current, attempt, task, self._prev_view())
            self._record_step(current, outcome)

            if current.data.type is StepType.condition:
                handle = "true" if outcome.success else "false"
                nxt = [e for e in out_edges[current.id] if e.sourceHandle == handle]
                current = by_id[nxt[0].target] if nxt else None
                continue
            if current.data.type is StepType.loop:
                if not outcome.success:
                    return False
                nxt = [e for e in out_edges[current.id] if e.sourceHandle == "done"]
                current = by_id[nxt[0].target] if nxt else None
                continue

            if outcome.success:
                nxt = out_edges[current.id]
                current = by_id[nxt[0].target] if nxt else None
                continue

            # failure handling
            policy = current.data.on_fail
            budget_left = attempt <= current.data.retry_limit
            if policy == "retry" and budget_left:
                self._log(
                    "log",
                    step_id=current.id,
                    message=f"retrying ({attempt}/{current.data.retry_limit})",
                )
                continue
            target = current.data.goto_target
            if target is not None and budget_left:
                self._log("log", step_id=current.id, message=f"on_fail -> goto {target}")
                current = by_id[target]
                continue
            return False
        return True

    def _prev_view(self) -> StepResultView | None:
        prev = self._context.get("prev") or {}
        if not prev:
            return None
        return StepResultView(
            step_id=str(prev.get("step_id", "")),
            type=str(prev.get("type", "")),
            success=bool(prev.get("success")),
            output=str(prev.get("output") or ""),
            exit_code=prev.get("exit_code"),
        )

    def _record_step(self, node: Node, outcome: StepOutcome) -> None:
        """Expose the step to later steps as ``steps.<id>`` / ``prev`` and stream variables."""
        entry = {
            "step_id": node.id,
            "type": node.data.type.value,
            "success": outcome.success,
            "status": "success" if outcome.success else "failed",
            "output": outcome.output,
            "exit_code": outcome.exit_code,
            "error": outcome.error,
            "data": outcome.data if outcome.data is not None else parse_json(outcome.output),
        }
        self._context["steps"][node.id] = entry
        self._context["prev"] = entry
        self._log("variables", variables=_snapshot(self._context))

    async def _run_loop(self, node: Node, attempt: int, task: str) -> StepOutcome:
        """Run the ``body`` branch once per item; collect each iteration's last output."""
        by_id, out_edges = self._graph
        with self._session() as db:
            sr = StepRun(
                run_id=self.run_id,
                step_id=node.id,
                step_type=node.data.type.value,
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
            type="loop",
            attempt=attempt,
            label=node.data.label,
        )
        started = time.monotonic()
        try:
            items = resolve_items(node.data.items_expression or "", self._context)
        except (TypeError, ValueError) as exc:
            outcome = StepOutcome(False, "", error=str(exc))
            self._finish_step(
                step_run_id, StepRunStatus.failed, f"[error] {outcome.error}", started, None
            )
            self._log(
                "step_end",
                step_id=node.id,
                step_run_id=step_run_id,
                status="failed",
                duration=0.0,
                error=outcome.error,
                output="",
            )
            return outcome
        body = [e for e in out_edges[node.id] if e.sourceHandle == "body"]
        results: list[Any] = []
        failures = 0
        saved_items = self._context.get("items") or {}
        for index, item in enumerate(items):
            self._context["items"] = {"current": item, "index": index, "total": len(items)}
            self._log(
                "log",
                step_id=node.id,
                step_run_id=step_run_id,
                message=f"loop iteration {index + 1}/{len(items)}",
            )
            if body:
                ok = await self._run_sequence(
                    by_id[body[0].target], task, stop_at=frozenset({node.id})
                )
            else:
                ok = True
            last = self._context.get("prev") or {}
            results.append(last.get("data") if last.get("data") is not None else last.get("output"))
            if not ok:
                failures += 1
                if node.data.on_fail == "stop":
                    break
        self._context["items"] = saved_items
        success = failures == 0 or node.data.on_fail != "stop"
        output = json.dumps(results, ensure_ascii=False, default=str)
        error = None if failures == 0 else f"{failures} of {len(items)} iterations failed"
        status = StepRunStatus.success if success else StepRunStatus.failed
        duration = self._finish_step(
            step_run_id,
            status,
            output if error is None else f"{output}\n[error] {error}",
            started,
            None,
        )
        self._log(
            "step_end",
            step_id=node.id,
            step_run_id=step_run_id,
            status=status.value,
            duration=duration,
            error=error,
            output=output[-2000:],
        )
        return StepOutcome(success, output, data=results, error=error)

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

        agent: AgentResult | None = None
        try:
            if data.type is StepType.condition:
                outcome = self._run_condition(data.expression, prev)
            elif data.type is StepType.shell:
                outcome = await self._run_shell(data.command or "")
            elif data.type is StepType.approval:
                outcome = await self._run_approval(node, step_run_id)
            elif not data.is_llm:
                outcome = await self._run_node(node)
            else:
                agent = await self._run_llm_step(node, task, prev, step_run_id)
                outcome = StepOutcome(agent.success, agent.output, error=agent.error)
        except asyncio.CancelledError:
            self._finish_step(step_run_id, StepRunStatus.failed, "cancelled", started, agent)
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
            step_run_id, status, output, started, agent, self._step_skills(node.data.skill_ids)
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

    async def _run_llm_step(
        self, node: Node, task: str, prev: StepResultView | None, step_run_id: str
    ) -> AgentResult:
        """Run the agent loop; on a provider failure switch to the fallback model once."""
        data = node.data
        kwargs: dict[str, Any] = {
            "system_prompt": render(
                system_prompt_for(
                    data.type,
                    data.system_prompt,
                    skills=self._skills_block(data.skill_ids),
                    rules=self._rules_block(node.id),
                ),
                self._variables(node, prev),
            ),
            "user_message": _user_message(
                task, prev, data.system_prompt, self._context.get("input") or {}
            ),
            "tools_allowed": list(data.tools_allowed),
            "max_turns": data.max_turns,
            "executor": ToolExecutor(
                self._sandbox,
                self.deps.shell_whitelist,
                ctx=self._tool_ctx,
                extra_tools=self._mcp_tools,
            ),
            "log": lambda ev, **d: self._log(ev, step_id=node.id, step_run_id=step_run_id, **d),
            "cancel": self.cancel,
        }
        primary = self._provider(data.model_id)
        self._log(
            "log", step_id=node.id, step_run_id=step_run_id, message=f"model: {primary.label}"
        )
        try:
            return await run_agent(primary, **kwargs)
        except ProviderError as exc:
            if not data.fallback_model_id:
                return AgentResult(
                    success=False, output="", model=primary.label, error=f"provider error: {exc}"
                )
            fallback = self._provider(data.fallback_model_id)
            self._log(
                "log",
                step_id=node.id,
                step_run_id=step_run_id,
                level="warning",
                message=f"{primary.label} failed ({exc}); falling back to {fallback.label}",
            )
            try:
                return await run_agent(fallback, **kwargs)
            except ProviderError as exc2:
                return AgentResult(
                    success=False,
                    output="",
                    model=fallback.label,
                    error=f"provider error after fallback: {exc2}",
                )

    def _finish_step(
        self,
        step_run_id: str,
        status: StepRunStatus,
        output: str,
        started: float,
        agent: AgentResult | None,
        skills: list[dict[str, str]] | None = None,
    ) -> float:
        duration = round(time.monotonic() - started, 3)
        with self._session() as db:
            sr = db.get(StepRun, step_run_id)
            if sr is not None and skills:
                sr.skills = skills
            if sr is not None:
                sr.status = status
                sr.output = output
                sr.duration = duration
                sr.finished_at = datetime.now(UTC)
                if agent is not None:
                    sr.messages = agent.messages
                    sr.tool_calls = agent.tool_calls
                    sr.tokens = agent.tokens
                    sr.model = agent.model or None
                    sr.input_tokens = agent.input_tokens
                    sr.output_tokens = agent.output_tokens
                    sr.cost_usd = (
                        estimate_cost(agent.model, agent.input_tokens, agent.output_tokens)
                        if agent.model
                        else None
                    )
                db.commit()
        return duration

    def _variables(self, node: Node, prev: StepResultView | None) -> dict[str, Any]:
        """Values for ``{{task}}``, ``{{prev_output}}``, ``{{input.x}}``, ``{{steps.x.output}}``, ``{{items.current}}``..."""
        return {
            **self._context,
            "prev_output": prev.output if prev is not None else "",
            "step_id": node.id,
        }

    def resolve_approval(self, decision: str, comment: str = "") -> bool:
        """Called by the API when a person approves/rejects. False if nothing is pending."""
        if self._approval is None:
            return False
        _, fut = self._approval
        if fut.done():
            return False
        fut.set_result((decision, comment))
        return True

    @property
    def pending_approval(self) -> str | None:
        """step_run_id waiting for a decision, if any."""
        return self._approval[0] if self._approval and not self._approval[1].done() else None

    async def _run_approval(self, node: Node, step_run_id: str) -> StepOutcome:
        """Pause until someone approves or rejects (or the wait times out)."""
        question = render(
            node.data.system_prompt or "Approve to continue?",
            self._variables(node, self._prev_view()),
        )
        timeout = max(30, int(node.data.approval_timeout_seconds or 86_400))
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[tuple[str, str]] = loop.create_future()
        self._approval = (step_run_id, fut)
        with self._session() as db:
            sr = db.get(StepRun, step_run_id)
            run = db.get(Run, self.run_id)
            if sr is not None:
                sr.status = StepRunStatus.waiting
            if run is not None:
                run.status = RunStatus.waiting
            db.commit()
        self._log("run_status", status=RunStatus.waiting.value)
        self._log(
            "approval_requested",
            step_id=node.id,
            step_run_id=step_run_id,
            question=question,
            prev_output=(self._context.get("prev") or {}).get("output", ""),
            timeout_seconds=timeout,
        )
        try:
            decision, comment = await asyncio.wait_for(fut, timeout=timeout)
        except TimeoutError:
            self._approval = None
            self._set_run(status=RunStatus.running)
            self._log("run_status", status=RunStatus.running.value)
            return StepOutcome(
                False, "", error=f"no decision within {timeout}s", data={"decision": "timeout"}
            )
        finally:
            self._approval = None
        self._set_run(status=RunStatus.running)
        self._log("run_status", status=RunStatus.running.value)
        self._log(
            "approval_resolved",
            step_id=node.id,
            step_run_id=step_run_id,
            decision=decision,
            comment=comment,
        )
        data = {"decision": decision, "comment": comment}
        if decision == "approve":
            return StepOutcome(True, comment or "approved", data=data)
        return StepOutcome(False, comment or "rejected", error="rejected by reviewer", data=data)

    # ---- skills -------------------------------------------------------------------

    async def _mount_skills(self, nodes: list[Node], user_id: str | None) -> None:
        """Load every skill referenced by a step and copy it into /skills/<name>/."""
        ids: list[str] = []
        for n in nodes:  # any node may reference skills (scripts can read /skills/<name>/ too)
            for sid in n.data.skill_ids:
                if sid not in ids:
                    ids.append(sid)
        if not ids or self.deps.skill_loader is None:
            return
        bundles, missing = self.deps.skill_loader(user_id, ids)
        for sid in missing:
            self._log("log", level="warning", message=f"skill {sid} not found; ignored")
        sandbox = self._sandbox_required()
        probe = await sandbox.exec(["mkdir", "-p", "--", SKILLS_MOUNT], timeout=30)
        if probe.exit_code != 0:
            self._log(
                "log",
                level="error",
                message=f"cannot create {SKILLS_MOUNT} in the sandbox (rebuild the sandbox image): "
                + probe.stderr.strip(),
            )
            return
        for b in bundles:
            self._skills[b.id] = b
            await sandbox.write_bytes(
                f"{b.mount_path}/SKILL.md",
                f"# {b.name}\n\n{b.description}\n\n{b.content}".encode(),
            )
            for path, blob in b.files:
                await sandbox.write_bytes(f"{b.mount_path}/{path}", blob)
        if bundles:
            self._log(
                "log",
                message="skills mounted: "
                + ", ".join(f"{b.name} ({len(b.files)} files)" for b in bundles),
            )

    def _rules_block(self, step_id: str) -> str:
        if self.deps.rule_loader is None:
            return ""
        return self.deps.rule_loader(self._user_id, self._flow_id, step_id)

    def _step_skills(self, ids: list[str]) -> list[dict[str, str]]:
        return [{"name": b.name, "kind": b.kind} for b in (self._skills.get(i) for i in ids) if b]

    def _skills_block(self, ids: list[str]) -> str:
        return skills_prompt_block([self._skills[i] for i in ids if i in self._skills])

    def _node_env(self) -> NodeEnv:
        ctx = self._tool_ctx or ToolContext()
        return NodeEnv(
            sandbox=self._sandbox,
            tool_ctx=ctx,
            shell_whitelist=self.deps.shell_whitelist,
            mcp_tools=self._mcp_tools,
        )

    async def _run_node(self, node: Node) -> StepOutcome:
        """Execute one of the non-LLM node types."""
        kind = node.data.type
        context = self._variables(node, self._prev_view())
        env = self._node_env()
        result: NodeResult
        if kind is StepType.data_transform:
            result = await run_data_transform(node, context, env)
        elif kind is StepType.http_request:
            self._log(
                "tool_call",
                step_id=node.id,
                tool="http_request",
                input={
                    "method": node.data.method or "GET",
                    "url": render(node.data.url or "", context),
                },
            )
            result = await run_http_request(node, context, env)
        elif kind is StepType.merge:
            result = run_merge(node, context)
        elif kind is StepType.template:
            result = await run_template(node, context, env)
        elif kind is StepType.output:
            result, record = await run_output(node, context, env)
            if record:
                self._outputs = [o for o in self._outputs if o.get("step_id") != node.id] + [record]
                self._log("output", step_id=node.id, output=record)
        else:
            return StepOutcome(False, "", error=f"unsupported node type {kind.value}")
        return StepOutcome(
            result.success,
            result.output,
            exit_code=result.exit_code,
            error=result.error,
            data=result.data,
        )

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


def _snapshot(context: dict[str, Any], limit: int = 4000) -> dict[str, Any]:
    """Context copy with long strings trimmed, safe to stream to the UI."""

    def trim(v: Any, depth: int = 0) -> Any:
        if isinstance(v, str):
            return v if len(v) <= limit else v[:limit] + f"… [{len(v) - limit} more chars]"
        if isinstance(v, dict):
            return {k: trim(x, depth + 1) for k, x in v.items()} if depth < 6 else "{…}"
        if isinstance(v, list):
            return (
                [trim(x, depth + 1) for x in v[:50]] + (["…"] if len(v) > 50 else [])
                if depth < 6
                else "[…]"
            )
        return v

    return {k: trim(v) for k, v in context.items()}


def _user_message(
    task: str,
    prev: StepResultView | None,
    system_prompt: str = "",
    inputs: dict[str, Any] | None = None,
) -> str:
    """Task, inputs and previous output. Sections already injected via
    ``{{...}}`` in the system prompt are omitted so they are not sent twice."""
    from app.executor.variables import used_variables

    used = used_variables(system_prompt)
    parts: list[str] = []
    if "task" not in used:
        parts.append(f"## Task\n{task.strip() or '(no task description given)'}")
    if inputs and not any(u == "input" or u.startswith("input.") for u in used):
        lines = []
        for k, v in inputs.items():
            if isinstance(v, dict) and v.get("path"):
                lines.append(f"- {k}: file at {v['path']}")
            elif v is not None and v != "":
                text = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
                lines.append(f"- {k}: {text[:2000]}")
        if lines:
            parts.append("## Inputs\n" + "\n".join(lines))
    if prev is not None and "prev_output" not in used:
        status = "succeeded" if prev.success else "FAILED"
        parts.append(
            f"## Previous step `{prev.step_id}` ({prev.type}) {status}\n{prev.output.strip() or '(no output)'}"
        )
    return "\n\n".join(parts) or "Proceed with the instructions in the system prompt."
