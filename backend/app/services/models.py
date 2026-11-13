"""Resolve which :class:`LLMProvider` a step should use and build it."""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.crypto import DecryptError, decrypt
from app.llm import (
    AnthropicProvider,
    GenericOpenAICompatibleProvider,
    LLMProvider,
    ModelParams,
    OllamaProvider,
    OpenAIProvider,
)
from app.models import ApiKey, KeyProvider, Model, ModelProvider
from app.services.keys import get_user_key

NO_KEY_ERROR = (
    "No Anthropic API key configured. Add one under Settings → API keys "
    "(or set ANTHROPIC_API_KEY on the server)."
)

ProviderResolver = Callable[[str | None, str | None], LLMProvider]
"""``(user_id, model_id) -> provider``; raises :class:`ModelConfigError`."""


class ModelConfigError(Exception):
    """The requested model cannot be used (missing, foreign, or no credentials)."""


_KEY_FOR_PROVIDER = {
    ModelProvider.anthropic: KeyProvider.anthropic,
    ModelProvider.openai: KeyProvider.openai,
    ModelProvider.openai_compatible: KeyProvider.other,
}


def _api_key_for(db: Session, settings: Settings, row: Model) -> str | None:
    """Explicit key on the model, else the user's newest key for that provider, else env."""
    if row.api_key_id:
        key = db.get(ApiKey, row.api_key_id)
        if key is None or key.user_id != row.user_id:
            raise ModelConfigError(f"model {row.label!r}: its API key no longer exists")
        try:
            return decrypt(key.encrypted_value)
        except DecryptError as exc:
            raise ModelConfigError(f"model {row.label!r}: stored key cannot be decrypted") from exc
    kp = _KEY_FOR_PROVIDER.get(row.provider)
    if kp is not None:
        stored = get_user_key(db, row.user_id, kp)
        if stored:
            return stored
    if row.provider is ModelProvider.anthropic:
        return settings.anthropic_api_key or None
    if row.provider is ModelProvider.openai:
        return settings.openai_api_key or None
    return None


def build_provider(row: Model, api_key: str | None, settings: Settings) -> LLMProvider:
    """Instantiate the provider class for a Model row."""
    params = ModelParams.from_dict(row.params)
    if row.provider is ModelProvider.anthropic:
        if not api_key:
            raise ModelConfigError(f"model {row.label!r}: no Anthropic API key available")
        return AnthropicProvider(
            row.model_name,
            api_key=api_key,
            params=params,
            use_fallbacks=settings.anthropic_fallbacks,
        )
    if row.provider is ModelProvider.openai:
        if not api_key:
            raise ModelConfigError(f"model {row.label!r}: no OpenAI API key available")
        return OpenAIProvider(row.model_name, api_key=api_key, params=params)
    if row.provider is ModelProvider.ollama:
        return OllamaProvider(row.model_name, base_url=row.base_url, params=params)
    if row.provider is ModelProvider.openai_compatible:
        if not row.base_url:
            raise ModelConfigError(f"model {row.label!r}: base_url is required")
        return GenericOpenAICompatibleProvider(
            row.model_name, base_url=row.base_url, api_key=api_key, params=params
        )
    raise ModelConfigError(f"unsupported provider {row.provider}")  # pragma: no cover


def implicit_default(db: Session, settings: Settings, user_id: str | None) -> LLMProvider:
    """Server default (Anthropic + settings model) when the user configured nothing."""
    key = get_user_key(db, user_id, KeyProvider.anthropic) if user_id else None
    key = key or settings.anthropic_api_key or None
    if not key:
        raise ModelConfigError(NO_KEY_ERROR)
    return AnthropicProvider(
        settings.anthropic_model,
        api_key=key,
        params=ModelParams(effort=settings.anthropic_effort or None),
        use_fallbacks=settings.anthropic_fallbacks,
    )


def resolve_provider(
    db: Session, settings: Settings, user_id: str | None, model_id: str | None
) -> LLMProvider:
    """Provider for ``model_id`` (must belong to the user) or the user's default."""
    row: Model | None = None
    if model_id:
        row = db.get(Model, model_id)
        if row is None or row.user_id != user_id:
            raise ModelConfigError(f"model {model_id!r} not found")
    elif user_id:
        row = db.scalar(select(Model).where(Model.user_id == user_id, Model.is_default.is_(True)))
    if row is None:
        return implicit_default(db, settings, user_id)
    return build_provider(row, _api_key_for(db, settings, row), settings)


def make_resolver(session_factory: sessionmaker[Session], settings: Settings) -> ProviderResolver:
    """Bind :func:`resolve_provider` to a session factory for the executor."""

    def _resolve(user_id: str | None, model_id: str | None) -> LLMProvider:
        with session_factory() as db:
            return resolve_provider(db, settings, user_id, model_id)

    return _resolve
