"""Connector settings (``/biz/connectors``) and the public channel webhooks (``/hooks/zalo``, ``/hooks/facebook``)."""

from __future__ import annotations

import json
import logging
import secrets
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.biz import alerts
from app.biz import service as biz
from app.biz.models import Appointment, Channel, Conversation
from app.config import get_settings
from app.connectors import facebook, google_calendar, messaging, zalo
from app.connectors.base import ConnectorError, secrets_of, set_secrets
from app.connectors.models import Connector, ConnectorKind, ConnectorStatus
from app.connectors.policy import FB_TAGS
from app.db import get_db
from app.schemas import ConnectorOut, ConnectorUpsert
from app.workspace import OwnerWorkspace, WorkspaceContext

log = logging.getLogger(__name__)
router = APIRouter(tags=["connectors"])
DbSession = Annotated[Session, Depends(get_db)]
MAX_WEBHOOK_BYTES = 1024 * 1024

#: Which secret keys each kind accepts (anything else is dropped).
SECRET_KEYS: dict[ConnectorKind, tuple[str, ...]] = {
    ConnectorKind.zalo: ("access_token", "refresh_token", "app_secret"),
    ConnectorKind.facebook: ("page_access_token", "app_secret"),
    ConnectorKind.google_calendar: ("client_id", "client_secret", "refresh_token"),
}
CONFIG_KEYS: dict[ConnectorKind, tuple[str, ...]] = {
    ConnectorKind.zalo: ("app_id", "oa_id", "zns_templates"),
    ConnectorKind.facebook: ("page_id",),
    ConnectorKind.google_calendar: ("calendar_id",),
}


def _base_url(request: Request) -> str:
    return get_settings().public_base_url.rstrip("/") or str(request.base_url).rstrip("/")


def _out(c: Connector, request: Request) -> ConnectorOut:
    s = secrets_of(c)
    base = _base_url(request)
    webhook = None
    if c.kind is ConnectorKind.zalo:
        webhook = f"{base}/hooks/zalo/{c.webhook_token}"
    elif c.kind is ConnectorKind.facebook:
        webhook = f"{base}/hooks/facebook/{c.webhook_token}"
    return ConnectorOut(
        id=c.id,
        kind=c.kind,
        enabled=c.enabled,
        config=dict(c.config or {}),
        secret_keys=sorted(s.keys()),
        webhook_url=webhook,
        verify_token=c.webhook_token if c.kind is ConnectorKind.facebook else None,
        status=c.status,
        last_checked_at=c.last_checked_at,
        last_error=c.last_error,
        last_event_at=c.last_event_at,
        updated_at=c.updated_at,
    )


