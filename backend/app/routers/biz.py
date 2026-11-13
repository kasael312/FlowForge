"""Industry data API (``/biz``): everything is scoped to the caller's current workspace."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.biz import importer
from app.biz import overview as overview_svc
from app.biz import service as biz
from app.biz.models import (
    Appointment,
    AppointmentStatus,
    CheckIn,
    Conversation,
    Customer,
    Direction,
    Membership,
    MembershipStatus,
    Message,
    MessageStatus,
    Sender,
    Service,
    Staff,
)
from app.biz.schemas import (
    AppointmentIn,
    AppointmentOut,
    AppointmentUpdate,
    CheckInIn,
    CheckInOut,
    ConversationOut,
    ConversationUpdate,
    CustomerIn,
    CustomerOut,
    CustomerUpdate,
    ImportPreview,
    ImportResult,
    MembershipIn,
    MembershipOut,
    MembershipUpdate,
    MessageIn,
    MessageOut,
    ServiceIn,
    ServiceOut,
    ServiceUpdate,
    SlotQuery,
    StaffIn,
    StaffOut,
    StaffUpdate,
)
from app.connectors import google_calendar, messaging
from app.connectors.base import ConnectorError
from app.connectors.models import ConnectorKind
from app.db import get_db
from app.workspace import CurrentWorkspace, WorkspaceContext

router = APIRouter(prefix="/biz", tags=["biz"])
DbSession = Annotated[Session, Depends(get_db)]
MAX_IMPORT_BYTES = 10 * 1024 * 1024


def _get(db: Session, model: type, ctx: WorkspaceContext, obj_id: str, what: str) -> Any:
    row = db.get(model, obj_id)
    if row is None or row.workspace_id != ctx.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{what} not found")
    return row


def _branch_ok(ctx: WorkspaceContext, branch_id: str | None) -> str | None:
    if branch_id is None or branch_id == "":
        return None
    if branch_id not in {b.get("id") for b in (ctx.workspace.branches or [])}:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "unknown branch_id")
    return branch_id


# ---------------------------------------------------------------- overview


@router.get("/overview")
def get_overview(ctx: CurrentWorkspace, db: DbSession) -> dict[str, Any]:
    """Numbers for the owner's home page (today, no-shows, expiring, inbox, hours saved)."""
    return overview_svc.overview(db, ctx.workspace)


# ---------------------------------------------------------------- customers


@router.get("/customers", response_model=list[CustomerOut])
def list_customers(
    ctx: CurrentWorkspace,
    db: DbSession,
    q: str = "",
    tag: str = "",
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[CustomerOut]:
    """Search by name (or exact phone), optionally by tag; newest first."""
    query = select(Customer).where(Customer.workspace_id == ctx.id)
    if q.strip():
        by_phone = biz.find_by_phone(db, ctx.id, q)
        like = f"%{q.strip()}%"
        cond = or_(
            Customer.name.ilike(like), Customer.zalo_id == q.strip(), Customer.fb_psid == q.strip()
        )
        if by_phone is not None:
            cond = or_(cond, Customer.id == by_phone.id)
        query = query.where(cond)
    rows = db.scalars(
        query.order_by(Customer.created_at.desc(), Customer.id.desc()).offset(offset).limit(limit)
    ).all()
    if tag.strip():
        rows = [c for c in rows if tag.strip() in (c.tags or [])]
    return [biz.customer_out(db, c) for c in rows]


@router.post("/customers", response_model=CustomerOut, status_code=status.HTTP_201_CREATED)
def create_customer(body: CustomerIn, ctx: CurrentWorkspace, db: DbSession) -> CustomerOut:
    if body.phone.strip() and biz.find_by_phone(db, ctx.id, body.phone) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "a customer with this phone already exists")
    c = Customer(
        workspace_id=ctx.id,
        name=body.name.strip(),
        zalo_id=body.zalo_id or None,
        fb_psid=body.fb_psid or None,
        tags=body.tags,
        source=body.source,
        notes=body.notes,
    )
    biz.set_phone(c, body.phone)
    db.add(c)
    db.commit()
    db.refresh(c)
    return biz.customer_out(db, c)


@router.get("/customers/{customer_id}", response_model=CustomerOut)
def get_customer(customer_id: str, ctx: CurrentWorkspace, db: DbSession) -> CustomerOut:
    return biz.customer_out(db, _get(db, Customer, ctx, customer_id, "customer"))


