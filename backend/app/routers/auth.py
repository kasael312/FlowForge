"""Register / login / logout / refresh / me."""

from __future__ import annotations

import time
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import (
    ACCESS_COOKIE,
    REFRESH_COOKIE,
    CurrentUser,
    TokenError,
    clear_auth_cookies,
    hash_password,
    load_user_for_token,
    set_auth_cookies,
    token_version_of,
    verify_password,
)
from app.config import get_settings
from app.db import get_db
from app.models import User
from app.schemas import Credentials, PasswordChange, UserOut


class LoginLimiter:
    """In-memory sliding window of failed logins per (client, email)."""

    def __init__(self) -> None:
        self._failures: dict[tuple[str, str], list[float]] = {}

    def _prune(self, key: tuple[str, str]) -> list[float]:
        window = get_settings().login_window_seconds
        now = time.monotonic()
        kept = [t for t in self._failures.get(key, []) if now - t < window]
        if kept:
            self._failures[key] = kept
        else:
            self._failures.pop(key, None)
        return kept

    def blocked(self, key: tuple[str, str]) -> bool:
        return len(self._prune(key)) >= get_settings().login_max_failures

    def record_failure(self, key: tuple[str, str]) -> None:
        self._failures.setdefault(key, []).append(time.monotonic())

    def reset(self, key: tuple[str, str]) -> None:
        self._failures.pop(key, None)


login_limiter = LoginLimiter()

router = APIRouter(prefix="/auth", tags=["auth"])
DbSession = Annotated[Session, Depends(get_db)]


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def register(body: Credentials, response: Response, db: DbSession) -> User:
    """Create an account and log it in (cookies are set on the response)."""
    policy = get_settings().registration
    if policy == "closed" or (policy == "first_user" and db.scalar(select(User.id)) is not None):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "registration is disabled")
    email = body.email.lower()
    if db.scalar(select(User).where(User.email == email)) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "email already registered")
    first = db.scalar(select(User.id)) is None
    user = User(
        email=email, password_hash=hash_password(body.password), token_version=0, is_admin=first
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    set_auth_cookies(response, user.id, token_version_of(user))
    return user


@router.post("/login", response_model=UserOut)
def login(body: Credentials, request: Request, response: Response, db: DbSession) -> User:
    """Verify credentials and set the auth cookies (rate limited per client + email)."""
    key = (request.client.host if request.client else "?", body.email.lower())
    if login_limiter.blocked(key):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, "too many failed logins; try again later"
        )
    user = db.scalar(select(User).where(User.email == body.email.lower()))
    if user is None or not verify_password(body.password, user.password_hash):
        login_limiter.record_failure(key)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid email or password")
    login_limiter.reset(key)
    set_auth_cookies(response, user.id, token_version_of(user))
    return user


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    response: Response,
    db: DbSession,
    access_token: Annotated[str | None, Cookie(alias=ACCESS_COOKIE)] = None,
) -> None:
    """Clear the cookies and revoke every session of the user (all devices)."""
    clear_auth_cookies(response)
    if access_token:
        try:
            user = load_user_for_token(db, access_token, "access")
        except TokenError:
            return
        user.token_version = token_version_of(user) + 1
        db.commit()


@router.post("/refresh", response_model=UserOut)
def refresh(
    response: Response,
    db: DbSession,
    refresh_token: Annotated[str | None, Cookie(alias=REFRESH_COOKIE)] = None,
) -> User:
    """Rotate the token pair using the refresh cookie."""
    if not refresh_token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "no refresh token")
    try:
        user = load_user_for_token(db, refresh_token, "refresh")
    except TokenError as exc:
        clear_auth_cookies(response)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc
    set_auth_cookies(response, user.id, token_version_of(user))
    return user


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser) -> User:
    """The authenticated user."""
    return user


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(
    body: PasswordChange, response: Response, user: CurrentUser, db: DbSession
) -> None:
    """Change the password after re-checking the current one."""
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "current password is incorrect")
    user.password_hash = hash_password(body.new_password)
    user.token_version = token_version_of(user) + 1  # log out every other session
    db.add(user)
    db.commit()
    set_auth_cookies(response, user.id, token_version_of(user))  # keep this one logged in
