"""Numbers for the owner's "Tổng quan" page."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.biz import service as biz
from app.biz.automation_models import Notification
from app.biz.models import (
    Appointment,
    AppointmentStatus,
    Conversation,
    Customer,
    Membership,
    MembershipStatus,
    Message,
    MessageStatus,
    Sender,
)
from app.models import Workspace

MINUTES_PER_AI_MESSAGE = 1.5


def overview(db: Session, ws: Workspace, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    tz = biz.tz_of(ws.timezone)
    local = now.astimezone(tz)
    day_lo = datetime.combine(local.date(), time.min, tzinfo=tz).astimezone(UTC)
    day_hi = day_lo + timedelta(days=1)
    week_lo = now - timedelta(days=7)
    month_lo = now - timedelta(days=30)

    today = db.scalars(
        select(Appointment).where(
            Appointment.workspace_id == ws.id,
            Appointment.start_at >= day_lo,
            Appointment.start_at < day_hi,
        )
    ).all()
    past_month = db.scalars(
        select(Appointment).where(
            Appointment.workspace_id == ws.id,
            Appointment.start_at >= month_lo,
            Appointment.start_at < now,
            Appointment.status.in_(
                (AppointmentStatus.arrived, AppointmentStatus.done, AppointmentStatus.no_show)
            ),
        )
    ).all()
    no_show = len([a for a in past_month if a.status is AppointmentStatus.no_show])
    expiring = [
        m
        for m in db.scalars(select(Membership).where(Membership.workspace_id == ws.id)).all()
        if biz.compute_status(m, local.date()) is MembershipStatus.expiring
    ]
    unread = (
        db.scalar(
            select(func.count())
            .select_from(Conversation)
            .where(Conversation.workspace_id == ws.id, Conversation.unread.is_(True))
        )
        or 0
    )
    suggestions = (
        db.scalar(
            select(func.count())
            .select_from(Message)
            .where(Message.workspace_id == ws.id, Message.status == MessageStatus.ai_suggested)
        )
        or 0
    )
    ai_week = (
        db.scalar(
            select(func.count())
            .select_from(Message)
            .where(
                Message.workspace_id == ws.id,
                Message.sender == Sender.ai,
                Message.status.in_((MessageStatus.sent, MessageStatus.handoff)),
                Message.created_at >= week_lo,
            )
        )
        or 0
    )
    new_customers_week = (
        db.scalar(
            select(func.count())
            .select_from(Customer)
            .where(Customer.workspace_id == ws.id, Customer.created_at >= week_lo)
        )
        or 0
    )
    customers = (
        db.scalar(select(func.count()).select_from(Customer).where(Customer.workspace_id == ws.id))
        or 0
    )
    notifications = db.scalars(
        select(Notification)
        .where(Notification.workspace_id == ws.id, Notification.read.is_(False))
        .order_by(Notification.created_at.desc())
        .limit(5)
    ).all()
    upcoming = sorted([a for a in today if a.status in biz.BLOCKING], key=lambda a: a.start_at)
    return {
        "date": local.strftime("%d/%m/%Y"),
        "appointments_today": len(
            [a for a in today if a.status is not AppointmentStatus.cancelled]
        ),
        "arrived_today": len(
            [a for a in today if a.status in (AppointmentStatus.arrived, AppointmentStatus.done)]
        ),
        "no_show_rate": round(no_show / len(past_month), 3) if past_month else None,
        "no_show_30d": no_show,
        "expiring_memberships": len(expiring),
        "unread_conversations": unread,
        "ai_suggestions": suggestions,
        "ai_messages_week": ai_week,
        "hours_saved_week": round(ai_week * MINUTES_PER_AI_MESSAGE / 60, 1),
        "new_customers_week": new_customers_week,
        "customers": customers,
        "upcoming_today": [
            {
                "id": a.id,
                "time": biz.aware(a.start_at).astimezone(tz).strftime("%H:%M"),
                "customer_name": a.customer.name if a.customer else "?",
                "service_name": a.service.name if a.service else "",
                "staff_name": a.staff.name if a.staff else "",
                "status": a.status.value,
            }
            for a in upcoming[:8]
        ],
        "notifications": [
            {
                "id": n.id,
                "kind": n.kind,
                "title": n.title,
                "body": n.body[:300],
                "created_at": n.created_at.isoformat(),
            }
            for n in notifications
        ],
    }
