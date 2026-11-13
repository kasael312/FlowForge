"""Auth: register/login/logout/refresh, token expiry and per-user isolation."""

from __future__ import annotations

from datetime import timedelta

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.auth import ACCESS_COOKIE, REFRESH_COOKIE, create_token
from app.routers.logs import WS_NOT_FOUND, WS_UNAUTHORIZED
from tests.conftest import login

ALICE = {"email": "Alice@Example.com", "password": "correct horse"}


def test_register_login_me_logout(anon_client: TestClient) -> None:
    r = anon_client.post("/auth/register", json=ALICE)
    assert r.status_code == 201, r.text
    assert r.json()["email"] == "alice@example.com"  # normalised
    assert ACCESS_COOKIE in r.cookies and REFRESH_COOKIE in r.cookies
    set_cookie = " ".join(r.headers.get_list("set-cookie")).lower()
    assert "httponly" in set_cookie and "samesite=lax" in set_cookie
    assert "password" not in r.text

    assert anon_client.get("/auth/me").json()["email"] == "alice@example.com"

    assert anon_client.post("/auth/register", json=ALICE).status_code == 409

    assert anon_client.post("/auth/logout").status_code == 204
    assert anon_client.get("/auth/me").status_code == 401

    bad = anon_client.post("/auth/login", json={**ALICE, "password": "wrong password"})
    assert bad.status_code == 401
    assert (
        anon_client.post(
            "/auth/login", json={"email": "nobody@x.io", "password": "whatever1"}
        ).status_code
        == 401
    )

    ok = anon_client.post("/auth/login", json=ALICE)
    assert ok.status_code == 200
    assert anon_client.get("/auth/me").status_code == 200


def test_register_validation(anon_client: TestClient) -> None:
    assert (
        anon_client.post(
            "/auth/register", json={"email": "not-an-email", "password": "longenough"}
        ).status_code
        == 422
    )
    assert (
        anon_client.post(
            "/auth/register", json={"email": "a@b.io", "password": "short"}
        ).status_code
        == 422
    )


def test_protected_routes_require_auth(anon_client: TestClient) -> None:
    assert anon_client.get("/flows").status_code == 401
    assert anon_client.post("/flows", json={"name": "x"}).status_code == 401
    assert anon_client.get("/runs").status_code == 401
    assert anon_client.get("/runs/abc").status_code == 401
    assert anon_client.get("/health").status_code == 200


def test_expired_and_invalid_tokens(anon_client: TestClient) -> None:
    anon_client.post("/auth/register", json=ALICE)
    user_id = anon_client.get("/auth/me").json()["id"]

    anon_client.cookies.clear()
    expired = create_token(user_id, "access", expires_delta=timedelta(seconds=-1))
    r = anon_client.get("/auth/me", headers={"authorization": f"Bearer {expired}"})
    assert r.status_code == 401 and "expired" in r.text

    # a refresh token must not be accepted as an access token
    refresh = create_token(user_id, "refresh")
    r = anon_client.get("/auth/me", headers={"authorization": f"Bearer {refresh}"})
    assert r.status_code == 401

    r = anon_client.get("/auth/me", headers={"authorization": "Bearer garbage"})
    assert r.status_code == 401


def test_refresh_rotates_tokens(anon_client: TestClient) -> None:
    anon_client.post("/auth/register", json=ALICE)
    old_access = anon_client.cookies[ACCESS_COOKIE]

    # Simulate a lost/expired access cookie but a valid refresh cookie.
    anon_client.cookies.delete(ACCESS_COOKIE, domain="testserver.local", path="/")
    assert anon_client.get("/auth/me").status_code == 401
    r = anon_client.post("/auth/refresh")
    assert r.status_code == 200, r.text
    assert anon_client.cookies[ACCESS_COOKIE] != old_access
    assert anon_client.get("/auth/me").status_code == 200

    # Without a refresh cookie, refresh fails.
    anon_client.cookies.delete(REFRESH_COOKIE, domain="testserver.local", path="/auth/refresh")
    assert anon_client.post("/auth/refresh").status_code == 401


def test_users_are_isolated(anon_client: TestClient, sample_flow: dict) -> None:
    login(anon_client, "a@example.com")
    flow_id = anon_client.post("/flows", json=sample_flow).json()["id"]
    assert len(anon_client.get("/flows").json()) == 1

    login(anon_client, "b@example.com")
    assert anon_client.get("/flows").json() == []
    assert anon_client.get(f"/flows/{flow_id}").status_code == 404
    assert anon_client.put(f"/flows/{flow_id}", json={"name": "hijack"}).status_code == 404
    assert anon_client.delete(f"/flows/{flow_id}").status_code == 404
    assert anon_client.post(f"/flows/{flow_id}/duplicate").status_code == 404
    assert (
        anon_client.post(
            f"/flows/{flow_id}/run", json={"repo_url": "https://github.com/a/b"}
        ).status_code
        == 404
    )

    with anon_client.websocket_connect("/runs/whatever/logs") as ws:
        try:
            ws.receive_json()
            raise AssertionError("expected close")
        except WebSocketDisconnect as exc:
            assert exc.code == WS_NOT_FOUND

    # flow still intact for A
    login(anon_client, "a@example.com")
    assert anon_client.get(f"/flows/{flow_id}").json()["name"] == "Fix issue"


def test_ws_requires_auth(anon_client: TestClient) -> None:
    with anon_client.websocket_connect("/runs/whatever/logs") as ws:
        try:
            ws.receive_json()
            raise AssertionError("expected close")
        except WebSocketDisconnect as exc:
            assert exc.code == WS_UNAUTHORIZED
