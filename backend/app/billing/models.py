"""Subscription and invoice tables."""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def _uuid() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.now(UTC)


class Subscription(Base):
    """One row per workspace: the plan it is on and until when."""

    __tablename__ = "billing_subscriptions"
    __table_args__ = (UniqueConstraint("workspace_id", name="uq_subscription_workspace"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    plan: Mapped[str] = mapped_column(String(16), default="trial")
    trial_ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: Paid plans: end of the paid period (``None`` while on trial).
    current_period_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Free-text bookkeeping for the operator (why a trial was extended, ...).
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class InvoiceStatus(str, enum.Enum):
    pending = "pending"
    paid = "paid"
    cancelled = "cancelled"


class Invoice(Base):
    """A request to pay for ``months`` of ``plan``; paid when the operator confirms the transfer."""

    __tablename__ = "billing_invoices"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    number: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    plan: Mapped[str] = mapped_column(String(16), nullable=False)
    months: Mapped[int] = mapped_column(Integer, default=1)
    amount_vnd: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[InvoiceStatus] = mapped_column(
        Enum(InvoiceStatus, native_enum=False), default=InvoiceStatus.pending
    )
    gateway: Mapped[str] = mapped_column(String(32), default="bank_transfer")
    #: Short code the customer puts in the transfer description; the operator matches on it.
    transfer_code: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    #: Gateway-specific instructions snapshot (bank, account, QR payload / image URL).
    payment: Mapped[dict] = mapped_column(JSON, default=dict)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmed_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
