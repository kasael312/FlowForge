"""Password hashing, JWT issuing/validation and the current-user dependency.

Tokens are carried in httpOnly cookies: ``access_token`` (short-lived, sent on
every request) and ``refresh_token`` (long-lived, scoped to ``/auth/refresh``).
A ``Authorization: Bearer <access>`` header is accepted too for API clients.
"""

from __future__ import annotations

import logging
import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Response, status
from sqlalchemy.orm import Session
from starlette.requests import HTTPConnection

from app.config import Settings, get_settings
from app.db import get_db
from app.models import User

log = logging.getLogger(__name__)

ACCESS_COOKIE = "access_token"
REFRESH_COOKIE = "refresh_token"
REFRESH_PATH = "/auth/refresh"
ALGORITHM = "HS256"
TokenType = Literal["access", "refresh"]

_runtime_secret: str | None = None


def jwt_secret(settings: Settings | None = None) -> str:
    """The signing secret; generated once per process when not configured."""
    global _runtime_secret
    s = settings or get_settings()
    if s.jwt_secret:
        return s.jwt_secret
    if _runtime_secret is None:
        _runtime_secret = secrets.token_urlsafe(48)
        log.warning("JWT_SECRET not set; using a random secret (sessions reset on restart)")
    return _runtime_secret


# ---------------------------------------------------------------- passwords


def hash_password(password: str) -> str:
    """bcrypt hash (72-byte input limit is enforced by the schema)."""
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    """Constant-time check of ``password`` against a stored hash."""
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except ValueError:
        return False


# ---------------------------------------------------------------- tokens


def token_version_of(user: User) -> int:
    return int(user.token_version or 0)


def create_token(
    user_id: str,
    token_type: TokenType,
    expires_delta: timedelta | None = None,
    version: int = 0,
) -> str:
    """Sign a JWT for ``user_id``."""
    s = get_settings()
    if expires_delta is None:
        expires_delta = (
            timedelta(minutes=s.access_token_minutes)
            if token_type == "access"
            else timedelta(days=s.refresh_token_days)
        )
    now = datetime.now(UTC)
    payload = {
        "sub": user_id,
        "type": token_type,
        "iat": int(now.timestamp()),
        "exp": int((now + expires_delta).timestamp()),
        "jti": secrets.token_hex(8),
        "ver": version,
    }
    return jwt.encode(payload, jwt_secret(s), algorithm=ALGORITHM)


class TokenError(Exception):
    """Invalid, expired or wrong-type token."""


def decode_claims(token: str, expected_type: TokenType) -> tuple[str, int]:
    """Validate a token and return ``(user_id, token_version)``."""
    try:
        payload = jwt.decode(token, jwt_secret(), algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("token expired") from exc
    except jwt.PyJWTError as exc:
        raise TokenError("invalid token") from exc
    if payload.get("type") != expected_type or not isinstance(payload.get("sub"), str):
        raise TokenError("wrong token type")
    return payload["sub"], int(payload.get("ver") or 0)


def decode_token(token: str, expected_type: TokenType) -> str:
    """Validate a token and return the user id it belongs to."""
    return decode_claims(token, expected_type)[0]


def load_user_for_token(db: Session, token: str, expected_type: TokenType) -> User:
    """Decode ``token`` and return its user; rejects tokens issued before a logout/password change."""
    user_id, version = decode_claims(token, expected_type)
    user = db.get(User, user_id)
    if user is None:
        raise TokenError("user no longer exists")
    if version != token_version_of(user):
        raise TokenError("session revoked")
    return user


def set_auth_cookies(response: Response, user_id: str, version: int = 0) -> None:
    """Issue a fresh access + refresh pair as httpOnly cookies."""
    s = get_settings()
    response.set_cookie(
        ACCESS_COOKIE,
        create_token(user_id, "access", version=version),
        max_age=s.access_token_minutes * 60,
        httponly=True,
        secure=s.cookie_secure,
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        REFRESH_COOKIE,
        create_token(user_id, "refresh", version=version),
        max_age=s.refresh_token_days * 86400,
        httponly=True,
        secure=s.cookie_secure,
        samesite="strict",
        path=REFRESH_PATH,
    )


def clear_auth_cookies(response: Response) -> None:
    """Remove both cookies."""
    s = get_settings()
    response.delete_cookie(
        ACCESS_COOKIE, path="/", httponly=True, secure=s.cookie_secure, samesite="lax"
    )
    response.delete_cookie(
        REFRESH_COOKIE, path=REFRESH_PATH, httponly=True, secure=s.cookie_secure, samesite="strict"
    )


# ---------------------------------------------------------------- dependency


def _extract_access_token(conn: HTTPConnection) -> str | None:
    auth = conn.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return conn.cookies.get(ACCESS_COOKIE)


def get_current_user(conn: HTTPConnection, db: Annotated[Session, Depends(get_db)]) -> User:
    """Resolve the authenticated user or raise 401.

    Works for both HTTP requests and WebSocket handshakes (``HTTPConnection``).
    """
    token = _extract_access_token(conn)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not authenticated")
    try:
        return load_user_for_token(db, token, "access")
    except TokenError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_admin(user: CurrentUser) -> User:
    if not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "admin only")
    return user


AdminUser = Annotated[User, Depends(require_admin)]