def _clean_config(kind: ConnectorKind, cfg: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k in CONFIG_KEYS[kind]:
        if k not in cfg:
            continue
        v = cfg[k]
        if k == "zns_templates":
            tpls = []
            for t in v or []:
                if not isinstance(t, dict) or not str(t.get("id") or "").strip():
                    continue
                tpls.append(
                    {
                        "id": str(t["id"]).strip()[:64],
                        "name": str(t.get("name") or "")[:100],
                        "price": float(t.get("price") or 0),
                        "params": [str(p)[:50] for p in (t.get("params") or [])][:20],
                    }
                )
            out[k] = tpls[:50]
        else:
            out[k] = str(v or "")[:200]
    return out


# ---------------------------------------------------------------- settings


@router.get("/biz/connectors", response_model=list[ConnectorOut])
def list_connectors(request: Request, ctx: OwnerWorkspace, db: DbSession) -> list[ConnectorOut]:
    rows = db.scalars(
        select(Connector).where(Connector.workspace_id == ctx.id).order_by(Connector.kind)
    ).all()
    return [_out(c, request) for c in rows]


@router.get("/biz/connectors/meta")
def connector_meta(_ctx: OwnerWorkspace) -> dict[str, Any]:
    """Static info for the UI: secret/config keys per kind and Meta tags."""
    return {
        "kinds": [k.value for k in ConnectorKind],
        "secret_keys": {k.value: list(v) for k, v in SECRET_KEYS.items()},
        "config_keys": {k.value: list(v) for k, v in CONFIG_KEYS.items()},
        "fb_tags": FB_TAGS,
    }


@router.put("/biz/connectors/{kind}", response_model=ConnectorOut)
def upsert_connector(
    kind: ConnectorKind, body: ConnectorUpsert, request: Request, ctx: OwnerWorkspace, db: DbSession
) -> ConnectorOut:
    """Create or update the connector of ``kind`` (owner only). Empty secret values keep the stored ones."""
    c = messaging.connector_for(db, ctx.id, kind)
    if c is None:
        c = Connector(
            workspace_id=ctx.id, kind=kind, webhook_token=secrets.token_urlsafe(24), config={}
        )
        db.add(c)
    if body.enabled is not None:
        c.enabled = body.enabled
    if body.config is not None:
        c.config = {**(c.config or {}), **_clean_config(kind, body.config)}
    if body.secrets is not None:
        allowed = SECRET_KEYS[kind]
        set_secrets(
            c, {k: (v if v is None else str(v)) for k, v in body.secrets.items() if k in allowed}
        )
    if body.rotate_webhook_token:
        c.webhook_token = secrets.token_urlsafe(24)
    db.commit()
    db.refresh(c)
    return _out(c, request)


@router.delete("/biz/connectors/{kind}", status_code=status.HTTP_204_NO_CONTENT)
def delete_connector(kind: ConnectorKind, ctx: OwnerWorkspace, db: DbSession) -> None:
    c = messaging.connector_for(db, ctx.id, kind)
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "connector not found")
    db.delete(c)
    db.commit()


@router.post("/biz/connectors/{kind}/test", response_model=ConnectorOut)
async def test_connector(
    kind: ConnectorKind, request: Request, ctx: OwnerWorkspace, db: DbSession
) -> ConnectorOut:
    """Call the platform with the stored credentials and record the result."""
    c = messaging.connector_for(db, ctx.id, kind)
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "connector not found")
    tester = {
        ConnectorKind.zalo: zalo.test_connection,
        ConnectorKind.facebook: facebook.test_connection,
        ConnectorKind.google_calendar: google_calendar.test_connection,
    }[kind]
    result = await tester(c)
    c.status = ConnectorStatus.connected if result.ok else ConnectorStatus.error
    c.last_error = None if result.ok else result.message
    c.last_checked_at = datetime.now(UTC)
    db.commit()
    if not result.ok:
        alerts.connector_error(db, ctx.id, kind.value, result.message)
    db.refresh(c)
    out = _out(c, request)
    out.test_message = result.message
    out.test_details = result.details
    return out


@router.post("/biz/connectors/google_calendar/sync")
async def sync_calendar(ctx: OwnerWorkspace, db: DbSession) -> dict[str, Any]:
    """Pull changes from Google, then push every upcoming appointment that is not mirrored yet."""
    c = messaging.connector_for(db, ctx.id, ConnectorKind.google_calendar)
    if c is None or not c.enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Google Calendar chưa được kết nối")
    tz = ctx.workspace.timezone
    try:
        pulled = await google_calendar.pull_events(db, c, ctx.id, tz)
        pushed = 0
        upcoming = db.scalars(
            select(Appointment).where(
                Appointment.workspace_id == ctx.id,
                Appointment.google_event_id.is_(None),
                Appointment.status.in_(google_calendar.SYNC_STATUSES),
                Appointment.end_at >= datetime.now(UTC),
            )
        ).all()
        for a in upcoming:
            await google_calendar.push_appointment(db, c, a, tz)
            pushed += 1
        c.status, c.last_error, c.last_checked_at = (
            ConnectorStatus.connected,
            None,
            datetime.now(UTC),
        )
        db.commit()
    except ConnectorError as exc:
        c.status, c.last_error, c.last_checked_at = (
            ConnectorStatus.error,
            str(exc),
            datetime.now(UTC),
        )
        db.commit()
        alerts.connector_error(db, ctx.id, ConnectorKind.google_calendar.value, str(exc))
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    return {**pulled, "pushed": pushed}


