"""Industry data: isolation, memberships, scheduling, inbox, import."""

from __future__ import annotations

import io
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from app.biz import importer
from app.biz import service as biz
from app.biz.models import Membership, MembershipStatus, Staff, StaffRole
from app.biz.phone import normalize_phone
from tests.conftest import login

GYM = {
    "name": "Gym A",
    "industry": "gym",
    "branch_count": 1,
    "main_channel": "zalo",
    "current_software": "",
}
SPA = {
    "name": "Spa B",
    "industry": "spa",
    "branch_count": 1,
    "main_channel": "facebook",
    "current_software": "",
}
HOURS = {"mon": [["08:00", "12:00"], ["14:00", "18:00"]], "tue": [["08:00", "12:00"]]}


def _onboard(client: TestClient, email: str, answers: dict) -> dict:
    login(client, email)
    r = client.post("/onboarding", json=answers)
    assert r.status_code == 201, r.text
    return r.json()


# ---------------------------------------------------------------- pure rules


def test_phone_normalisation() -> None:
    assert normalize_phone("+84 912-345 678") == "0912345678"
    assert normalize_phone("84912345678") == "0912345678"
    assert normalize_phone("0912.345.678") == "0912345678"
    assert normalize_phone("") == ""


def test_membership_status_rules() -> None:
    today = date(2026, 11, 10)
    m = Membership(
        start_date=today, end_date=today + timedelta(days=30), sessions_total=0, sessions_used=0
    )
    assert biz.compute_status(m, today) is MembershipStatus.active
    m.end_date = today + timedelta(days=5)
    assert biz.compute_status(m, today) is MembershipStatus.expiring
    m.end_date = today - timedelta(days=1)
    assert biz.compute_status(m, today) is MembershipStatus.expired
    m.end_date = None
    m.sessions_total, m.sessions_used = 10, 8
    assert biz.compute_status(m, today) is MembershipStatus.expiring
    m.sessions_used = 10
    assert biz.compute_status(m, today) is MembershipStatus.expired
    m.status = MembershipStatus.frozen
    assert biz.compute_status(m, today) is MembershipStatus.frozen


def test_free_slots_respect_working_hours_and_overlaps() -> None:
    tz = ZoneInfo("Asia/Ho_Chi_Minh")
    staff = Staff(name="PT", role=StaffRole.pt, working_hours=HOURS)
    monday = date(2026, 11, 9)
    now = datetime(2026, 11, 8, 0, 0, tzinfo=UTC)
    local = lambda h, m=0: datetime(2026, 11, 9, h, m, tzinfo=tz).astimezone(UTC)
    busy = [(local(9), local(10)), (local(15, 30), local(16))]
    slots = biz.free_slots(monday, staff, busy, duration_min=60, step_min=30, tz=tz, now=now)
    starts = [s.astimezone(tz).strftime("%H:%M") for s, _ in slots]
    assert "08:00" in starts and "10:00" in starts and "11:00" in starts
    assert "09:00" not in starts and "08:30" not in starts  # overlap with 09:00-10:00
    assert "11:30" not in starts  # would end 12:30, past the morning span
    assert "14:00" in starts and "14:30" in starts and "15:00" not in starts and "16:00" in starts
    assert "17:00" in starts and "17:30" not in starts
    assert (
        biz.free_slots(date(2026, 11, 11), staff, [], duration_min=60, step_min=30, tz=tz, now=now)
        == []
    )  # wed off
    # past slots are hidden
    later = datetime(2026, 11, 9, 10, 15, tzinfo=tz).astimezone(UTC)
    assert all(
        s.astimezone(tz).hour >= 10
        for s, _ in biz.free_slots(
            monday, staff, [], duration_min=60, step_min=30, tz=tz, now=later
        )
    )


# ---------------------------------------------------------------- API + isolation


