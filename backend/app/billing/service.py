"""Subscription state, usage against plan limits, invoices and activation."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.billing.gateways import get_gateway
from app.billing.models import Invoice, InvoiceStatus, Subscription
from app.billing.plans import GRACE_DAYS, PAID_PLANS, PLANS, Plan, get_plan, plan_out
from app.biz.models import Message, MessageStatus, Sender
from app.config import get_settings
from app.models import Workspace

AI_STATUSES = (MessageStatus.sent, MessageStatus.handoff, MessageStatus.ai_suggested)
_ALNUM = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def get_subscription(db: Session, ws: Workspace) -> Subscription:
    """The workspace's subscription; a trial is created lazily from the workspace age."""
    sub = db.scalar(select(Subscription).where(Subscription.workspace_id == ws.id))
    if sub is None:
        days = get_settings().billing_trial_days
        created = _aware(ws.created_at) or datetime.now(UTC)
        sub = Subscription(
            workspace_id=ws.id, plan="trial", trial_ends_at=created + timedelta(days=days)
        )
        db.add(sub)
        db.commit()
        db.refresh(sub)
    return sub


def status_of(sub: Subscription, now: datetime | None = None) -> str:
    """``trial`` | ``active`` | ``past_due`` | ``expired``."""
    now = now or datetime.now(UTC)
    period_end = _aware(sub.current_period_end)
    if period_end is not None:
        if now < period_end:
            return "active"
        if now < period_end + timedelta(days=GRACE_DAYS):
            return "past_due"
        return "expired"
    trial_end = _aware(sub.trial_ends_at) or now
    return "trial" if now < trial_end else "expired"


def effective_plan(sub: Subscription, now: datetime | None = None) -> Plan:
    st = status_of(sub, now)
    if st in ("active", "past_due"):
        return get_plan(sub.plan)
    return PLANS["trial"]


def is_active(db: Session, ws: Workspace, now: datetime | None = None) -> bool:
    return status_of(get_subscription(db, ws), now) != "expired"


def month_start(now: datetime) -> datetime:
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def ai_conversations_this_month(db: Session, ws_id: str, now: datetime | None = None) -> int:
    """Distinct conversations in which the AI wrote (sent, suggested or handed off) this month."""
    now = now or datetime.now(UTC)
    return (
        db.scalar(
            select(func.count(func.distinct(Message.conversation_id))).where(
                Message.workspace_id == ws_id,
                Message.sender == Sender.ai,
                Message.status.in_(AI_STATUSES),
                Message.created_at >= month_start(now),
            )
        )
        or 0
    )


def ai_allowed(db: Session, ws: Workspace, now: datetime | None = None) -> tuple[bool, str]:
    """Whether the AI may start another conversation now, with a Vietnamese reason when not."""
    now = now or datetime.now(UTC)
    sub = get_subscription(db, ws)
    st = status_of(sub, now)
    if st == "expired":
        why = "Gói dùng thử đã hết hạn" if sub.plan == "trial" else "Gói đã hết hạn"
        return (
            False,
            f"{why}: trợ lý AI tạm dừng, nhân viên trả lời tay. Vào Gói & thanh toán để gia hạn.",
        )
    plan = effective_plan(sub, now)
    used = ai_conversations_this_month(db, ws.id, now)
    if used >= plan.ai_conversations_month:
        return False, (
            f"Đã dùng hết {plan.ai_conversations_month} hội thoại AI của gói {plan.name} trong tháng này. "
            "Nâng gói để AI tiếp tục trả lời."
        )
    return True, ""


def state(db: Session, ws: Workspace, now: datetime | None = None) -> dict[str, Any]:
    """Everything the billing page and the admin table show for one workspace."""
    now = now or datetime.now(UTC)
    sub = get_subscription(db, ws)
    st = status_of(sub, now)
    plan = effective_plan(sub, now)
    used = ai_conversations_this_month(db, ws.id, now)
    end = _aware(sub.current_period_end) if sub.current_period_end else _aware(sub.trial_ends_at)
    days_left = max(0, (end - now).days) if end else None
    warnings: list[str] = []
    if st == "trial" and days_left is not None and days_left <= 7:
        warnings.append(
            f"Gói dùng thử còn {days_left} ngày. Chọn gói để không gián đoạn trợ lý AI."
        )
    if st == "past_due":
        warnings.append("Gói đã hết hạn, còn vài ngày ân hạn. Hãy gia hạn để AI không bị tạm dừng.")
    if st == "expired":
        warnings.append("Gói đã hết hạn: trợ lý AI và việc tự động đang tạm dừng.")
    if plan.ai_conversations_month and used >= plan.ai_conversations_month * 0.8:
        warnings.append(f"Đã dùng {used}/{plan.ai_conversations_month} hội thoại AI tháng này.")
    return {
        "plan": plan_out(plan),
        "subscribed_plan": sub.plan,
        "status": st,
        "trial_ends_at": _aware(sub.trial_ends_at).isoformat() if sub.trial_ends_at else None,
        "current_period_end": _aware(sub.current_period_end).isoformat()
        if sub.current_period_end
        else None,
        "days_left": days_left,
        "usage": {
            "ai_conversations": used,
            "ai_conversations_limit": plan.ai_conversations_month,
            "branches": len(ws.branches or []),
            "branches_limit": plan.branches,
            "month": month_start(now).strftime("%m/%Y"),
        },
        "warnings": warnings,
        "blocked": st == "expired" or used >= plan.ai_conversations_month,
    }


