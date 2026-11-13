"""Per-user LLM model configurations and the pricing table."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.auth import AdminUser, CurrentUser
from app.config import get_settings
from app.db import get_db
from app.llm import ProviderError
from app.llm.pricing import load_pricing, lookup, save_pricing
from app.models import ApiKey, Model, ModelProvider, User
from app.net import UnsafeUrl, check_public_url_sync
from app.schemas import KeyTestResult, ModelCreate, ModelOut, ModelUpdate, PricingTable
from app.services.models import ModelConfigError, _api_key_for, build_provider

router = APIRouter(tags=["models"])
DbSession = Annotated[Session, Depends(get_db)]


def _out(db: Session, m: Model) -> ModelOut:
    last4 = None
    if m.api_key_id:
        key = db.get(ApiKey, m.api_key_id)
        last4 = key.last4 if key else None
    pricing_key = f"{m.provider.value}/{m.model_name}"
    return ModelOut(
        id=m.id,
        provider=m.provider,
        model_name=m.model_name,
        label=m.label,
        base_url=m.base_url,
        api_key_id=m.api_key_id,
        api_key_last4=last4,
        params=m.params or {},
        is_default=bool(m.is_default),
        created_at=m.created_at,
        pricing_key=pricing_key,
        price=lookup(pricing_key),
    )


def _owned(db: Session, user_id: str, model_id: str) -> Model:
    m = db.get(Model, model_id)
    if m is None or m.user_id != user_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "model not found")
    return m


def _check_key(db: Session, user_id: str, api_key_id: str | None, provider: ModelProvider) -> None:
    if api_key_id is None:
        return
    key = db.get(ApiKey, api_key_id)
    if key is None or key.user_id != user_id:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "api_key_id not found")
    if provider is ModelProvider.ollama:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "ollama models do not use an API key"
        )


def _check_base_url(user: User, base_url: str | None) -> None:
    """Non-admins may only point models at public hosts or operator-trusted internal URLs."""
    if not base_url or user.is_admin:
        return
    try:
        check_public_url_sync(base_url)
    except UnsafeUrl as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"base_url: {exc}") from exc


def _set_default(db: Session, user_id: str, model_id: str) -> None:
    db.execute(update(Model).where(Model.user_id == user_id).values(is_default=False))
    db.execute(update(Model).where(Model.id == model_id).values(is_default=True))


@router.get("/models", response_model=list[ModelOut])
def list_models(user: CurrentUser, db: DbSession) -> list[ModelOut]:
    """My models, default first."""
    rows = db.scalars(
        select(Model)
        .where(Model.user_id == user.id)
        .order_by(Model.is_default.desc(), Model.created_at)
    ).all()
    return [_out(db, m) for m in rows]


@router.post("/models", response_model=ModelOut, status_code=status.HTTP_201_CREATED)
def create_model(body: ModelCreate, user: CurrentUser, db: DbSession) -> ModelOut:
    """Add a model. The first model of a user becomes the default automatically."""
    _check_key(db, user.id, body.api_key_id, body.provider)
    _check_base_url(user, body.base_url)
    if body.provider is ModelProvider.openai_compatible and not body.base_url:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "base_url is required for OpenAI-compatible hosts",
        )
    first = db.scalar(select(Model.id).where(Model.user_id == user.id)) is None
    m = Model(
        user_id=user.id,
        provider=body.provider,
        model_name=body.model_name.strip(),
        label=body.label.strip(),
        base_url=body.base_url,
        api_key_id=body.api_key_id,
        params=body.params.model_dump(exclude_none=True),
        is_default=False,
    )
    db.add(m)
    db.flush()
    if body.is_default or first:
        _set_default(db, user.id, m.id)
    db.commit()
    db.refresh(m)
    return _out(db, m)


@router.put("/models/{model_id}", response_model=ModelOut)
def update_model(model_id: str, body: ModelUpdate, user: CurrentUser, db: DbSession) -> ModelOut:
    """Edit a model; ``is_default: true`` moves the default here."""
    m = _owned(db, user.id, model_id)
    if body.api_key_id is not None or "api_key_id" in body.model_fields_set:
        _check_key(db, user.id, body.api_key_id, m.provider)
    if body.base_url is not None:
        _check_base_url(user, body.base_url)
        m.api_key_id = body.api_key_id
    if body.model_name is not None:
        m.model_name = body.model_name.strip()
    if body.label is not None:
        m.label = body.label.strip()
    if "base_url" in body.model_fields_set:
        m.base_url = ModelCreate._url(body.base_url) if body.base_url else None
    if body.params is not None:
        m.params = body.params.model_dump(exclude_none=True)
    if body.is_default:
        _set_default(db, user.id, m.id)
    db.commit()
    db.refresh(m)
    return _out(db, m)


@router.delete("/models/{model_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_model(model_id: str, user: CurrentUser, db: DbSession) -> None:
    """Delete a model. If it was the default, the oldest remaining one becomes default."""
    m = _owned(db, user.id, model_id)
    was_default = bool(m.is_default)
    db.delete(m)
    db.flush()
    if was_default:
        nxt = db.scalar(select(Model).where(Model.user_id == user.id).order_by(Model.created_at))
        if nxt is not None:
            nxt.is_default = True
    db.commit()


@router.post("/models/{model_id}/default", response_model=ModelOut)
def make_default(model_id: str, user: CurrentUser, db: DbSession) -> ModelOut:
    """Make this model the default for steps without an explicit model."""
    m = _owned(db, user.id, model_id)
    _set_default(db, user.id, m.id)
    db.commit()
    db.refresh(m)
    return _out(db, m)


@router.post("/models/{model_id}/test", response_model=KeyTestResult)
async def test_model(model_id: str, user: CurrentUser, db: DbSession) -> KeyTestResult:
    """Build the provider and make its cheapest authenticated call."""
    m = _owned(db, user.id, model_id)
    settings = get_settings()
    try:
        provider = build_provider(m, _api_key_for(db, settings, m), settings)
        return KeyTestResult(ok=True, message=await provider.ping())
    except ModelConfigError as exc:
        return KeyTestResult(ok=False, message=str(exc))
    except ProviderError as exc:
        return KeyTestResult(ok=False, message=str(exc))


@router.get("/pricing", response_model=PricingTable)
def get_pricing(_user: CurrentUser) -> PricingTable:
    """Current USD-per-1M-token table."""
    return PricingTable(prices=load_pricing())


@router.put("/pricing", response_model=PricingTable)
def put_pricing(body: PricingTable, _user: AdminUser) -> PricingTable:
    """Replace the table (shared by all users of this server; admin only)."""
    try:
        return PricingTable(prices=save_pricing(body.prices))
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