@router.put("/customers/{customer_id}", response_model=CustomerOut)
def update_customer(
    customer_id: str, body: CustomerUpdate, ctx: CurrentWorkspace, db: DbSession
) -> CustomerOut:
    c = _get(db, Customer, ctx, customer_id, "customer")
    if body.phone is not None:
        other = biz.find_by_phone(db, ctx.id, body.phone)
        if other is not None and other.id != c.id:
            raise HTTPException(status.HTTP_409_CONFLICT, "another customer has this phone")
        biz.set_phone(c, body.phone)
    for key in ("name", "zalo_id", "fb_psid", "source", "notes", "last_visit_at"):
        v = getattr(body, key)
        if v is not None:
            setattr(c, key, v.strip() if isinstance(v, str) and key == "name" else v)
    if body.tags is not None:
        c.tags = CustomerIn(name="x", tags=body.tags).tags
    db.commit()
    db.refresh(c)
    return biz.customer_out(db, c)


@router.delete("/customers/{customer_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_customer(customer_id: str, ctx: CurrentWorkspace, db: DbSession) -> None:
    db.delete(_get(db, Customer, ctx, customer_id, "customer"))
    db.commit()


@router.get("/customers/{customer_id}/memberships", response_model=list[MembershipOut])
def customer_memberships(
    customer_id: str, ctx: CurrentWorkspace, db: DbSession
) -> list[MembershipOut]:
    c = _get(db, Customer, ctx, customer_id, "customer")
    rows = db.scalars(
        select(Membership)
        .where(Membership.customer_id == c.id)
        .order_by(Membership.created_at.desc())
    ).all()
    out = [biz.membership_out(db, m) for m in rows]
    db.commit()  # refreshed statuses
    return out


@router.get("/customers/{customer_id}/appointments", response_model=list[AppointmentOut])
def customer_appointments(
    customer_id: str, ctx: CurrentWorkspace, db: DbSession
) -> list[AppointmentOut]:
    c = _get(db, Customer, ctx, customer_id, "customer")
    rows = db.scalars(
        select(Appointment)
        .where(Appointment.customer_id == c.id)
        .order_by(Appointment.start_at.desc())
        .limit(50)
    ).all()
    return [_appt_out(db, a) for a in rows]


# ---------------------------------------------------------------- services


@router.get("/services", response_model=list[ServiceOut])
def list_services(
    ctx: CurrentWorkspace, db: DbSession, include_inactive: bool = False
) -> list[Service]:
    q = select(Service).where(Service.workspace_id == ctx.id)
    if not include_inactive:
        q = q.where(Service.active.is_(True))
    return list(db.scalars(q.order_by(Service.category, Service.name)).all())


@router.post("/services", response_model=ServiceOut, status_code=status.HTTP_201_CREATED)
def create_service(body: ServiceIn, ctx: CurrentWorkspace, db: DbSession) -> Service:
    s = Service(workspace_id=ctx.id, **body.model_dump())
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


@router.put("/services/{service_id}", response_model=ServiceOut)
def update_service(
    service_id: str, body: ServiceUpdate, ctx: CurrentWorkspace, db: DbSession
) -> Service:
    s = _get(db, Service, ctx, service_id, "service")
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(s, k, v)
    db.commit()
    db.refresh(s)
    return s


@router.delete("/services/{service_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_service(service_id: str, ctx: CurrentWorkspace, db: DbSession) -> None:
    s = _get(db, Service, ctx, service_id, "service")
    if db.scalar(select(func.count()).select_from(Membership).where(Membership.service_id == s.id)):
        s.active = False  # keep history; hide instead of deleting
    else:
        db.delete(s)
    db.commit()


# ---------------------------------------------------------------- memberships


@router.get("/memberships", response_model=list[MembershipOut])
def list_memberships(
    ctx: CurrentWorkspace,
    db: DbSession,
    status_filter: Annotated[MembershipStatus | None, Query(alias="status")] = None,
    customer_id: str | None = None,
    limit: int = Query(default=200, ge=1, le=1000),
) -> list[MembershipOut]:
    q = select(Membership).where(Membership.workspace_id == ctx.id)
    if customer_id:
        q = q.where(Membership.customer_id == customer_id)
    rows = db.scalars(q.order_by(Membership.created_at.desc()).limit(limit)).all()
    out = [biz.membership_out(db, m) for m in rows]
    db.commit()
    if status_filter is not None:
        out = [o for o in out if o.status is status_filter]
    return out


@router.post("/memberships", response_model=MembershipOut, status_code=status.HTTP_201_CREATED)
def create_membership(body: MembershipIn, ctx: CurrentWorkspace, db: DbSession) -> MembershipOut:
    _get(db, Customer, ctx, body.customer_id, "customer")
    svc = _get(db, Service, ctx, body.service_id, "service")
    m = Membership(
        workspace_id=ctx.id,
        customer_id=body.customer_id,
        service_id=body.service_id,
        start_date=body.start_date,
        end_date=body.end_date,
        sessions_total=body.sessions_total or 0,
        sessions_used=body.sessions_used,
        amount_paid=body.amount_paid,
    )
    if body.status is MembershipStatus.frozen:
        m.status = MembershipStatus.frozen
    biz.apply_service_defaults(m, svc)
    biz.refresh_status(m)
    db.add(m)
    db.commit()
    db.refresh(m)
    return biz.membership_out(db, m)


@router.put("/memberships/{membership_id}", response_model=MembershipOut)
def update_membership(
    membership_id: str, body: MembershipUpdate, ctx: CurrentWorkspace, db: DbSession
) -> MembershipOut:
    m = _get(db, Membership, ctx, membership_id, "membership")
    data = body.model_dump(exclude_none=True)
    new_status = data.pop("status", None)
    for k, v in data.items():
        setattr(m, k, v)
    if new_status is not None:
        m.status = new_status  # frozen sticks; anything else gets recomputed
    biz.refresh_status(m)
    db.commit()
    db.refresh(m)
    return biz.membership_out(db, m)


@router.post("/memberships/{membership_id}/use", response_model=MembershipOut)
def use_session(membership_id: str, ctx: CurrentWorkspace, db: DbSession) -> MembershipOut:
    """Consume one session (PT / treatment packages)."""
    m = _get(db, Membership, ctx, membership_id, "membership")
    if m.sessions_total and m.sessions_used >= m.sessions_total:
        raise HTTPException(status.HTTP_409_CONFLICT, "no sessions left")
    m.sessions_used += 1
    biz.refresh_status(m)
    db.commit()
    return biz.membership_out(db, m)


@router.delete("/memberships/{membership_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_membership(membership_id: str, ctx: CurrentWorkspace, db: DbSession) -> None:
    db.delete(_get(db, Membership, ctx, membership_id, "membership"))
    db.commit()


# ---------------------------------------------------------------- staff


@router.get("/staff", response_model=list[StaffOut])
def list_staff(ctx: CurrentWorkspace, db: DbSession, include_inactive: bool = False) -> list[Staff]:
    q = select(Staff).where(Staff.workspace_id == ctx.id)
    if not include_inactive:
        q = q.where(Staff.active.is_(True))
    return list(db.scalars(q.order_by(Staff.name)).all())


@router.post("/staff", response_model=StaffOut, status_code=status.HTTP_201_CREATED)
def create_staff(body: StaffIn, ctx: CurrentWorkspace, db: DbSession) -> Staff:
    s = Staff(
        workspace_id=ctx.id, **{**body.model_dump(), "branch_id": _branch_ok(ctx, body.branch_id)}
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


@router.put("/staff/{staff_id}", response_model=StaffOut)
def update_staff(staff_id: str, body: StaffUpdate, ctx: CurrentWorkspace, db: DbSession) -> Staff:
    s = _get(db, Staff, ctx, staff_id, "staff")
    data = body.model_dump(exclude_none=True)
    if "branch_id" in data:
        data["branch_id"] = _branch_ok(ctx, data["branch_id"])
    for k, v in data.items():
        setattr(s, k, v)
    db.commit()
    db.refresh(s)
    return s


@router.delete("/staff/{staff_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_staff(staff_id: str, ctx: CurrentWorkspace, db: DbSession) -> None:
    s = _get(db, Staff, ctx, staff_id, "staff")
    s.active = False  # appointments keep their history
    db.commit()


# ---------------------------------------------------------------- appointments


def _appt_out(db: Session, a: Appointment) -> AppointmentOut:
    cust = db.get(Customer, a.customer_id)
    svc = db.get(Service, a.service_id) if a.service_id else None
    st = db.get(Staff, a.staff_id) if a.staff_id else None
    return AppointmentOut(
        id=a.id,
        customer_id=a.customer_id,
        customer_name=cust.name if cust else "?",
        service_id=a.service_id,
        service_name=svc.name if svc else None,
        staff_id=a.staff_id,
        staff_name=st.name if st else None,
        branch_id=a.branch_id,
        start_at=a.start_at,
        end_at=a.end_at,
        status=a.status,
        source=a.source,
        notes=a.notes or "",
        created_at=a.created_at,
    )


async def _mirror_to_google(db: Session, ctx: WorkspaceContext, a: Appointment) -> None:
    """Best effort: push the appointment to Google Calendar when that connector is on."""
    c = messaging.connector_for(db, ctx.id, ConnectorKind.google_calendar)
    if c is None or not c.enabled:
        return
    try:
        await google_calendar.push_appointment(db, c, a, str(biz.tz_of(ctx.workspace.timezone)))
        db.commit()
    except ConnectorError as exc:
        c.last_error = str(exc)
        db.commit()


def _day_bounds(ctx: WorkspaceContext, day: date) -> tuple[datetime, datetime]:
    tz = biz.tz_of(ctx.workspace.timezone)
    start = datetime.combine(day, time.min, tzinfo=tz).astimezone(UTC)
    return start, start + timedelta(days=1)


@router.get("/appointments", response_model=list[AppointmentOut])
def list_appointments(
    ctx: CurrentWorkspace,
    db: DbSession,
    day: date | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    staff_id: str | None = None,
    branch_id: str | None = None,
    status_filter: Annotated[AppointmentStatus | None, Query(alias="status")] = None,
) -> list[AppointmentOut]:
    """Appointments for one day (workspace timezone) or an explicit range."""
    tz = biz.tz_of(ctx.workspace.timezone)
    if day is not None:
        lo, hi = _day_bounds(ctx, day)
    else:
        lo = biz.ensure_utc(start, tz) if start else datetime.now(UTC) - timedelta(days=7)
        hi = biz.ensure_utc(end, tz) if end else lo + timedelta(days=30)
    q = select(Appointment).where(
        Appointment.workspace_id == ctx.id, Appointment.start_at < hi, Appointment.end_at > lo
    )
    if staff_id:
        q = q.where(Appointment.staff_id == staff_id)
    if branch_id:
        q = q.where(Appointment.branch_id == branch_id)
    if status_filter is not None:
        q = q.where(Appointment.status == status_filter)
    return [_appt_out(db, a) for a in db.scalars(q.order_by(Appointment.start_at)).all()]


def _resolve_times(
    ctx: WorkspaceContext,
    db: Session,
    body: AppointmentIn | AppointmentUpdate,
    current: Appointment | None,
) -> tuple[datetime, datetime]:
    tz = biz.tz_of(ctx.workspace.timezone)
    start_at = (
        biz.ensure_utc(body.start_at, tz)
        if body.start_at
        else (biz.aware(current.start_at) if current else None)
    )
    if start_at is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "start_at is required")
    if body.end_at:
        end_at = biz.ensure_utc(body.end_at, tz)
    else:
        service_id = (
            body.service_id
            if body.service_id is not None
            else (current.service_id if current else None)
        )
        svc = db.get(Service, service_id) if service_id else None
        minutes = (
            (svc.duration_min if svc and svc.duration_min else 60)
            if body.start_at or current is None
            else int(
                (biz.aware(current.end_at) - biz.aware(current.start_at)).total_seconds() // 60
            )
        )
        end_at = start_at + timedelta(minutes=minutes)
    if end_at <= start_at:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "end_at must be after start_at")
    return start_at, end_at


@router.post("/appointments", response_model=AppointmentOut, status_code=status.HTTP_201_CREATED)
async def create_appointment(
    body: AppointmentIn, ctx: CurrentWorkspace, db: DbSession, force: bool = False
) -> AppointmentOut:
    """Book; ``409`` when the staff member is already busy unless ``force=true``."""
    _get(db, Customer, ctx, body.customer_id, "customer")
    if body.service_id:
        _get(db, Service, ctx, body.service_id, "service")
    if body.staff_id:
        _get(db, Staff, ctx, body.staff_id, "staff")
    start_at, end_at = _resolve_times(ctx, db, body, None)
    clash = biz.conflicting_appointment(db, ctx.id, body.staff_id, start_at, end_at)
    if clash is not None and not force:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"staff is busy {biz.aware(clash.start_at).isoformat()} – {biz.aware(clash.end_at).isoformat()}",
        )
    a = Appointment(
        workspace_id=ctx.id,
        customer_id=body.customer_id,
        service_id=body.service_id,
        staff_id=body.staff_id,
        branch_id=_branch_ok(ctx, body.branch_id),
        start_at=start_at,
        end_at=end_at,
        status=body.status,
        source=body.source,
        notes=body.notes,
    )
    db.add(a)
    db.commit()
    db.refresh(a)
    await _mirror_to_google(db, ctx, a)
    return _appt_out(db, a)


@router.put("/appointments/{appointment_id}", response_model=AppointmentOut)
async def update_appointment(
    appointment_id: str,
    body: AppointmentUpdate,
    ctx: CurrentWorkspace,
    db: DbSession,
    force: bool = False,
) -> AppointmentOut:
    a = _get(db, Appointment, ctx, appointment_id, "appointment")
    if body.staff_id:
        _get(db, Staff, ctx, body.staff_id, "staff")
    if body.start_at or body.end_at or body.service_id is not None:
        a.start_at, a.end_at = _resolve_times(ctx, db, body, a)
    staff_id = body.staff_id if body.staff_id is not None else a.staff_id
    rescheduled = bool(
        body.start_at or body.end_at or (body.staff_id and body.staff_id != a.staff_id)
    )
    if rescheduled and not force:  # a plain status change never re-checks the slot
        clash = biz.conflicting_appointment(
            db, ctx.id, staff_id, biz.aware(a.start_at), biz.aware(a.end_at), exclude_id=a.id
        )
        if clash is not None:
            raise HTTPException(status.HTTP_409_CONFLICT, "staff is busy at that time")
    for k in ("service_id", "staff_id", "notes"):
        v = getattr(body, k)
        if v is not None:
            setattr(a, k, v)
    if body.branch_id is not None:
        a.branch_id = _branch_ok(ctx, body.branch_id)
    if body.status is not None:
        a.status = body.status
        if body.status in (AppointmentStatus.arrived, AppointmentStatus.done):
            cust = db.get(Customer, a.customer_id)
            if cust is not None:
                cust.last_visit_at = datetime.now(UTC)
    db.commit()
    db.refresh(a)
    await _mirror_to_google(db, ctx, a)
    return _appt_out(db, a)


@router.delete("/appointments/{appointment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_appointment(appointment_id: str, ctx: CurrentWorkspace, db: DbSession) -> None:
    db.delete(_get(db, Appointment, ctx, appointment_id, "appointment"))
    db.commit()


@router.post("/appointments/slots")
def free_slots(body: SlotQuery, ctx: CurrentWorkspace, db: DbSession) -> dict[str, Any]:
    """Free start times on a day for one staff member (or every active staff of a branch)."""
    tz = biz.tz_of(ctx.workspace.timezone)
    lo, hi = _day_bounds(ctx, body.day)
    staff_q = select(Staff).where(Staff.workspace_id == ctx.id, Staff.active.is_(True))
    if body.staff_id:
        staff_q = staff_q.where(Staff.id == body.staff_id)
    if body.branch_id:
        staff_q = staff_q.where(Staff.branch_id == body.branch_id)
    result = []
    for st in db.scalars(staff_q.order_by(Staff.name)).all():
        busy = biz.busy_intervals(db, ctx.id, st.id, lo, hi)
        slots = biz.free_slots(
            body.day, st, busy, duration_min=body.duration_min, step_min=body.step_min, tz=tz
        )
        result.append(
            {
                "staff_id": st.id,
                "staff_name": st.name,
                "slots": [
                    {
                        "start_at": s.isoformat(),
                        "end_at": e.isoformat(),
                        "local": s.astimezone(tz).strftime("%H:%M"),
                    }
                    for s, e in slots
                ],
            }
        )
    return {"day": body.day.isoformat(), "timezone": str(tz), "staff": result}


# ---------------------------------------------------------------- check-ins


@router.get("/checkins", response_model=list[CheckInOut])
def list_checkins(
    ctx: CurrentWorkspace,
    db: DbSession,
    customer_id: str | None = None,
    day: date | None = None,
    limit: int = Query(default=200, ge=1, le=1000),
) -> list[CheckIn]:
    q = select(CheckIn).where(CheckIn.workspace_id == ctx.id)
    if customer_id:
        q = q.where(CheckIn.customer_id == customer_id)
    if day is not None:
        lo, hi = _day_bounds(ctx, day)
        q = q.where(CheckIn.at >= lo, CheckIn.at < hi)
    return list(db.scalars(q.order_by(CheckIn.at.desc()).limit(limit)).all())


@router.post("/checkins", response_model=CheckInOut, status_code=status.HTTP_201_CREATED)
def create_checkin(body: CheckInIn, ctx: CurrentWorkspace, db: DbSession) -> CheckIn:
    c = _get(db, Customer, ctx, body.customer_id, "customer")
    at = (
        biz.ensure_utc(body.at, biz.tz_of(ctx.workspace.timezone)) if body.at else datetime.now(UTC)
    )
    row = CheckIn(
        workspace_id=ctx.id, customer_id=c.id, branch_id=_branch_ok(ctx, body.branch_id), at=at
    )
    c.last_visit_at = max(at, biz.aware(c.last_visit_at)) if c.last_visit_at else at
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


# ---------------------------------------------------------------- inbox


def _conv_out(db: Session, c: Conversation) -> ConversationOut:
    cust = db.get(Customer, c.customer_id) if c.customer_id else None
    last = db.scalar(
        select(Message)
        .where(Message.conversation_id == c.id)
        .order_by(Message.created_at.desc(), Message.id.desc())
    )
    return ConversationOut(
        id=c.id,
        channel=c.channel,
        external_thread_id=c.external_thread_id,
        customer_id=c.customer_id,
        customer_name=cust.name if cust else None,
        ai_enabled=c.ai_enabled,
        unread=c.unread,
        last_message_at=c.last_message_at,
        last_inbound_at=c.last_inbound_at,
        last_text=(last.text[:120] if last else ""),
        window=messaging.window_state(c),
        created_at=c.created_at,
    )


@router.get("/conversations", response_model=list[ConversationOut])
def list_conversations(
    ctx: CurrentWorkspace,
    db: DbSession,
    unread: bool | None = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[ConversationOut]:
    q = select(Conversation).where(Conversation.workspace_id == ctx.id)
    if unread is not None:
        q = q.where(Conversation.unread.is_(unread))
    rows = db.scalars(
        q.order_by(Conversation.last_message_at.desc().nullslast(), Conversation.id).limit(limit)
    ).all()
    return [_conv_out(db, c) for c in rows]


@router.get("/conversations/{conversation_id}", response_model=ConversationOut)
def get_conversation(conversation_id: str, ctx: CurrentWorkspace, db: DbSession) -> ConversationOut:
    return _conv_out(db, _get(db, Conversation, ctx, conversation_id, "conversation"))


@router.put("/conversations/{conversation_id}", response_model=ConversationOut)
def update_conversation(
    conversation_id: str, body: ConversationUpdate, ctx: CurrentWorkspace, db: DbSession
) -> ConversationOut:
    c = _get(db, Conversation, ctx, conversation_id, "conversation")
    if body.customer_id is not None:
        _get(db, Customer, ctx, body.customer_id, "customer")
        c.customer_id = body.customer_id
    if body.ai_enabled is not None:
        c.ai_enabled = body.ai_enabled
    if body.unread is not None:
        c.unread = body.unread
    db.commit()
    return _conv_out(db, c)


@router.get("/conversations/{conversation_id}/messages", response_model=list[MessageOut])
def list_messages(conversation_id: str, ctx: CurrentWorkspace, db: DbSession) -> list[Message]:
    c = _get(db, Conversation, ctx, conversation_id, "conversation")
    return list(
        db.scalars(
            select(Message)
            .where(Message.conversation_id == c.id)
            .order_by(Message.created_at, Message.id)
        ).all()
    )


@router.post(
    "/conversations/{conversation_id}/messages",
    response_model=MessageOut,
    status_code=status.HTTP_201_CREATED,
)
async def post_message(
    conversation_id: str, body: MessageIn, ctx: CurrentWorkspace, db: DbSession
) -> Message:
    """Staff reply delivered through the channel connector (24h rules apply), or a simulated inbound message."""
    c = _get(db, Conversation, ctx, conversation_id, "conversation")
    now = datetime.now(UTC)
    if body.sender == "customer":
        m = Message(
            workspace_id=ctx.id,
            conversation_id=c.id,
            direction=Direction.inbound,
            sender=Sender.customer,
            status=MessageStatus.sent,
            text=body.text,
        )
        c.last_inbound_at = now
        c.unread = True
    else:
        result = None
        if body.deliver and messaging.connector_for(db, ctx.id, messaging.CHANNEL_KIND[c.channel]):
            try:
                result = await messaging.deliver(
                    db,
                    c,
                    body.text,
                    zns_template_id=body.zns_template_id,
                    zns_params=body.zns_params,
                    fb_tag=body.fb_tag,
                )
            except messaging.PolicyError as exc:
                raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
            except ConnectorError as exc:
                raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
        m = Message(
            workspace_id=ctx.id,
            conversation_id=c.id,
            direction=Direction.outbound,
            sender=Sender.staff,
            status=MessageStatus.sent,
            text=body.text,
            external_id=(result.external_id if result else None) or None,
        )
        c.unread = False
    c.last_message_at = now
    db.add(m)
    db.commit()
    db.refresh(m)
    return m


@router.post(
    "/conversations/{conversation_id}/messages/{message_id}/send", response_model=MessageOut
)
def approve_suggestion(
    conversation_id: str,
    message_id: str,
    ctx: CurrentWorkspace,
    db: DbSession,
    text: str | None = None,
) -> Message:
    """Mark an AI-suggested reply as sent (optionally edited)."""
    c = _get(db, Conversation, ctx, conversation_id, "conversation")
    m = db.get(Message, message_id)
    if m is None or m.conversation_id != c.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "message not found")
    if m.status is not MessageStatus.ai_suggested:
        raise HTTPException(status.HTTP_409_CONFLICT, "message is not a pending suggestion")
    if text is not None and text.strip():
        m.text = text.strip()[:4000]
        m.sender = Sender.staff
    m.status = MessageStatus.sent
    m.created_at = datetime.now(UTC)
    c.last_message_at = m.created_at
    c.unread = False
    db.commit()
    db.refresh(m)
    return m


@router.delete(
    "/conversations/{conversation_id}/messages/{message_id}", status_code=status.HTTP_204_NO_CONTENT
)
def discard_suggestion(
    conversation_id: str, message_id: str, ctx: CurrentWorkspace, db: DbSession
) -> None:
    """Discard an AI suggestion (only suggestions can be removed; sent messages are history)."""
    c = _get(db, Conversation, ctx, conversation_id, "conversation")
    m = db.get(Message, message_id)
    if m is None or m.conversation_id != c.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "message not found")
    if m.status is not MessageStatus.ai_suggested:
        raise HTTPException(status.HTTP_409_CONFLICT, "only AI suggestions can be discarded")
    db.delete(m)
    db.commit()


# ---------------------------------------------------------------- import


async def _read_upload(file: UploadFile) -> bytes:
    data = await file.read()
    if len(data) > MAX_IMPORT_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "file exceeds 10 MB")
    if not data:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "file is empty")
    return data


