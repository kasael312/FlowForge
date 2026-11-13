"""Symmetric encryption of stored secrets (API keys) with Fernet.

The Fernet key is derived from ``APP_SECRET`` (falling back to the JWT secret)
so that a single configured secret is enough. Rotating the secret makes
previously stored keys unreadable; users must re-enter them.
"""

from __future__ import annotations

import base64
import hashlib
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.auth import jwt_secret
from app.config import get_settings


class DecryptError(Exception):
    """Stored value cannot be decrypted with the current secret."""


@lru_cache
def _fernet() -> Fernet:
    secret = get_settings().app_secret or jwt_secret()
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
    return Fernet(key)


def encrypt(value: str) -> str:
    """Encrypt ``value`` to an opaque token string."""
    return _fernet().encrypt(value.encode()).decode()


def decrypt(token: str) -> str:
    """Decrypt a token produced by :func:`encrypt`."""
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise DecryptError("cannot decrypt stored secret (APP_SECRET changed?)") from exc


def reset_cache() -> None:
    """Forget the derived key (tests that change the secret)."""
    _fernet.cache_clear()
