"""LLM providers behind one interface.

Adding a provider: subclass :class:`LLMProvider` (or
:class:`OpenAICompatibleProvider` if the host speaks the OpenAI dialect),
register it in :data:`PROVIDERS` and add the value to ``ModelProvider`` in
``app.models``.
"""

from app.llm.anthropic_provider import AnthropicProvider
from app.llm.base import (
    Completion,
    LLMProvider,
    ModelParams,
    ProviderAuthError,
    ProviderError,
    RateLimitError,
    ToolCall,
    ToolSpec,
    Usage,
)
from app.llm.openai_compat import (
    GenericOpenAICompatibleProvider,
    OllamaProvider,
    OpenAIProvider,
)

PROVIDERS: dict[str, type[LLMProvider]] = {
    "anthropic": AnthropicProvider,
    "openai": OpenAIProvider,
    "ollama": OllamaProvider,
    "openai_compatible": GenericOpenAICompatibleProvider,
}

__all__ = [
    "PROVIDERS",
    "AnthropicProvider",
    "Completion",
    "GenericOpenAICompatibleProvider",
    "LLMProvider",
    "ModelParams",
    "OllamaProvider",
    "OpenAIProvider",
    "ProviderAuthError",
    "ProviderError",
    "RateLimitError",
    "ToolCall",
    "ToolSpec",
    "Usage",
]
