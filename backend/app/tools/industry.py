"""Tools the industry flows call: customer card, free slots, booking, messaging, KB, owner notice.

They read ``ctx.workspace_id`` (set by the executor from the flow) so a flow can
never touch another workspace, ``ctx.flow_config`` (the card's settings, e.g.
``mode``) and ``ctx.dry_run`` (the "Chạy thử" button: describe, never persist).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.biz import service as biz
from app.biz.models import (
    Appointment,
    AppointmentSource,
    AppointmentStatus,
    Conversation,
    Customer,
    MessageStatus,
    Sender,
    Service,
    Staff,
)
from app.db import SessionLocal
from app.tools.base import Tool, ToolCategory, ToolContext, ToolError, schema

#: Tests swap this for the in-memory factory.
session_factory: sessionmaker[Session] = SessionLocal


def _ws(ctx: ToolContext) -> str:
    if not ctx.workspace_id:
        raise ToolError("this tool only works inside an industry flow (no workspace)")
    return ctx.workspace_id


def _workspace(db: Session, ws_id: str):
    from app.models import Workspace

    ws = db.get(Workspace, ws_id)
    if ws is None:
        raise ToolError("workspace not found")
    return ws


def _fmt_dt(dt: datetime, tz) -> str:
    return biz.aware(dt).astimezone(tz).strftime("%H:%M %d/%m/%Y")


class CustomerCard(Tool):
    name = "biz_customer_card"
    description = "Thông tin khách: tên, SĐT, nhãn, gói đang dùng (trạng thái, hạn, số buổi còn), 5 lịch hẹn gần nhất. Dùng conversation_id hoặc customer_id."
    category = ToolCategory.industry
    input_schema = schema(
        {"customer_id": {"type": "string"}, "conversation_id": {"type": "string"}}
    )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        ws_id = _ws(ctx)
        with session_factory() as db:
            cust = None
            if args.get("conversation_id"):
                conv = db.get(Conversation, args["conversation_id"])
                if conv is None or conv.workspace_id != ws_id:
                    raise ToolError("conversation not found")
                cust = db.get(Customer, conv.customer_id) if conv.customer_id else None
            elif args.get("customer_id"):
                cust = db.get(Customer, args["customer_id"])
            if cust is None or cust.workspace_id != ws_id:
                return "Khách chưa có hồ sơ (khách mới)."
            out = biz.customer_out(db, cust)
            tz = biz.tz_of(_workspace(db, ws_id).timezone)
            lines = [
                f"Tên: {out.name}",
                f"SĐT: {out.phone or 'chưa có'}",
                f"Nhãn: {', '.join(out.tags) or '-'}",
                f"Lần đến gần nhất: {_fmt_dt(cust.last_visit_at, tz) if cust.last_visit_at else 'chưa'}",
            ]
            if out.membership:
                m = out.membership
                lines.append(
                    f"Gói: {m['service_name']} · {m['status']} · hết hạn {m['end_date'] or '-'} · còn {m['sessions_left'] if m['sessions_left'] is not None else '-'} buổi"
                )
            else:
                lines.append("Gói: chưa có")
            appts = db.scalars(
                select(Appointment)
                .where(Appointment.customer_id == cust.id)
                .order_by(Appointment.start_at.desc())
                .limit(5)
            ).all()
            if appts:
                lines.append("Lịch hẹn gần nhất:")
                for a in appts:
                    svc = db.get(Service, a.service_id) if a.service_id else None
                    lines.append(
                        f"  - {_fmt_dt(a.start_at, tz)} · {svc.name if svc else 'dịch vụ'} · {a.status.value}"
                    )
            lines.append(f"customer_id={cust.id}")
            return "\n".join(lines)


class ListServices(Tool):
    name = "biz_list_services"
    description = (
        "Danh sách dịch vụ/gói đang bán với giá, số buổi, thời lượng và service_id (để đặt lịch)."
    )
    category = ToolCategory.industry
    input_schema = schema({})

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        ws_id = _ws(ctx)
        with session_factory() as db:
            rows = db.scalars(
                select(Service)
                .where(Service.workspace_id == ws_id, Service.active.is_(True))
                .order_by(Service.category, Service.name)
            ).all()
            if not rows:
                return "Chưa có dịch vụ nào."
            return "\n".join(
                f"- {s.name} ({s.category.value}): {int(s.price):,}đ".replace(",", ".")
                + (f", {s.sessions} buổi" if s.sessions else "")
                + (f", {s.duration_min} phút" if s.duration_min else "")
                + f" · service_id={s.id}"
                for s in rows
            )


class ListStaff(Tool):
    name = "biz_list_staff"
    description = "Nhân viên đang làm việc (PT, kỹ thuật viên) kèm chi nhánh và staff_id."
    category = ToolCategory.industry
    input_schema = schema({})

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        ws_id = _ws(ctx)
        with session_factory() as db:
            ws = _workspace(db, ws_id)
            names = {b.get("id"): b.get("name") for b in (ws.branches or [])}
            rows = db.scalars(
                select(Staff)
                .where(Staff.workspace_id == ws_id, Staff.active.is_(True))
                .order_by(Staff.name)
            ).all()
            return (
                "\n".join(
                    f"- {s.name} · {s.role.value} · {names.get(s.branch_id, 'chưa gán chi nhánh')} · staff_id={s.id}"
                    for s in rows
                )
                or "Chưa có nhân viên."
            )


class FreeSlots(Tool):
    name = "biz_free_slots"
    description = "Giờ trống trong ngày (YYYY-MM-DD, giờ địa phương) theo nhân viên/chi nhánh để đề xuất cho khách. Trả về tối đa 8 khung giờ mỗi nhân viên."
    category = ToolCategory.industry
    input_schema = schema(
        {
            "day": {"type": "string"},
            "staff_id": {"type": "string"},
            "branch_id": {"type": "string"},
            "duration_min": {"type": "integer"},
        },
        ["day"],
    )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        ws_id = _ws(ctx)
        try:
            day = date.fromisoformat(str(args["day"])[:10])
        except ValueError as exc:
            raise ToolError("day must be YYYY-MM-DD") from exc
        with session_factory() as db:
            ws = _workspace(db, ws_id)
            tz = biz.tz_of(ws.timezone)
            lo = datetime.combine(day, datetime.min.time(), tzinfo=tz).astimezone(UTC)
            hi = lo + timedelta(days=1)
            q = select(Staff).where(Staff.workspace_id == ws_id, Staff.active.is_(True))
            if args.get("staff_id"):
                q = q.where(Staff.id == args["staff_id"])
            if args.get("branch_id"):
                q = q.where(Staff.branch_id == args["branch_id"])
            out = []
            for st in db.scalars(q.order_by(Staff.name)).all():
                busy = biz.busy_intervals(db, ws_id, st.id, lo, hi)
                slots = biz.free_slots(
                    day,
                    st,
                    busy,
                    duration_min=int(args.get("duration_min") or 60),
                    step_min=30,
                    tz=tz,
                )
                times = ", ".join(s.astimezone(tz).strftime("%H:%M") for s, _ in slots[:8])
                out.append(f"- {st.name} (staff_id={st.id}): {times or 'hết chỗ / nghỉ'}")
            return f"Ngày {day.strftime('%d/%m/%Y')}:\n" + (
                "\n".join(out) if out else "Không có nhân viên phù hợp."
            )


class BookAppointment(Tool):
    name = "biz_book_appointment"
    description = "Đặt lịch hẹn cho khách (start_at ISO giờ địa phương, ví dụ 2026-11-12T09:00). Trả lỗi nếu nhân viên bận. Chỉ gọi sau khi khách đồng ý giờ."
    category = ToolCategory.industry
    input_schema = schema(
        {
            "customer_id": {"type": "string"},
            "start_at": {"type": "string"},
            "service_id": {"type": "string"},
            "staff_id": {"type": "string"},
            "branch_id": {"type": "string"},
            "notes": {"type": "string"},
        },
        ["customer_id", "start_at"],
    )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        ws_id = _ws(ctx)
        with session_factory() as db:
            ws = _workspace(db, ws_id)
            tz = biz.tz_of(ws.timezone)
            cust = db.get(Customer, args["customer_id"])
            if cust is None or cust.workspace_id != ws_id:
                raise ToolError("customer not found")
            try:
                start = biz.ensure_utc(datetime.fromisoformat(str(args["start_at"])), tz)
            except ValueError as exc:
                raise ToolError("start_at must be ISO 8601") from exc
            svc = db.get(Service, args["service_id"]) if args.get("service_id") else None
            minutes = svc.duration_min if svc and svc.duration_min else 60
            end = start + timedelta(minutes=minutes)
            staff_id = args.get("staff_id") or None
            clash = biz.conflicting_appointment(db, ws_id, staff_id, start, end)
            if clash is not None:
                raise ToolError(
                    f"nhân viên bận lúc {_fmt_dt(clash.start_at, tz)}; hãy đề xuất giờ khác (dùng biz_free_slots)"
                )
            if ctx.dry_run:
                return f"[chạy thử] Sẽ đặt lịch {_fmt_dt(start, tz)} ({minutes} phút) cho {cust.name} – không lưu."
            a = Appointment(
                workspace_id=ws_id,
                customer_id=cust.id,
                service_id=svc.id if svc else None,
                staff_id=staff_id,
                branch_id=args.get("branch_id") or (ws.branches or [{}])[0].get("id"),
                start_at=start,
                end_at=end,
                status=AppointmentStatus.booked,
                source=AppointmentSource.ai,
                notes=str(args.get("notes") or "")[:2000],
            )
            db.add(a)
            db.commit()
            return f"Đã đặt lịch {_fmt_dt(start, tz)} ({minutes} phút) cho {cust.name}. appointment_id={a.id}"


class SendMessage(Tool):
    name = "biz_send_message"
    description = "Gửi (hoặc đề xuất cho nhân viên duyệt, tuỳ chế độ của việc tự động) một tin nhắn tới khách qua kênh họ đang chat. Gọi đúng một lần ở cuối với toàn bộ câu trả lời."
    category = ToolCategory.industry
    input_schema = schema(
        {
            "text": {"type": "string"},
            "conversation_id": {"type": "string"},
            "customer_id": {"type": "string"},
            "kind": {"type": "string", "description": "reply | reminder | renewal | care | review"},
        },
        ["text"],
    )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        from app.connectors import messaging
        from app.connectors.base import ConnectorError

        ws_id = _ws(ctx)
        text = str(args["text"]).strip()
        if not text:
            raise ToolError("text is empty")
        mode = str(ctx.flow_config.get("mode") or "suggest")
        with session_factory() as db:
            conv = None
            if args.get("conversation_id"):
                conv = db.get(Conversation, args["conversation_id"])
            elif args.get("customer_id"):
                conv = db.scalar(
                    select(Conversation)
                    .where(
                        Conversation.workspace_id == ws_id,
                        Conversation.customer_id == args["customer_id"],
                    )
                    .order_by(Conversation.last_message_at.desc().nullslast())
                )
            if conv is None or conv.workspace_id != ws_id:
                if ctx.dry_run:
                    return f"[chạy thử] Khách chưa có hội thoại Zalo/Facebook; tin sẽ chờ nhân viên gửi tay:\n{text}"
                raise ToolError("khách chưa có hội thoại trên Zalo/Facebook để gửi")
            if ctx.dry_run:
                return f"[chạy thử · chế độ {mode}] Tin nhắn sẽ {'gửi tự động' if mode == 'auto' else 'được đề xuất cho nhân viên duyệt'} tới {conv.channel.value}:\n{text}"
            if mode != "auto" or not conv.ai_enabled:
                messaging.record_outbound(
                    db, conv, text, Sender.ai, None, status=MessageStatus.ai_suggested
                )
                conv.unread = True
                db.commit()
                return "Đã tạo đề xuất; nhân viên sẽ duyệt trước khi gửi."
            try:
                result = await messaging.deliver(
                    db,
                    conv,
                    text,
                    zns_template_id=ctx.flow_config.get("zns_template_id") or None,
                    zns_params={
                        "ten": (db.get(Customer, conv.customer_id).name if conv.customer_id else "")
                    },
                    fb_tag=ctx.flow_config.get("fb_tag") or None,
                )
            except ConnectorError as exc:
                messaging.record_outbound(
                    db, conv, text, Sender.ai, None, status=MessageStatus.ai_suggested
                )
                conv.unread = True
                db.commit()
                return f"Không gửi tự động được ({exc}); đã chuyển thành đề xuất cho nhân viên."
            messaging.record_outbound(db, conv, text, Sender.ai, result)
            db.commit()
            return f"Đã gửi qua {conv.channel.value} ({result.method})."


class KbSearch(Tool):
    name = "biz_kb_search"
    description = "Tra cứu tài liệu của cơ sở (bảng giá, nội quy, FAQ) theo câu hỏi; trả về các đoạn liên quan nhất."
    category = ToolCategory.industry
    input_schema = schema({"question": {"type": "string"}}, ["question"])

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        from app.kb import service as kb

        ws_id = _ws(ctx)
        with session_factory() as db:
            hits = await kb.search(db, ws_id, str(args["question"]), k=4)
        return (
            "\n\n".join(f"[{h.title}]\n{h.text}" for h in hits)
            or "Không tìm thấy đoạn nào trong tài liệu."
        )


class NotifyOwner(Tool):
    name = "biz_notify_owner"
    description = "Gửi thông báo cho chủ cơ sở (báo cáo, cảnh báo): lưu vào mục Thông báo và gửi Zalo nếu chủ đã liên kết."
    category = ToolCategory.industry
    input_schema = schema(
        {
            "title": {"type": "string"},
            "text": {"type": "string"},
            "kind": {"type": "string", "description": "report | alert"},
        },
        ["text"],
    )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        from app.biz.automation_models import Notification

        ws_id = _ws(ctx)
        title = str(args.get("title") or "Thông báo")[:200]
        kind = "alert" if args.get("kind") == "alert" else "report"
        if ctx.dry_run:
            return f"[chạy thử] Thông báo cho chủ cơ sở ({kind}): {title}\n{args['text']}"
        with session_factory() as db:
            n = Notification(workspace_id=ws_id, kind=kind, title=title, body=str(args["text"]))
            db.add(n)
            db.commit()
            delivered = await _deliver_owner_zalo(db, ws_id, f"{title}\n{args['text']}")
            if delivered:
                n.delivered_via = "zalo"
                db.commit()
        return "Đã lưu thông báo" + (" và gửi Zalo cho chủ cơ sở." if delivered else ".")


async def _deliver_owner_zalo(db: Session, ws_id: str, text: str) -> bool:
    """Owner Zalo: the workspace stores ``owner_zalo_user_id`` (set from the Kết nối page)."""
    from app.connectors import zalo
    from app.connectors.base import ConnectorError
    from app.connectors.messaging import connector_for
    from app.connectors.models import ConnectorKind

    ws = _workspace(db, ws_id)
    uid = (
        (ws.onboarding or {}).get("owner_zalo_user_id") if isinstance(ws.onboarding, dict) else None
    )
    c = connector_for(db, ws_id, ConnectorKind.zalo)
    if not uid or c is None or not c.enabled:
        return False
    try:
        await zalo.send_text(c, str(uid), text[:2000])
        return True
    except ConnectorError:
        return False


INDUSTRY_TOOLS: list[Tool] = [
    CustomerCard(),
    ListServices(),
    ListStaff(),
    FreeSlots(),
    BookAppointment(),
    SendMessage(),
    KbSearch(),
    NotifyOwner(),
]
