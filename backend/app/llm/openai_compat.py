"""OpenAI Chat Completions API and everything that speaks the same dialect.

Covers OpenAI itself, Ollama (``/v1`` endpoint) and generic hosts such as
Groq, DeepSeek or OpenRouter. Only ``httpx`` is used so tests can mock the
transport.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import httpx

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
)

OPENAI_BASE_URL = "https://api.openai.com/v1"
OLLAMA_BASE_URL = "http://localhost:11434"


def to_openai_tools(tools: list[ToolSpec]) -> list[dict[str, Any]]:
    """Canonical tool specs -> OpenAI ``function`` tools."""
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.input_schema,
            },
        }
        for t in tools
    ]


def to_openai_messages(system: str, messages: list[Message]) -> list[dict[str, Any]]:
    """Canonical (Anthropic-shaped) history -> OpenAI chat messages."""
    out: list[dict[str, Any]] = []
    if system:
        out.append({"role": "system", "content": system})
    for m in messages:
        role, content = m["role"], m["content"]
        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue
        if role == "assistant":
            text = "\n".join(b.get("text", "") for b in content if b.get("type") == "text").strip()
            calls = [
                {
                    "id": b["id"],
                    "type": "function",
                    "function": {"name": b["name"], "arguments": json.dumps(b.get("input") or {})},
                }
                for b in content
                if b.get("type") == "tool_use"
            ]
            msg: dict[str, Any] = {"role": "assistant", "content": text or None}
            if calls:
                msg["tool_calls"] = calls
            out.append(msg)
            continue
        # user: tool results become `tool` messages, text stays user text
        texts = []
        for b in content:
            if b.get("type") == "tool_result":
                body = b.get("content", "")
                if isinstance(body, list):
                    body = "\n".join(x.get("text", "") for x in body if isinstance(x, dict))
                prefix = "ERROR: " if b.get("is_error") else ""
                out.append(
                    {"role": "tool", "tool_call_id": b["tool_use_id"], "content": f"{prefix}{body}"}
                )
            elif b.get("type") == "text":
                texts.append(b.get("text", ""))
        if texts:
            out.append({"role": "user", "content": "\n".join(texts)})
    return out


def from_openai_message(msg: dict[str, Any]) -> tuple[str, list[ToolCall], list[dict[str, Any]]]:
    """OpenAI assistant message -> (text, tool calls, canonical content blocks)."""
    text = msg.get("content") or ""
    if isinstance(text, list):  # some hosts return content parts
        text = "".join(p.get("text", "") for p in text if isinstance(p, dict))
    blocks: list[dict[str, Any]] = []
    if text:
        blocks.append({"type": "text", "text": text})
    calls: list[ToolCall] = []
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function") or {}
        raw = fn.get("arguments") or "{}"
        try:
            args = json.loads(raw) if isinstance(raw, str) else dict(raw)
        except json.JSONDecodeError:
            args = {"_raw": raw}
        call_id = tc.get("id") or f"call_{uuid.uuid4().hex[:12]}"
        calls.append(ToolCall(id=call_id, name=fn.get("name", ""), arguments=args))
        blocks.append(
            {"type": "tool_use", "id": call_id, "name": fn.get("name", ""), "input": args}
        )
    return text.strip(), calls, blocks


class OpenAICompatibleProvider(LLMProvider):
    """Generic ``/chat/completions`` client (OpenAI, Groq, DeepSeek, OpenRouter, ...)."""

    kind = "openai_compatible"

    def __init__(
        self,
        model: str,
        *,
        base_url: str,
        api_key: str | None = None,
        params: ModelParams | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 600.0,
    ) -> None:
        super().__init__(model, params)
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._http = httpx.AsyncClient(timeout=timeout, transport=transport)

    def _headers(self) -> dict[str, str]:
        h = {"content-type": "application/json"}
        if self.api_key:
            h["authorization"] = f"Bearer {self.api_key}"
        return h

    async def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            r = await self._http.post(f"{self.base_url}{path}", json=body, headers=self._headers())
        except httpx.HTTPError as exc:
            raise ProviderError(
                f"connection error: {exc.__class__.__name__}", retryable=True
            ) from exc
        if r.status_code in (401, 403):
            raise ProviderAuthError(
                f"{self.kind}: authentication failed ({r.status_code})", status=r.status_code
            )
        if r.status_code == 429:
            raise RateLimitError(f"{self.kind}: rate limited")
        if r.status_code >= 400:
            snippet = r.text[:300]
            raise ProviderError(
                f"{self.kind}: HTTP {r.status_code}: {snippet}",
                status=r.status_code,
                retryable=r.status_code >= 500,
            )
        try:
            return r.json()
        except ValueError as exc:
            raise ProviderError(f"{self.kind}: invalid JSON response", retryable=True) from exc

    async def complete(
        self,
        messages: list[Message],
        *,
        tools: list[ToolSpec] | None = None,
        system: str = "",
        max_tokens: int = 16_000,
    ) -> Completion:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": to_openai_messages(system, messages),
            "max_tokens": self.params.max_tokens or max_tokens,
        }
        if self.params.temperature is not None:
            body["temperature"] = self.params.temperature
        if tools:
            body["tools"] = to_openai_tools(tools)
            body["tool_choice"] = "auto"
        data = await self._post("/chat/completions", body)
        choices = data.get("choices") or []
        if not choices:
            raise ProviderError(f"{self.kind}: empty response", retryable=True)
        choice = choices[0]
        text, calls, blocks = from_openai_message(choice.get("message") or {})
        finish = choice.get("finish_reason")
        reason = (
            "tool_use"
            if calls
            else "max_tokens"
            if finish == "length"
            else "refusal"
            if finish == "content_filter"
            else "end_turn"
        )
        usage = data.get("usage") or {}
        return Completion(
            text=text,
            tool_calls=calls,
            stop_reason=reason,
            usage=Usage(
                int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0)
            ),
            content=blocks,
            model=str(data.get("model") or self.model),
        )

    async def ping(self) -> str:
        try:
            r = await self._http.get(f"{self.base_url}/models", headers=self._headers())
        except httpx.HTTPError as exc:
            raise ProviderError(f"connection error: {exc.__class__.__name__}") from exc
        if r.status_code in (401, 403):
            raise ProviderAuthError(status=r.status_code)
        if r.status_code >= 400:
            raise ProviderError(f"HTTP {r.status_code}", status=r.status_code)
        try:
            ids = [m.get("id") for m in (r.json().get("data") or [])]
        except (ValueError, AttributeError):
            ids = []
        if self.model and ids and self.model not in ids:
            return f"OK, but model {self.model!r} is not listed by the host ({len(ids)} models available)"
        return f"OK ({len(ids)} models available)" if ids else "OK"

    async def aclose(self) -> None:
        await self._http.aclose()


class OpenAIProvider(OpenAICompatibleProvider):
    """api.openai.com."""

    kind = "openai"

    def __init__(
        self, model: str, *, api_key: str | None, params: ModelParams | None = None, **kw: Any
    ) -> None:
        super().__init__(model, base_url=OPENAI_BASE_URL, api_key=api_key, params=params, **kw)


class OllamaProvider(OpenAICompatibleProvider):
    """Local Ollama through its OpenAI-compatible ``/v1`` endpoint. No key needed."""

    kind = "ollama"

    def __init__(
        self,
        model: str,
        *,
        base_url: str | None = None,
        params: ModelParams | None = None,
        **kw: Any,
    ) -> None:
        root = (base_url or OLLAMA_BASE_URL).rstrip("/")
        if not root.endswith("/v1"):
            root = f"{root}/v1"
        super().__init__(model, base_url=root, api_key=None, params=params, **kw)


class GenericOpenAICompatibleProvider(OpenAICompatibleProvider):
    """Any host with ``base_url`` + key (Groq, DeepSeek, OpenRouter, vLLM, ...)."""

    kind = "openai_compatible"
