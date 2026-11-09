"""Tests for the WebSocket log stream and the DB replay fallback."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.executor.engine import ExecutorDeps
from app.executor.sandbox import ExecResult
from app.routers.logs import WS_NOT_FOUND
from app.services.runs import run_manager
from tests.fakes import FakeAnthropic, FakeSandbox, text_response, tool_response


@pytest.fixture(autouse=True)
def _wire_manager(deps: ExecutorDeps) -> Generator[None, None, None]:
    # The logs router uses the process-wide bus; point the executor at it too.
    from app.executor.events import bus

    bus.reset()
    deps.bus = bus
    run_manager._deps = deps
    run_manager.history_ttl = 3600
    yield
    run_manager._deps = None
    bus.reset()


def _collect(client: TestClient, run_id: str) -> list[dict]:
    events: list[dict] = []
    with client.websocket_connect(f"/runs/{run_id}/logs") as ws:
        while True:
            ev = ws.receive_json()
            events.append(ev)
            if ev["event"] == "end":
                return events


def test_live_stream(
    client: TestClient, sample_flow: dict, fake_client: FakeAnthropic, fake_sandbox: FakeSandbox
) -> None:
    release = asyncio.Event()

    async def gated_create(**kwargs):
        await release.wait()
        return fake_client.responses.pop(0)

    fake_client.responses = [
        text_response("plan"),
        tool_response([("run_shell", {"command": "pytest -q"})], text="running tests"),
        text_response("code done"),
        text_response("PASS"),
        text_response("APPROVE"),
    ]
    fake_sandbox.scripted["pytest -q"] = ExecResult(0, "1 passed", "")
    fake_client.beta.messages.create = gated_create  # type: ignore[method-assign]

    flow_id = client.post("/flows", json=sample_flow).json()["id"]
    run_id = client.post(
        f"/flows/{flow_id}/run", json={"repo_url": "https://github.com/a/b"}
    ).json()["id"]

    with client.websocket_connect(f"/runs/{run_id}/logs") as ws:
        first = ws.receive_json()
        assert first["event"] == "run_status" and first["status"] == "running"
        # the executor is blocked on the model; nothing else should have arrived yet
        # except the clone log and the first step_start
        ev = ws.receive_json()
        assert ev["event"] == "log" and "cloned" in ev["message"]
        ev = ws.receive_json()
        assert ev["event"] == "step_start" and ev["step_id"] == "plan"

        client.portal.call(release.set)  # type: ignore[attr-defined]
        events = [first, ev]
        while True:
            ev = ws.receive_json()
            events.append(ev)
            if ev["event"] == "end":
                break

    kinds = [e["event"] for e in events]
    assert kinds[-2:] == ["run_status", "end"]
    assert events[-2]["status"] == "success"
    assert kinds.count("step_start") == 5 and kinds.count("step_end") == 5
    tool_calls = [e for e in events if e["event"] == "tool_call"]
    assert tool_calls[0]["tool"] == "run_shell" and tool_calls[0]["step_id"] == "code"
    tool_results = [e for e in events if e["event"] == "tool_result"]
    assert tool_results[0]["output"].startswith("exit_code: 0")
    texts = [e["text"] for e in events if e["event"] == "text"]
    assert "running tests" in texts and "APPROVE" in texts
    assert all("ts" in e for e in events[:-1])


def test_late_subscriber_gets_history_then_end(
    client: TestClient, sample_flow: dict, fake_client: FakeAnthropic
) -> None:
    fake_client.responses = [text_response(t) for t in ("plan", "code", "PASS", "APPROVE")]
    flow_id = client.post("/flows", json=sample_flow).json()["id"]
    run_id = client.post(
        f"/flows/{flow_id}/run", json={"repo_url": "https://github.com/a/b"}
    ).json()["id"]
    deadline = time.monotonic() + 5
    while client.get(f"/runs/{run_id}").json()["status"] in ("queued", "running"):
        assert time.monotonic() < deadline
        time.sleep(0.02)

    events = _collect(client, run_id)
    assert events[0]["status"] == "running"
    assert events[-2]["status"] == "success" and events[-1] == {"event": "end"}
    assert not any(e.get("replayed") for e in events)


def test_replay_from_db_after_history_evicted(
    client: TestClient, sample_flow: dict, fake_client: FakeAnthropic, fake_sandbox: FakeSandbox
) -> None:
    from app.executor.events import bus

    fake_client.responses = [
        text_response("plan"),
        tool_response([("write_file", {"path": "a.py", "content": "x"})], text="writing"),
        text_response("code done"),
        text_response("FAILED", stop_reason="refusal"),  # test fails -> goto code
        text_response("code v2"),
        text_response("PASS"),
        text_response("APPROVE"),
    ]
    flow_id = client.post("/flows", json=sample_flow).json()["id"]
    run_id = client.post(
        f"/flows/{flow_id}/run", json={"repo_url": "https://github.com/a/b"}
    ).json()["id"]
    live = _collect(client, run_id)
    bus.forget(run_id)  # simulate restart / eviction

    replayed = _collect(client, run_id)
    assert all(e.get("replayed") for e in replayed[:-1])
    assert [e["event"] for e in replayed] == [e["event"] for e in live if e["event"] != "log"]
    assert [e.get("step_id") for e in replayed if e["event"] == "step_start"] == [
        "plan",
        "code",
        "test",
        "code",
        "test",
        "check",
        "review",
    ]
    tc = next(e for e in replayed if e["event"] == "tool_call")
    assert tc["tool"] == "write_file" and tc["input"]["path"] == "a.py"
    tr = next(e for e in replayed if e["event"] == "tool_result")
    assert "a.py" in tr["output"] and tr["is_error"] is False
    assert replayed[-2]["status"] == "success"


def test_unknown_run_closes_with_4404(client: TestClient) -> None:
    with (
        client.websocket_connect("/runs/nope/logs") as ws,
        pytest.raises(WebSocketDisconnect) as exc,
    ):
        ws.receive_json()
    assert exc.value.code == WS_NOT_FOUND


def test_client_disconnect_does_not_break_run(
    client: TestClient, sample_flow: dict, fake_client: FakeAnthropic
) -> None:
    release = asyncio.Event()

    async def gated_create(**kwargs):
        await release.wait()
        return fake_client.responses.pop(0)

    fake_client.responses = [text_response(t) for t in ("plan", "code", "PASS", "APPROVE")]
    fake_client.beta.messages.create = gated_create  # type: ignore[method-assign]
    flow_id = client.post("/flows", json=sample_flow).json()["id"]
    run_id = client.post(
        f"/flows/{flow_id}/run", json={"repo_url": "https://github.com/a/b"}
    ).json()["id"]
    with client.websocket_connect(f"/runs/{run_id}/logs") as ws:
        assert ws.receive_json()["event"] == "run_status"
    # disconnected while the run is blocked; now let it finish
    client.portal.call(release.set)  # type: ignore[attr-defined]
    deadline = time.monotonic() + 5
    while client.get(f"/runs/{run_id}").json()["status"] in ("queued", "running"):
        assert time.monotonic() < deadline
        time.sleep(0.02)
    assert client.get(f"/runs/{run_id}").json()["status"] == "success"
    assert _collect(client, run_id)[-2]["status"] == "success"
