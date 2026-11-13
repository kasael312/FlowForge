"""Business rules shared by routers, importers and (later) industry flows."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.biz.models import (
    Appointment,
    AppointmentStatus,
    Customer,
    Membership,
    MembershipStatus,
    Service,
    Staff,
)
from app.biz.phone import decrypt_phone, encrypt_phone, normalize_phone, phone_hash
from app.biz.schemas import CustomerOut, MembershipOut

EXPIRING_DAYS = 7
EXPIRING_SESSIONS = 2
DAY_KEYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
BLOCKING = (AppointmentStatus.booked, AppointmentStatus.confirmed, AppointmentStatus.arrived)


def aware(dt: datetime) -> datetime:
    """SQLite hands back naive datetimes; everything we store is UTC."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def tz_of(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or "Asia/Ho_Chi_Minh")
    except Exception:  # noqa: BLE001 - bad tz name in settings
        return ZoneInfo("Asia/Ho_Chi_Minh")


# ---------------------------------------------------------------- customers


def set_phone(c: Customer, raw: str | None) -> None:
    c.phone_encrypted, c.phone_hash, c.phone_last4 = encrypt_phone(raw)


def find_by_phone(db: Session, workspace_id: str, raw: str | None) -> Customer | None:
    phone = normalize_phone(raw)
    if not phone:
        return None
    return db.scalar(
        select(Customer).where(
            Customer.workspace_id == workspace_id, Customer.phone_hash == phone_hash(phone)
        )
    )


def customer_out(db: Session, c: Customer, today: date | None = None) -> CustomerOut:
    latest = db.scalar(
        select(Membership)
        .where(Membership.customer_id == c.id)
        .order_by(Membership.created_at.desc(), Membership.id.desc())
    )
    summary = None
    if latest is not None:
        refresh_status(latest, today)
        svc = db.get(Service, latest.service_id)
        summary = {
            "id": latest.id,
            "status": latest.status.value,
            "service_name": svc.name if svc else "?",
            "end_date": latest.end_date.isoformat() if latest.end_date else None,
            "sessions_left": sessions_left(latest),
        }
    return CustomerOut(
        id=c.id,
        name=c.name,
        phone=decrypt_phone(c.phone_encrypted),
        zalo_id=c.zalo_id,
        fb_psid=c.fb_psid,
        tags=list(c.tags or []),
        source=c.source,
        notes=c.notes or "",
        created_at=c.created_at,
        last_visit_at=c.last_visit_at,
        membership=summary,
    )


# ---------------------------------------------------------------- memberships


def sessions_left(m: Membership) -> int | None:
    if not m.sessions_total:
        return None
    return max(0, m.sessions_total - m.sessions_used)


def compute_status(m: Membership, today: date | None = None) -> MembershipStatus:
    """``frozen`` is manual; otherwise derive from dates and sessions."""
    if m.status is MembershipStatus.frozen:
        return MembershipStatus.frozen
    today = today or datetime.now(UTC).date()
    left = sessions_left(m)
    if (m.end_date and m.end_date < today) or (left is not None and left <= 0):
        return MembershipStatus.expired
    if (m.end_date and (m.end_date - today).days <= EXPIRING_DAYS) or (
        left is not None and left <= EXPIRING_SESSIONS
    ):
        return MembershipStatus.expiring
    return MembershipStatus.active


def refresh_status(m: Membership, today: date | None = None) -> MembershipStatus:
    m.status = compute_status(m, today)
    return m.status


def apply_service_defaults(m: Membership, svc: Service) -> None:
    """Fill end_date / sessions_total from the service when the caller left them empty."""
    if m.sessions_total is None or m.sessions_total == 0:
        m.sessions_total = svc.sessions or 0
    if m.end_date is None and svc.validity_days:
        m.end_date = m.start_date + timedelta(days=svc.validity_days)


