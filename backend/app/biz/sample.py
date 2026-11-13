"""Sample data seeded right after onboarding so the owner's screens are not empty."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.biz import service as biz
from app.biz.models import (
    Appointment,
    AppointmentSource,
    AppointmentStatus,
    Channel,
    CheckIn,
    Conversation,
    Customer,
    Direction,
    Membership,
    Message,
    MessageStatus,
    Sender,
    Service,
    ServiceCategory,
    Staff,
    StaffRole,
)
from app.models import Workspace

SAMPLE_TAG = "mẫu"
HOURS = {
    d: [["08:00", "12:00"], ["14:00", "21:00"]] for d in ("mon", "tue", "wed", "thu", "fri", "sat")
}

GYM_SERVICES: list[dict[str, Any]] = [
    {
        "name": "Gói tập 1 tháng",
        "category": ServiceCategory.gym_package,
        "price": 500_000,
        "validity_days": 30,
        "sessions": 0,
        "duration_min": 0,
        "sale_notes": "Khách mới hỏi giá: gợi ý gói 3 tháng tiết kiệm 20%.",
    },
    {
        "name": "Gói tập 3 tháng",
        "category": ServiceCategory.gym_package,
        "price": 1_200_000,
        "validity_days": 90,
        "sessions": 0,
        "duration_min": 0,
    },
    {
        "name": "PT 10 buổi",
        "category": ServiceCategory.pt_session,
        "price": 3_000_000,
        "validity_days": 60,
        "sessions": 10,
        "duration_min": 60,
        "sale_notes": "Buổi đầu miễn phí đo InBody.",
    },
    {
        "name": "Lớp Yoga tháng",
        "category": ServiceCategory.course,
        "price": 600_000,
        "validity_days": 30,
        "sessions": 12,
        "duration_min": 60,
    },
]
SPA_SERVICES: list[dict[str, Any]] = [
    {
        "name": "Chăm sóc da cơ bản",
        "category": ServiceCategory.spa_treatment,
        "price": 350_000,
        "validity_days": 0,
        "sessions": 1,
        "duration_min": 60,
    },
    {
        "name": "Liệu trình trị mụn 10 buổi",
        "category": ServiceCategory.spa_treatment,
        "price": 4_500_000,
        "validity_days": 120,
        "sessions": 10,
        "duration_min": 75,
        "sale_notes": "Nhắc khách đến đúng chu kỳ 7-10 ngày.",
    },
    {
        "name": "Massage body 90 phút",
        "category": ServiceCategory.spa_treatment,
        "price": 450_000,
        "validity_days": 0,
        "sessions": 1,
        "duration_min": 90,
    },
    {
        "name": "Gội đầu dưỡng sinh",
        "category": ServiceCategory.spa_treatment,
        "price": 150_000,
        "validity_days": 0,
        "sessions": 1,
        "duration_min": 45,
    },
]
CUSTOMERS = [
    ("Nguyễn Minh Anh", "0901234567", ["khách quen"]),
    ("Trần Quốc Bảo", "0912345678", []),
    ("Lê Thu Hà", "0923456789", ["vip"]),
    ("Phạm Văn Dũng", "0934567890", []),
    ("Võ Ngọc Lan", "0945678901", ["mới"]),
]


def seed_sample(db: Session, ws: Workspace) -> None:
    """Idempotent: skips when the workspace already has customers."""
    if db.query(Customer).filter_by(workspace_id=ws.id).first() is not None:
        return
    tz = biz.tz_of(ws.timezone)
    branch = (ws.branches or [{}])[0].get("id")
    gym = ws.industry in ("gym", "both")
    spa = ws.industry in ("spa", "both")

    services: list[Service] = []
    for spec in (GYM_SERVICES if gym else []) + (SPA_SERVICES if spa else []):
        s = Service(workspace_id=ws.id, description="", **spec)
        db.add(s)
        services.append(s)
    staff: list[Staff] = []
    if gym:
        staff.append(
            Staff(
                workspace_id=ws.id,
                name="PT Hoàng",
                role=StaffRole.pt,
                working_hours=HOURS,
                branch_id=branch,
            )
        )
    if spa:
        staff.append(
            Staff(
                workspace_id=ws.id,
                name="KTV Linh",
                role=StaffRole.therapist,
                working_hours=HOURS,
                branch_id=branch,
            )
        )
    staff.append(
        Staff(
            workspace_id=ws.id,
            name="Lễ tân Mai",
            role=StaffRole.receptionist,
            working_hours=HOURS,
            branch_id=branch,
        )
    )
    db.add_all(staff)
    db.flush()

    today = datetime.now(UTC).date()
    customers: list[Customer] = []
    for i, (name, phone, tags) in enumerate(CUSTOMERS):
        c = Customer(
            workspace_id=ws.id,
            name=name,
            tags=[*tags, SAMPLE_TAG],
            source="sample",
            notes="Dữ liệu mẫu, bạn có thể xoá.",
        )
        biz.set_phone(c, phone)
        c.last_visit_at = datetime.now(UTC) - timedelta(days=[1, 3, 12, 0, 20][i])
        db.add(c)
        customers.append(c)
    db.flush()

    # memberships: active, expiring (3 days), expired, few sessions left
    plan = [
        (0, 0, today - timedelta(days=10), None, 0),
        (
            1,
            1 if len(services) > 1 else 0,
            today - timedelta(days=87),
            today + timedelta(days=3),
            0,
        ),
        (2, 0, today - timedelta(days=45), today - timedelta(days=15), 0),
        (
            3,
            next((i for i, s in enumerate(services) if s.sessions >= 10), 0),
            today - timedelta(days=20),
            None,
            8,
        ),
    ]
    for ci, si, start, end, used in plan:
        svc = services[si]
        m = Membership(
            workspace_id=ws.id,
            customer_id=customers[ci].id,
            service_id=svc.id,
            start_date=start,
            end_date=end,
            sessions_used=used,
            amount_paid=svc.price,
        )
        biz.apply_service_defaults(m, svc)
        biz.refresh_status(m)
        db.add(m)

    # appointments today and tomorrow + one no-show yesterday
    worker = staff[0]
    svc_appt = next((s for s in services if s.duration_min), services[0])
    for day_offset, hour, ci, status in (
        (0, 9, 0, AppointmentStatus.confirmed),
        (0, 15, 2, AppointmentStatus.booked),
        (1, 10, 1, AppointmentStatus.booked),
        (-1, 18, 3, AppointmentStatus.no_show),
    ):
        start = datetime.combine(
            today + timedelta(days=day_offset), time(hour, 0), tzinfo=tz
        ).astimezone(UTC)
        db.add(
            Appointment(
                workspace_id=ws.id,
                customer_id=customers[ci].id,
                service_id=svc_appt.id,
                staff_id=worker.id,
                branch_id=branch,
                start_at=start,
                end_at=start + timedelta(minutes=svc_appt.duration_min or 60),
                status=status,
                source=AppointmentSource.manual,
            )
        )
    if gym:
        for ci, days_ago in ((0, 1), (0, 3), (1, 3), (3, 0)):
            db.add(
                CheckIn(
                    workspace_id=ws.id,
                    customer_id=customers[ci].id,
                    branch_id=branch,
                    at=datetime.now(UTC) - timedelta(days=days_ago, hours=2),
                )
            )

    # a sample conversation with an AI-suggested reply
    conv = Conversation(
        workspace_id=ws.id,
        channel=Channel.zalo,
        external_thread_id="sample-zalo-1",
        customer_id=customers[4].id,
        unread=True,
        last_message_at=datetime.now(UTC) - timedelta(minutes=30),
        last_inbound_at=datetime.now(UTC) - timedelta(minutes=30),
    )
    db.add(conv)
    db.flush()
    db.add_all(
        [
            Message(
                workspace_id=ws.id,
                conversation_id=conv.id,
                direction=Direction.inbound,
                sender=Sender.customer,
                status=MessageStatus.sent,
                text="Chào shop, bên mình có gói nào cho người mới bắt đầu không?",
                created_at=datetime.now(UTC) - timedelta(minutes=30),
            ),
            Message(
                workspace_id=ws.id,
                conversation_id=conv.id,
                direction=Direction.outbound,
                sender=Sender.ai,
                status=MessageStatus.ai_suggested,
                text=f"Dạ chào chị, bên em có {services[0].name} giá {int(services[0].price):,}đ phù hợp cho người mới ạ. Chị muốn em giữ lịch trải nghiệm thử không ạ?".replace(
                    ",", "."
                ),
                created_at=datetime.now(UTC) - timedelta(minutes=29),
            ),
        ]
    )
    db.flush()
