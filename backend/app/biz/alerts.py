"""Error alerts: lost connectors, failed automations, billing blocks.

Each alert becomes an owner notification (shown on Tổng quan, pushed to the owner's Zalo when
configured) and an operator alert for the admin panel (pushed to ``ALERT_WEBHOOK_URL`` when set).
Alerts with the same key are deduplicated for 24 hours so a flapping connector does not spam.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.biz.automation_models import AdminAlert, Notification
from app.models import Flow, Run, Workspace

log = logging.getLogger(__name__)
DEDUPE_HOURS = 24


def raise_alert(
    db: Session,
    ws_id: str,
    key: str,
    title: str,
    body: str,
    *,
    detail: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> Notification | None:
    """Record the alert for owner + admin; returns ``None`` when a recent duplicate exists."""
    now = now or datetime.now(UTC)
    recent = db.scalar(
        select(AdminAlert.id).where(
            AdminAlert.workspace_id == ws_id,
            AdminAlert.key == key,
            AdminAlert.created_at >= now - timedelta(hours=DEDUPE_HOURS),
        )
    )
    if recent is not None:
        return None
    n = Notification(workspace_id=ws_id, kind="alert", title=title, body=body, created_at=now)
    db.add(n)
    db.add(
        AdminAlert(
            workspace_id=ws_id, key=key, title=title, body=body, detail=detail, created_at=now
        )
    )
    db.commit()
    db.refresh(n)
    _schedule_delivery(ws_id, title, body)
    return n


def _schedule_delivery(ws_id: str, title: str, body: str) -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return  # no event loop (tests / CLI): the alert stays in the database
    loop.create_task(deliver(ws_id, title, body), name=f"alert-{ws_id}")


async def deliver(ws_id: str, title: str, body: str) -> dict[str, bool]:
    """Push to the owner's Zalo and the operator webhook; failures are logged, never raised."""
    from app.config import get_settings
    from app.db import SessionLocal
    from app.tools.industry import _deliver_owner_zalo

    text = f"[FlowForge] {title}\n{body}"
    out = {"owner_zalo": False, "admin_webhook": False}
    try:
        with SessionLocal() as db:
            out["owner_zalo"] = await _deliver_owner_zalo(db, ws_id, text)
    except Exception:
        log.warning("owner zalo alert failed", exc_info=True)
    url = get_settings().alert_webhook_url.strip()
    if url:
        try:
            from app import net

            async with net.guarded_client(timeout=10) as client:
                r = await client.post(
                    url, json={"text": text, "workspace_id": ws_id, "title": title, "body": body}
                )
                out["admin_webhook"] = r.status_code < 300
        except Exception:
            log.warning("admin webhook alert failed", exc_info=True)
    return out


# ------------------------------------------------------------------ specific alerts


def connector_error(db: Session, ws_id: str, kind: str, message: str) -> Notification | None:
    names = {
        "zalo": "Zalo OA",
        "facebook": "Facebook Messenger",
        "google_calendar": "Google Calendar",
    }
    name = names.get(kind, kind)
    return raise_alert(
        db,
        ws_id,
        f"connector:{kind}",
        f"Mất kết nối {name}",
        f"{name} báo lỗi: {message[:300]}\nVào mục Kết nối để kiểm tra lại token / cấu hình. "
        "Trong lúc này tin nhắn qua kênh này không được gửi tự động.",
        detail={"kind": kind, "message": message[:1000]},
    )


def run_failed(db: Session, run: Run) -> Notification | None:
    """A workspace automation failed: tell the owner (plain words) and the operator (with ids)."""
    flow = db.get(Flow, run.flow_id)
    if flow is None or not flow.workspace_id:
        return None
    ws = db.get(Workspace, flow.workspace_id)
    if ws is None:
        return None
    return raise_alert(
        db,
        ws.id,
        f"run:{flow.flow_key or flow.id}",
        f'Việc tự động "{flow.name}" bị lỗi',
        (run.error or "lỗi không rõ")[:300]
        + "\nViệc này sẽ được thử lại ở lần chạy sau; nếu lỗi lặp lại, hãy kiểm tra Kết nối và Model AI.",
        detail={
            "run_id": run.id,
            "flow_id": flow.id,
            "flow_key": flow.flow_key,
            "error": run.error,
        },
    )


def billing_block(db: Session, ws: Workspace, reason: str) -> Notification | None:
    return raise_alert(
        db,
        ws.id,
        "billing:blocked",
        "Trợ lý AI tạm dừng",
        reason,
        detail={"reason": reason},
    )
