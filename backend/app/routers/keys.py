"""Per-user API key storage. Values are encrypted at rest and never returned."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import CurrentUser
from app.crypto import DecryptError, decrypt, encrypt
from app.db import get_db
from app.models import ApiKey
from app.schemas import ApiKeyCreate, ApiKeyOut, ApiKeyTest, KeyTestResult
from app.services import keys as key_service

router = APIRouter(prefix="/keys", tags=["keys"])
DbSession = Annotated[Session, Depends(get_db)]


def _owned(db: Session, user_id: str, key_id: str) -> ApiKey:
    row = db.get(ApiKey, key_id)
    if row is None or row.user_id != user_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "key not found")
    return row


@router.get("", response_model=list[ApiKeyOut])
def list_keys(user: CurrentUser, db: DbSession) -> list[ApiKey]:
    """The user's keys (metadata only)."""
    return list(
        db.scalars(
            select(ApiKey).where(ApiKey.user_id == user.id).order_by(ApiKey.created_at.desc())
        ).all()
    )


@router.post("", response_model=ApiKeyOut, status_code=status.HTTP_201_CREATED)
def create_key(body: ApiKeyCreate, user: CurrentUser, db: DbSession) -> ApiKey:
    """Store a key encrypted with ``APP_SECRET``."""
    row = ApiKey(
        user_id=user.id,
        provider=body.provider,
        label=body.label.strip(),
        encrypted_value=encrypt(body.value),
        last4=body.value[-4:],
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.delete("/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_key(key_id: str, user: CurrentUser, db: DbSession) -> None:
    """Delete one of the user's keys."""
    db.delete(_owned(db, user.id, key_id))
    db.commit()


@router.post("/test", response_model=KeyTestResult)
async def test_value(body: ApiKeyTest, _user: CurrentUser) -> KeyTestResult:
    """Validate a key against its provider before saving it."""
    ok, message = await key_service.test_key(body.provider, body.value.strip())
    return KeyTestResult(ok=ok, message=message)


@router.post("/{key_id}/test", response_model=KeyTestResult)
async def test_stored(key_id: str, user: CurrentUser, db: DbSession) -> KeyTestResult:
    """Validate an already stored key."""
    row = _owned(db, user.id, key_id)
    try:
        value = decrypt(row.encrypted_value)
    except DecryptError as exc:
        return KeyTestResult(ok=False, message=str(exc))
    ok, message = await key_service.test_key(row.provider, value)
    return KeyTestResult(ok=ok, message=message)
