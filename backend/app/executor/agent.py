"""The per-step agent loop: call the model, execute tools, repeat until done.

Provider-agnostic: any :class:`LLMProvider` works, tool calls are normalised
by the provider into :class:`ToolCall` objects.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.executor.tools import ToolError, ToolExecutor
from app.llm.base import LLMProvider, ToolSpec

LogFn = Callable[..., None]
"""``log(event, **data)`` callback used to stream progress."""

MAX_TOKENS = 16_000


@dataclass(slots=True)
class AgentResult:
    """Everything recorded about one agent loop."""

    success: bool
    output: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""
    error: str | None = None

    @property
    def tokens(self) -> int:
        return self.input_tokens + self.output_tokens


async def run_agent(
    provider: LLMProvider,
    *,
    system_prompt: str,
    user_message: str,
    tools_allowed: list[str],
    max_turns: int,
    executor: ToolExecutor,
    log: LogFn,
    cancel: asyncio.Event | None = None,
) -> AgentResult:
    """Run the tool-use loop for one step.

    The loop ends when the model stops calling tools, when it is refused or
    truncated, when ``max_turns`` is hit, or when ``cancel`` is set.
    :class:`ProviderError` propagates to the caller (which may fall back to
    another model). All messages and tool calls are recorded on the result.
    """
    messages: list[dict[str, Any]] = [{"role": "user", "content": user_message}]
    result = AgentResult(success=False, output="", messages=messages, model=provider.label)
    tools = [ToolSpec.from_dict(t) for t in executor.definitions(tools_allowed)]

    for turn in range(1, max_turns + 1):
        if cancel is not None and cancel.is_set():
            result.error = "cancelled"
            return result

        completion = await provider.complete(
            messages, tools=tools or None, system=system_prompt, max_tokens=MAX_TOKENS
        )
        result.input_tokens += completion.usage.input_tokens
        result.output_tokens += completion.usage.output_tokens
        if completion.model:
            result.model = f"{provider.kind}/{completion.model}"

        messages.append({"role": "assistant", "content": completion.content})
        if completion.text:
            log("text", turn=turn, text=completion.text)

        if completion.stop_reason == "refusal":
            result.error = f"model refused the request ({completion.detail or 'unspecified'})"
            result.output = completion.text
            return result
        if completion.stop_reason == "max_tokens":
            result.error = "response truncated (max_tokens)"
            result.output = completion.text
            return result
        if completion.stop_reason != "tool_use" or not completion.tool_calls:
            result.success = True
            result.output = completion.text
            return result

        tool_results: list[dict[str, Any]] = []
        for call in completion.tool_calls:
            log("tool_call", turn=turn, tool=call.name, input=call.arguments, tool_use_id=call.id)
            started = time.monotonic()
            try:
                if call.name not in tools_allowed:
                    raise ToolError(f"tool {call.name!r} is not allowed in this step")
                output = await executor.call(call.name, call.arguments)
                is_error = False
            except ToolError as exc:
                output, is_error = f"Error: {exc}", True
            except TypeError as exc:  # bad arguments from the model
                output, is_error = f"Error: invalid arguments: {exc}", True
            duration = round(time.monotonic() - started, 3)
            record = {
                "turn": turn,
                "tool": call.name,
                "input": call.arguments,
                "output": output,
                "is_error": is_error,
                "duration": duration,
            }
            result.tool_calls.append(record)
            log("tool_result", **record, tool_use_id=call.id)
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": call.id,
                    "content": output,
                    "is_error": is_error,
                }
            )
        messages.append({"role": "user", "content": tool_results})

    result.error = f"max_turns ({max_turns}) exceeded"
    result.output = _last_text(messages)
    return result


def _last_text(messages: list[dict[str, Any]]) -> str:
    for msg in reversed(messages):
        if msg["role"] != "assistant":
            continue
        for block in msg["content"]:
            if isinstance(block, dict) and block.get("type") == "text" and block.get("text"):
                return str(block["text"])
    return ""
