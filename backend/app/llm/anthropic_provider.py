"""Anthropic provider (Claude) on top of the official SDK (beta messages API)."""

from __future__ import annotations

from typing import Any

import anthropic
from anthropic import AsyncAnthropic

from app.llm.base import (
    Completion,
    LLMProvider,
    Message,
    ModelParams,
    ProviderAuthError,
    ProviderError,
    RateLimitError,
    ToolCall,
    ToolSpec,
    Usage,
    text_of_content,
)

FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AnthropicProvider(LLMProvider):
    """Claude via ``client.beta.messages.create`` with server-side refusal fallbacks."""

    kind = "anthropic"

    def __init__(
        self,
        model: str,
        *,
        api_key: str | None = None,
        params: ModelParams | None = None,
        use_fallbacks: bool = True,
        client: AsyncAnthropic | None = None,
    ) -> None:
        super().__init__(model, params)
        self.use_fallbacks = use_fallbacks
        self.client = client or AsyncAnthropic(api_key=api_key, max_retries=2)

    def _tools(self, tools: list[ToolSpec]) -> list[dict[str, Any]]:
        out = []
        for t in tools:
            spec: dict[str, Any] = {
                "name": t.name,
                "description": t.description,
                "input_schema": t.input_schema,
            }
            schema = t.input_schema
            if schema.get("additionalProperties") is False and "required" in schema:
                spec["strict"] = True
            out.append(spec)
        return out

    async def complete(
        self,
        messages: list[Message],
        *,
        tools: list[ToolSpec] | None = None,
        system: str = "",
        max_tokens: int = 16_000,
    ) -> Completion:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.params.max_tokens or max_tokens,
            "messages": messages,
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = self._tools(tools)
        if self.params.effort:
            kwargs["output_config"] = {"effort": self.params.effort}
        if self.use_fallbacks:
            kwargs["betas"] = [FALLBACK_BETA]
            kwargs["fallbacks"] = "default"
        # Claude 5.x rejects sampling params; temperature is intentionally not sent.
        try:
            response = await self.client.beta.messages.create(**kwargs)
        except anthropic.AuthenticationError as exc:
            raise ProviderAuthError(str(exc)) from exc
        except anthropic.RateLimitError as exc:
            raise RateLimitError(str(exc)) from exc
        except anthropic.APIStatusError as exc:
            raise ProviderError(
                str(exc), status=exc.status_code, retryable=exc.status_code >= 500
            ) from exc
        except anthropic.APIConnectionError as exc:
            raise ProviderError(f"connection error: {exc}", retryable=True) from exc

        content = response.model_dump(mode="json")["content"]
        calls = [
            ToolCall(id=b["id"], name=b["name"], arguments=b.get("input") or {})
            for b in content
            if b.get("type") == "tool_use"
        ]
        stop = response.stop_reason
        detail = None
        if stop == "refusal":
            details = getattr(response, "stop_details", None)
            detail = getattr(details, "category", None) if details else None
        reason = (
            "tool_use"
            if stop == "tool_use" and calls
            else "refusal"
            if stop == "refusal"
            else "max_tokens"
            if stop == "max_tokens"
            else "end_turn"
        )
        usage = response.usage
        return Completion(
            text=text_of_content(content),
            tool_calls=calls,
            stop_reason=reason,
            usage=Usage(int(usage.input_tokens or 0), int(usage.output_tokens or 0)),
            content=content,
            model=response.model,
            detail=detail,
        )

    async def ping(self) -> str:
        try:
            page = await self.client.models.list(limit=1)
        except anthropic.AuthenticationError as exc:
            raise ProviderAuthError(str(exc)) from exc
        except anthropic.APIError as exc:
            raise ProviderError(str(exc)) from exc
        first = next(iter(page.data), None)
        return f"OK, models available (e.g. {first.id})" if first else "OK"
