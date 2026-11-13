"""Triggers per flow, the public webhook endpoint, and run-input uploads."""

from __future__ import annotations

import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import CurrentUser
from app.config import get_settings
from app.db import get_db
from app.models import Trigger, TriggerType
from app.routers.flows import get_owned_flow
from app.schemas import RunOut, TriggerCreate, TriggerOut, TriggerUpdate, UploadOut
from app.services import triggers as svc
from app.services.runs import run_manager
from app.services.uploads import UploadError, store_upload

router = APIRouter(tags=["triggers"])
DbSession = Annotated[Session, Depends(get_db)]


def _base_url(request: Request) -> str:
    return get_settings().public_base_url.rstrip("/") or str(request.base_url).rstrip("/")


def _out(t: Trigger, request: Request, reveal: bool = False) -> TriggerOut:
    """``reveal`` includes the webhook secret (only right after create / rotate)."""
    out = TriggerOut.model_validate(t)
    if t.type in (TriggerType.webhook, TriggerType.github):
        out.webhook = svc.describe_webhook(
            t, _base_url(request), svc.get_secret(t) if reveal else None
        )
    if t.type is TriggerType.file_watch:
        out.watch_folder = str(svc.watch_folder(get_settings(), t).resolve())
    return out


def _owned(db: Session, user_id: str, trigger_id: str) -> Trigger:
    t = db.get(Trigger, trigger_id)
    if t is None or t.user_id != user_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "trigger not found")
    return t


@router.get("/flows/{flow_id}/triggers", response_model=list[TriggerOut])
def list_triggers(
    flow_id: str, request: Request, user: CurrentUser, db: DbSession
) -> list[TriggerOut]:
    get_owned_flow(db, user, flow_id)
    rows = db.scalars(
        select(Trigger).where(Trigger.flow_id == flow_id).order_by(Trigger.created_at)
    ).all()
    return [_out(t, request) for t in rows]


@router.post(
    "/flows/{flow_id}/triggers", response_model=TriggerOut, status_code=status.HTTP_201_CREATED
)
def create_trigger(
    flow_id: str, body: TriggerCreate, request: Request, user: CurrentUser, db: DbSession
) -> TriggerOut:
    """Create a trigger. Webhook/GitHub triggers get a secret; file_watch gets a folder."""
    get_owned_flow(db, user, flow_id)
    if body.type is TriggerType.manual:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "manual runs don't need a trigger"
        )
    try:
        cfg = svc.validate_config(body.type, body.config)
    except (svc.TriggerError, ValueError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    t = Trigger(
        flow_id=flow_id,
        user_id=user.id,
        type=body.type,
        name=body.name.strip(),
        config=cfg,
        enabled=body.enabled,
    )
    if body.type in (TriggerType.webhook, TriggerType.github):
        svc.set_secret(t, svc.new_secret())
    db.add(t)
    db.commit()
    db.refresh(t)
    if body.type is TriggerType.file_watch:
        svc.scan_folder(svc.watch_folder(get_settings(), t), "*")  # creates the folder
    return _out(t, request, reveal=True)


@router.put("/triggers/{trigger_id}", response_model=TriggerOut)
def update_trigger(
    trigger_id: str, body: TriggerUpdate, request: Request, user: CurrentUser, db: DbSession
) -> TriggerOut:
    t = _owned(db, user.id, trigger_id)
    if body.name is not None:
        t.name = body.name.strip()
    if body.config is not None:
        try:
            t.config = svc.validate_config(t.type, body.config)
        except (svc.TriggerError, ValueError) as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    if body.enabled is not None:
        t.enabled = body.enabled
    if body.rotate_secret and t.type in (TriggerType.webhook, TriggerType.github):
        svc.set_secret(t, svc.new_secret())
    db.commit()
    db.refresh(t)
    return _out(t, request, reveal=body.rotate_secret)


@router.delete("/triggers/{trigger_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_trigger(trigger_id: str, user: CurrentUser, db: DbSession) -> None:
    db.delete(_owned(db, user.id, trigger_id))
    db.commit()


@router.post(
    "/triggers/{trigger_id}/fire", response_model=RunOut, status_code=status.HTTP_202_ACCEPTED
)
async def fire_trigger(trigger_id: str, user: CurrentUser, db: DbSession) -> Any:
    """Start a run from this trigger's defaults right now (test button)."""
    t = _owned(db, user.id, trigger_id)
    try:
        return svc.fire(db, t, starter=run_manager.launch)
    except svc.TriggerError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc


@router.post("/hooks/{trigger_id}", status_code=status.HTTP_202_ACCEPTED)
async def webhook(trigger_id: str, request: Request, db: DbSession) -> dict[str, Any]:
    """Public endpoint: HMAC-signed POST starts a run of the trigger's flow.

    Signature: ``X-FlowForge-Signature: sha256=<hex hmac-sha256(secret, raw body)>``
    (GitHub's ``X-Hub-Signature-256`` works the same), or ``Authorization: Bearer <secret>``.
    Returns 404 for unknown/disabled triggers and 401 for bad signatures; never
    reveals which one is the case beyond the status code.
    """
    t = db.get(Trigger, trigger_id)
    if t is None or not t.enabled or t.type not in (TriggerType.webhook, TriggerType.github):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
    body = await request.body()
    if len(body) > 1_000_000:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "payload too large")
    secret = svc.get_secret(t)
    if not secret or not svc.verify_signature(secret, body, dict(request.headers)):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid signature")
    payload: Any = None
    if body:
        try:
            payload = json.loads(body)
        except ValueError:
            payload = body.decode(errors="replace")
    try:
        inputs, task = svc.webhook_inputs(t, payload, dict(request.headers))
        run = svc.fire(db, t, starter=run_manager.launch, inputs=inputs, task=task)
    except svc.TriggerError as exc:
        t.last_error = str(exc)
        db.commit()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    return {"run_id": run.id, "status": run.status.value}


@router.post("/uploads", response_model=UploadOut, status_code=status.HTTP_201_CREATED)
async def upload(file: UploadFile, user: CurrentUser, db: DbSession) -> Any:
    """Upload a file to use as a ``file`` input of a run."""
    data = await file.read()
    try:
        return store_upload(
            db,
            get_settings(),
            user.id,
            file.filename or "file",
            data,
            file.content_type or "application/octet-stream",
        )
    except UploadError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