# ---------------------------------------------------------------- public webhooks


def _after_inbound(db: Session, workspace_id: str, message: Any) -> dict[str, Any] | None:
    """Hand the new customer message to the industry automations (AI reply / handoff)."""
    if message is None:
        return None
    from app.biz import automations
    from app.models import Workspace
    from app.services.runs import run_manager

    ws = db.get(Workspace, workspace_id)
    conv = db.get(Conversation, message.conversation_id)
    if ws is None or conv is None:
        return None
    try:
        return automations.on_inbound(db, ws, conv, message, run_manager.launch)
    except Exception as exc:
        log.exception("automation on_inbound failed")
        return {"action": "error", "detail": str(exc)}


def _by_token(db: Session, kind: ConnectorKind, token: str) -> Connector:
    c = db.scalar(select(Connector).where(Connector.kind == kind, Connector.webhook_token == token))
    if c is None or not c.enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown webhook")
    return c


async def _raw(request: Request) -> bytes:
    body = await request.body()
    if len(body) > MAX_WEBHOOK_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "payload too large")
    return body


@router.post("/hooks/zalo/{token}", status_code=status.HTTP_200_OK)
async def zalo_webhook(token: str, request: Request, db: DbSession) -> dict[str, Any]:
    c = _by_token(db, ConnectorKind.zalo, token)
    raw = await _raw(request)
    try:
        payload = json.loads(raw or b"{}")
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid JSON") from exc
    if not zalo.verify_signature(
        c, raw, request.headers.get("x-zevent-signature"), str(payload.get("timestamp", ""))
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "bad signature")
    c.last_event_at = datetime.now(UTC)
    event = zalo.parse_event(payload)
    stored = messaging.record_inbound(db, c.workspace_id, Channel.zalo, event) if event else None
    db.commit()
    outcome = _after_inbound(db, c.workspace_id, stored)
    return {"ok": True, "stored": stored is not None, "automation": outcome}


@router.get("/hooks/facebook/{token}")
def facebook_verify(token: str, request: Request, db: DbSession) -> PlainTextResponse:
    """Meta's subscription handshake: echo ``hub.challenge`` when the verify token matches."""
    c = _by_token(db, ConnectorKind.facebook, token)
    q = request.query_params
    if (
        q.get("hub.mode") == "subscribe"
        and q.get("hub.verify_token") == c.webhook_token
        and q.get("hub.challenge")
    ):
        return PlainTextResponse(q["hub.challenge"])
    raise HTTPException(status.HTTP_403_FORBIDDEN, "verification failed")


@router.post("/hooks/facebook/{token}", status_code=status.HTTP_200_OK)
async def facebook_webhook(token: str, request: Request, db: DbSession) -> dict[str, Any]:
    c = _by_token(db, ConnectorKind.facebook, token)
    raw = await _raw(request)
    if not facebook.verify_signature(c, raw, request.headers.get("x-hub-signature-256")):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "bad signature")
    try:
        payload = json.loads(raw or b"{}")
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid JSON") from exc
    c.last_event_at = datetime.now(UTC)
    stored = 0
    outcomes = []
    for ev in facebook.parse_events(payload):
        m = messaging.record_inbound(db, c.workspace_id, Channel.facebook, ev)
        if m is not None:
            stored += 1
            db.commit()
            outcomes.append(_after_inbound(db, c.workspace_id, m))
    db.commit()
    return {"ok": True, "stored": stored, "automation": outcomes}


def tz_of_ctx(ctx: WorkspaceContext) -> str:
    return str(biz.tz_of(ctx.workspace.timezone))
