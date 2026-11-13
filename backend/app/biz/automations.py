"""The six industry flows: definitions, seeding, candidate collection, inbound handling, dry runs.

Each definition is a real Flow (graph of existing node types) plus a
``config_schema`` rendered as a small form on the "Việc tự động" card. Scheduled
flows are started by :class:`BizScheduler`; the reply flow is started by the
channel webhooks through :func:`on_inbound`.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.billing import service as billing
from app.biz import alerts
from app.biz import service as biz
from app.biz.automation_models import AutomationLog, Notification
from app.biz.models import (
    Appointment,
    AppointmentStatus,
    CheckIn,
    Conversation,
    Customer,
    Membership,
    MembershipStatus,
    Message,
    MessageStatus,
    Sender,
    Service,
)
from app.executor.nodes import render_jinja
from app.kb import guard
from app.kb import service as kb
from app.models import Flow, Run, RunStatus, Workspace, WorkspaceMember

log = logging.getLogger(__name__)

PACK = "gym_spa"


# ---------------------------------------------------------------- graph helpers


def _node(id_: str, type_: str, x: int, **data: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "label": id_,
        "type": type_,
        "system_prompt": "",
        "tools_allowed": [],
        "max_turns": 8,
        "on_fail": "stop",
        "retry_limit": 1,
        "command": None,
        "expression": None,
    }
    base.update(data)
    return {"id": id_, "type": "step", "position": {"x": x, "y": 0}, "data": base}


def _edge(a: str, b: str) -> dict[str, Any]:
    return {"id": f"{a}->{b}", "source": a, "target": b, "sourceHandle": None}


def _f(key: str, label: str, type_: str = "text", **extra: Any) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "type": type_,
        "options": extra.pop("options", []),
        "default": extra.pop("default", None),
        "help": extra.pop("help", ""),
        **extra,
    }


MODE_FIELD = _f(
    "mode",
    "Cách gửi",
    "select",
    options=[
        {"value": "suggest", "label": "Gợi ý, nhân viên duyệt rồi mới gửi"},
        {"value": "auto", "label": "Tự gửi ngay"},
    ],
    default="suggest",
)
ZNS_FIELD = _f(
    "zns_template_id",
    "Mẫu ZNS (Zalo, khi khách im hơn 24h)",
    "text",
    help="Template ID đã khai báo ở Kết nối; để trống thì tin chờ nhân viên gửi tay.",
)


#: Graph for every message-type flow: render the prepared message → deliver through the send tool.
def _message_graph(kind: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    nodes = [
        _node("soan", "template", 0, label="Soạn tin", template="{{ input.message }}"),
        _node(
            "gui",
            "output",
            260,
            label="Gửi tin",
            output_kind="text",
            output_source="{{steps.soan.output}}",
            deliver_tool="biz_send_message",
            deliver_args={
                "text": "{{output}}",
                "customer_id": "{{input.customer_id}}",
                "kind": kind,
            },
        ),
    ]
    return nodes, [_edge("soan", "gui")]


REPLY_PROMPT = """{{input.system_context}}

# Hội thoại hiện tại
- conversation_id: {{input.conversation_id}}
- customer_id: {{input.customer_id}}
- Chế độ gửi: {{input.mode}}
- Kênh: {{input.channel}}
- Hôm nay: {{input.today}} ({{input.weekday}})

# Tin khách vừa nhắn
{{input.message}}

