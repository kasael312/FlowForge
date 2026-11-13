"""Provider-agnostic LLM interface.

The canonical message format used across FlowForge is Anthropic-shaped
(``role`` + ``content`` blocks of type ``text`` / ``tool_use`` / ``tool_result``),
because that is what the executor already persists. Each provider converts to
and from its own wire format inside :meth:`LLMProvider.complete`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal

StopReason = Literal["end_turn", "tool_use", "max_tokens", "refusal"]

Message = dict[str, Any]
"""``{"role": "user"|"assistant", "content": str | list[block]}``."""


@dataclass(slots=True)
class ToolSpec:
    """A tool exposed to the model (JSON-schema input)."""

    name: str
    description: str
    input_schema: dict[str, Any]

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ToolSpec:
        return cls(
            name=d["name"], description=d.get("description", ""), input_schema=d["input_schema"]
        )


@dataclass(slots=True)
class ToolCall:
    """A tool invocation requested by the model."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(slots=True)
class Usage:
    """Token counts of one completion."""

    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(slots=True)
class Completion:
    """Normalised result of one model call."""

    text: str
    tool_calls: list[ToolCall]
    stop_reason: StopReason
    usage: Usage
    #: Canonical assistant content blocks to append to the conversation.
    content: list[dict[str, Any]] = field(default_factory=list)
    model: str = ""
    #: Provider-specific refusal category or similar, for logs.
    detail: str | None = None


class ProviderError(Exception):
    """A provider call failed. ``retryable`` hints that a fallback model may succeed."""

    def __init__(self, message: str, *, status: int | None = None, retryable: bool = True) -> None:
        super().__init__(message)
        self.status = status
        self.retryable = retryable


class ProviderAuthError(ProviderError):
    """Invalid or missing credentials."""

    def __init__(self, message: str = "authentication failed", *, status: int | None = 401) -> None:
        super().__init__(message, status=status, retryable=True)


class RateLimitError(ProviderError):
    """429 / quota exhausted."""

    def __init__(self, message: str = "rate limited", *, status: int | None = 429) -> None:
        super().__init__(message, status=status, retryable=True)


@dataclass(slots=True)
class ModelParams:
    """User-tunable sampling parameters (not every provider honours all of them)."""

    temperature: float | None = None
    max_tokens: int | None = None
    #: Anthropic effort level (``low`` ... ``max``).
    effort: str | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> ModelParams:
        d = d or {}
        t = d.get("temperature")
        m = d.get("max_tokens")
        return cls(
            temperature=float(t) if t is not None else None,
            max_tokens=int(m) if m is not None else None,
            effort=str(d["effort"]) if d.get("effort") else None,
        )


class LLMProvider(ABC):
    """Interface every provider implements."""

    #: Provider family name, e.g. ``"anthropic"``.
    kind: str = "base"

    def __init__(self, model: str, params: ModelParams | None = None) -> None:
        self.model = model
        self.params = params or ModelParams()

    @property
    def label(self) -> str:
        """``kind/model`` for logs and step records."""
        return f"{self.kind}/{self.model}"

    @abstractmethod
    async def complete(
        self,
        messages: list[Message],
        *,
        tools: list[ToolSpec] | None = None,
        system: str = "",
        max_tokens: int = 16_000,
    ) -> Completion:
        """One model turn. Raises :class:`ProviderError` on transport/API failure."""

    @abstractmethod
    async def ping(self) -> str:
        """Cheapest authenticated request; returns a human-readable success message."""


def text_of_content(content: list[dict[str, Any]]) -> str:
    """Concatenate ``text`` blocks of canonical content."""
    return "\n".join(b.get("text", "") for b in content if b.get("type") == "text").strip()
