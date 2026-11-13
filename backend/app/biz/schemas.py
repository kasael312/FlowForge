"""Pydantic schemas for the industry data API."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.biz.models import (
    AppointmentSource,
    AppointmentStatus,
    Channel,
    Direction,
    MembershipStatus,
    MessageStatus,
    Sender,
    ServiceCategory,
    StaffRole,
)
from app.biz.phone import normalize_phone
from app.schemas import UtcDateTime

# ---------------------------------------------------------------- customers


class CustomerIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    phone: str = Field(default="", max_length=30)
    zalo_id: str | None = Field(default=None, max_length=100)
    fb_psid: str | None = Field(default=None, max_length=100)
    tags: list[str] = Field(default_factory=list, max_length=30)
    source: str = Field(default="manual", max_length=50)
    notes: str = Field(default="", max_length=5000)

    @field_validator("tags")
    @classmethod
    def _tags(cls, v: list[str]) -> list[str]:
        out: list[str] = []
        for t in v:
            t = t.strip()[:40]
            if t and t not in out:
                out.append(t)
        return out


class CustomerUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    phone: str | None = Field(default=None, max_length=30)
    zalo_id: str | None = None
    fb_psid: str | None = None
    tags: list[str] | None = None
    source: str | None = None
    notes: str | None = None
    last_visit_at: datetime | None = None


class CustomerOut(BaseModel):
    id: str
    name: str
    phone: str
    zalo_id: str | None
    fb_psid: str | None
    tags: list[str]
    source: str
    notes: str
    created_at: UtcDateTime
    last_visit_at: UtcDateTime | None
    #: Summary of the newest membership (status, service name, end date, sessions left).
    membership: dict[str, Any] | None = None


# ---------------------------------------------------------------- services


class ServiceIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    category: ServiceCategory
    duration_min: int = Field(default=60, ge=0, le=24 * 60)
    price: float = Field(default=0, ge=0)
    sessions: int = Field(default=0, ge=0, le=10_000)
    validity_days: int = Field(default=30, ge=0, le=3650)
    description: str = Field(default="", max_length=5000)
    sale_notes: str = Field(default="", max_length=5000)
    active: bool = True


class ServiceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    category: ServiceCategory | None = None
    duration_min: int | None = Field(default=None, ge=0, le=24 * 60)
    price: float | None = Field(default=None, ge=0)
    sessions: int | None = Field(default=None, ge=0, le=10_000)
    validity_days: int | None = Field(default=None, ge=0, le=3650)
    description: str | None = None
    sale_notes: str | None = None
    active: bool | None = None


class ServiceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    category: ServiceCategory
    duration_min: int
    price: float
    sessions: int
    validity_days: int
    description: str
    sale_notes: str
    active: bool
    created_at: UtcDateTime


# ---------------------------------------------------------------- memberships


class MembershipIn(BaseModel):
    customer_id: str
    service_id: str
    start_date: date
    end_date: date | None = None
    sessions_total: int | None = Field(default=None, ge=0)
    sessions_used: int = Field(default=0, ge=0)
    amount_paid: float = Field(default=0, ge=0)
    status: MembershipStatus | None = None


class MembershipUpdate(BaseModel):
    end_date: date | None = None
    sessions_total: int | None = Field(default=None, ge=0)
    sessions_used: int | None = Field(default=None, ge=0)
    amount_paid: float | None = Field(default=None, ge=0)
    status: MembershipStatus | None = None


class MembershipOut(BaseModel):
    id: str
    customer_id: str
    customer_name: str
    service_id: str
    service_name: str
    start_date: date
    end_date: date | None
    sessions_total: int
    sessions_used: int
    sessions_left: int | None
    status: MembershipStatus
    amount_paid: float
    days_left: int | None
    created_at: UtcDateTime


# ---------------------------------------------------------------- staff


class StaffIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    role: StaffRole
    working_hours: dict[str, list[list[str]]] = Field(default_factory=dict)
    branch_id: str | None = None
    phone: str = Field(default="", max_length=30)
    active: bool = True

    @field_validator("working_hours")
    @classmethod
    def _hours(cls, v: dict[str, list[list[str]]]) -> dict[str, list[list[str]]]:
        days = {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}
        out: dict[str, list[list[str]]] = {}
        for day, spans in v.items():
            if day not in days:
                raise ValueError(f"unknown day {day!r} (use mon..sun)")
            cleaned = []
            for span in spans:
                if len(span) != 2:
                    raise ValueError("each span is [start, end] like ['08:00', '12:00']")
                a, b = (str(span[0]).strip(), str(span[1]).strip())
                for t in (a, b):
                    hh, _, mm = t.partition(":")
                    if not (
                        hh.isdigit() and mm.isdigit() and 0 <= int(hh) < 24 and 0 <= int(mm) < 60
                    ):
                        raise ValueError(f"bad time {t!r} (HH:MM)")
                if a >= b:
                    raise ValueError(f"span {a}-{b} must end after it starts")
                cleaned.append([a, b])
            out[day] = sorted(cleaned)
        return out


class StaffUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    role: StaffRole | None = None
    working_hours: dict[str, list[list[str]]] | None = None
    branch_id: str | None = None
    phone: str | None = None
    active: bool | None = None

    _hours = field_validator("working_hours")(StaffIn._hours.__func__)  # type: ignore[attr-defined]


class StaffOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    role: StaffRole
    working_hours: dict[str, list[list[str]]]
    branch_id: str | None
    phone: str
    active: bool
    created_at: UtcDateTime


# ---------------------------------------------------------------- appointments


class AppointmentIn(BaseModel):
    customer_id: str
    service_id: str | None = None
    staff_id: str | None = None
    branch_id: str | None = None
    start_at: datetime
    end_at: datetime | None = None
    status: AppointmentStatus = AppointmentStatus.booked
    source: AppointmentSource = AppointmentSource.manual
    notes: str = Field(default="", max_length=2000)


class AppointmentUpdate(BaseModel):
    service_id: str | None = None
    staff_id: str | None = None
    branch_id: str | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None
    status: AppointmentStatus | None = None
    notes: str | None = None


class AppointmentOut(BaseModel):
    id: str
    customer_id: str
    customer_name: str
    service_id: str | None
    service_name: str | None
    staff_id: str | None
    staff_name: str | None
    branch_id: str | None
    start_at: UtcDateTime
    end_at: UtcDateTime
    status: AppointmentStatus
    source: AppointmentSource
    notes: str
    created_at: UtcDateTime


class SlotQuery(BaseModel):
    """``POST /biz/appointments/slots``: free slots for a day."""

    day: date
    staff_id: str | None = None
    branch_id: str | None = None
    duration_min: int = Field(default=60, ge=5, le=24 * 60)
    step_min: int = Field(default=30, ge=5, le=240)


# ---------------------------------------------------------------- check-ins


class CheckInIn(BaseModel):
    customer_id: str
    branch_id: str | None = None
    at: datetime | None = None


class CheckInOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    customer_id: str
    branch_id: str | None
    at: UtcDateTime


# ---------------------------------------------------------------- inbox


class ConversationOut(BaseModel):
    id: str
    channel: Channel
    external_thread_id: str
    customer_id: str | None
    customer_name: str | None
    ai_enabled: bool
    unread: bool
    last_message_at: UtcDateTime | None
    last_inbound_at: UtcDateTime | None
    last_text: str = ""
    #: 24h messaging window: ``{open, fallback: null | 'zns' | 'tag'}``.
    window: dict[str, Any] = Field(default_factory=dict)
    created_at: UtcDateTime


class ConversationUpdate(BaseModel):
    customer_id: str | None = None
    ai_enabled: bool | None = None
    unread: bool | None = None


class MessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    #: ``staff`` writes a reply; ``customer`` simulates an inbound message (tests / demo).
    sender: Literal["staff", "customer"] = "staff"
    #: Zalo outside the 24h window: ZNS template id + its parameters.
    zns_template_id: str | None = None
    zns_params: dict[str, Any] = Field(default_factory=dict)
    #: Messenger outside the 24h window: Meta message tag.
    fb_tag: str | None = None
    #: Record without delivering (no connector yet / internal note).
    deliver: bool = True


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    conversation_id: str
    direction: Direction
    text: str
    sender: Sender
    status: MessageStatus
    external_id: str | None = None
    created_at: UtcDateTime


# ---------------------------------------------------------------- import

ImportEntity = Literal["customers", "memberships", "services"]


class ImportPreview(BaseModel):
    entity: ImportEntity
    columns: list[str]
    sample: list[dict[str, Any]]
    row_count: int
    #: Target fields with labels and whether they are required.
    fields: list[dict[str, Any]]
    #: ``{target_field: source_column}`` guessed from header names.
    suggested_mapping: dict[str, str]


class ImportResult(BaseModel):
    entity: ImportEntity
    created: int
    updated: int
    skipped: int
    errors: list[str]


def clean_phone(value: str | None) -> str:
    return normalize_phone(value)