# Cách làm việc
1. Gọi biz_customer_card để biết khách là ai và gói của họ (nếu chưa rõ).
2. Nếu khách hỏi giá, gói, nội quy, giờ mở cửa: dùng phần Kiến thức/Dịch vụ ở trên; thiếu thì gọi biz_kb_search.
3. Nếu khách muốn đặt lịch: hỏi đủ ngày giờ và dịch vụ; gọi biz_free_slots để đề xuất 2-3 khung giờ trống; chỉ gọi biz_book_appointment khi khách đã chốt giờ, rồi xác nhận lại ngày giờ, nhân viên, chi nhánh.
4. Trả lời ngắn (tối đa 4 câu), đúng giọng điệu đã chọn, không bịa thông tin.
5. Kết thúc bằng đúng MỘT lần gọi biz_send_message với conversation_id ở trên và toàn bộ câu trả lời; sau đó trả lời lại nội dung đã gửi."""


# ---------------------------------------------------------------- definitions


@dataclass
class FlowDef:
    key: str
    name: str
    description: str
    config_schema: list[dict[str, Any]]
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]
    #: ``collect(db, ws, flow, now) -> [(entity_type, entity_id, milestone, inputs)]`` for scheduled flows.
    collect: Callable[..., list[tuple[str, str, str, dict[str, Any]]]] | None = None
    #: Inputs for the "Chạy thử" button.
    sample: Callable[[Session, Workspace, Flow], dict[str, Any]] | None = None
    enabled_default: bool = True
    industries: tuple[str, ...] = ("gym", "spa", "both")
    trigger_label: str = ""
    inputs_schema: list[dict[str, Any]] = field(default_factory=list)


def _money(n: float) -> str:
    return f"{int(n):,}đ".replace(",", ".")


def _render(template: str, **vars_: Any) -> str:
    try:
        return render_jinja(template, vars_).strip()
    except Exception as exc:  # noqa: BLE001 - owner-written template
        return f"[Lỗi mẫu tin: {exc}]"


def _customer_vars(db: Session, ws: Workspace, c: Customer) -> dict[str, Any]:
    tz = biz.tz_of(ws.timezone)
    out = biz.customer_out(db, c)
    return {
        "ten_khach": c.name,
        "sdt": out.phone,
        "ten_co_so": ws.name,
        "customer_id": c.id,
        "goi": (out.membership or {}).get("service_name") or "",
        "het_han": (out.membership or {}).get("end_date") or "",
        "con_buoi": (out.membership or {}).get("sessions_left"),
        "lan_den": biz.aware(c.last_visit_at).astimezone(tz).strftime("%d/%m")
        if c.last_visit_at
        else "",
    }


def _owner_ids(db: Session, ws_id: str) -> list[str]:
    return list(
        db.scalars(
            select(WorkspaceMember.user_id).where(
                WorkspaceMember.workspace_id == ws_id, WorkspaceMember.role == "owner"
            )
        ).all()
    )


# 1 ------------------------------------------------------------- reply & book


def _sample_reply(db: Session, ws: Workspace, flow: Flow) -> dict[str, Any]:
    cust = db.scalar(
        select(Customer)
        .where(Customer.workspace_id == ws.id)
        .order_by(Customer.created_at)
        .limit(1)
    )
    conv = db.scalar(
        select(Conversation)
        .where(Conversation.workspace_id == ws.id)
        .order_by(Conversation.last_message_at.desc().nullslast())
    )
    return reply_inputs(
        db,
        ws,
        flow,
        conversation_id=conv.id if conv else "",
        customer_id=cust.id if cust else "",
        channel=conv.channel.value if conv else "zalo",
        message="Chào shop, bên mình có gói nào cho người mới không? Mai 9h sáng tập thử được không?",
    )


def reply_inputs(
    db: Session,
    ws: Workspace,
    flow: Flow,
    *,
    conversation_id: str,
    customer_id: str,
    channel: str,
    message: str,
) -> dict[str, Any]:
    s = kb.get_settings_row(db, ws.id)
    tz = biz.tz_of(ws.timezone)
    now = datetime.now(UTC).astimezone(tz)
    cfg = dict(flow.config or {})
    return {
        "conversation_id": conversation_id,
        "customer_id": customer_id,
        "channel": channel,
        "message": message,
        "mode": cfg.get("mode", "suggest"),
        "today": now.strftime("%Y-%m-%d %H:%M"),
        "weekday": ["Thứ hai", "Thứ ba", "Thứ tư", "Thứ năm", "Thứ sáu", "Thứ bảy", "Chủ nhật"][
            now.weekday()
        ],
        "system_context": kb.compose_system_prompt(ws, s, services=kb.services_block(db, ws.id)),
    }


REPLY = FlowDef(
    key="reply_and_book",
    name="Trả lời khách & đặt lịch",
    description="Khi khách nhắn Zalo/Facebook, AI trả lời theo bảng giá, nội quy, FAQ bạn đã tải lên; tra lịch trống của nhân viên và đặt lịch khi khách chốt giờ. Câu hỏi y tế, chấn thương, thuốc sẽ chuyển cho nhân viên.",
    config_schema=[
        MODE_FIELD,
        _f("hours_from", "Giờ AI bắt đầu trả lời", "time", default="07:00"),
        _f(
            "hours_to",
            "Giờ AI ngừng trả lời",
            "time",
            default="22:00",
            help="Ngoài giờ này tin được giữ lại cho nhân viên, AI không trả lời.",
        ),
        _f(
            "handoff_keywords",
            "Từ khoá chuyển người (phẩy)",
            "text",
            default="khiếu nại, hoàn tiền, gặp quản lý",
            help="Thêm vào rào chắn y tế ở mục Trợ lý AI.",
        ),
    ],
    nodes=[
        _node(
            "reply",
            "custom",
            0,
            label="AI trả lời",
            system_prompt=REPLY_PROMPT,
            max_turns=8,
            tools_allowed=[
                "biz_customer_card",
                "biz_kb_search",
                "biz_list_services",
                "biz_list_staff",
                "biz_free_slots",
                "biz_book_appointment",
                "biz_send_message",
            ],
        ),
        _node(
            "ketqua",
            "output",
            260,
            label="Kết quả",
            output_kind="text",
            output_source="{{steps.reply.output}}",
        ),
    ],
    edges=[_edge("reply", "ketqua")],
    sample=_sample_reply,
    trigger_label="Khi khách nhắn tin",
    inputs_schema=[
        {
            "name": k,
            "label": k,
            "type": "textarea" if k in ("message", "system_context") else "text",
            "required": False,
            "default": None,
            "options": [],
            "placeholder": "",
            "description": "",
        }
        for k in (
            "conversation_id",
            "customer_id",
            "channel",
            "message",
            "mode",
            "today",
            "weekday",
            "system_context",
        )
    ],
)


# 2 ------------------------------------------------------------- appointment reminder

REMINDER_TEMPLATE = "{{ ten_co_so }} nhắc {{ ten_khach }}: lịch {{ dich_vu }} lúc {{ gio }} ngày {{ ngay }}{% if nhan_vien %} với {{ nhan_vien }}{% endif %}. Trả lời 1 để XÁC NHẬN, 2 để ĐỔI LỊCH, 3 để HUỶ ạ."


def _collect_reminders(
    db: Session, ws: Workspace, flow: Flow, now: datetime
) -> list[tuple[str, str, str, dict[str, Any]]]:
    cfg = dict(flow.config or {})
    tz = biz.tz_of(ws.timezone)
    out = []
    milestones = [
        ("24h", int(cfg.get("hours_before_1") or 24)),
        ("2h", int(cfg.get("hours_before_2") or 2)),
    ]
    horizon = now + timedelta(hours=max(h for _, h in milestones) + 1)
    rows = db.scalars(
        select(Appointment).where(
            Appointment.workspace_id == ws.id,
            Appointment.status.in_((AppointmentStatus.booked, AppointmentStatus.confirmed)),
            Appointment.start_at > now,
            Appointment.start_at <= horizon,
        )
    ).all()
    for a in rows:
        start = biz.aware(a.start_at)
        for name, hours in milestones:
            # due when inside the window [hours before, hours before - 90 minutes); never after the slot
            if (
                start - timedelta(hours=hours)
                <= now
                < start - timedelta(hours=hours) + timedelta(minutes=90)
            ):
                cust = db.get(Customer, a.customer_id)
                if cust is None:
                    continue
                svc = db.get(Service, a.service_id) if a.service_id else None
                staff = a.staff.name if a.staff else ""
                local = start.astimezone(tz)
                vars_ = {
                    **_customer_vars(db, ws, cust),
                    "dich_vu": svc.name if svc else "buổi hẹn",
                    "gio": local.strftime("%H:%M"),
                    "ngay": local.strftime("%d/%m"),
                    "nhan_vien": staff,
                }
                message = _render(cfg.get("message_template") or REMINDER_TEMPLATE, **vars_)
                out.append(
                    (
                        "appointment",
                        a.id,
                        name,
                        {"customer_id": cust.id, "appointment_id": a.id, "message": message},
                    )
                )
    return out


REMINDER = FlowDef(
    key="appointment_reminder",
    name="Nhắc lịch hẹn",
    description="Trước giờ hẹn 24 giờ và 2 giờ, nhắn cho khách kèm lựa chọn Xác nhận / Đổi lịch / Huỷ. Khách trả lời 1, 2, 3 thì lịch tự cập nhật hoặc chuyển nhân viên.",
    config_schema=[
        _f("hours_before_1", "Nhắc lần 1 (giờ trước hẹn)", "number", default=24),
        _f("hours_before_2", "Nhắc lần 2 (giờ trước hẹn)", "number", default=2),
        _f(
            "channel",
            "Kênh",
            "select",
            options=[{"value": "chat", "label": "Zalo / Facebook khách đang chat"}],
            default="chat",
        ),
        MODE_FIELD | {"default": "auto"},
        ZNS_FIELD,
        _f(
            "message_template",
            "Mẫu tin",
            "textarea",
            default=REMINDER_TEMPLATE,
            help="Biến: {{ ten_khach }}, {{ ten_co_so }}, {{ dich_vu }}, {{ gio }}, {{ ngay }}, {{ nhan_vien }}",
        ),
    ],
    nodes=_message_graph("reminder")[0],
    edges=_message_graph("reminder")[1],
    collect=_collect_reminders,
    sample=lambda db, ws, flow: {
        "customer_id": _first_customer_id(db, ws),
        "message": _render(
            (flow.config or {}).get("message_template") or REMINDER_TEMPLATE,
            ten_khach="Nguyễn Văn Thử",
            ten_co_so=ws.name,
            dich_vu="PT 60 phút",
            gio="09:00",
            ngay=(date.today() + timedelta(days=1)).strftime("%d/%m"),
            nhan_vien="PT Hoàng",
        ),
    },
    trigger_label="Tự động trước giờ hẹn",
)


def _first_customer_id(db: Session, ws: Workspace) -> str:
    c = db.scalar(
        select(Customer)
        .where(Customer.workspace_id == ws.id)
        .order_by(Customer.created_at)
        .limit(1)
    )
    return c.id if c else ""


# 3 ------------------------------------------------------------- renewal reminder

RENEWAL_TEMPLATE = "{{ ten_co_so }} xin chào {{ ten_khach }}! {{ goi }} của mình {% if con_buoi is not none %}chỉ còn {{ con_buoi }} buổi{% else %}sẽ hết hạn ngày {{ het_han }}{% endif %}. {% if uu_dai %}Gia hạn trong tuần này được {{ uu_dai }}. {% endif %}Mình cần {{ ten_co_so }} giữ gói cho không ạ?"


def _collect_renewals(
    db: Session, ws: Workspace, flow: Flow, now: datetime
) -> list[tuple[str, str, str, dict[str, Any]]]:
    cfg = dict(flow.config or {})
    today = now.astimezone(biz.tz_of(ws.timezone)).date()
    days_1, days_2 = int(cfg.get("days_before_1") or 7), int(cfg.get("days_before_2") or 1)
    sessions_left_at = int(cfg.get("sessions_left") or 2)
    out = []
    rows = db.scalars(
        select(Membership).where(
            Membership.workspace_id == ws.id, Membership.status != MembershipStatus.frozen
        )
    ).all()
    for m in rows:
        biz.refresh_status(m, today)
        if m.status is MembershipStatus.expired:
            continue
        milestone = None
        left = biz.sessions_left(m)
        if m.end_date:
            days = (m.end_date - today).days
            if days == days_1:
                milestone = f"d{days_1}"
            elif days == days_2:
                milestone = f"d{days_2}"
        if milestone is None and left is not None and left == sessions_left_at:
            milestone = f"s{sessions_left_at}"
        if milestone is None:
            continue
        cust = db.get(Customer, m.customer_id)
        if cust is None:
            continue
        svc = db.get(Service, m.service_id)
        vars_ = {
            **_customer_vars(db, ws, cust),
            "goi": svc.name if svc else "",
            "het_han": m.end_date.strftime("%d/%m") if m.end_date else "",
            "con_buoi": left,
            "uu_dai": (cfg.get("offer") or "").strip() if cfg.get("offer_enabled") else "",
        }
        message = _render(cfg.get("message_template") or RENEWAL_TEMPLATE, **vars_)
        out.append(
            (
                "membership",
                m.id,
                milestone,
                {"customer_id": cust.id, "membership_id": m.id, "message": message},
            )
        )
    db.commit()
    return out


RENEWAL = FlowDef(
    key="renewal_reminder",
    name="Nhắc gia hạn gói",
    description="7 ngày và 1 ngày trước khi gói hết hạn, hoặc khi chỉ còn 2 buổi, nhắn mời khách gia hạn; kèm ưu đãi nếu bạn bật.",
    config_schema=[
        _f("days_before_1", "Nhắc lần 1 (ngày trước hết hạn)", "number", default=7),
        _f("days_before_2", "Nhắc lần 2 (ngày trước hết hạn)", "number", default=1),
        _f("sessions_left", "Nhắc khi còn (buổi)", "number", default=2),
        _f("offer_enabled", "Kèm ưu đãi", "bool", default=False),
        _f(
            "offer",
            "Nội dung ưu đãi",
            "text",
            default="giảm 10%",
            help="Chỉ dùng khi bật “Kèm ưu đãi”.",
        ),
        MODE_FIELD,
        ZNS_FIELD,
        _f(
            "message_template",
            "Mẫu tin",
            "textarea",
            default=RENEWAL_TEMPLATE,
            help="Biến: {{ ten_khach }}, {{ goi }}, {{ het_han }}, {{ con_buoi }}, {{ uu_dai }}, {{ ten_co_so }}",
        ),
    ],
    nodes=_message_graph("renewal")[0],
    edges=_message_graph("renewal")[1],
    collect=_collect_renewals,
    sample=lambda db, ws, flow: {
        "customer_id": _first_customer_id(db, ws),
        "message": _render(
            (flow.config or {}).get("message_template") or RENEWAL_TEMPLATE,
            ten_khach="Nguyễn Văn Thử",
            ten_co_so=ws.name,
            goi="Gói tập 3 tháng",
            het_han=(date.today() + timedelta(days=7)).strftime("%d/%m"),
            con_buoi=None,
            uu_dai=((flow.config or {}).get("offer") or "")
            if (flow.config or {}).get("offer_enabled")
            else "",
        ),
    },
    trigger_label="Tự động mỗi ngày",
)


# 4 ------------------------------------------------------------- churn care

CARE_TEMPLATE = "{{ ten_khach }} ơi, lâu rồi không thấy mình ghé {{ ten_co_so }} ({{ lan_den }}). Mọi thứ ổn chứ ạ? Tuần này mình muốn đặt lịch giờ nào không, {{ ten_co_so }} giữ chỗ cho?"


def _collect_care(
    db: Session, ws: Workspace, flow: Flow, now: datetime
) -> list[tuple[str, str, str, dict[str, Any]]]:
    cfg = dict(flow.config or {})
    tz = biz.tz_of(ws.timezone)
    days = int(cfg.get("days_inactive") or 10)
    cycle = int(cfg.get("cycle_days") or 10)
    out = []
    customers = db.scalars(select(Customer).where(Customer.workspace_id == ws.id)).all()
    for c in customers:
        if "mẫu" in (c.tags or []) and not cfg.get("include_sample"):
            continue
        active = db.scalar(
            select(Membership).where(
                Membership.customer_id == c.id,
                Membership.status.in_((MembershipStatus.active, MembershipStatus.expiring)),
            )
        )
        if active is None:
            continue  # only customers with a live package
        last = c.last_visit_at
        last_checkin = db.scalar(
            select(CheckIn.at)
            .where(CheckIn.customer_id == c.id)
            .order_by(CheckIn.at.desc())
            .limit(1)
        )
        if last_checkin and (last is None or biz.aware(last_checkin) > biz.aware(last)):
            last = last_checkin
        if last is None:
            continue
        gap = (now - biz.aware(last)).days
        threshold = cycle if ws.industry == "spa" else days
        if gap < threshold:
            continue
        milestone = f"gap{threshold}-{biz.aware(last).astimezone(tz).strftime('%Y%m%d')}"  # once per absence
        vars_ = _customer_vars(db, ws, c)
        message = _render(cfg.get("message_template") or CARE_TEMPLATE, **vars_)
        out.append(("customer", c.id, milestone, {"customer_id": c.id, "message": message}))
    return out


CARE = FlowDef(
    key="churn_care",
    name="Chăm khách sắp bỏ",
    description="Gym: khách có gói nhưng 10 ngày không check-in. Spa: quá chu kỳ liệu trình chưa quay lại. Gửi tin hỏi thăm; mặc định nhân viên duyệt trước khi gửi.",
    config_schema=[
        _f("days_inactive", "Gym: số ngày không check-in", "number", default=10),
        _f("cycle_days", "Spa: chu kỳ liệu trình (ngày)", "number", default=10),
        MODE_FIELD,
        ZNS_FIELD,
        _f(
            "message_template",
            "Mẫu tin",
            "textarea",
            default=CARE_TEMPLATE,
            help="Biến: {{ ten_khach }}, {{ ten_co_so }}, {{ lan_den }}, {{ goi }}",
        ),
    ],
    nodes=_message_graph("care")[0],
    edges=_message_graph("care")[1],
    collect=_collect_care,
    sample=lambda db, ws, flow: {
        "customer_id": _first_customer_id(db, ws),
        "message": _render(
            (flow.config or {}).get("message_template") or CARE_TEMPLATE,
            ten_khach="Nguyễn Văn Thử",
            ten_co_so=ws.name,
            lan_den=(date.today() - timedelta(days=12)).strftime("%d/%m"),
            goi="Gói tập 3 tháng",
        ),
    },
    trigger_label="Tự động mỗi ngày",
)


# 5 ------------------------------------------------------------- review request

REVIEW_TEMPLATE = "{{ ten_co_so }} cảm ơn {{ ten_khach }} đã đồng hành! Nếu hài lòng, mình cho {{ ten_co_so }} một đánh giá 5 sao tại đây nhé: {{ link }} 🙏"


def _collect_reviews(
    db: Session, ws: Workspace, flow: Flow, now: datetime
) -> list[tuple[str, str, str, dict[str, Any]]]:
    cfg = dict(flow.config or {})
    link = (cfg.get("review_link") or "").strip()
    if not link:
        return []
    visits_needed = int(cfg.get("after_visits") or 3)
    out = []
    customers = db.scalars(select(Customer).where(Customer.workspace_id == ws.id)).all()
    for c in customers:
        if "mẫu" in (c.tags or []) and not cfg.get("include_sample"):
            continue
        visits = (
            db.scalar(select(func.count()).select_from(CheckIn).where(CheckIn.customer_id == c.id))
            or 0
        )
        visits += (
            db.scalar(
                select(func.count())
                .select_from(Appointment)
                .where(
                    Appointment.customer_id == c.id,
                    Appointment.status.in_((AppointmentStatus.arrived, AppointmentStatus.done)),
                )
            )
            or 0
        )
        finished = db.scalar(
            select(Membership).where(
                Membership.customer_id == c.id,
                Membership.sessions_total > 0,
                Membership.sessions_used >= Membership.sessions_total,
            )
        )
        if visits < visits_needed and finished is None:
            continue
        vars_ = {**_customer_vars(db, ws, c), "link": link}
        out.append(
            (
                "customer",
                c.id,
                "review",
                {
                    "customer_id": c.id,
                    "message": _render(cfg.get("message_template") or REVIEW_TEMPLATE, **vars_),
                },
            )
        )
    return out


REVIEW = FlowDef(
    key="review_request",
    name="Xin đánh giá",
    description="Sau lần đến thứ 3 hoặc khi kết thúc liệu trình, gửi link Google Maps nhờ khách đánh giá. Mỗi khách chỉ nhận đúng một lần.",
    config_schema=[
        _f(
            "review_link",
            "Link đánh giá Google Maps",
            "text",
            default="",
            help="Dán link “Viết bài đánh giá” của cơ sở. Để trống thì việc này không chạy.",
        ),
        _f("after_visits", "Gửi sau lần đến thứ", "number", default=3),
        MODE_FIELD | {"default": "auto"},
        ZNS_FIELD,
        _f(
            "message_template",
            "Mẫu tin",
            "textarea",
            default=REVIEW_TEMPLATE,
            help="Biến: {{ ten_khach }}, {{ ten_co_so }}, {{ link }}",
        ),
    ],
    nodes=_message_graph("review")[0],
    edges=_message_graph("review")[1],
    collect=_collect_reviews,
    sample=lambda db, ws, flow: {
        "customer_id": _first_customer_id(db, ws),
        "message": _render(
            (flow.config or {}).get("message_template") or REVIEW_TEMPLATE,
            ten_khach="Nguyễn Văn Thử",
            ten_co_so=ws.name,
            link=(flow.config or {}).get("review_link") or "https://g.page/r/xxxx/review",
        ),
    },
    enabled_default=False,
    trigger_label="Tự động mỗi ngày",
)


# 6 ------------------------------------------------------------- daily report

REPORT_TEMPLATE = """📊 {{ ten_co_so }} – báo cáo ngày {{ ngay }}
• Lịch hôm nay: {{ lich_hom_nay }} (đã đến {{ da_den }}, không đến {{ no_show }})
• Lịch ngày mai: {{ lich_ngay_mai }}
• Doanh thu gói bán hôm nay: {{ doanh_thu }}
• Gói sắp hết hạn (7 ngày): {{ sap_het_han }}
• Tin chưa trả lời: {{ tin_chua_tra_loi }}
• Khách mới hôm nay: {{ khach_moi }}"""


def daily_stats(db: Session, ws: Workspace, now: datetime) -> dict[str, Any]:
    tz = biz.tz_of(ws.timezone)
    local = now.astimezone(tz)
    day = local.date()
    lo = datetime.combine(day, time.min, tzinfo=tz).astimezone(UTC)
    hi = lo + timedelta(days=1)
    appts = db.scalars(
        select(Appointment).where(
            Appointment.workspace_id == ws.id, Appointment.start_at >= lo, Appointment.start_at < hi
        )
    ).all()
    tomorrow = (
        db.scalar(
            select(func.count())
            .select_from(Appointment)
            .where(
                Appointment.workspace_id == ws.id,
                Appointment.start_at >= hi,
                Appointment.start_at < hi + timedelta(days=1),
                Appointment.status.in_(biz.BLOCKING),
            )
        )
        or 0
    )
    revenue = (
        db.scalar(
            select(func.coalesce(func.sum(Membership.amount_paid), 0.0)).where(
                Membership.workspace_id == ws.id,
                Membership.created_at >= lo,
                Membership.created_at < hi,
            )
        )
        or 0.0
    )
    expiring = sum(
        1
        for m in db.scalars(select(Membership).where(Membership.workspace_id == ws.id)).all()
        if biz.compute_status(m, day) is MembershipStatus.expiring
    )
    unread = (
        db.scalar(
            select(func.count())
            .select_from(Conversation)
            .where(Conversation.workspace_id == ws.id, Conversation.unread.is_(True))
        )
        or 0
    )
    new_customers = (
        db.scalar(
            select(func.count())
            .select_from(Customer)
            .where(
                Customer.workspace_id == ws.id, Customer.created_at >= lo, Customer.created_at < hi
            )
        )
        or 0
    )
    return {
        "ten_co_so": ws.name,
        "ngay": local.strftime("%d/%m/%Y"),
        "lich_hom_nay": len([a for a in appts if a.status is not AppointmentStatus.cancelled]),
        "da_den": len(
            [a for a in appts if a.status in (AppointmentStatus.arrived, AppointmentStatus.done)]
        ),
        "no_show": len([a for a in appts if a.status is AppointmentStatus.no_show]),
        "lich_ngay_mai": tomorrow,
        "doanh_thu": _money(revenue),
        "sap_het_han": expiring,
        "tin_chua_tra_loi": unread,
        "khach_moi": new_customers,
    }


def _collect_report(
    db: Session, ws: Workspace, flow: Flow, now: datetime
) -> list[tuple[str, str, str, dict[str, Any]]]:
    cfg = dict(flow.config or {})
    tz = biz.tz_of(ws.timezone)
    local = now.astimezone(tz)
    hh, _, mm = str(cfg.get("send_at") or "21:00").partition(":")
    due = local.replace(hour=int(hh or 21), minute=int(mm or 0), second=0, microsecond=0)
    if not (due <= local < due + timedelta(minutes=90)):
        return []
    stats = daily_stats(db, ws, now)
    message = _render(cfg.get("message_template") or REPORT_TEMPLATE, **stats)
    return [
        (
            "day",
            local.strftime("%Y-%m-%d"),
            "report",
            {"message": message, "title": f"Báo cáo ngày {stats['ngay']}"},
        )
    ]


REPORT = FlowDef(
    key="daily_report",
    name="Báo cáo cuối ngày",
    description="Mỗi tối gửi cho bạn: lịch hôm nay và ngày mai, số khách không đến, doanh thu gói, gói sắp hết hạn, tin chưa trả lời, khách mới.",
    config_schema=[
        _f("send_at", "Giờ gửi", "time", default="21:00"),
        _f(
            "message_template",
            "Mẫu báo cáo",
            "textarea",
            default=REPORT_TEMPLATE,
            help="Biến: {{ ngay }}, {{ lich_hom_nay }}, {{ da_den }}, {{ no_show }}, {{ lich_ngay_mai }}, {{ doanh_thu }}, {{ sap_het_han }}, {{ tin_chua_tra_loi }}, {{ khach_moi }}",
        ),
    ],
    nodes=[
        _node("soan", "template", 0, label="Soạn báo cáo", template="{{ input.message }}"),
        _node(
            "gui",
            "output",
            260,
            label="Gửi chủ cơ sở",
            output_kind="text",
            output_source="{{steps.soan.output}}",
            deliver_tool="biz_notify_owner",
            deliver_args={"text": "{{output}}", "title": "{{input.title}}", "kind": "report"},
        ),
    ],
    edges=[_edge("soan", "gui")],
    collect=_collect_report,
    sample=lambda db, ws, flow: {
        "title": "Báo cáo thử",
        "message": _render(
            (flow.config or {}).get("message_template") or REPORT_TEMPLATE,
            **daily_stats(db, ws, datetime.now(UTC)),
        ),
    },
    trigger_label="Mỗi tối",
)

DEFS: dict[str, FlowDef] = {d.key: d for d in (REPLY, REMINDER, RENEWAL, CARE, REVIEW, REPORT)}


# ---------------------------------------------------------------- seeding


def default_config(d: FlowDef) -> dict[str, Any]:
    return {f["key"]: f.get("default") for f in d.config_schema}


def seed_flows(db: Session, ws: Workspace, owner_user_id: str) -> int:
    """Create the six flows for a workspace (idempotent by flow_key)."""
    existing = {
        f.flow_key for f in db.scalars(select(Flow).where(Flow.workspace_id == ws.id)).all()
    }
    added = 0
    for d in DEFS.values():
        if d.key in existing or ws.industry not in d.industries:
            continue
        db.add(
            Flow(
                user_id=owner_user_id,
                workspace_id=ws.id,
                industry_pack=PACK,
                flow_key=d.key,
                name=d.name,
                description=d.description,
                nodes=d.nodes,
                edges=d.edges,
                input_schema=d.inputs_schema,
                sandbox={"enabled": False},
                config_schema=d.config_schema,
                config=default_config(d),
                enabled=d.enabled_default,
            )
        )
        added += 1
    if added:
        db.commit()
    return added


def workspace_flows(db: Session, ws_id: str) -> list[Flow]:
    return list(
        db.scalars(select(Flow).where(Flow.workspace_id == ws_id).order_by(Flow.created_at)).all()
    )


# ---------------------------------------------------------------- starting runs


def start_run(
    db: Session,
    flow: Flow,
    inputs: dict[str, Any],
    starter,
    *,
    task: str = "",
    dry_run: bool = False,
) -> Run:
    run = Run(
        flow_id=flow.id,
        user_id=flow.user_id,
        repo_url="",
        branch="main",
        task_input=task,
        inputs={**inputs, **({"_dry_run": True} if dry_run else {})},
        sandbox={"enabled": False},
        status=RunStatus.queued,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    if starter is not None:
        starter(run.id)
    return run


def already_done(db: Session, ws_id: str, flow_key: str, entity_id: str, milestone: str) -> bool:
    return (
        db.scalar(
            select(AutomationLog.id).where(
                AutomationLog.workspace_id == ws_id,
                AutomationLog.flow_key == flow_key,
                AutomationLog.entity_id == entity_id,
                AutomationLog.milestone == milestone,
            )
        )
        is not None
    )


def tick_workspace(db: Session, ws: Workspace, starter, now: datetime | None = None) -> list[Run]:
    """Evaluate every enabled scheduled flow of ``ws`` and start the runs that are due."""
    now = now or datetime.now(UTC)
    started: list[Run] = []
    if not billing.is_active(db, ws, now):
        return started  # expired trial / subscription: nothing runs until renewed
    for flow in workspace_flows(db, ws.id):
        d = DEFS.get(flow.flow_key or "")
        if d is None or d.collect is None or not flow.enabled:
            continue
        try:
            candidates = d.collect(db, ws, flow, now)
        except Exception:
            log.exception("collect failed for %s/%s", ws.id, flow.flow_key)
            continue
        for entity_type, entity_id, milestone, inputs in candidates:
            if already_done(db, ws.id, d.key, entity_id, milestone):
                continue
            run = start_run(
                db, flow, inputs, starter, task=f"{d.name}: {entity_type} {entity_id} ({milestone})"
            )
            db.add(
                AutomationLog(
                    workspace_id=ws.id,
                    flow_key=d.key,
                    entity_type=entity_type,
                    entity_id=entity_id,
                    milestone=milestone,
                    run_id=run.id,
                    outcome="queued",
                )
            )
            db.commit()
            started.append(run)
    return started


# ---------------------------------------------------------------- inbound messages


def _within_hours(cfg: dict[str, Any], now_local: datetime) -> bool:
    def parse(v: Any, default: str) -> time:
        hh, _, mm = str(v or default).partition(":")
        try:
            return time(int(hh), int(mm or 0))
        except ValueError:
            hh2, _, mm2 = default.partition(":")
            return time(int(hh2), int(mm2))

    start, end = parse(cfg.get("hours_from"), "07:00"), parse(cfg.get("hours_to"), "22:00")
    t = now_local.time()
    return start <= t < end if start <= end else not (end <= t < start)


def _handle_reminder_reply(db: Session, ws: Workspace, conv: Conversation, text: str) -> str | None:
    """1 / 2 / 3 after a reminder: confirm, ask staff to reschedule, cancel."""
    choice = text.strip()
    if choice not in ("1", "2", "3") or not conv.customer_id:
        return None
    recent = db.scalar(
        select(AutomationLog)
        .where(
            AutomationLog.workspace_id == ws.id,
            AutomationLog.flow_key == "appointment_reminder",
            AutomationLog.created_at >= datetime.now(UTC) - timedelta(days=2),
        )
        .order_by(AutomationLog.created_at.desc())
    )
    if recent is None:
        return None
    appt = db.get(Appointment, recent.entity_id)
    if (
        appt is None
        or appt.customer_id != conv.customer_id
        or appt.status not in (AppointmentStatus.booked, AppointmentStatus.confirmed)
    ):
        return None
    tz = biz.tz_of(ws.timezone)
    when = biz.aware(appt.start_at).astimezone(tz).strftime("%H:%M %d/%m")
    if choice == "1":
        appt.status = AppointmentStatus.confirmed
        reply = f"Dạ {ws.name} đã ghi nhận xác nhận lịch {when}. Hẹn gặp mình ạ!"
    elif choice == "3":
        appt.status = AppointmentStatus.cancelled
        reply = (
            f"Dạ {ws.name} đã huỷ lịch {when}. Khi nào mình rảnh nhắn lại để đặt lịch mới nhé ạ."
        )
    else:
        conv.ai_enabled = False
        reply = f"Dạ mình muốn đổi lịch {when}, nhân viên sẽ nhắn lại để chọn giờ mới ngay ạ."
    db.commit()
    return reply


def on_inbound(
    db: Session, ws: Workspace, conv: Conversation, message: Message, starter
) -> dict[str, Any]:
    """Decide what happens after a customer message: handoff, reminder reply, or the AI flow."""
    from app.connectors.messaging import record_outbound

    outcome: dict[str, Any] = {"action": "none"}
    if not conv.ai_enabled:
        return {"action": "staff"}  # a human took over this conversation
    flow = db.scalar(
        select(Flow).where(Flow.workspace_id == ws.id, Flow.flow_key == "reply_and_book")
    )
    if flow is None or not flow.enabled:
        return {"action": "disabled"}
    cfg = dict(flow.config or {})
    tz = biz.tz_of(ws.timezone)
    now_local = datetime.now(UTC).astimezone(tz)
    reply = _handle_reminder_reply(db, ws, conv, message.text)
    if reply is not None:
        record_outbound(
            db,
            conv,
            reply,
            Sender.ai,
            None,
            status=MessageStatus.ai_suggested
            if cfg.get("mode", "suggest") != "auto"
            else MessageStatus.sent,
        )
        db.commit()
        return {"action": "reminder_reply", "text": reply}
    s = kb.get_settings_row(db, ws.id)
    extra = [
        k.strip() for k in str(cfg.get("handoff_keywords") or "").split(",") if k.strip()
    ] + list(s.handoff_keywords or [])
    decision = guard.check(message.text, extra, enabled=bool(s.medical_guard))
    if decision.handoff:
        text = kb.handoff_text(s)
        record_outbound(db, conv, text, Sender.ai, None, status=MessageStatus.handoff)
        conv.ai_enabled = False
        conv.unread = True
        db.commit()
        return {"action": "handoff", "category": decision.category, "text": text}
    if not _within_hours(cfg, now_local):
        conv.unread = True
        db.commit()
        return {"action": "outside_hours"}
    allowed, reason = billing.ai_allowed(db, ws)
    if not allowed:
        conv.unread = True
        db.commit()
        alerts.billing_block(db, ws, reason)
        return {"action": "blocked", "reason": reason}
    inputs = reply_inputs(
        db,
        ws,
        flow,
        conversation_id=conv.id,
        customer_id=conv.customer_id or "",
        channel=conv.channel.value,
        message=message.text,
    )
    run = start_run(
        db, flow, inputs, starter, task=f"Trả lời {conv.channel.value} {conv.external_thread_id}"
    )
    db.add(
        AutomationLog(
            workspace_id=ws.id,
            flow_key="reply_and_book",
            entity_type="message",
            entity_id=message.id,
            milestone="reply",
            run_id=run.id,
            outcome="queued",
        )
    )
    db.commit()
    outcome = {"action": "run", "run_id": run.id}
    return outcome


# ---------------------------------------------------------------- stats for cards


def card_stats(db: Session, ws_id: str, flow: Flow) -> dict[str, Any]:
    since = datetime.now(UTC) - timedelta(days=7)
    runs = db.scalars(
        select(Run)
        .where(Run.flow_id == flow.id, Run.created_at >= since)
        .order_by(Run.created_at.desc())
    ).all()
    real = [r for r in runs if not (r.inputs or {}).get("_dry_run")]
    last = real[0] if real else None
    return {
        "runs_7d": len(real),
        "failed_7d": len([r for r in real if r.status is RunStatus.failed]),
        "last_run_at": last.created_at if last else None,
        "last_error": last.error if last and last.status is RunStatus.failed else None,
    }


def unread_notifications(db: Session, ws_id: str, limit: int = 20) -> list[Notification]:
    return list(
        db.scalars(
            select(Notification)
            .where(Notification.workspace_id == ws_id)
            .order_by(Notification.created_at.desc())
            .limit(limit)
        ).all()
    )
