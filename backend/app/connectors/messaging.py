"""Inbound events → conversations; outbound replies through the right channel + 24h policy."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.biz import service as biz
from app.biz.models import (
    Channel,
    Conversation,
    Customer,
    Direction,
    Message,
    MessageStatus,
    Sender,
)
from app.biz.phone import decrypt_phone
from app.connectors import facebook, zalo
from app.connectors.base import ConnectorError, SendResult
from app.connectors.models import Connector, ConnectorKind
from app.connectors.policy import FB_TAGS, within_window

CHANNEL_KIND = {Channel.zalo: ConnectorKind.zalo, Channel.facebook: ConnectorKind.facebook}


def connector_for(db: Session, workspace_id: str, kind: ConnectorKind) -> Connector | None:
    return db.scalar(
        select(Connector).where(Connector.workspace_id == workspace_id, Connector.kind == kind)
    )


def record_inbound(
    db: Session, workspace_id: str, channel: Channel, event: dict[str, Any]
) -> Message | None:
    """Store an inbound message; create the conversation (and a stub customer) on first contact.

    Returns None when the same external message id was already stored (webhook retries).
    """
    thread_id = str(event["thread_id"])
    conv = db.scalar(
        select(Conversation).where(
            Conversation.workspace_id == workspace_id,
            Conversation.channel == channel,
            Conversation.external_thread_id == thread_id,
        )
    )
    if conv is None:
        field = Customer.zalo_id if channel is Channel.zalo else Customer.fb_psid
        cust = db.scalar(
            select(Customer).where(Customer.workspace_id == workspace_id, field == thread_id)
        )
        if cust is None:
            label = "Zalo" if channel is Channel.zalo else "Facebook"
            cust = Customer(
                workspace_id=workspace_id,
                name=f"Khách {label} {thread_id[-4:]}",
                source=channel.value,
                tags=["chưa xác minh"],
            )
            if channel is Channel.zalo:
                cust.zalo_id = thread_id
            else:
                cust.fb_psid = thread_id
            db.add(cust)
            db.flush()
        conv = Conversation(
            workspace_id=workspace_id,
            channel=channel,
            external_thread_id=thread_id,
            customer_id=cust.id,
        )
        db.add(conv)
        db.flush()
    ext = event.get("external_id") or None
    if (
        ext
        and db.scalar(
            select(Message.id).where(Message.conversation_id == conv.id, Message.external_id == ext)
        )
        is not None
    ):
        return None
    at = event.get("at") or datetime.now(UTC)
    m = Message(
        workspace_id=workspace_id,
        conversation_id=conv.id,
        direction=Direction.inbound,
        sender=Sender.customer,
        status=MessageStatus.sent,
        text=str(event.get("text") or ""),
        external_id=ext,
        created_at=at,
    )
    conv.last_inbound_at = at
    conv.last_message_at = at
    conv.unread = True
    db.add(m)
    db.flush()
    return m


class PolicyError(ConnectorError):
    """The 24h window is closed and no fallback (ZNS template / tag) was given."""


async def deliver(
    db: Session,
    conv: Conversation,
    text: str,
    *,
    zns_template_id: str | None = None,
    zns_params: dict[str, Any] | None = None,
    fb_tag: str | None = None,
    now: datetime | None = None,
) -> SendResult:
    """Send ``text`` to the conversation's customer honouring the 24h rules.

    Zalo: free CS message inside the window, otherwise a ZNS template to the phone.
    Facebook: RESPONSE inside the window, otherwise a Meta message tag is required.
    """
    kind = CHANNEL_KIND[conv.channel]
    c = connector_for(db, conv.workspace_id, kind)
    if c is None or not c.enabled:
        raise ConnectorError("Kênh này chưa được kết nối (vào mục Kết nối để thiết lập).")
    open_window = within_window(conv.last_inbound_at, now)
    if conv.channel is Channel.zalo:
        if open_window:
            return await zalo.send_text(c, conv.external_thread_id, text)
        if not zns_template_id:
            raise PolicyError(
                "Khách nhắn lần cuối hơn 24 giờ trước: Zalo chỉ cho gửi bằng mẫu ZNS (chọn mẫu, tính phí)."
            )
        cust = db.get(Customer, conv.customer_id) if conv.customer_id else None
        phone = decrypt_phone(cust.phone_encrypted) if cust else ""
        if not phone:
            raise PolicyError("Khách chưa có số điện thoại nên không gửi được ZNS.")
        return await zalo.send_zns(c, phone, zns_template_id, zns_params or {})
    if open_window:
        return await facebook.send_text(c, conv.external_thread_id, text)
    if not fb_tag:
        raise PolicyError(
            "Khách nhắn lần cuối hơn 24 giờ trước: Messenger yêu cầu gắn tag ("
            + ", ".join(FB_TAGS)
            + ")."
        )
    return await facebook.send_text(c, conv.external_thread_id, text, tag=fb_tag)


def record_outbound(
    db: Session,
    conv: Conversation,
    text: str,
    sender: Sender,
    result: SendResult | None,
    status: MessageStatus = MessageStatus.sent,
) -> Message:
    m = Message(
        workspace_id=conv.workspace_id,
        conversation_id=conv.id,
        direction=Direction.outbound,
        sender=sender,
        status=status,
        text=text,
        external_id=(result.external_id if result else None) or None,
    )
    now = datetime.now(UTC)
    conv.last_message_at = now
    conv.unread = False
    db.add(m)
    db.flush()
    return m


def window_state(conv: Conversation, now: datetime | None = None) -> dict[str, Any]:
    """For the UI: whether a free reply is possible and what the fallback is."""
    open_window = within_window(conv.last_inbound_at, now)
    last = biz.aware(conv.last_inbound_at) if conv.last_inbound_at else None
    return {
        "open": open_window,
        "closes_at": (last.isoformat() if last else None) and None,
        "fallback": None if open_window else ("zns" if conv.channel is Channel.zalo else "tag"),
    }