def test_onboarding_seeds_sample_data_and_isolation(anon_client: TestClient) -> None:
    _onboard(anon_client, "gym@example.com", GYM)
    customers = anon_client.get("/biz/customers").json()
    assert len(customers) == 5 and all("mẫu" in c["tags"] for c in customers)
    assert customers[0]["phone"].startswith("09")
    services = anon_client.get("/biz/services").json()
    assert {s["category"] for s in services} == {"gym_package", "pt_session", "course"}
    staff = anon_client.get("/biz/staff").json()
    assert {s["role"] for s in staff} == {"pt", "receptionist"}
    mems = anon_client.get("/biz/memberships").json()
    assert {m["status"] for m in mems} >= {"active", "expiring", "expired"}
    today = date.today()
    assert len(anon_client.get("/biz/appointments", params={"day": today.isoformat()}).json()) == 2
    assert len(anon_client.get("/biz/checkins").json()) == 4
    convs = anon_client.get("/biz/conversations").json()
    assert len(convs) == 1 and convs[0]["unread"] and convs[0]["customer_name"] == "Võ Ngọc Lan"
    msgs = anon_client.get(f"/biz/conversations/{convs[0]['id']}/messages").json()
    assert [m["status"] for m in msgs] == ["sent", "ai_suggested"]
    gym_customer = customers[0]["id"]

    # a spa workspace sees only its own rows; cross-workspace ids are 404
    _onboard(anon_client, "spa@example.com", SPA)
    assert {s["category"] for s in anon_client.get("/biz/services").json()} == {"spa_treatment"}
    assert anon_client.get(f"/biz/customers/{gym_customer}").status_code == 404
    assert anon_client.delete(f"/biz/customers/{gym_customer}").status_code == 404
    spa_service = anon_client.get("/biz/services").json()[0]["id"]
    assert (
        anon_client.post(
            "/biz/memberships",
            json={
                "customer_id": gym_customer,
                "service_id": spa_service,
                "start_date": "2026-11-01",
            },
        ).status_code
        == 404
    )
    # a user without a workspace gets 404 everywhere
    login(anon_client, "nobody@example.com")
    assert anon_client.get("/biz/customers").status_code == 404


def test_customers_crud_and_phone_dedupe(anon_client: TestClient) -> None:
    _onboard(anon_client, "crud@example.com", GYM)
    r = anon_client.post(
        "/biz/customers",
        json={
            "name": "Khách Mới",
            "phone": "+84 977 000 111",
            "tags": ["vip-test", "vip-test", " "],
            "notes": "x",
        },
    )
    assert r.status_code == 201, r.text
    c = r.json()
    assert c["phone"] == "0977000111" and c["tags"] == ["vip-test"] and c["membership"] is None
    assert (
        anon_client.post(
            "/biz/customers", json={"name": "Trùng", "phone": "0977000111"}
        ).status_code
        == 409
    )
    assert anon_client.get("/biz/customers", params={"q": "0977000111"}).json()[0]["id"] == c["id"]
    assert anon_client.get("/biz/customers", params={"q": "khách m"}).json()[0]["id"] == c["id"]
    assert [
        x["id"] for x in anon_client.get("/biz/customers", params={"tag": "vip-test"}).json()
    ] == [c["id"]]
    r = anon_client.put(f"/biz/customers/{c['id']}", json={"phone": "0977000222", "tags": ["gold"]})
    assert r.json()["phone"] == "0977000222" and r.json()["tags"] == ["gold"]
    assert anon_client.delete(f"/biz/customers/{c['id']}").status_code == 204
    assert anon_client.get(f"/biz/customers/{c['id']}").status_code == 404


def test_memberships_and_sessions(anon_client: TestClient) -> None:
    _onboard(anon_client, "mem@example.com", GYM)
    cust = anon_client.post("/biz/customers", json={"name": "A", "phone": "0900000001"}).json()
    pt = next(s for s in anon_client.get("/biz/services").json() if s["category"] == "pt_session")
    r = anon_client.post(
        "/biz/memberships",
        json={
            "customer_id": cust["id"],
            "service_id": pt["id"],
            "start_date": date.today().isoformat(),
            "amount_paid": 3000000,
        },
    )
    assert r.status_code == 201, r.text
    m = r.json()
    assert (
        m["sessions_total"] == 10
        and m["sessions_left"] == 10
        and m["status"] == "active"
        and m["days_left"] == 60
    )
    for _ in range(8):
        m = anon_client.post(f"/biz/memberships/{m['id']}/use").json()
    assert m["sessions_left"] == 2 and m["status"] == "expiring"
    m = anon_client.put(f"/biz/memberships/{m['id']}", json={"status": "frozen"}).json()
    assert m["status"] == "frozen"
    m = anon_client.put(f"/biz/memberships/{m['id']}", json={"status": "active"}).json()
    assert m["status"] == "expiring"  # recomputed, not trusted
    anon_client.post(f"/biz/memberships/{m['id']}/use")
    m = anon_client.post(f"/biz/memberships/{m['id']}/use").json()
    assert (
        m["status"] == "expired"
        and anon_client.post(f"/biz/memberships/{m['id']}/use").status_code == 409
    )
    assert (
        anon_client.get(f"/biz/customers/{cust['id']}").json()["membership"]["status"] == "expired"
    )
    assert [
        x["id"]
        for x in anon_client.get(
            "/biz/memberships", params={"status": "expired", "customer_id": cust["id"]}
        ).json()
    ] == [m["id"]]


