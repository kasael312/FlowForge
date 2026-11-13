"""Hardening from the step-8 security review."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import net
from app.config import get_settings
from app.models import User
from app.services import triggers as trig
from tests.conftest import DEFAULT_PASSWORD, login, make_step

# ---------------------------------------------------------------- sessions


def test_logout_and_password_change_revoke_sessions(anon_client: TestClient) -> None:
    login(anon_client, "rev@example.com")
    access = anon_client.cookies.get("access_token")
    assert anon_client.get("/auth/me").status_code == 200
    # a second "device" holding the same access token
    other = {"Authorization": f"Bearer {access}"}
    assert anon_client.get("/auth/me", headers=other).status_code == 200
    anon_client.post("/auth/logout")
    r = anon_client.get("/auth/me", headers=other)
    assert r.status_code == 401 and "revoked" in r.json()["detail"]

    login(anon_client, "rev@example.com")
    old = anon_client.cookies.get("access_token")
    r = anon_client.post(
        "/auth/password",
        json={"current_password": DEFAULT_PASSWORD, "new_password": "Another-pass-9"},
    )
    assert r.status_code == 204
    assert anon_client.get("/auth/me").status_code == 200  # this session got fresh cookies
    assert (
        anon_client.get("/auth/me", headers={"Authorization": f"Bearer {old}"}).status_code == 401
    )


def test_login_rate_limit(anon_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.routers.auth import login_limiter

    login_limiter._failures.clear()
    monkeypatch.setattr(get_settings(), "login_max_failures", 3)
    login(anon_client, "rl@example.com")
    anon_client.cookies.clear()
    for _ in range(3):
        assert (
            anon_client.post(
                "/auth/login", json={"email": "rl@example.com", "password": "wrong-pass-1"}
            ).status_code
            == 401
        )
    r = anon_client.post(
        "/auth/login", json={"email": "rl@example.com", "password": DEFAULT_PASSWORD}
    )
    assert r.status_code == 429
    login_limiter._failures.clear()
    assert (
        anon_client.post(
            "/auth/login", json={"email": "rl@example.com", "password": DEFAULT_PASSWORD}
        ).status_code
        == 200
    )


def test_registration_policy_and_first_admin(
    anon_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "registration", "first_user")
    r = anon_client.post(
        "/auth/register", json={"email": "first@example.com", "password": DEFAULT_PASSWORD}
    )
    assert r.status_code == 201 and r.json()["is_admin"] is True
    r = anon_client.post(
        "/auth/register", json={"email": "second@example.com", "password": DEFAULT_PASSWORD}
    )
    assert r.status_code == 403
    monkeypatch.setattr(settings, "registration", "open")
    r = anon_client.post(
        "/auth/register", json={"email": "second@example.com", "password": DEFAULT_PASSWORD}
    )
    assert r.status_code == 201 and r.json()["is_admin"] is False
    monkeypatch.setattr(settings, "registration", "closed")
    assert (
        anon_client.post(
            "/auth/register", json={"email": "third@example.com", "password": DEFAULT_PASSWORD}
        ).status_code
        == 403
    )


def test_pricing_is_admin_only(anon_client: TestClient) -> None:
    login(anon_client, "admin1@example.com")  # first user → admin
    prices = anon_client.get("/pricing").json()["prices"]
    assert anon_client.put("/pricing", json={"prices": prices}).status_code == 200
    login(anon_client, "plain@example.com")
    assert anon_client.get("/pricing").status_code == 200
    assert anon_client.put("/pricing", json={"prices": prices}).status_code == 403


# ---------------------------------------------------------------- request hardening


def test_csrf_origin_check_and_headers(client: TestClient, sample_flow: dict) -> None:
    r = client.get("/auth/me")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["cache-control"] == "no-store"
    bad = client.post("/flows", json=sample_flow, headers={"Origin": "https://evil.example"})
    assert bad.status_code == 403
    ok = client.post(
        "/flows", json=sample_flow, headers={"Origin": get_settings().cors_origin_list[0]}
    )
    assert ok.status_code == 201
    same = client.post("/flows", json=sample_flow, headers={"Origin": "http://testserver"})
    assert same.status_code == 201
    # webhooks are server-to-server and exempt
    assert (
        client.post("/hooks/nope", json={}, headers={"Origin": "https://evil.example"}).status_code
        == 404
    )


def test_body_size_limit(client: TestClient) -> None:
    r = client.post(
        "/flows",
        content=b"{}",
        headers={"Content-Length": str(200 * 1024 * 1024), "content-type": "application/json"},
    )
    assert r.status_code == 413


# ---------------------------------------------------------------- SSRF guard


def test_public_url_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        net,
        "resolver_override",
        lambda host: ["10.0.0.9"] if host == "intranet.example" else ["93.184.216.34"],
    )
    assert net.check_public_url_sync("https://api.example/x") == "https://api.example/x"
    for bad in (
        "http://localhost:8000/x",
        "http://127.0.0.1/x",
        "http://169.254.169.254/latest/meta-data",
        "http://[::1]/",
        "http://10.1.2.3/",
        "http://172.17.0.1/",
        "http://intranet.example/",
        "http://metadata.google.internal/",
        "ftp://example.com/",
    ):
        with pytest.raises(net.UnsafeUrl):
            net.check_public_url_sync(bad)
    # operator-trusted internal prefixes pass (local Ollama by default)
    assert net.check_public_url_sync("http://localhost:11434/v1") == "http://localhost:11434/v1"


async def test_guarded_client_blocks_redirect_to_private(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    monkeypatch.setattr(net, "resolver_override", lambda host: ["93.184.216.34"])
    calls: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(str(req.url))
        if req.url.host == "public.example":
            return httpx.Response(302, headers={"location": "http://169.254.169.254/latest"})
        return httpx.Response(200, text="secret")

    client = net.guarded_client(transport=httpx.MockTransport(handler), follow_redirects=True)
    with pytest.raises(httpx.RequestError, match="blocked"):
        await client.get("http://public.example/")
    assert calls == ["http://public.example/"]


def test_models_and_mcp_reject_internal_targets(anon_client: TestClient) -> None:
    login(anon_client, "adm@example.com")  # admin
    login(anon_client, "usr@example.com")  # plain user
    r = anon_client.post(
        "/models",
        json={
            "provider": "openai_compatible",
            "model_name": "m",
            "label": "L",
            "base_url": "http://127.0.0.1:8000/v1",
            "params": {},
        },
    )
    assert r.status_code == 422 and "base_url" in r.text
    r = anon_client.post(
        "/models",
        json={
            "provider": "ollama",
            "model_name": "m",
            "label": "O",
            "base_url": "http://localhost:11434",
            "params": {},
        },
    )
    assert r.status_code == 201  # trusted internal prefix
    assert (
        anon_client.post("/mcp-servers", json={"name": "s", "command": "evil"}).status_code == 403
    )
    assert (
        anon_client.post(
            "/mcp-servers", json={"name": "s", "url": "http://10.0.0.1/mcp"}
        ).status_code
        == 422
    )


# ---------------------------------------------------------------- file watch / SQL / shared


def test_file_watch_pattern_validation(client: TestClient, sample_flow: dict, tmp_path) -> None:
    flow = client.post("/flows", json=sample_flow).json()
    for bad in ("../../*", "**/*", "a/b", "../flowforge.db"):
        r = client.post(
            f"/flows/{flow['id']}/triggers",
            json={"type": "file_watch", "name": "w", "config": {"pattern": bad}},
        )
        assert r.status_code == 422, bad
    with pytest.raises(trig.TriggerError):
        trig.safe_glob_pattern("../x")
    folder = tmp_path / "watch"
    (folder / "sub").mkdir(parents=True)
    (folder / "ok.txt").write_text("1")
    (folder / "sub" / "nested.txt").write_text("2")
    (tmp_path / "outside.txt").write_text("3")
    (folder / "link.txt").symlink_to(tmp_path / "outside.txt")
    assert [p.name for p in trig.scan_folder(folder, "*.txt")] == ["ok.txt"]


def test_shared_view_masks_credentials(client: TestClient) -> None:
    node = make_step(
        "h",
        "http_request",
        url="https://api.example/x",
        headers={"Authorization": "Bearer abc", "X-Plain": "1"},
    )
    out = make_step(
        "o",
        "output",
        output_source="x",
        deliver_tool="slack_post",
        deliver_args={"token": "t", "text": "hi"},
    )
    flow = client.post(
        "/flows",
        json={
            "name": "s",
            "nodes": [node, out],
            "edges": [{"id": "e", "source": "h", "target": "o", "sourceHandle": None}],
        },
    ).json()
    flow = client.put(f"/flows/{flow['id']}", json={"visibility": "shared_link"}).json()
    client.cookies.clear()
    shared = client.get(f"/shared/{flow['share_token']}").json()
    data = {n["id"]: n["data"] for n in shared["nodes"]}
    assert data["h"]["headers"] == {"Authorization": "***", "X-Plain": "1"}
    assert data["o"]["deliver_args"] == {"token": "***", "text": "hi"}


def test_imported_skill_file_type_is_derived_from_extension(
    client: TestClient, db_session: Session
) -> None:
    import base64

    from app.services.flowio import apply_import

    user = db_session.get(User, client.get("/auth/me").json()["id"])
    doc = {
        "nodes": [make_step("a", "custom", system_prompt="x", skill_ids=["s1"])],
        "edges": [],
        "skills": [
            {
                "id": "s1",
                "name": "evil",
                "content": "x",
                "files": [
                    {
                        "path": "page.txt",
                        "content_type": "text/html",
                        "data": base64.b64encode(b"<script>1</script>").decode(),
                    }
                ],
            }
        ],
    }
    flow, _ = apply_import(db_session, user, doc)
    sid = flow.nodes[0]["data"]["skill_ids"][0]
    r = client.get(f"/skills/{sid}/files/page.txt")
    assert r.headers["content-type"].startswith("text/plain")
    assert r.headers["content-disposition"].startswith("attachment")
    assert r.headers["x-content-type-options"] == "nosniff"


def test_template_range_is_capped() -> None:
    from app.executor.nodes import render_jinja

    assert render_jinja("{% for i in range(3) %}{{ i }}{% endfor %}", {}) == "012"
    with pytest.raises(ValueError):
        render_jinja("{% for i in range(10**9) %}x{% endfor %}", {})
