"""Industry flows: seeding, collectors, once-only milestones, inbound handling, dry runs, AI reply."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.biz import automations
from app.biz.automation_models import AutomationLog, Notification
from app.biz.models import (
    Appointment,
    AppointmentStatus,
    CheckIn,
    Conversation,
    Customer,
    Membership,
    Message,
    MessageStatus,
)
from app.executor.engine import ExecutorDeps, FlowExecutor
from app.models import Flow, Run, RunStatus, Workspace
from app.tools import ToolContext
from tests.conftest import login
from tests.fakes import FakeAnthropic, text_response, tool_response


def tool_use_response(name: str, args: dict) -> object:
    return tool_response([(name, args)])


GYM = {
    "name": "Gym A",
    "industry": "gym",
    "branch_count": 1,
    "main_channel": "zalo",
    "current_software": "",
}


def _onboard(client: TestClient, email: str, answers: dict = GYM) -> dict:
    login(client, email)
    r = client.post("/onboarding", json=answers)
    assert r.status_code == 201, r.text
    return r.json()


def _ws(db: Session, ws_id: str) -> Workspace:
    ws = db.get(Workspace, ws_id)
    assert ws is not None
    return ws


# ---------------------------------------------------------------- cards


def test_onboarding_seeds_six_cards(client: TestClient) -> None:
    _onboard(client, "cards@example.com")
    cards = client.get("/biz/automations").json()
    assert [c["key"] for c in cards] == [
        "reply_and_book",
        "appointment_reminder",
        "renewal_reminder",
        "churn_care",
        "review_request",
        "daily_report",
    ]
    by = {c["key"]: c for c in cards}
    assert (
        by["reply_and_book"]["enabled"]
        and by["reply_and_book"]["needs_model"]
        and by["reply_and_book"]["config"]["mode"] == "suggest"
    )
    assert (
        by["review_request"]["enabled"] is False
        and by["review_request"]["config"]["review_link"] == ""
    )
    assert (
        by["appointment_reminder"]["config"]["hours_before_1"] == 24
        and by["daily_report"]["config"]["send_at"] == "21:00"
    )
    assert {f["key"] for f in by["renewal_reminder"]["config_schema"]} >= {
        "days_before_1",
        "offer_enabled",
        "mode",
        "message_template",
    }
    # the cards are real flows of the owner, hidden-sandbox
    flows = client.get("/flows").json()
    assert {f["flow_key"] for f in flows if f["flow_key"]} == set(by)
    full = client.get(f"/flows/{by['appointment_reminder']['id']}").json()
    assert (
        full["sandbox"] == {"enabled": False}
        and full["nodes"][1]["data"]["deliver_tool"] == "biz_send_message"
    )
    # update config / toggle (owner only; unknown keys dropped)
    r = client.put(
        "/biz/automations/review_request",
        json={"enabled": True, "config": {"review_link": "https://g.page/r/abc/review", "junk": 1}},
    )
    assert (
        r.status_code == 200
        and r.json()["enabled"]
        and r.json()["config"]["review_link"].startswith("https://")
        and "junk" not in r.json()["config"]
    )
    login(client, "staff@example.com")
    login(client, "cards@example.com")
    client.post("/workspaces/current/members", json={"email": "staff@example.com", "role": "staff"})
    login(client, "staff@example.com")
    assert client.put("/biz/automations/review_request", json={"enabled": False}).status_code == 403
    assert len(client.get("/biz/automations").json()) == 6


# ---------------------------------------------------------------- collectors


def test_reminder_collector_milestones(client: TestClient, db_session: Session) -> None:
    ws_out = _onboard(client, "rem@example.com")
    ws = _ws(db_session, ws_out["id"])
    flow = db_session.scalar(
        select(Flow).where(Flow.workspace_id == ws.id, Flow.flow_key == "appointment_reminder")
    )
    cust = db_session.scalar(select(Customer).where(Customer.workspace_id == ws.id))
    now = datetime(2026, 11, 12, 2, 0, tzinfo=UTC)  # 09:00 local
    a = Appointment(
        workspace_id=ws.id,
        customer_id=cust.id,
        start_at=now + timedelta(hours=23, minutes=50),
        end_at=now + timedelta(hours=24, minutes=50),
        status=AppointmentStatus.booked,
    )
    db_session.add(a)
    db_session.commit()
    due = automations._collect_reminders(db_session, ws, flow, now)
    assert [(e, m) for e, _, m, _ in due if e == "appointment"][:1] == [("appointment", "24h")]
    msg = next(i["message"] for _, eid, m, i in due if eid == a.id)
    assert (
        cust.name in msg and "Trả lời 1 để XÁC NHẬN" in msg and "08:50 ngày 13/11" in msg
    )  # local time
    assert not [
        x
        for x in automations._collect_reminders(db_session, ws, flow, now - timedelta(hours=3))
        if x[1] == a.id
    ]
    assert [
        m
        for _, eid, m, _ in automations._collect_reminders(
            db_session, ws, flow, now + timedelta(hours=22)
        )
        if eid == a.id
    ] == ["2h"]
    # custom template + once-only via tick
    flow.config = {**flow.config, "message_template": "Nhắc {{ ten_khach }} {{ gio }}"}
    db_session.commit()
    started = automations.tick_workspace(db_session, ws, None, now)
    runs_for_a = [r for r in started if r.inputs.get("appointment_id") == a.id]
    assert len(runs_for_a) == 1 and runs_for_a[0].inputs["message"] == f"Nhắc {cust.name} 08:50"
    assert automations.tick_workspace(
        db_session, ws, None, now + timedelta(minutes=10)
    ) == [] or all(
        r.inputs.get("appointment_id") != a.id
        for r in automations.tick_workspace(db_session, ws, None, now + timedelta(minutes=10))
    )
    assert (
        db_session.scalar(
            select(AutomationLog).where(
                AutomationLog.entity_id == a.id, AutomationLog.milestone == "24h"
            )
        )
        is not None
    )


def test_renewal_churn_review_collectors(client: TestClient, db_session: Session) -> None:
    ws_out = _onboard(client, "ren@example.com")
    ws = _ws(db_session, ws_out["id"])
    flows = {f.flow_key: f for f in automations.workspace_flows(db_session, ws.id)}
    today = datetime.now(UTC)
    # renewals: sample data has a membership expiring in 3 days (not a milestone) → craft 7-day and 2-session cases
    cust = db_session.scalar(select(Customer).where(Customer.workspace_id == ws.id))
    svc_id = db_session.scalar(
        select(Membership.service_id).where(Membership.workspace_id == ws.id)
    )
    m7 = Membership(
        workspace_id=ws.id,
        customer_id=cust.id,
        service_id=svc_id,
        start_date=today.date() - timedelta(days=23),
        end_date=today.date() + timedelta(days=7),
        sessions_total=0,
    )
    m2 = Membership(
        workspace_id=ws.id,
        customer_id=cust.id,
        service_id=svc_id,
        start_date=today.date(),
        end_date=None,
        sessions_total=10,
        sessions_used=8,
    )
    db_session.add_all([m7, m2])
    db_session.commit()
    flows["renewal_reminder"].config = {
        **flows["renewal_reminder"].config,
        "offer_enabled": True,
        "offer": "tặng 2 buổi PT",
    }
    db_session.commit()
    due = automations._collect_renewals(db_session, ws, flows["renewal_reminder"], today)
    by = {(eid, m): i for _, eid, m, i in due}
    assert (m7.id, "d7") in by and "tặng 2 buổi PT" in by[(m7.id, "d7")]["message"]
    assert (m2.id, "s2") in by and "còn 2 buổi" in by[(m2.id, "s2")]["message"]

    # churn: gym customer with live package and no visit for 12 days → once per absence
    db_session.query(CheckIn).filter_by(customer_id=cust.id).delete()
    cust.last_visit_at = today - timedelta(days=12)
    cust.tags = []
    db_session.commit()
    due = automations._collect_care(db_session, ws, flows["churn_care"], today)
    assert [eid for _, eid, _, _ in due] == [cust.id] and "lâu rồi không thấy" in due[0][3][
        "message"
    ]
    automations.tick_workspace(db_session, ws, None, today)
    assert [
        eid
        for _, eid, _, _ in automations._collect_care(db_session, ws, flows["churn_care"], today)
        if not automations.already_done(db_session, ws.id, "churn_care", eid, due[0][2])
    ] == []
    # spa uses the treatment cycle instead
    ws.industry = "spa"
    flows["churn_care"].config = {**flows["churn_care"].config, "cycle_days": 30}
    db_session.commit()
    assert automations._collect_care(db_session, ws, flows["churn_care"], today) == []
    ws.industry = "gym"

    # review: needs a link; 3 visits → once
    flows["review_request"].enabled = True
    assert automations._collect_reviews(db_session, ws, flows["review_request"], today) == []
    flows["review_request"].config = {
        **flows["review_request"].config,
        "review_link": "https://g.page/r/x/review",
    }
    db_session.commit()
    m2.sessions_used = 10  # treatment finished → eligible for a review request
    db_session.commit()
    due = automations._collect_reviews(db_session, ws, flows["review_request"], today)
    eligible = {eid for _, eid, _, _ in due}
    assert (
        cust.id in eligible
    )  # sample customer 0 has 2 check-ins + 1 arrived/booked? ensure via membership finished
    for _, eid, milestone, inputs in due:
        assert milestone == "review" and "https://g.page/r/x/review" in inputs["message"]
    started = automations.tick_workspace(db_session, ws, None, today)
    assert {
        r.inputs["customer_id"] for r in started if r.flow_id == flows["review_request"].id
    } == eligible
    assert [
        r
        for r in automations.tick_workspace(db_session, ws, None, today)
        if r.flow_id == flows["review_request"].id
    ] == []


def test_daily_report_due_window_and_stats(client: TestClient, db_session: Session) -> None:
    ws_out = _onboard(client, "rep@example.com")
    ws = _ws(db_session, ws_out["id"])
    flow = next(
        f for f in automations.workspace_flows(db_session, ws.id) if f.flow_key == "daily_report"
    )
    tz_now = datetime.now(UTC)
    # 21:05 local
    local = tz_now.astimezone(__import__("zoneinfo").ZoneInfo("Asia/Ho_Chi_Minh")).replace(
        hour=21, minute=5
    )
    due = automations._collect_report(db_session, ws, flow, local.astimezone(UTC))
    assert len(due) == 1 and due[0][2] == "report"
    msg = due[0][3]["message"]
    assert (
        "báo cáo ngày" in msg
        and "Lịch hôm nay: 2" in msg
        and "không đến 0" in msg
        and "Tin chưa trả lời: 1" in msg
    )
    assert (
        automations._collect_report(db_session, ws, flow, local.replace(hour=15).astimezone(UTC))
        == []
    )
    stats = automations.daily_stats(db_session, ws, tz_now)
    assert stats["lich_ngay_mai"] == 1 and stats["khach_moi"] == 5 and stats["sap_het_han"] >= 1


# ---------------------------------------------------------------- running without a sandbox


async def test_message_flow_runs_without_sandbox_and_dry_run(
    client: TestClient, db_session: Session, deps: ExecutorDeps
) -> None:
    ws_out = _onboard(client, "run@example.com")
    ws = _ws(db_session, ws_out["id"])
    flow = next(
        f
        for f in automations.workspace_flows(db_session, ws.id)
        if f.flow_key == "appointment_reminder"
    )
    cust = db_session.scalar(select(Customer).where(Customer.workspace_id == ws.id))
    conv = db_session.scalar(select(Conversation).where(Conversation.workspace_id == ws.id))
    conv.customer_id = cust.id
    db_session.commit()
    deps.tool_context_factory = lambda uid: ToolContext()
    # dry run → tool describes, nothing stored
    run = automations.start_run(
        db_session, flow, {"customer_id": cust.id, "message": "Nhắc thử"}, None, dry_run=True
    )
    assert await FlowExecutor(deps, run.id).execute() is RunStatus.success
    with deps.session_factory() as db:
        row = db.get(Run, run.id)
        assert (
            row.outputs
            and "[chạy thử" in row.outputs[0]["delivered"]["result"]
            and "Nhắc thử" in row.outputs[0]["delivered"]["result"]
        )
        assert (
            db.scalar(
                select(Message).where(
                    Message.conversation_id == conv.id, Message.text == "Nhắc thử"
                )
            )
            is None
        )
    logs = [e["message"] for e in deps.bus.history(run.id) if e["event"] == "log"]
    assert any("lightweight run" in m for m in logs)
    # real run, mode suggest → AI suggestion in the inbox
    run2 = automations.start_run(
        db_session, flow, {"customer_id": cust.id, "message": "Nhắc thật"}, None
    )
    assert await FlowExecutor(deps, run2.id).execute() is RunStatus.success
    with deps.session_factory() as db:
        m = db.scalar(
            select(Message).where(Message.conversation_id == conv.id, Message.text == "Nhắc thật")
        )
        assert m is not None and m.status is MessageStatus.ai_suggested and m.sender.value == "ai"
        assert db.get(Conversation, conv.id).unread is True
    # daily report → notification
    report = next(
        f for f in automations.workspace_flows(db_session, ws.id) if f.flow_key == "daily_report"
    )
    run3 = automations.start_run(
        db_session, report, {"title": "Báo cáo", "message": "Nội dung báo cáo"}, None
    )
    assert await FlowExecutor(deps, run3.id).execute() is RunStatus.success
    with deps.session_factory() as db:
        n = db.scalar(select(Notification).where(Notification.workspace_id == ws.id))
        assert n is not None and n.body == "Nội dung báo cáo" and n.kind == "report"
    assert client.get("/biz/notifications").json()[0]["title"] == "Báo cáo"


async def test_ai_reply_flow_uses_tools(
    client: TestClient, db_session: Session, deps: ExecutorDeps, fake_client: FakeAnthropic
) -> None:
    ws_out = _onboard(client, "ai@example.com")
    ws = _ws(db_session, ws_out["id"])
    flow = next(
        f for f in automations.workspace_flows(db_session, ws.id) if f.flow_key == "reply_and_book"
    )
    conv = db_session.scalar(select(Conversation).where(Conversation.workspace_id == ws.id))
    deps.tool_context_factory = lambda uid: ToolContext()
    fake_client.responses = [
        tool_response([("biz_customer_card", {"conversation_id": conv.id})]),
        tool_use_response(
            "biz_free_slots", {"day": (datetime.now(UTC) + timedelta(days=1)).strftime("%Y-%m-%d")}
        ),
        tool_use_response(
            "biz_send_message",
            {
                "conversation_id": conv.id,
                "text": "Dạ bên em có gói 1 tháng 500k ạ, mai 9h còn chỗ, chị muốn đặt không ạ?",
            },
        ),
        text_response("Đã gửi tư vấn và đề xuất giờ."),
    ]
    inputs = automations.reply_inputs(
        db_session,
        ws,
        flow,
        conversation_id=conv.id,
        customer_id=conv.customer_id or "",
        channel="zalo",
        message="Gói cho người mới giá sao, mai 9h được không?",
    )
    assert "# Dịch vụ và giá" in inputs["system_context"] and inputs["mode"] == "suggest"
    run = automations.start_run(db_session, flow, inputs, None)
    assert await FlowExecutor(deps, run.id).execute() is RunStatus.success
    sent_system = fake_client.requests[0]["system"]
    assert (
        "Bạn là trợ lý chăm sóc khách hàng của Gym A" in sent_system
        and "conversation_id: " + conv.id in sent_system
    )
    tool_names = [t["name"] for t in fake_client.requests[0]["tools"]]
    assert (
        "biz_send_message" in tool_names
        and "biz_book_appointment" in tool_names
        and "run_shell" not in tool_names
    )
    with deps.session_factory() as db:
        suggestion = db.scalar(
            select(Message).where(
                Message.conversation_id == conv.id,
                Message.status == MessageStatus.ai_suggested,
                Message.text.like("Dạ bên em%"),
            )
        )
        assert suggestion is not None
        steps = db.get(Run, run.id).step_runs
        calls = [c["tool"] for c in steps[0].tool_calls]
        assert calls == ["biz_customer_card", "biz_free_slots", "biz_send_message"]
        assert "staff_id=" in steps[0].tool_calls[1]["output"]


# ---------------------------------------------------------------- inbound handling


def test_on_inbound_handoff_reminder_reply_and_run(client: TestClient, db_session: Session) -> None:
    ws_out = _onboard(client, "inb@example.com")
    ws = _ws(db_session, ws_out["id"])
    conv = db_session.scalar(select(Conversation).where(Conversation.workspace_id == ws.id))
    started: list[str] = []

    def mk(text: str) -> Message:
        m = Message(
            workspace_id=ws.id,
            conversation_id=conv.id,
            direction="in",
            sender="customer",
            status="sent",
            text=text,
        )
        db_session.add(m)
        db_session.commit()
        return m

    # medical question → polite handoff, AI switched off for this conversation
    out = automations.on_inbound(
        db_session, ws, conv, mk("Em bị đau lưng có tập được không?"), started.append
    )
    assert (
        out["action"] == "handoff"
        and out["category"] == "chấn thương"
        and out["text"].startswith("Dạ câu này em")
    )
    db_session.refresh(conv)
    assert conv.ai_enabled is False and started == []
    assert (
        automations.on_inbound(db_session, ws, conv, mk("hello"), started.append)["action"]
        == "staff"
    )
    conv.ai_enabled = True
    db_session.commit()
    # owner keywords from the card config
    flow = next(
        f for f in automations.workspace_flows(db_session, ws.id) if f.flow_key == "reply_and_book"
    )
    assert (
        automations.on_inbound(db_session, ws, conv, mk("tôi muốn hoàn tiền"), started.append)[
            "action"
        ]
        == "handoff"
    )
    conv.ai_enabled = True
    # outside working hours → kept for staff
    flow.config = {**flow.config, "hours_from": "00:00", "hours_to": "00:01"}
    db_session.commit()
    assert (
        automations.on_inbound(db_session, ws, conv, mk("giá gói?"), started.append)["action"]
        == "outside_hours"
    )
    flow.config = {**flow.config, "hours_from": "00:00", "hours_to": "23:59"}
    db_session.commit()
    # normal question → run started with the composed context
    out = automations.on_inbound(
        db_session, ws, conv, mk("Gói 3 tháng giá bao nhiêu?"), started.append
    )
    assert out["action"] == "run" and started == [out["run_id"]]
    run = db_session.get(Run, out["run_id"])
    assert (
        run.inputs["message"] == "Gói 3 tháng giá bao nhiêu?"
        and run.sandbox == {"enabled": False}
        and "Gym A" in run.inputs["system_context"]
    )
    # reminder reply "1" confirms the reminded appointment
    appt = db_session.scalar(
        select(Appointment).where(
            Appointment.workspace_id == ws.id, Appointment.customer_id == conv.customer_id
        )
    )
    if appt is None:
        appt = Appointment(
            workspace_id=ws.id,
            customer_id=conv.customer_id,
            start_at=datetime.now(UTC) + timedelta(hours=20),
            end_at=datetime.now(UTC) + timedelta(hours=21),
            status=AppointmentStatus.booked,
        )
        db_session.add(appt)
        db_session.commit()
    appt.status = AppointmentStatus.booked
    db_session.add(
        AutomationLog(
            workspace_id=ws.id,
            flow_key="appointment_reminder",
            entity_type="appointment",
            entity_id=appt.id,
            milestone="24h",
        )
    )
    db_session.commit()
    out = automations.on_inbound(db_session, ws, conv, mk("1"), started.append)
    assert out["action"] == "reminder_reply" and "xác nhận" in out["text"]
    db_session.refresh(appt)
    assert appt.status is AppointmentStatus.confirmed
    out = automations.on_inbound(db_session, ws, conv, mk("3"), started.append)
    db_session.refresh(appt)
    assert out["action"] == "reminder_reply" and appt.status is AppointmentStatus.cancelled
    # disabled card → nothing happens
    flow.enabled = False
    db_session.commit()
    assert (
        automations.on_inbound(db_session, ws, conv, mk("xin chào"), started.append)["action"]
        == "disabled"
    )


def test_webhook_triggers_automation(client: TestClient, db_session: Session) -> None:
    _onboard(client, "hook@example.com")
    z = client.put(
        "/biz/connectors/zalo",
        json={"config": {"app_id": "app1"}, "secrets": {"access_token": "tok"}},
    ).json()
    token = z["webhook_url"].rsplit("/", 1)[-1]
    body = json.dumps(
        {
            "event_name": "user_send_text",
            "app_id": "app1",
            "timestamp": "1762771200000",
            "sender": {"id": "u-1"},
            "message": {"text": "Em bị đau gối", "msg_id": "m1"},
        }
    ).encode()
    r = client.post(
        f"/hooks/zalo/{token}", content=body, headers={"content-type": "application/json"}
    )
    assert r.status_code == 200 and r.json()["automation"]["action"] == "handoff"
    conv = next(
        c for c in client.get("/biz/conversations").json() if c["external_thread_id"] == "u-1"
    )
    msgs = client.get(f"/biz/conversations/{conv['id']}/messages").json()
    assert (
        msgs[-1]["status"] == "handoff"
        and msgs[-1]["sender"] == "ai"
        and conv["ai_enabled"] is False
    )


def test_try_endpoint_dry_runs_a_message_flow(client: TestClient, deps: ExecutorDeps) -> None:
    from app.services.runs import run_manager

    deps.tool_context_factory = lambda uid: ToolContext()
    run_manager._deps = deps
    try:
        _try_body(client)
    finally:
        run_manager._deps = None


def _try_body(client: TestClient) -> None:
    _onboard(client, "try@example.com")
    r = client.post("/biz/automations/renewal_reminder/try", params={"wait_seconds": 30})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "success" and body["error"] is None
    assert (
        body["messages"]
        and "[chạy thử" in body["messages"][0]
        and "Nguyễn Văn Thử" in body["messages"][0]
    )
    assert "system_context" not in body["inputs"] and body["inputs"]["customer_id"]
    # dry runs do not count as real runs on the card
    card = next(c for c in client.get("/biz/automations").json() if c["key"] == "renewal_reminder")
    assert card["runs_7d"] == 0
    assert client.post("/biz/automations/nope/try").status_code == 404