def test_appointments_conflicts_and_slots(anon_client: TestClient) -> None:
    ws = _onboard(anon_client, "appt@example.com", SPA)
    branch = ws["branches"][0]["id"]
    cust = anon_client.post("/biz/customers", json={"name": "B", "phone": "0900000002"}).json()
    st = anon_client.post(
        "/biz/staff",
        json={"name": "KTV Hoa", "role": "therapist", "working_hours": HOURS, "branch_id": branch},
    ).json()
    assert (
        anon_client.post(
            "/biz/staff", json={"name": "X", "role": "pt", "branch_id": "nope"}
        ).status_code
        == 422
    )
    assert (
        anon_client.post(
            "/biz/staff",
            json={"name": "X", "role": "pt", "working_hours": {"mon": [["09:00", "08:00"]]}},
        ).status_code
        == 422
    )
    svc = anon_client.get("/biz/services").json()[0]
    # next Monday 09:00 local
    today = date.today()
    monday = today + timedelta(days=(7 - today.weekday()) % 7 or 7)
    start = f"{monday.isoformat()}T09:00:00"  # naive = workspace timezone
    r = anon_client.post(
        "/biz/appointments",
        json={
            "customer_id": cust["id"],
            "service_id": svc["id"],
            "staff_id": st["id"],
            "branch_id": branch,
            "start_at": start,
            "source": "staff",
        },
    )
    assert r.status_code == 201, r.text
    a = r.json()
    assert a["staff_name"] == "KTV Hoa" and a["end_at"] > a["start_at"]
    minutes = (
        datetime.fromisoformat(a["end_at"]) - datetime.fromisoformat(a["start_at"])
    ).total_seconds() / 60
    assert minutes == svc["duration_min"]
    # overlapping booking for the same staff → 409 unless forced
    r = anon_client.post(
        "/biz/appointments",
        json={
            "customer_id": cust["id"],
            "staff_id": st["id"],
            "start_at": f"{monday.isoformat()}T09:30:00",
        },
    )
    assert r.status_code == 409
    assert (
        anon_client.post(
            "/biz/appointments?force=true",
            json={
                "customer_id": cust["id"],
                "staff_id": st["id"],
                "start_at": f"{monday.isoformat()}T09:30:00",
            },
        ).status_code
        == 201
    )
    slots = anon_client.post(
        "/biz/appointments/slots",
        json={"day": monday.isoformat(), "staff_id": st["id"], "duration_min": 60, "step_min": 30},
    ).json()
    locals_ = [s["local"] for s in slots["staff"][0]["slots"]]
    assert (
        "08:00" in locals_
        and "09:00" not in locals_
        and "09:30" not in locals_
        and "10:30" in locals_
        and "14:00" in locals_
    )
    # status change to arrived updates last visit; cancelled frees the slot
    r = anon_client.put(f"/biz/appointments/{a['id']}", json={"status": "arrived"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "arrived"
    assert anon_client.get(f"/biz/customers/{cust['id']}").json()["last_visit_at"] is not None
    anon_client.put(f"/biz/appointments/{a['id']}", json={"status": "cancelled"})
    listed = anon_client.get(
        "/biz/appointments", params={"day": monday.isoformat(), "staff_id": st["id"]}
    ).json()
    assert {x["status"] for x in listed} == {"cancelled", "booked"}
    assert (
        anon_client.get(
            "/biz/appointments", params={"day": monday.isoformat(), "status": "booked"}
        ).json()[0]["customer_name"]
        == "B"
    )
    # check-in
    r = anon_client.post("/biz/checkins", json={"customer_id": cust["id"], "branch_id": branch})
    assert r.status_code == 201
    assert len(anon_client.get("/biz/checkins", params={"customer_id": cust["id"]}).json()) == 1


def test_inbox_suggestion_flow(anon_client: TestClient) -> None:
    _onboard(anon_client, "inbox@example.com", GYM)
    conv = anon_client.get("/biz/conversations", params={"unread": True}).json()[0]
    msgs = anon_client.get(f"/biz/conversations/{conv['id']}/messages").json()
    suggestion = next(m for m in msgs if m["status"] == "ai_suggested")
    r = anon_client.post(
        f"/biz/conversations/{conv['id']}/messages/{suggestion['id']}/send",
        params={"text": "Dạ chào chị, em gửi bảng giá ạ."},
    )
    assert r.status_code == 200 and r.json()["status"] == "sent" and r.json()["sender"] == "staff"
    assert (
        anon_client.post(
            f"/biz/conversations/{conv['id']}/messages/{suggestion['id']}/send"
        ).status_code
        == 409
    )
    assert (
        anon_client.delete(
            f"/biz/conversations/{conv['id']}/messages/{suggestion['id']}"
        ).status_code
        == 409
    )
    assert anon_client.get(f"/biz/conversations/{conv['id']}").json()["unread"] is False
    r = anon_client.post(
        f"/biz/conversations/{conv['id']}/messages",
        json={"text": "Mấy giờ mở cửa?", "sender": "customer"},
    )
    assert r.status_code == 201 and r.json()["direction"] == "in"
    c = anon_client.get(f"/biz/conversations/{conv['id']}").json()
    assert c["unread"] is True and c["last_text"] == "Mấy giờ mở cửa?"
    assert (
        anon_client.put(f"/biz/conversations/{conv['id']}", json={"ai_enabled": False}).json()[
            "ai_enabled"
        ]
        is False
    )


# ---------------------------------------------------------------- import


def test_import_helpers() -> None:
    assert importer.fold("Số Điện Thoại") == "so dien thoai"
    m = importer.suggest_mapping("customers", ["Họ tên", "SĐT", "Nhãn", "Ghi chú", "Nguồn"])
    assert m == {
        "name": "Họ tên",
        "phone": "SĐT",
        "tags": "Nhãn",
        "source": "Nguồn",
        "notes": "Ghi chú",
    }
    m = importer.suggest_mapping(
        "memberships", ["Khách hàng", "Điện thoại", "Gói tập", "Ngày bắt đầu", "Hết hạn", "Số buổi"]
    )
    assert (
        m["phone"] == "Điện thoại"
        and m["service_name"] == "Gói tập"
        and m["start_date"] == "Ngày bắt đầu"
        and m["end_date"] == "Hết hạn"
        and m["sessions_total"] == "Số buổi"
    )
    assert importer.parse_date("15/11/2026") == date(2026, 11, 15) and importer.parse_date(
        "2026-11-15"
    ) == date(2026, 11, 15)
    assert (
        importer.parse_number("1.200.000") == 1_200_000
        and importer.parse_number("1,200,000 đ") == 1_200_000
        and importer.parse_number("12,5") == 12.5
    )
    cols, rows = importer.parse_table("Tên;SĐT\nA;0901\nB;0902\n".encode("utf-8-sig"), "x.csv")
    assert cols == ["Tên", "SĐT"] and rows[1] == {"Tên": "B", "SĐT": "0902"}


def _xlsx(headers: list[str], rows: list[list]) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.append(headers)
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_import_preview_and_apply(anon_client: TestClient) -> None:
    _onboard(anon_client, "imp@example.com", GYM)
    data = _xlsx(
        ["Họ tên", "Số điện thoại", "Nhãn", "Lần đến gần nhất"],
        [
            ["Khách Một", "0911 111 111", "vip, mới", date(2026, 11, 1)],
            ["Khách Hai", "+84 922 222 222", "", None],
            ["", "0933", "", None],
        ],
    )
    r = anon_client.post(
        "/biz/import/preview",
        params={"entity": "customers"},
        files={
            "file": (
                "khach.xlsx",
                data,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    assert r.status_code == 200, r.text
    p = r.json()
    assert (
        p["columns"] == ["Họ tên", "Số điện thoại", "Nhãn", "Lần đến gần nhất"]
        and p["row_count"] == 3
    )
    assert p["suggested_mapping"] == {
        "name": "Họ tên",
        "phone": "Số điện thoại",
        "tags": "Nhãn",
        "last_visit_at": "Lần đến gần nhất",
    }
    assert p["sample"][0]["Lần đến gần nhất"] == "2026-11-01"
    r = anon_client.post(
        "/biz/import",
        params={
            "entity": "customers",
            "mapping": '{"name": "Họ tên", "phone": "Số điện thoại", "tags": "Nhãn", "last_visit_at": "Lần đến gần nhất"}',
        },
        files={"file": ("khach.xlsx", data, "application/octet-stream")},
    )
    assert r.status_code == 200, r.text
    res = r.json()
    assert (res["created"], res["updated"], res["skipped"]) == (2, 0, 1) and "dòng 4" in res[
        "errors"
    ][0]
    one = anon_client.get("/biz/customers", params={"q": "0911111111"}).json()[0]
    assert one["tags"] == ["mới", "vip"] and one["last_visit_at"].startswith("2026-11-01")
    # re-import updates by phone instead of duplicating
    r = anon_client.post(
        "/biz/import",
        params={"entity": "customers", "mapping": '{"name": "Họ tên", "phone": "Số điện thoại"}'},
        files={"file": ("khach.xlsx", data, "application/octet-stream")},
    )
    assert (r.json()["created"], r.json()["updated"]) == (0, 2)
    # missing required mapping → 422
    assert (
        anon_client.post(
            "/biz/import",
            params={"entity": "customers", "mapping": '{"phone": "Số điện thoại"}'},
            files={"file": ("k.csv", b"a,b\n1,2\n", "text/csv")},
        ).status_code
        == 422
    )

    # memberships referencing customers by phone and services by name
    csv_data = "SĐT,Gói,Bắt đầu,Số buổi,Đã dùng,Số tiền\n0911111111,PT 10 buổi,01/11/2026,10,3,3.000.000\n0999999999,PT 10 buổi,01/11/2026,,,\n0922222222,Không có gói,2026-11-01,,,\n".encode()
    r = anon_client.post(
        "/biz/import/preview",
        params={"entity": "memberships"},
        files={"file": ("goi.csv", csv_data, "text/csv")},
    )
    sm = r.json()["suggested_mapping"]
    assert (
        sm["phone"] == "SĐT"
        and sm["service_name"] == "Gói"
        and sm["start_date"] == "Bắt đầu"
        and sm["sessions_used"] == "Đã dùng"
        and sm["amount_paid"] == "Số tiền"
    )
    import json

    r = anon_client.post(
        "/biz/import",
        params={"entity": "memberships", "mapping": json.dumps(sm)},
        files={"file": ("goi.csv", csv_data, "text/csv")},
    )
    res = r.json()
    assert (
        res["created"] == 1
        and res["skipped"] == 2
        and "0999999999" in res["errors"][0]
        and "Không có gói" in res["errors"][1]
    )
    m = anon_client.get("/biz/memberships", params={"customer_id": one["id"]}).json()[0]
    assert (
        m["sessions_total"] == 10
        and m["sessions_used"] == 3
        and m["amount_paid"] == 3_000_000
        and m["service_name"] == "PT 10 buổi"
    )

    # services
    svc_csv = "Tên,Loại,Giá,Số buổi,Hiệu lực\nGói VIP,gym_package,2.500.000,0,90\nPT 10 buổi,pt_session,3.200.000,10,60\n".encode()
    r = anon_client.post(
        "/biz/import",
        params={
            "entity": "services",
            "mapping": '{"name":"Tên","category":"Loại","price":"Giá","sessions":"Số buổi","validity_days":"Hiệu lực"}',
        },
        files={"file": ("dv.csv", svc_csv, "text/csv")},
    )
    assert (r.json()["created"], r.json()["updated"]) == (1, 1)
    assert (
        next(s for s in anon_client.get("/biz/services").json() if s["name"] == "PT 10 buổi")[
            "price"
        ]
        == 3_200_000
    )


@pytest.mark.parametrize("bad", [b"", b"\x00\x01garbage"])
def test_import_rejects_bad_files(anon_client: TestClient, bad: bytes) -> None:
    _onboard(anon_client, "bad@example.com", GYM)
    r = anon_client.post(
        "/biz/import/preview",
        params={"entity": "customers"},
        files={"file": ("x.xlsx", bad, "application/octet-stream")},
    )
    assert r.status_code == 422


def test_overview_numbers(anon_client: TestClient) -> None:
    _onboard(anon_client, "ov@example.com", GYM)
    o = anon_client.get("/biz/overview").json()
    assert o["appointments_today"] == 2 and o["customers"] == 5 and o["unread_conversations"] == 1
    assert (
        o["expiring_memberships"] >= 1 and o["ai_suggestions"] == 1 and o["hours_saved_week"] == 0.0
    )
    assert o["no_show_rate"] == 1.0 and o["no_show_30d"] == 1  # sample no-show yesterday
    assert [u["status"] for u in o["upcoming_today"]] == ["confirmed", "booked"]
    # an AI message sent this week counts as time saved
    conv = anon_client.get("/biz/conversations").json()[0]
    msgs = anon_client.get(f"/biz/conversations/{conv['id']}/messages").json()
    suggestion = next(m for m in msgs if m["status"] == "ai_suggested")
    anon_client.post(f"/biz/conversations/{conv['id']}/messages/{suggestion['id']}/send")
    o = anon_client.get("/biz/overview").json()
    assert o["ai_messages_week"] == 1 and o["hours_saved_week"] == 0.0  # 1.5 min rounds to 0.0 h
    assert o["unread_conversations"] == 0 and o["ai_suggestions"] == 0
