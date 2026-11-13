"""Connectors: 24h policy, webhooks (signatures, dedupe), Zalo/Facebook/Google clients via MockTransport."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.biz.models import Appointment, Conversation
from app.connectors import base, policy
from app.connectors.models import Connector, ConnectorKind
from tests.conftest import login

GYM = {
    "name": "Gym A",
    "industry": "gym",
    "branch_count": 1,
    "main_channel": "zalo",
    "current_software": "",
}


def _onboard(client: TestClient, email: str) -> dict:
    login(client, email)
    r = client.post("/onboarding", json=GYM)
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture
def mock_http(monkeypatch: pytest.MonkeyPatch):
    """Route connector HTTP to a handler; returns the list of requests seen."""
    calls: list[httpx.Request] = []
    handlers: dict[str, object] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        fn = handlers.get("fn")
        return fn(req) if fn else httpx.Response(500, json={"error": "no handler"})  # type: ignore[operator]

    monkeypatch.setattr(base, "transport_override", httpx.MockTransport(handler))

    def use(fn) -> list[httpx.Request]:
        handlers["fn"] = fn
        return calls

    return use


# ---------------------------------------------------------------- policy


def test_24h_window_rule() -> None:
    now = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
    assert policy.within_window(now - timedelta(hours=23, minutes=59), now)
    assert not policy.within_window(now - timedelta(hours=24), now)
    assert not policy.within_window(None, now)
    assert policy.within_window(datetime(2026, 11, 10, 11, 0), now)  # naive = UTC
    assert policy.fb_tag_allowed("CONFIRMED_EVENT_UPDATE") and not policy.fb_tag_allowed("PROMO")


# ---------------------------------------------------------------- settings API


def test_connector_settings_and_isolation(client: TestClient, anon_client: TestClient) -> None:
    _onboard(client, "owner@example.com")
    r = client.put(
        "/biz/connectors/zalo",
        json={
            "config": {
                "app_id": "123",
                "zns_templates": [
                    {"id": "77", "name": "Nhắc lịch", "price": 350, "params": ["ten", "gio"]}
                ],
                "junk": 1,
            },
            "secrets": {"access_token": "tok", "app_secret": "sec", "evil": "x"},
        },
    )
    assert r.status_code == 200, r.text
    z = r.json()
    assert (
        z["kind"] == "zalo"
        and z["secret_keys"] == ["access_token", "app_secret"]
        and "junk" not in z["config"]
    )
    assert z["config"]["zns_templates"][0]["price"] == 350 and z["webhook_url"].endswith(
        f"/hooks/zalo/{z['webhook_url'].rsplit('/', 1)[-1]}"
    )
    assert '"tok"' not in r.text and '"sec"' not in r.text  # values never echoed
    # empty keeps, null removes
    z = client.put(
        "/biz/connectors/zalo", json={"secrets": {"access_token": "", "app_secret": None}}
    ).json()
    assert z["secret_keys"] == ["access_token"]
    fb = client.put(
        "/biz/connectors/facebook", json={"secrets": {"page_access_token": "pat"}}
    ).json()
    assert fb["verify_token"] and fb["webhook_url"].endswith(fb["verify_token"])
    assert sorted(c["kind"] for c in client.get("/biz/connectors").json()) == ["facebook", "zalo"]
    old_token = fb["verify_token"]
    assert (
        client.put("/biz/connectors/facebook", json={"rotate_webhook_token": True}).json()[
            "verify_token"
        ]
        != old_token
    )
    meta = client.get("/biz/connectors/meta").json()
    assert "CONFIRMED_EVENT_UPDATE" in meta["fb_tags"]
    assert client.put("/biz/connectors/dental", json={}).status_code == 422
    # staff cannot edit; other workspace sees nothing
    client.post(
        "/workspaces/current/members", json={"email": "staff@example.com", "role": "staff"}
    ) if login(anon_client, "staff@example.com") else None
    login(client, "owner@example.com")
    client.post("/workspaces/current/members", json={"email": "staff@example.com", "role": "staff"})
    login(client, "staff@example.com")
    assert client.get("/biz/connectors").status_code == 403  # connections are owner-only
    assert client.put("/biz/connectors/zalo", json={"enabled": False}).status_code == 403
    _onboard(client, "other@example.com")
    assert client.get("/biz/connectors").json() == []
    assert client.delete("/biz/connectors/zalo").status_code == 404


# ---------------------------------------------------------------- Zalo


def _zalo_sig(app_id: str, body: bytes, ts: str, secret: str) -> str:
    return "mac=" + hashlib.sha256((app_id + body.decode() + ts + secret).encode()).hexdigest()


def test_zalo_webhook_and_sending(client: TestClient, mock_http) -> None:
    _onboard(client, "zalo@example.com")
    z = client.put(
        "/biz/connectors/zalo",
        json={
            "config": {
                "app_id": "app1",
                "zns_templates": [{"id": "T1", "name": "Nhắc", "price": 400, "params": ["ten"]}],
            },
            "secrets": {"access_token": "tok", "app_secret": "s3cret"},
        },
    ).json()
    token = z["webhook_url"].rsplit("/", 1)[-1]

    # inbound text event with valid signature → conversation + stub customer
    body = json.dumps(
        {
            "event_name": "user_send_text",
            "app_id": "app1",
            "timestamp": "1762771200000",
            "sender": {"id": "u-9988"},
            "recipient": {"id": "oa1"},
            "message": {"text": "Cho em hỏi giá", "msg_id": "m1"},
        }
    ).encode()
    r = client.post(
        f"/hooks/zalo/{token}",
        content=body,
        headers={
            "content-type": "application/json",
            "X-ZEvent-Signature": _zalo_sig("app1", body, "1762771200000", "s3cret"),
        },
    )
    assert r.status_code == 200 and r.json()["stored"] is True
    assert (
        client.post(
            f"/hooks/zalo/{token}",
            content=body,
            headers={"content-type": "application/json", "X-ZEvent-Signature": "mac=bad"},
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/hooks/zalo/nope", content=body, headers={"content-type": "application/json"}
        ).status_code
        == 404
    )
    # retry with the same msg_id is ignored
    r = client.post(
        f"/hooks/zalo/{token}",
        content=body,
        headers={
            "content-type": "application/json",
            "X-ZEvent-Signature": _zalo_sig("app1", body, "1762771200000", "s3cret"),
        },
    )
    assert r.json()["stored"] is False
    convs = client.get("/biz/conversations").json()
    conv = next(c for c in convs if c["external_thread_id"] == "u-9988")
    assert (
        conv["channel"] == "zalo"
        and conv["unread"]
        and conv["customer_name"] == "Khách Zalo 9988"
        and conv["window"]["open"] is False
    )
    msgs = client.get(f"/biz/conversations/{conv['id']}/messages").json()
    assert msgs[-1]["text"] == "Cho em hỏi giá" and msgs[-1]["direction"] == "in"

    # outside the window: free reply rejected (409), ZNS template works
    sent: list[dict] = []

    def handler(req: httpx.Request) -> httpx.Response:
        sent.append(
            {
                "url": str(req.url),
                "body": json.loads(req.content or b"{}"),
                "token": req.headers.get("access_token"),
            }
        )
        if req.url.path.endswith("/message/template"):
            return httpx.Response(
                200, json={"error": 0, "message": "Success", "data": {"msg_id": "zns-1"}}
            )
        if req.url.path.endswith("/oa/message/cs"):
            return httpx.Response(200, json={"error": 0, "data": {"message_id": "cs-1"}})
        if req.url.path.endswith("/oa/getoa"):
            return httpx.Response(
                200,
                json={"error": 0, "data": {"oa_id": "oa1", "name": "Gym A OA", "num_follower": 12}},
            )
        return httpx.Response(200, json={"error": -1, "message": "unknown"})

    mock_http(handler)
    r = client.post(f"/biz/conversations/{conv['id']}/messages", json={"text": "Dạ giá 500k ạ"})
    assert r.status_code == 409 and "24 giờ" in r.json()["detail"]
    # the stub customer has no phone yet → ZNS impossible
    r = client.post(
        f"/biz/conversations/{conv['id']}/messages", json={"text": "x", "zns_template_id": "T1"}
    )
    assert r.status_code == 409 and "số điện thoại" in r.json()["detail"]
    client.put(f"/biz/customers/{conv['customer_id']}", json={"phone": "0988123456"})
    r = client.post(
        f"/biz/conversations/{conv['id']}/messages",
        json={"text": "Nhắc lịch", "zns_template_id": "T1", "zns_params": {"ten": "Anh"}},
    )
    assert r.status_code == 201, r.text
    assert sent[-1]["url"].startswith("https://business.openapi.zalo.me/message/template")
    assert (
        sent[-1]["body"]["phone"] == "84988123456"
        and sent[-1]["body"]["template_id"] == "T1"
        and sent[-1]["token"] == "tok"
    )
    assert r.json()["external_id"] == "zns-1"
    assert (
        client.post(
            f"/biz/conversations/{conv['id']}/messages", json={"text": "x", "zns_template_id": "T9"}
        ).status_code
        == 502
    )

    # a fresh inbound message opens the window → CS message
    fresh = json.dumps(
        {
            "event_name": "user_send_text",
            "app_id": "app1",
            "timestamp": str(int(datetime.now(UTC).timestamp() * 1000)),
            "sender": {"id": "u-9988"},
            "message": {"text": "ok", "msg_id": "m2"},
        }
    ).encode()
    client.post(
        f"/hooks/zalo/{token}",
        content=fresh,
        headers={
            "content-type": "application/json",
            "X-ZEvent-Signature": _zalo_sig(
                "app1", fresh, json.loads(fresh)["timestamp"], "s3cret"
            ),
        },
    )
    assert client.get(f"/biz/conversations/{conv['id']}").json()["window"]["open"] is True
    r = client.post(f"/biz/conversations/{conv['id']}/messages", json={"text": "Dạ vâng"})
    assert (
        r.status_code == 201
        and sent[-1]["url"].endswith("/v3.0/oa/message/cs")
        and sent[-1]["body"]["recipient"] == {"user_id": "u-9988"}
    )

    # test connection stores the OA name
    r = client.post("/biz/connectors/zalo/test")
    assert (
        r.status_code == 200
        and r.json()["status"] == "connected"
        and r.json()["config"]["oa_name"] == "Gym A OA"
    )
    assert "Gym A OA" in r.json()["test_message"]


def test_zalo_token_refresh_on_expiry(client: TestClient, mock_http) -> None:
    _onboard(client, "zalo2@example.com")
    client.put(
        "/biz/connectors/zalo",
        json={
            "config": {"app_id": "app1"},
            "secrets": {"access_token": "old", "refresh_token": "rt", "app_secret": "s3cret"},
        },
    )
    seen: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.host == "oauth.zaloapp.com":
            assert req.headers["secret_key"] == "s3cret"
            return httpx.Response(
                200, json={"access_token": "new", "refresh_token": "rt2", "expires_in": "3600"}
            )
        seen.append(req.headers.get("access_token", ""))
        if req.headers.get("access_token") == "old":
            return httpx.Response(200, json={"error": -216, "message": "Access token is invalid"})
        return httpx.Response(200, json={"error": 0, "data": {"oa_id": "oa1", "name": "OA"}})

    mock_http(handler)
    r = client.post("/biz/connectors/zalo/test")
    assert r.status_code == 200 and r.json()["status"] == "connected" and seen == ["old", "new"]
    assert r.json()["secret_keys"] == ["access_token", "app_secret", "refresh_token"]


# ---------------------------------------------------------------- Facebook


def test_facebook_verify_webhook_and_tags(client: TestClient, mock_http) -> None:
    _onboard(client, "fb@example.com")
    fb = client.put(
        "/biz/connectors/facebook",
        json={"secrets": {"page_access_token": "pat", "app_secret": "appsec"}},
    ).json()
    token = fb["verify_token"]
    r = client.get(
        f"/hooks/facebook/{token}",
        params={"hub.mode": "subscribe", "hub.verify_token": token, "hub.challenge": "12345"},
    )
    assert r.status_code == 200 and r.text == "12345"
    assert (
        client.get(
            f"/hooks/facebook/{token}",
            params={"hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "1"},
        ).status_code
        == 403
    )

    payload = json.dumps(
        {
            "object": "page",
            "entry": [
                {
                    "id": "page1",
                    "messaging": [
                        {
                            "sender": {"id": "psid-1"},
                            "recipient": {"id": "page1"},
                            "timestamp": 1762771200000,
                            "message": {"mid": "mid1", "text": "Hello shop"},
                        },
                        {
                            "sender": {"id": "page1"},
                            "message": {"mid": "e", "text": "echo", "is_echo": True},
                        },
                    ],
                }
            ],
        }
    ).encode()
    sig = "sha256=" + hmac.new(b"appsec", payload, hashlib.sha256).hexdigest()
    assert (
        client.post(
            f"/hooks/facebook/{token}",
            content=payload,
            headers={"content-type": "application/json"},
        ).status_code
        == 401
    )
    r = client.post(
        f"/hooks/facebook/{token}",
        content=payload,
        headers={"content-type": "application/json", "X-Hub-Signature-256": sig},
    )
    assert r.status_code == 200 and r.json()["stored"] == 1
    conv = next(c for c in client.get("/biz/conversations").json() if c["channel"] == "facebook")
    assert conv["customer_name"] == "Khách Facebook id-1" and conv["window"]["fallback"] == "tag"

    sent: list[dict] = []

    def handler(req: httpx.Request) -> httpx.Response:
        sent.append(json.loads(req.content or b"{}"))
        assert req.url.params["access_token"] == "pat"
        return httpx.Response(200, json={"recipient_id": "psid-1", "message_id": "m.123"})

    mock_http(handler)
    assert (
        client.post(
            f"/biz/conversations/{conv['id']}/messages", json={"text": "Chào bạn"}
        ).status_code
        == 409
    )
    r = client.post(
        f"/biz/conversations/{conv['id']}/messages",
        json={"text": "Nhắc lịch mai 9h", "fb_tag": "CONFIRMED_EVENT_UPDATE"},
    )
    assert (
        r.status_code == 201
        and sent[-1]["messaging_type"] == "MESSAGE_TAG"
        and sent[-1]["tag"] == "CONFIRMED_EVENT_UPDATE"
    )
    assert (
        client.post(
            f"/biz/conversations/{conv['id']}/messages",
            json={"text": "Khuyến mãi", "fb_tag": "PROMO"},
        ).status_code
        == 502
    )
    # recorded without delivery when asked
    r = client.post(
        f"/biz/conversations/{conv['id']}/messages",
        json={"text": "ghi chú nội bộ", "deliver": False},
    )
    assert r.status_code == 201 and len(sent) == 1


# ---------------------------------------------------------------- Google Calendar


def test_google_calendar_push_and_pull(client: TestClient, db_session: Session, mock_http) -> None:
    ws = _onboard(client, "gcal@example.com")
    client.put(
        "/biz/connectors/google_calendar",
        json={
            "config": {"calendar_id": "primary"},
            "secrets": {"client_id": "cid", "client_secret": "cs", "refresh_token": "rt"},
        },
    )
    events: dict[str, dict] = {}
    pulled: list[dict] = [
        {
            "id": "ext-1",
            "status": "confirmed",
            "summary": "Khách vãng lai",
            "start": {
                "dateTime": (datetime.now(UTC) + timedelta(days=2))
                .replace(microsecond=0)
                .isoformat()
            },
            "end": {
                "dateTime": (datetime.now(UTC) + timedelta(days=2, hours=1))
                .replace(microsecond=0)
                .isoformat()
            },
        },
    ]

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "at", "expires_in": 3600})
        assert req.headers["authorization"] == "Bearer at"
        path = req.url.path
        if req.method == "GET" and path.endswith("/calendars/primary"):
            return httpx.Response(200, json={"summary": "Lịch Gym", "timeZone": "Asia/Ho_Chi_Minh"})
        if req.method == "GET" and path.endswith("/events"):
            items = pulled + [{**v, "id": k} for k, v in events.items()]
            return httpx.Response(200, json={"items": items, "nextSyncToken": "sync-1"})
        if req.method == "POST" and path.endswith("/events"):
            body = json.loads(req.content)
            eid = f"ev-{len(events) + 1}"
            events[eid] = {**body, "status": "confirmed"}
            return httpx.Response(200, json={"id": eid})
        if req.method == "PUT":
            events[path.rsplit("/", 1)[-1]] = {**json.loads(req.content), "status": "confirmed"}
            return httpx.Response(200, json={"id": path.rsplit("/", 1)[-1]})
        if req.method == "DELETE":
            events.pop(path.rsplit("/", 1)[-1], None)
            return httpx.Response(204)
        return httpx.Response(404, json={"error": {"message": "nope"}})

    mock_http(handler)
    r = client.post("/biz/connectors/google_calendar/test")
    assert r.status_code == 200 and r.json()["config"]["calendar_name"] == "Lịch Gym"

    # booking pushes an event; cancelling deletes it
    cust = client.get("/biz/customers").json()[0]
    start = (datetime.now(UTC) + timedelta(days=3)).replace(minute=0, second=0, microsecond=0)
    a = client.post(
        "/biz/appointments", json={"customer_id": cust["id"], "start_at": start.isoformat()}
    ).json()
    assert len(events) == 1 and next(iter(events.values()))["summary"].startswith(cust["name"])
    row = db_session.get(Appointment, a["id"])
    assert row is not None and row.google_event_id == "ev-1"
    client.put(f"/biz/appointments/{a['id']}", json={"status": "cancelled"})
    assert events == {}

    # sync: pulls the external event into a placeholder customer and pushes the sample appointments
    r = client.post("/biz/connectors/google_calendar/sync")
    assert r.status_code == 200, r.text
    assert r.json()["created"] == 1 and r.json()["pushed"] >= 1
    ext = next(
        x
        for x in client.get(
            "/biz/appointments",
            params={
                "start": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
                "end": (datetime.now(UTC) + timedelta(days=4)).isoformat(),
            },
        ).json()
        if x["customer_name"] == "Google: Khách vãng lai"
    )
    # the external event moved → second sync moves the appointment; cancelled → cancels it
    pulled[0]["start"]["dateTime"] = (
        (datetime.now(UTC) + timedelta(days=2, hours=3)).replace(microsecond=0).isoformat()
    )
    pulled[0]["end"]["dateTime"] = (
        (datetime.now(UTC) + timedelta(days=2, hours=4)).replace(microsecond=0).isoformat()
    )
    assert client.post("/biz/connectors/google_calendar/sync").json()["moved"] == 1
    pulled[0]["status"] = "cancelled"
    assert client.post("/biz/connectors/google_calendar/sync").json()["cancelled"] == 1
    moved = db_session.get(Appointment, ext["id"])
    assert (
        moved is not None and moved.status.value == "cancelled" and moved.workspace_id == ws["id"]
    )
    # events created by us carry the appointment id so pulls never duplicate them
    assert all("flowforge_appointment" in json.dumps(v) for v in events.values())
    assert isinstance(
        db_session.scalar(
            __import__("sqlalchemy")
            .select(Connector)
            .where(Connector.kind == ConnectorKind.google_calendar)
        ),
        Connector,
    )
    assert isinstance(db_session.query(Conversation).first(), Conversation)
