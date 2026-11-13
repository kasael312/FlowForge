"""Dry-run a single step with simulated inputs (no repository, no sandbox).

LLM steps run one model turn with tools disabled so the prompt can be tested
cheaply; ``condition`` steps evaluate their expression; ``shell`` steps cannot
be tried without a sandbox and are rejected.
"""

from __future__ import annotations

from app.executor.agent import run_agent
from app.executor.conditions import ConditionError, StepResultView, evaluate
from app.executor.prompts import system_prompt_for
from app.executor.sandbox import ExecResult
from app.executor.tools import ToolExecutor
from app.executor.variables import render, used_variables
from app.llm.base import LLMProvider, ProviderError
from app.schemas import Node, StepTryRequest, StepTryResult, StepType


class _NoSandbox:
    """Sandbox stand-in: every command reports that the dry run has no repo."""

    async def start(self) -> None:  # pragma: no cover - never called
        return None

    async def stop(self) -> None:  # pragma: no cover
        return None

    async def exec(self, argv: list[str], **_: object) -> ExecResult:
        return ExecResult(1, "", "dry run: no repository is available when trying a step")


def _prev(req: StepTryRequest) -> StepResultView:
    return StepResultView(
        step_id="previous",
        type="code",
        success=req.prev_success,
        output=req.prev_output,
        exit_code=req.prev_exit_code,
    )


def build_prompts(
    node: Node, req: StepTryRequest, skills: str = "", rules: str = ""
) -> tuple[str, str]:
    """System prompt and user message exactly as the executor would send them."""
    variables = {
        "task": req.task,
        "prev_output": req.prev_output,
        "repo": req.repo,
        "branch": req.branch,
        "step_id": node.id,
        "input": req.inputs,
    }
    system = render(
        system_prompt_for(node.data.type, node.data.system_prompt, skills=skills, rules=rules),
        variables,
    )
    used = used_variables(node.data.system_prompt)
    parts: list[str] = []
    if "task" not in used:
        parts.append(f"## Task\n{req.task.strip() or '(no task description given)'}")
    if "prev_output" not in used:
        status = "succeeded" if req.prev_success else "FAILED"
        parts.append(
            f"## Previous step `previous` (code) {status}\n{req.prev_output.strip() or '(no output)'}"
        )
    user = "\n\n".join(parts) or "Proceed with the instructions in the system prompt."
    return system, user


async def try_step(
    node: Node,
    req: StepTryRequest,
    provider: LLMProvider | None,
    config_error: str | None = None,
    skills: str = "",
    rules: str = "",
) -> StepTryResult:
    """Execute one step in isolation and describe what happened."""
    data = node.data
    if data.type is StepType.condition:
        try:
            value = evaluate(data.expression or "", _prev(req))
        except ConditionError as exc:
            return StepTryResult(
                step_id=node.id, type=data.type, success=False, output="", error=str(exc)
            )
        return StepTryResult(
            step_id=node.id,
            type=data.type,
            success=value,
            output=f"{data.expression or 'success'} -> {value}",
        )
    if data.type is StepType.shell:
        return StepTryResult(
            step_id=node.id,
            type=data.type,
            success=False,
            output="",
            error="shell steps need a repository; start a real run to test them",
        )
    system, user = build_prompts(node, req, skills, rules)
    if provider is None:
        return StepTryResult(
            step_id=node.id,
            type=data.type,
            success=False,
            output="",
            error=config_error or "No model available. Add one under Settings → Models.",
            system_prompt=system,
            user_message=user,
        )
    try:
        result = await run_agent(
            provider,
            system_prompt=system
            + "\n\nThis is a dry run: tools are unavailable, reply with what you would do.",
            user_message=user,
            tools_allowed=[],
            max_turns=1,
            executor=ToolExecutor(_NoSandbox(), frozenset()),
            log=lambda *_a, **_k: None,
        )
    except ProviderError as exc:
        return StepTryResult(
            step_id=node.id,
            type=data.type,
            success=False,
            output="",
            error=f"{provider.label}: {exc}",
            system_prompt=system,
            user_message=user,
        )
    return StepTryResult(
        step_id=node.id,
        type=data.type,
        success=result.success,
        output=result.output,
        error=result.error,
        tokens=result.tokens,
        system_prompt=system,
        user_message=user,
    )
