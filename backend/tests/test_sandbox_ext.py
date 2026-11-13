"""Sandbox options (limits, allowlist proxy), run timeout, artifacts."""

from __future__ import annotations

import asyncio
import importlib.util
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import get_settings
from app.executor.engine import ExecutorDeps, FlowExecutor
from app.executor.sandbox import DockerSandbox, ExecResult, SandboxOptions
from app.models import Flow, Run, RunStatus
from app.services.artifacts import ArtifactStore
from tests.conftest import make_step
from tests.fakes import FakeAnthropic, FakeSandbox, text_response

# ---------------------------------------------------------------- options + docker argv


def test_sandbox_options_clamped_to_caps() -> None:
    caps = SandboxOptions(cpus=2, memory_mb=2048, timeout_seconds=3600, network="none")
    o = SandboxOptions.from_dict(
        {
            "cpus": 8,
            "memory_mb": 99999,
            "timeout_seconds": 5,
            "network": "allowlist",
            "allowed_hosts": ["API.Example.com", " *.pypi.org "],
        },
        caps=caps,
    )
    assert (o.cpus, o.memory_mb, o.timeout_seconds, o.network) == (2.0, 2048, 30, "allowlist")
    assert o.allowed_hosts == ["api.example.com", "*.pypi.org"]
    assert SandboxOptions.from_dict(None, caps=caps).to_dict()["network"] == "none"
    assert SandboxOptions.from_dict({"network": "bogus"}, caps=caps).network == "none"
    assert SandboxOptions.from_dict({"cpus": 0.5, "memory_mb": 512}, caps=caps).cpus == 0.5


def test_docker_argv_per_network_mode() -> None:
    sb = DockerSandbox(
        "abcdef123456789",
        "/host/ws",
        image="img",
        options=SandboxOptions(cpus=1.5, memory_mb=512, network="none"),
    )
    argv = sb.run_argv()
    assert argv[:5] == ["docker", "run", "-d", "--name", "flowforge-run-abcdef123456"]
    assert "--memory" in argv and argv[argv.index("--memory") + 1] == "512m"
    assert argv[argv.index("--cpus") + 1] == "1.5"
    assert (
        argv[argv.index("--network") + 1] == "none" and "--cap-drop" in argv and argv[-1] == "img"
    )
    assert not any(a.startswith("HTTP_PROXY") for a in argv)

    sb = DockerSandbox(
        "abcdef123456789",
        "/host/ws",
        image="img",
        options=SandboxOptions(network="allowlist", allowed_hosts=["api.github.com", "*.pypi.org"]),
    )
    argv = sb.run_argv()
    assert argv[argv.index("--network") + 1] == "flowforge-net-abcdef123456"
    proxy_url = next(a for a in argv if a.startswith("HTTPS_PROXY="))
    assert re.fullmatch(
        r"HTTPS_PROXY=http://ff:[A-Za-z0-9_-]+@flowforge-proxy-abcdef123456:3128", proxy_url
    )
    proxy = sb.proxy_argv()
    assert f"FF_PROXY_TOKEN={sb.proxy_token}" in proxy
    assert proxy[proxy.index("--network") + 1] == "flowforge-net-abcdef123456"
    assert "FF_ALLOWED_HOSTS=api.github.com,*.pypi.org" in proxy and proxy[-2:] == [
        "python3",
        "/opt/flowforge/proxy.py",
    ]

    sb = DockerSandbox("abcdef123456789", "/host/ws", image="img", network="bridge")
    assert sb.run_argv()[sb.run_argv().index("--network") + 1] == "bridge"


