"""Plans, usage limits, VietQR invoices, admin confirmation, alerts and the admin panel."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.billing import service as billing
from app.billing.gateways import crc16_ccitt, vietqr_payload
from app.billing.models import Invoice, Subscription
from app.biz import alerts, automations
from app.biz.automation_models import AdminAlert, Notification
from app.biz.models import Channel, Conversation, Direction, Message, MessageStatus, Sender
from app.config import get_settings
from app.models import Workspace
from tests.conftest import login

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


# ---------------------------------------------------------------- VietQR payload


def test_vietqr_payload_has_valid_crc_and_fields() -> None:
    p = vietqr_payload("970436", "0011002233", 499000, "FFABC123")
    assert p.startswith("000201010212")
    assert "A000000727" in p and "970436" in p and "0011002233" in p and "QRIBFTTA" in p
    assert "5303704" in p and "5406499000" in p and "5802VN" in p and "FFABC123" in p
    body, crc = p[:-4], p[-4:]
    assert crc == f"{crc16_ccitt(body.encode()):04X}"


# ---------------------------------------------------------------- subscription state


def test_trial_then_expired_then_paid(db_session: Session) -> None:
    ws = Workspace(name="A", industry="gym", branches=[{"id": "b1", "name": "x"}])
    db_session.add(ws)
    db_session.commit()
    sub = billing.get_subscription(db_session, ws)
    now = datetime.now(UTC)
    assert sub.plan == "trial" and billing.status_of(sub, now) == "trial"
    assert billing.status_of(sub, now + timedelta(days=31)) == "expired"
    assert not billing.is_active(db_session, ws, now + timedelta(days=31))
    inv = billing.create_invoice(db_session, ws, "pro", months=2, now=now)
    assert inv.amount_vnd == 1_290_000 * 2 and inv.transfer_code.startswith("FF")
    assert inv.number.startswith(f"FF{now:%Y%m}-")
    billing.confirm_invoice(db_session, inv, "admin", now=now + timedelta(days=40))
    db_session.refresh(sub)
    assert sub.plan == "pro"
    assert billing.status_of(sub, now + timedelta(days=41)) == "active"
    assert billing.status_of(sub, now + timedelta(days=40 + 60 + 3)) == "past_due"
    assert billing.status_of(sub, now + timedelta(days=40 + 60 + 8)) == "expired"
    # a second confirmed invoice of the same plan extends from the current end
    inv2 = billing.create_invoice(db_session, ws, "pro", months=1, now=now)
    billing.confirm_invoice(db_session, inv2, "admin", now=now + timedelta(days=41))
    db_session.refresh(sub)
    assert sub.current_period_end.replace(tzinfo=UTC) == (now + timedelta(days=40 + 90)).replace(
        microsecond=sub.current_period_end.microsecond
    )
    with pytest.raises(ValueError):
        billing.confirm_invoice(db_session, inv2, "admin")


def test_ai_quota_counts_distinct_conversations(db_session: Session) -> None:
    ws = Workspace(name="A", industry="gym", branches=[])
    db_session.add(ws)
    db_session.commit()
    plan = billing.PLANS["trial"]
    for i in range(plan.ai_conversations_month):
        conv = Conversation(workspace_id=ws.id, channel=Channel.zalo, external_thread_id=f"t{i}")
        db_session.add(conv)
        db_session.flush()
        for _ in range(2):  # several AI messages in one conversation count once
            db_session.add(
                Message(
                    workspace_id=ws.id,
                    conversation_id=conv.id,
                    direction=Direction.outbound,
                    sender=Sender.ai,
                    status=MessageStatus.sent,
                    text="hi",
                )
            )
    db_session.commit()
    assert billing.ai_conversations_this_month(db_session, ws.id) == plan.ai_conversations_month
    ok, reason = billing.ai_allowed(db_session, ws)
    assert not ok and "hội thoại AI" in reason
    st = billing.state(db_session, ws)
    assert st["blocked"] and st["usage"]["ai_conversations"] == plan.ai_conversations_month
    assert any("Đã dùng" in w for w in st["warnings"])


# ---------------------------------------------------------------- owner API


def test_owner_billing_page_and_invoice_flow(anon_client: TestClient, db_session: Session) -> None:
    admin_email = "root@example.com"
    login(anon_client, admin_email)  # first account is the system admin
    anon_client.post("/auth/logout")
    _onboard(anon_client, "owner@example.com")
    b = anon_client.get("/biz/billing").json()
    assert b["status"] == "trial" and b["plan"]["key"] == "trial" and b["days_left"] in (29, 30)
    assert [p["key"] for p in b["plans"]] == ["trial", "basic", "pro"]
    assert b["usage"]["branches"] == 1 and b["pending_invoice"] is None

    get_settings.cache_clear()
    settings = get_settings()
    settings.billing_bank_account = "0011002233"
    settings.billing_bank_account_name = "CONG TY FLOWFORGE"
    r = anon_client.post("/biz/billing/invoices", json={"plan": "basic", "months": 3})
    assert r.status_code == 201, r.text
    inv = r.json()
    assert inv["amount_vnd"] == 499_000 * 3 and inv["status"] == "pending"
    assert inv["payment"]["qr_image_url"].startswith(
        "https://img.vietqr.io/image/970436-0011002233"
    )
    assert inv["transfer_code"] in inv["payment"]["qr_payload"]
    assert inv["payment"]["configured"] is True
    # a new request replaces the pending one
    r2 = anon_client.post("/biz/billing/invoices", json={"plan": "pro", "months": 1})
    assert r2.status_code == 201
    rows = anon_client.get("/biz/billing/invoices").json()
    assert [x["status"] for x in rows] == ["pending", "cancelled"]
    assert anon_client.get("/biz/billing").json()["pending_invoice"]["id"] == r2.json()["id"]
    pdf = anon_client.get(f"/biz/billing/invoices/{r2.json()['id']}/pdf")
    assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf"
    assert pdf.content.startswith(b"%PDF") and len(pdf.content) > 1000
    # unknown plan / foreign invoice
    assert anon_client.post("/biz/billing/invoices", json={"plan": "trial"}).status_code == 422
    assert anon_client.get("/biz/billing/invoices/nope/pdf").status_code == 404

    # admin confirms → owner is on Pro
    anon_client.post("/auth/logout")
    login(anon_client, admin_email)
    pend = anon_client.get("/admin/invoices?status=pending").json()
    assert len(pend) == 1 and pend[0]["workspace_name"]
    c = anon_client.post(f"/admin/invoices/{pend[0]['id']}/confirm")
    assert c.status_code == 200 and c.json()["subscription"]["plan"] == "pro"
    assert anon_client.post(f"/admin/invoices/{pend[0]['id']}/confirm").status_code == 409
    anon_client.post("/auth/logout")
    login(anon_client, "owner@example.com")
    b = anon_client.get("/biz/billing").json()
    assert b["status"] == "active" and b["plan"]["key"] == "pro" and b["days_left"] in (29, 30)
    assert b["usage"]["branches_limit"] == 5
    settings.billing_bank_account = ""
    settings.billing_bank_account_name = ""


def test_branch_limit_enforced_on_paid_plan(anon_client: TestClient, db_session: Session) -> None:
    _onboard(anon_client, "owner2@example.com")
    ws_id = anon_client.get("/workspaces/current").json()["id"]
    ws = db_session.get(Workspace, ws_id)
    billing.set_plan(db_session, ws, "basic", 1)
    two = [{"name": "A"}, {"name": "B"}]
    r = anon_client.put("/workspaces/current", json={"branches": two})
    assert r.status_code == 402 and "chi nhánh" in r.json()["detail"]
    billing.set_plan(db_session, ws, "pro", 1)
    assert anon_client.put("/workspaces/current", json={"branches": two}).status_code == 200


def test_non_admin_cannot_use_admin_panel(anon_client: TestClient) -> None:
    login(anon_client, "first@example.com")
    anon_client.post("/auth/logout")
    _onboard(anon_client, "second@example.com")
    assert anon_client.get("/admin/workspaces").status_code == 403
    assert anon_client.get("/admin/summary").status_code == 403


# ---------------------------------------------------------------- enforcement in automations


def test_expired_trial_blocks_ai_and_scheduler(
    anon_client: TestClient, db_session: Session
) -> None:
    _onboard(anon_client, "owner3@example.com")
    ws = db_session.get(Workspace, anon_client.get("/workspaces/current").json()["id"])
    sub = billing.get_subscription(db_session, ws)
    sub.trial_ends_at = datetime.now(UTC) - timedelta(days=1)
    db_session.commit()
    # scheduler: nothing starts
    assert automations.tick_workspace(db_session, ws, lambda _rid: None) == []
    # inbound: blocked, owner notified once, admin alert recorded
    conv = db_session.scalar(select(Conversation).where(Conversation.workspace_id == ws.id))
    msg = Message(
        workspace_id=ws.id,
        conversation_id=conv.id,
        direction=Direction.inbound,
        sender=Sender.customer,
        status=MessageStatus.sent,
        text="gói tập giá bao nhiêu",
    )
    db_session.add(msg)
    db_session.commit()
    out = automations.on_inbound(db_session, ws, conv, msg, lambda _rid: None)
    assert out["action"] == "blocked" and "hết hạn" in out["reason"]
    out2 = automations.on_inbound(db_session, ws, conv, msg, lambda _rid: None)
    assert out2["action"] == "blocked"
    notes = db_session.scalars(
        select(Notification).where(Notification.workspace_id == ws.id, Notification.kind == "alert")
    ).all()
    assert len(notes) == 1 and notes[0].title == "Trợ lý AI tạm dừng"
    assert (
        db_session.scalar(select(AdminAlert).where(AdminAlert.key == "billing:blocked")) is not None
    )
    # admin extends the trial → AI allowed again
    billing.extend_trial(db_session, ws, 14, "test")
    assert billing.ai_allowed(db_session, ws)[0] is True


def test_alerts_dedupe_and_connector_error(db_session: Session) -> None:
    ws = Workspace(name="A", industry="gym", branches=[])
    db_session.add(ws)
    db_session.commit()
    assert alerts.connector_error(db_session, ws.id, "zalo", "token expired") is not None
    assert alerts.connector_error(db_session, ws.id, "zalo", "token expired again") is None
    assert alerts.connector_error(db_session, ws.id, "facebook", "bad page token") is not None
    rows = db_session.scalars(select(AdminAlert).order_by(AdminAlert.created_at)).all()
    assert [a.key for a in rows] == ["connector:zalo", "connector:facebook"]
    assert "Zalo OA" in rows[0].title and rows[0].detail["message"] == "token expired"


# ---------------------------------------------------------------- admin panel


def test_admin_panel_lists_usage_and_extends_trial(
    anon_client: TestClient, db_session: Session
) -> None:
    login(anon_client, "root2@example.com")
    anon_client.post("/auth/logout")
    _onboard(anon_client, "owner4@example.com")
    ws_id = anon_client.get("/workspaces/current").json()["id"]
    anon_client.post("/auth/logout")
    login(anon_client, "root2@example.com")

    s = anon_client.get("/admin/summary").json()
    assert s["workspaces"] == 1 and s["trials"] == 1 and s["pending_invoices"] == 0
    rows = anon_client.get("/admin/workspaces").json()
    assert len(rows) == 1
    row = rows[0]
    assert row["owner_email"] == "owner4@example.com" and row["status"] == "trial"
    assert row["customers"] == 5 and row["usage"]["ai_conversations_limit"] == 100
    before = row["trial_ends_at"]
    r = anon_client.post(
        f"/admin/workspaces/{ws_id}/extend-trial", json={"days": 14, "note": "VIP"}
    )
    assert (
        r.status_code == 200 and r.json()["trial_ends_at"] > before and r.json()["days_left"] >= 43
    )
    sub = db_session.scalar(select(Subscription).where(Subscription.workspace_id == ws_id))
    assert "root2@example.com: +14 ngày. VIP" in sub.notes
    r = anon_client.post(f"/admin/workspaces/{ws_id}/plan", json={"plan": "basic", "months": 2})
    assert r.status_code == 200 and r.json()["status"] == "active" and r.json()["plan"] == "basic"
    assert (
        anon_client.post(f"/admin/workspaces/{ws_id}/plan", json={"plan": "gold"}).status_code
        == 422
    )
    assert anon_client.get("/admin/workspaces?q=zzz").json() == []
    logs = anon_client.get(f"/admin/workspaces/{ws_id}/logs").json()
    assert isinstance(logs, list)
    assert anon_client.get("/admin/workspaces/nope/logs").status_code == 404
    # alerts list + read
    alerts.connector_error(db_session, ws_id, "zalo", "x")
    al = anon_client.get("/admin/alerts?unread_only=true").json()
    assert len(al) == 1 and al[0]["workspace_name"]
    assert anon_client.post(f"/admin/alerts/{al[0]['id']}/read").json()["read"] is True
    assert anon_client.get("/admin/alerts?unread_only=true").json() == []
    assert anon_client.get("/admin/summary").json()["open_alerts"] == 0
    inv = db_session.scalar(select(Invoice))
    assert inv is None
