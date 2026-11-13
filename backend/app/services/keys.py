"""Stored API keys: lookup for the executor and live validation against providers."""

from __future__ import annotations

import logging

import httpx
from anthropic import AsyncAnthropic, AuthenticationError, PermissionDeniedError
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.crypto import DecryptError, decrypt
from app.models import ApiKey, KeyProvider

log = logging.getLogger(__name__)


def get_user_key(db: Session, user_id: str, provider: KeyProvider) -> str | None:
    """Decrypted value of the user's newest key for ``provider``, if any."""
    row = db.scalar(
        select(ApiKey)
        .where(ApiKey.user_id == user_id, ApiKey.provider == provider)
        .order_by(ApiKey.created_at.desc(), ApiKey.id.desc())
    )
    if row is None:
        return None
    try:
        return decrypt(row.encrypted_value)
    except DecryptError:
        log.error("api key %s for user %s cannot be decrypted", row.id, user_id)
        return None


def key_lookup(session_factory: sessionmaker[Session], provider: KeyProvider):
    """Build a ``(user_id) -> key | None`` callable for the executor."""

    def lookup(user_id: str | None) -> str | None:
        if not user_id:
            return None
        with session_factory() as db:
            return get_user_key(db, user_id, provider)

    return lookup


async def test_key(provider: KeyProvider, value: str) -> tuple[bool, str]:
    """Make the cheapest authenticated request to ``provider`` and report the outcome."""
    try:
        if provider is KeyProvider.anthropic:
            client = AsyncAnthropic(api_key=value, max_retries=0, timeout=15.0)
            page = await client.models.list(limit=1)
            first = next(iter(page.data), None)
            return True, f"OK, models available (e.g. {first.id})" if first else "OK"
        if provider is KeyProvider.openai:
            async with httpx.AsyncClient(timeout=15.0) as http:
                r = await http.get(
                    "https://api.openai.com/v1/models", headers={"Authorization": f"Bearer {value}"}
                )
            return (True, "OK") if r.status_code == 200 else (False, f"rejected ({r.status_code})")
        if provider is KeyProvider.github:
            async with httpx.AsyncClient(timeout=15.0) as http:
                r = await http.get(
                    "https://api.github.com/user",
                    headers={
                        "Authorization": f"Bearer {value}",
                        "Accept": "application/vnd.github+json",
                    },
                )
            if r.status_code == 200:
                return True, f"OK, authenticated as {r.json().get('login', '?')}"
            return False, f"rejected ({r.status_code})"
    except (AuthenticationError, PermissionDeniedError) as exc:
        return False, f"rejected: {exc.__class__.__name__}"
    except httpx.HTTPError as exc:
        return False, f"network error: {exc.__class__.__name__}"
    except Exception as exc:  # noqa: BLE001 - never let a provider error leak a key
        log.warning("key test failed: %s", exc.__class__.__name__)
        return False, f"error: {exc.__class__.__name__}"
    return False, "unsupported provider"
