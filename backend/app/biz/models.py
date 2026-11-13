"""SQLAlchemy models for the industry data (all scoped by ``workspace_id``)."""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, date, datetime

from sqlalchemy import JSON, Boolean, Date, DateTime, Enum, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def _uuid() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.now(UTC)


def _ws_col() -> Mapped[str]:
    return mapped_column(
        String(32), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )


class ServiceCategory(str, enum.Enum):
    gym_package = "gym_package"
    pt_session = "pt_session"
    spa_treatment = "spa_treatment"
    course = "course"


class MembershipStatus(str, enum.Enum):
    active = "active"
    expiring = "expiring"
    expired = "expired"
    frozen = "frozen"


class AppointmentStatus(str, enum.Enum):
    booked = "booked"
    confirmed = "confirmed"
    arrived = "arrived"
    done = "done"
    cancelled = "cancelled"
    no_show = "no_show"


class AppointmentSource(str, enum.Enum):
    ai = "ai"
    staff = "staff"
    manual = "manual"


class StaffRole(str, enum.Enum):
    pt = "pt"
    therapist = "therapist"
    receptionist = "receptionist"


class Channel(str, enum.Enum):
    zalo = "zalo"
    facebook = "facebook"


class Direction(str, enum.Enum):
    inbound = "in"
    outbound = "out"


class Sender(str, enum.Enum):
    customer = "customer"
    ai = "ai"
    staff = "staff"


class MessageStatus(str, enum.Enum):
    ai_suggested = "ai_suggested"
    sent = "sent"
    handoff = "handoff"


class Customer(Base):
    __tablename__ = "biz_customers"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = _ws_col()
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    #: Phone number encrypted at rest (Fernet); ``phone_hash`` allows exact lookups/dedupe.
    phone_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    phone_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    phone_last4: Mapped[str | None] = mapped_column(String(4), nullable=True)
    zalo_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    fb_psid: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    source: Mapped[str] = mapped_column(String(50), default="manual")
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_visit_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    memberships: Mapped[list[Membership]] = relationship(
        back_populates="customer", cascade="all, delete-orphan"
    )


class Service(Base):
    __tablename__ = "biz_services"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = _ws_col()
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[ServiceCategory] = mapped_column(Enum(ServiceCategory, native_enum=False))
    duration_min: Mapped[int] = mapped_column(Integer, default=60)
    price: Mapped[float] = mapped_column(Float, default=0.0)
    #: Number of sessions a purchase grants (0 = time-based package).
    sessions: Mapped[int] = mapped_column(Integer, default=0)
    #: Validity in days for time-based packages (0 = until sessions run out).
    validity_days: Mapped[int] = mapped_column(Integer, default=30)
    description: Mapped[str] = mapped_column(Text, default="")
    #: Private notes for the AI (never shown to customers).
    sale_notes: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Membership(Base):
    __tablename__ = "biz_memberships"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = _ws_col()
    customer_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("biz_customers.id", ondelete="CASCADE"), index=True
    )
    service_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("biz_services.id", ondelete="CASCADE"), index=True
    )
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    sessions_total: Mapped[int] = mapped_column(Integer, default=0)
    sessions_used: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[MembershipStatus] = mapped_column(
        Enum(MembershipStatus, native_enum=False), default=MembershipStatus.active
    )
    amount_paid: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    customer: Mapped[Customer] = relationship(back_populates="memberships")
    service: Mapped[Service] = relationship()


class Staff(Base):
    __tablename__ = "biz_staff"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = _ws_col()
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[StaffRole] = mapped_column(Enum(StaffRole, native_enum=False))
    #: ``{"mon": [["08:00", "12:00"], ["14:00", "20:00"]], ...}``; missing day = off.
    working_hours: Mapped[dict] = mapped_column(JSON, default=dict)
    branch_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    phone: Mapped[str] = mapped_column(String(30), default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Appointment(Base):
    __tablename__ = "biz_appointments"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = _ws_col()
    customer_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("biz_customers.id", ondelete="CASCADE"), index=True
    )
    service_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("biz_services.id", ondelete="SET NULL"), nullable=True
    )
    staff_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("biz_staff.id", ondelete="SET NULL"), nullable=True, index=True
    )
    branch_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[AppointmentStatus] = mapped_column(
        Enum(AppointmentStatus, native_enum=False), default=AppointmentStatus.booked
    )
    source: Mapped[AppointmentSource] = mapped_column(
        Enum(AppointmentSource, native_enum=False), default=AppointmentSource.manual
    )
    notes: Mapped[str] = mapped_column(Text, default="")
    #: Mirrored Google Calendar event (when the connector is enabled).
    google_event_id: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    customer: Mapped[Customer] = relationship()
    service: Mapped[Service | None] = relationship()
    staff: Mapped[Staff | None] = relationship()


class CheckIn(Base):
    __tablename__ = "biz_checkins"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = _ws_col()
    customer_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("biz_customers.id", ondelete="CASCADE"), index=True
    )
    branch_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class Conversation(Base):
    __tablename__ = "biz_conversations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = _ws_col()
    channel: Mapped[Channel] = mapped_column(Enum(Channel, native_enum=False))
    external_thread_id: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    customer_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("biz_customers.id", ondelete="SET NULL"), nullable=True, index=True
    )
    #: False once a staff member takes over ("Tôi tiếp quản").
    ai_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: Last inbound message time (24h messaging windows on Zalo / Meta).
    last_inbound_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    unread: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    customer: Mapped[Customer | None] = relationship()
    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan", order_by="Message.created_at"
    )


class Message(Base):
    __tablename__ = "biz_messages"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = _ws_col()
    conversation_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("biz_conversations.id", ondelete="CASCADE"), index=True
    )
    direction: Mapped[Direction] = mapped_column(Enum(Direction, native_enum=False))
    text: Mapped[str] = mapped_column(Text, default="")
    sender: Mapped[Sender] = mapped_column(Enum(Sender, native_enum=False))
    status: Mapped[MessageStatus] = mapped_column(
        Enum(MessageStatus, native_enum=False), default=MessageStatus.sent
    )
    external_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )

    conversation: Mapped[Conversation] = relationship(back_populates="messages")
