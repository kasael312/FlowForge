"""Build a :class:`ToolContext` for a user: credentials from stored keys + env."""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.models import KeyProvider
from app.services.keys import get_user_key
from app.tools import ToolContext

ENV_FALLBACK: dict[str, Callable[[Settings], str]] = {
    "anthropic": lambda s: s.anthropic_api_key,
    "openai": lambda s: s.openai_api_key,
    "github": lambda s: s.github_token,
}


def credential_resolver(
    session_factory: sessionmaker[Session], settings: Settings, user_id: str | None
) -> Callable[[str], str | None]:
    """``provider -> secret`` with a small per-run cache."""
    cache: dict[str, str | None] = {}

    def resolve(provider: str) -> str | None:
        if provider in cache:
            return cache[provider]
        value: str | None = None
        try:
            kp = KeyProvider(provider)
        except ValueError:
            kp = None
        if kp is not None and user_id:
            with session_factory() as db:
                value = get_user_key(db, user_id, kp)
        if not value and provider in ENV_FALLBACK:
            value = ENV_FALLBACK[provider](settings) or None
        cache[provider] = value
        return value

    return resolve


def build_tool_context(
    session_factory: sessionmaker[Session], settings: Settings, user_id: str | None
) -> ToolContext:
    """Context without a sandbox (tool tests, try-step); the executor sets the sandbox."""
    return ToolContext(
        credential=credential_resolver(session_factory, settings, user_id),
        user_id=user_id,
        shell_whitelist=settings.shell_whitelist_set,
    )