# ------------------------------------------------------------------ invoices


def _transfer_code(db: Session) -> str:
    while True:
        code = "FF" + "".join(secrets.choice(_ALNUM) for _ in range(6))
        if db.scalar(select(Invoice.id).where(Invoice.transfer_code == code)) is None:
            return code


def _number(db: Session, now: datetime) -> str:
    prefix = f"FF{now:%Y%m}-"
    n = (
        db.scalar(
            select(func.count()).select_from(Invoice).where(Invoice.number.like(prefix + "%"))
        )
        or 0
    )
    return f"{prefix}{n + 1:04d}"


def create_invoice(
    db: Session,
    ws: Workspace,
    plan_key: str,
    months: int = 1,
    gateway_key: str = "bank_transfer",
    now: datetime | None = None,
) -> Invoice:
    if plan_key not in PAID_PLANS:
        raise ValueError("chỉ có thể mua gói Cơ bản hoặc Pro")
    months = max(1, min(12, int(months)))
    now = now or datetime.now(UTC)
    plan = get_plan(plan_key)
    gateway = get_gateway(gateway_key)
    inv = Invoice(
        workspace_id=ws.id,
        number=_number(db, now),
        plan=plan_key,
        months=months,
        amount_vnd=plan.price_vnd * months,
        gateway=gateway.key,
        transfer_code=_transfer_code(db),
        created_at=now,
    )
    pay = gateway.create_payment(inv)
    inv.payment = {"method": pay.method, "instructions": pay.instructions, **pay.details}
    db.add(inv)
    db.commit()
    db.refresh(inv)
    return inv


def pending_invoice(db: Session, ws_id: str) -> Invoice | None:
    return db.scalar(
        select(Invoice)
        .where(Invoice.workspace_id == ws_id, Invoice.status == InvoiceStatus.pending)
        .order_by(Invoice.created_at.desc())
    )


def confirm_invoice(
    db: Session, inv: Invoice, admin_user_id: str, now: datetime | None = None
) -> Subscription:
    """Mark paid and extend / switch the subscription from the later of now or its current end."""
    now = now or datetime.now(UTC)
    if inv.status is not InvoiceStatus.pending:
        raise ValueError("hoá đơn này không còn chờ thanh toán")
    ws = db.get(Workspace, inv.workspace_id)
    if ws is None:
        raise ValueError("workspace không tồn tại")
    sub = get_subscription(db, ws)
    base = _aware(sub.current_period_end) if sub.plan == inv.plan else None
    start = max(now, base) if base else now
    sub.plan = inv.plan
    sub.current_period_end = start + timedelta(days=30 * inv.months)
    inv.status = InvoiceStatus.paid
    inv.paid_at = now
    inv.confirmed_by = admin_user_id
    db.commit()
    db.refresh(sub)
    return sub


def cancel_invoice(db: Session, inv: Invoice) -> Invoice:
    if inv.status is not InvoiceStatus.pending:
        raise ValueError("chỉ huỷ được hoá đơn đang chờ")
    inv.status = InvoiceStatus.cancelled
    db.commit()
    return inv


def extend_trial(db: Session, ws: Workspace, days: int, note: str = "") -> Subscription:
    sub = get_subscription(db, ws)
    base = max(datetime.now(UTC), _aware(sub.trial_ends_at) or datetime.now(UTC))
    sub.trial_ends_at = base + timedelta(days=max(1, min(365, days)))
    if sub.plan != "trial" and status_of(sub) == "expired":
        sub.plan, sub.current_period_end = "trial", None
    if note:
        sub.notes = (sub.notes + "\n" if sub.notes else "") + note
    db.commit()
    db.refresh(sub)
    return sub


def set_plan(
    db: Session, ws: Workspace, plan_key: str, months: int, note: str = ""
) -> Subscription:
    """Operator override (comped months, migrations)."""
    if plan_key not in PAID_PLANS:
        raise ValueError("plan must be basic or pro")
    sub = get_subscription(db, ws)
    sub.plan = plan_key
    sub.current_period_end = datetime.now(UTC) + timedelta(days=30 * max(1, min(24, months)))
    if note:
        sub.notes = (sub.notes + "\n" if sub.notes else "") + note
    db.commit()
    db.refresh(sub)
    return sub


def invoice_out(inv: Invoice) -> dict[str, Any]:
    return {
        "id": inv.id,
        "number": inv.number,
        "plan": inv.plan,
        "plan_name": get_plan(inv.plan).name,
        "months": inv.months,
        "amount_vnd": inv.amount_vnd,
        "status": inv.status.value,
        "gateway": inv.gateway,
        "transfer_code": inv.transfer_code,
        "payment": inv.payment or {},
        "paid_at": _aware(inv.paid_at).isoformat() if inv.paid_at else None,
        "created_at": _aware(inv.created_at).isoformat() if inv.created_at else None,
    }