@router.post("/import/preview", response_model=ImportPreview)
async def import_preview(file: UploadFile, entity: str, _ctx: CurrentWorkspace) -> ImportPreview:
    """Detect columns, show the first rows and suggest a mapping."""
    if entity not in importer.FIELDS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "entity must be customers, memberships or services",
        )
    data = await _read_upload(file)
    try:
        columns, rows = importer.parse_table(data, file.filename or "")
    except importer.ImportError_ as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    return ImportPreview(
        entity=entity,  # type: ignore[arg-type]
        columns=columns,
        sample=rows[:10],
        row_count=len(rows),
        fields=importer.FIELDS[entity],
        suggested_mapping=importer.suggest_mapping(entity, columns),
    )


@router.post("/import", response_model=ImportResult)
async def import_rows(
    file: UploadFile, entity: str, mapping: str, ctx: CurrentWorkspace, db: DbSession
) -> ImportResult:
    """Import with ``mapping`` = JSON ``{target_field: source_column}``."""
    if entity not in importer.FIELDS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "entity must be customers, memberships or services",
        )
    try:
        mapping_dict = {str(k): str(v) for k, v in json.loads(mapping).items() if v}
    except (ValueError, AttributeError) as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "mapping must be a JSON object"
        ) from exc
    data = await _read_upload(file)
    try:
        _columns, rows = importer.parse_table(data, file.filename or "")
        result = importer.apply_import(db, ctx.id, entity, rows, mapping_dict)
    except importer.ImportError_ as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    return ImportResult(**result)