def membership_out(db: Session, m: Membership, today: date | None = None) -> MembershipOut:
    refresh_status(m, today)
    cust = db.get(Customer, m.customer_id)
    svc = db.get(Service, m.service_id)
    today = today or datetime.now(UTC).date()
    return MembershipOut(
        id=m.id,
        customer_id=m.customer_id,
        customer_name=cust.name if cust else "?",
        service_id=m.service_id,
        service_name=svc.name if svc else "?",
        start_date=m.start_date,
        end_date=m.end_date,
        sessions_total=m.sessions_total,
        sessions_used=m.sessions_used,
        sessions_left=sessions_left(m),
        status=m.status,
        amount_paid=m.amount_paid,
        days_left=(m.end_date - today).days if m.end_date else None,
        created_at=m.created_at,
    )


# ---------------------------------------------------------------- scheduling


def _parse_hhmm(value: str) -> time:
    hh, _, mm = value.partition(":")
    return time(int(hh), int(mm))


def working_spans(staff: Staff | None, day: date, tz: ZoneInfo) -> list[tuple[datetime, datetime]]:
    """Aware UTC intervals the staff member works on ``day`` (empty = off / no staff)."""
    if staff is None:
        return []
    spans = (staff.working_hours or {}).get(DAY_KEYS[day.weekday()], [])
    out = []
    for a, b in spans:
        start = datetime.combine(day, _parse_hhmm(a), tzinfo=tz).astimezone(UTC)
        end = datetime.combine(day, _parse_hhmm(b), tzinfo=tz).astimezone(UTC)
        if end > start:
            out.append((start, end))
    return out


def overlaps(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> bool:
    return a_start < b_end and b_start < a_end


def free_slots(
    day: date,
    staff: Staff | None,
    busy: list[tuple[datetime, datetime]],
    *,
    duration_min: int,
    step_min: int,
    tz: ZoneInfo,
    now: datetime | None = None,
) -> list[tuple[datetime, datetime]]:
    """Start/end pairs (UTC) inside working hours that do not overlap ``busy`` and are in the future."""
    now = now or datetime.now(UTC)
    dur = timedelta(minutes=duration_min)
    step = timedelta(minutes=step_min)
    slots: list[tuple[datetime, datetime]] = []
    for w_start, w_end in working_spans(staff, day, tz):
        cursor = w_start
        while cursor + dur <= w_end:
            end = cursor + dur
            if cursor >= now and not any(overlaps(cursor, end, b0, b1) for b0, b1 in busy):
                slots.append((cursor, end))
            cursor += step
    return slots


def busy_intervals(
    db: Session, workspace_id: str, staff_id: str | None, day_start: datetime, day_end: datetime
) -> list[tuple[datetime, datetime]]:
    q = select(Appointment).where(
        Appointment.workspace_id == workspace_id,
        Appointment.status.in_(BLOCKING),
        Appointment.start_at < day_end,
        Appointment.end_at > day_start,
    )
    if staff_id:
        q = q.where(Appointment.staff_id == staff_id)
    return [(aware(a.start_at), aware(a.end_at)) for a in db.scalars(q).all()]


def conflicting_appointment(
    db: Session,
    workspace_id: str,
    staff_id: str | None,
    start_at: datetime,
    end_at: datetime,
    exclude_id: str | None = None,
) -> Appointment | None:
    """Another blocking appointment for the same staff that overlaps the interval."""
    if not staff_id:
        return None
    q = select(Appointment).where(
        Appointment.workspace_id == workspace_id,
        Appointment.staff_id == staff_id,
        Appointment.status.in_(BLOCKING),
        Appointment.start_at < end_at,
        Appointment.end_at > start_at,
    )
    if exclude_id:
        q = q.where(Appointment.id != exclude_id)
    return db.scalar(q)


def ensure_utc(dt: datetime, tz: ZoneInfo) -> datetime:
    """Naive datetimes are interpreted in the workspace timezone."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=tz).astimezone(UTC)
    return dt.astimezone(UTC)
