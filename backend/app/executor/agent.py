"""The per-step agent loop: call Claude, execute tools, repeat until done."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from anthropic import AsyncAnthropic

from app.executor.tools import ToolError, ToolExecutor, tool_definitions

LogFn = Callable[..., None]
"""``log(event, **data)`` callback used to stream progress."""

MAX_TOKENS = 16_000
FALLBACK_BETA = "server-side-fallback-2026-07-01"


@dataclass(slots=True)
class AgentResult:
    """Everything recorded about one agent loop."""

    success: bool
    output: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    tokens: int = 0
    error: str | None = None


@dataclass(slots=True)
class AgentConfig:
    """Model configuration shared by all steps of a run."""

    model: str
    use_fallbacks: bool = True
    effort: str | None = None


def _text_of(content: list[Any]) -> str:
    return "\n".join(b.text for b in content if getattr(b, "type", None) == "text").strip()


async def run_agent(
    client: AsyncAnthropic,
    config: AgentConfig,
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

    The loop ends when the model stops calling tools (``end_turn``), when it is
    refused or truncated, when ``max_turns`` is hit, or when ``cancel`` is set.
    All messages and tool calls are recorded on the returned :class:`AgentResult`.
    """
    messages: list[dict[str, Any]] = [{"role": "user", "content": user_message}]
    result = AgentResult(success=False, output="", messages=messages)
    tools = tool_definitions(tools_allowed)

    for turn in range(1, max_turns + 1):
        if cancel is not None and cancel.is_set():
            result.error = "cancelled"
            return result

        kwargs: dict[str, Any] = {
            "model": config.model,
            "max_tokens": MAX_TOKENS,
            "system": system_prompt,
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools
        if config.effort:
            kwargs["output_config"] = {"effort": config.effort}
        if config.use_fallbacks:
            kwargs["betas"] = [FALLBACK_BETA]
            kwargs["fallbacks"] = "default"

        response = await client.beta.messages.create(**kwargs)
        usage = response.usage
        result.tokens += int(getattr(usage, "input_tokens", 0) or 0) + int(
            getattr(usage, "output_tokens", 0) or 0
        )

        assistant_content = response.model_dump(mode="json")["content"]
        messages.append({"role": "assistant", "content": assistant_content})

        text = _text_of(response.content)
        if text:
            log("text", turn=turn, text=text)

        stop = response.stop_reason
        if stop == "refusal":
            details = getattr(response, "stop_details", None)
            cat = getattr(details, "category", None) if details else None
            result.error = f"model refused the request ({cat or 'unspecified'})"
            result.output = text
            return result
        if stop == "max_tokens":
            result.error = "response truncated (max_tokens)"
            result.output = text
            return result
        if stop == "pause_turn":
            continue

        tool_uses = [b for b in response.content if getattr(b, "type", None) == "tool_use"]
        if stop != "tool_use" or not tool_uses:
            result.success = True
            result.output = text
            return result

        tool_results: list[dict[str, Any]] = []
        for tu in tool_uses:
            raw_input = tu.input if isinstance(tu.input, dict) else json.loads(str(tu.input))
            log("tool_call", turn=turn, tool=tu.name, input=raw_input, tool_use_id=tu.id)
            started = time.monotonic()
            try:
                if tu.name not in tools_allowed:
                    raise ToolError(f"tool {tu.name!r} is not allowed in this step")
                output = await executor.call(tu.name, raw_input)
                is_error = False
            except ToolError as exc:
                output, is_error = f"Error: {exc}", True
            except TypeError as exc:  # bad arguments from the model
                output, is_error = f"Error: invalid arguments: {exc}", True
            duration = round(time.monotonic() - started, 3)
            record = {
                "turn": turn,
                "tool": tu.name,
                "input": raw_input,
                "output": output,
                "is_error": is_error,
                "duration": duration,
            }
            result.tool_calls.append(record)
            log("tool_result", **record, tool_use_id=tu.id)
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": tu.id,
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