def _load_proxy_module():
    path = Path(__file__).resolve().parents[2] / "sandbox" / "proxy.py"
    spec = importlib.util.spec_from_file_location("ffproxy", path)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def test_proxy_allowlist_matching() -> None:
    proxy = _load_proxy_module()
    allowed = ["api.github.com", "*.pypi.org"]
    assert proxy.host_allowed("api.github.com", allowed)
    assert proxy.host_allowed("API.GITHUB.COM.", allowed)
    assert not proxy.host_allowed("github.com", allowed)
    assert not proxy.host_allowed("evil-api.github.com", allowed)
    assert proxy.host_allowed("pypi.org", allowed) and proxy.host_allowed(
        "files.pythonhosted.pypi.org", allowed
    )
    assert not proxy.host_allowed("notpypi.org", allowed)
    assert proxy.host_allowed("anything", ["*"]) and not proxy.host_allowed("x", [])


async def test_proxy_blocks_and_forwards() -> None:
    """Run the real proxy in-process: a disallowed CONNECT gets 403, an allowed plain
    HTTP request is forwarded to a local upstream."""
    proxy = _load_proxy_module()

    async def upstream(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        head = await reader.readuntil(b"\r\n\r\n")
        assert head.startswith(b"GET /hello?x=1 HTTP/1.1")
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok")
        await writer.drain()
        writer.close()

    up = await asyncio.start_server(upstream, "127.0.0.1", 0)
    up_port = up.sockets[0].getsockname()[1]
    proxy.ALLOWED[:] = ["127.0.0.1"]
    proxy.ALLOW_PRIVATE = True  # the fake upstream lives on loopback
    srv = await asyncio.start_server(proxy.handle, "127.0.0.1", 0)
    port = srv.sockets[0].getsockname()[1]
    try:
        r, w = await asyncio.open_connection("127.0.0.1", port)
        w.write(b"CONNECT evil.example:443 HTTP/1.1\r\nHost: evil.example:443\r\n\r\n")
        await w.drain()
        resp = await r.read()
        assert resp.startswith(b"HTTP/1.1 403") and b"not in the allowlist" in resp
        w.close()

        r, w = await asyncio.open_connection("127.0.0.1", port)
        w.write(
            f"GET http://127.0.0.1:{up_port}/hello?x=1 HTTP/1.1\r\nHost: 127.0.0.1\r\nProxy-Connection: keep-alive\r\n\r\n".encode()
        )
        await w.drain()
        resp = await asyncio.wait_for(r.read(), timeout=5)
        assert resp.startswith(b"HTTP/1.1 200") and resp.endswith(b"ok")
        w.close()
    finally:
        srv.close()
        up.close()
        await srv.wait_closed()
        await up.wait_closed()


# ---------------------------------------------------------------- flow/run settings API


def test_flow_sandbox_settings_and_run_override(
    client: TestClient, deps: ExecutorDeps, fake_client: FakeAnthropic
) -> None:
    from app.services.runs import run_manager

    run_manager._deps = deps
    try:
        r = client.post(
            "/flows",
            json={
                "name": "f",
                "nodes": [make_step("a", "plan")],
                "sandbox": {
                    "cpus": 1,
                    "memory_mb": 1024,
                    "network": "allowlist",
                    "allowed_hosts": ["api.example.com", "*.pypi.org"],
                },
            },
        )
        assert r.status_code == 201, r.text
        flow = r.json()
        assert flow["sandbox"] == {
            "cpus": 1.0,
            "memory_mb": 1024,
            "network": "allowlist",
            "allowed_hosts": ["api.example.com", "*.pypi.org"],
        }
        assert (
            client.post(
                "/flows", json={"name": "f", "sandbox": {"allowed_hosts": ["bad host!"]}}
            ).status_code
            == 422
        )
        assert client.post("/flows", json={"name": "f", "sandbox": {"cpus": 0}}).status_code == 422
        r = client.put(
            f"/flows/{flow['id']}", json={"sandbox": {"timeout_seconds": 120, "network": "none"}}
        )
        assert r.json()["sandbox"] == {
            "timeout_seconds": 120,
            "network": "none",
            "allowed_hosts": [],
        }

        fake_client.responses = [text_response("ok")]
        r = client.post(f"/flows/{flow['id']}/run", json={"sandbox": {"memory_mb": 256}})
        assert r.status_code == 202, r.text
        assert r.json()["sandbox"] == {
            "timeout_seconds": 120,
            "network": "none",
            "allowed_hosts": [],
            "memory_mb": 256,
        }
    finally:
        run_manager._deps = None


# ---------------------------------------------------------------- executor: options, timeout, artifacts


def _run(db: Session, nodes: list[dict], sandbox: dict | None = None) -> str:
    flow = Flow(name="f", nodes=nodes, edges=[])
    db.add(flow)
    db.flush()
    run = Run(flow_id=flow.id, repo_url="", sandbox=sandbox)
    db.add(run)
    db.commit()
    return run.id


async def test_executor_passes_options_and_logs_them(
    deps: ExecutorDeps, db_session: Session, fake_sandbox: FakeSandbox
) -> None:
    received: dict = {}

    def factory(run_id: str, host_path: str, options: SandboxOptions | None = None) -> FakeSandbox:
        received["options"] = options
        return fake_sandbox

    deps.sandbox_factory = factory
    deps.sandbox_caps = SandboxOptions(cpus=4, memory_mb=4096, timeout_seconds=600, network="none")
    fake_sandbox.scripted["ls"] = ExecResult(0, "", "")
    run_id = _run(
        db_session,
        [make_step("s", "shell", command="ls")],
        sandbox={
            "cpus": 1,
            "network": "allowlist",
            "allowed_hosts": ["x.io"],
            "timeout_seconds": 99999,
        },
    )
    assert await FlowExecutor(deps, run_id).execute() is RunStatus.success
    o = received["options"]
    assert (o.cpus, o.memory_mb, o.timeout_seconds, o.network, o.allowed_hosts) == (
        1.0,
        4096,
        600,
        "allowlist",
        ["x.io"],
    )
    msgs = [e.get("message", "") for e in deps.bus.history(run_id) if e["event"] == "log"]
    assert any(
        "sandbox: 1.0 cpu, 4096 MB, network=allowlist (x.io), timeout 600s" == m for m in msgs
    )


async def test_run_timeout_fails_the_run(
    deps: ExecutorDeps, db_session: Session, fake_client: FakeAnthropic
) -> None:
    async def slow(**kwargs):
        await asyncio.sleep(5)
        return text_response("late")

    fake_client.beta.messages.create = slow  # type: ignore[method-assign]
    deps.sandbox_caps = SandboxOptions(timeout_seconds=3600)
    run_id = _run(db_session, [make_step("a", "plan")], sandbox={"timeout_seconds": 30})
    # SandboxOptions clamps to >= 30s; shrink after resolution by monkeypatching from_dict result via caps
    deps.sandbox_caps = SandboxOptions(timeout_seconds=30)
    # make the limit effectively 1s for the test
    import app.executor.engine as engine_mod

    orig = engine_mod.SandboxOptions.from_dict

    def tiny(d, *, caps=None):
        o = orig(d, caps=caps)
        o.timeout_seconds = 1
        return o

    engine_mod.SandboxOptions.from_dict = tiny  # type: ignore[method-assign]
    try:
        assert await FlowExecutor(deps, run_id).execute() is RunStatus.failed
    finally:
        engine_mod.SandboxOptions.from_dict = orig  # type: ignore[method-assign]
    with deps.session_factory() as db:
        run = db.get(Run, run_id)
    assert run is not None and "time limit of 1s" in (run.error or "")


async def test_artifacts_collected_and_downloadable(
    deps: ExecutorDeps,
    db_session: Session,
    fake_sandbox: FakeSandbox,
    anon_client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.conftest import login

    settings = get_settings()
    monkeypatch.setattr(settings, "artifacts_dir", str(tmp_path / "artifacts"))
    monkeypatch.setattr(settings, "artifact_max_bytes", 1000)
    deps.artifact_store = ArtifactStore(deps.session_factory, settings)
    fake_sandbox.binaries["output/report.md"] = b"# Report"
    fake_sandbox.binaries["output/charts/sales.png"] = b"\x89PNG..."
    fake_sandbox.binaries["output/huge.bin"] = b"x" * 5000
    fake_sandbox.scripted["find"] = ExecResult(
        0,
        "/repo/output/report.md\x008\x00/repo/output/charts/sales.png\x007\x00/repo/output/huge.bin\x005000\x00"
        "/repo/output/.hidden\x001\x00/repo/output/../../etc/evil\x003\x00",
        "",
    )
    fake_sandbox.scripted["ls"] = ExecResult(0, "", "")

    uid = login(anon_client, "art@example.com")
    with deps.session_factory() as db:
        flow = Flow(user_id=uid, name="f", nodes=[make_step("s", "shell", command="ls")], edges=[])
        db.add(flow)
        db.flush()
        run = Run(flow_id=flow.id, user_id=uid, repo_url="")
        db.add(run)
        db.commit()
        run_id = run.id
    assert await FlowExecutor(deps, run_id).execute() is RunStatus.success
    events = deps.bus.history(run_id)
    kept = next(e for e in events if e["event"] == "artifacts")["artifacts"]
    assert [a["name"] for a in kept] == [
        "charts/sales.png",
        "report.md",
    ]  # huge.bin skipped by size cap
    assert (tmp_path / "artifacts" / run_id / "charts" / "sales.png").read_bytes() == b"\x89PNG..."

    rows = anon_client.get(f"/runs/{run_id}/artifacts").json()
    assert [(a["name"], a["size"], a["content_type"]) for a in rows] == [
        ("charts/sales.png", 7, "image/png"),
        ("report.md", 8, "text/markdown"),
    ]
    dl = anon_client.get(f"/runs/{run_id}/artifacts/{rows[1]['id']}/download")
    assert (
        dl.status_code == 200
        and dl.content == b"# Report"
        and "report.md" in dl.headers["content-disposition"]
    )
    assert anon_client.get(f"/runs/{run_id}/artifacts/nope/download").status_code == 404
    login(anon_client, "other@example.com")
    assert anon_client.get(f"/runs/{run_id}/artifacts").status_code == 404


def test_proxy_address_and_auth_rules() -> None:
    proxy = _load_proxy_module()
    proxy.ALLOW_PRIVATE = False
    assert proxy.address_allowed("93.184.216.34")
    for bad in (
        "127.0.0.1",
        "10.1.2.3",
        "172.17.0.1",
        "169.254.169.254",
        "192.168.1.1",
        "::1",
        "0.0.0.0",
        "fe80::1",
    ):
        assert not proxy.address_allowed(bad), bad
    assert proxy.authorized(b"Host: x\r\n", token="")  # no token configured → open
    import base64

    good = base64.b64encode(b"ff:secret1").decode()
    assert proxy.authorized(f"Proxy-Authorization: Basic {good}\r\n".encode(), token="secret1")
    assert not proxy.authorized(b"Host: x\r\n", token="secret1")
    assert not proxy.authorized(b"Proxy-Authorization: Basic bm9wZQ==\r\n", token="secret1")


def test_network_mode_is_capped_by_server(monkeypatch: pytest.MonkeyPatch) -> None:
    caps = SandboxOptions(network="none", max_network="allowlist")
    assert SandboxOptions.from_dict({"network": "bridge"}, caps=caps).network == "allowlist"
    assert SandboxOptions.from_dict({"network": "allowlist"}, caps=caps).network == "allowlist"
    assert SandboxOptions.from_dict({}, caps=caps).network == "none"
    strict = SandboxOptions(network="none", max_network="none")
    assert SandboxOptions.from_dict({"network": "allowlist"}, caps=strict).network == "none"
