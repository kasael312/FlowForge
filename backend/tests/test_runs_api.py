"""Tests for the /runs endpoints, with the executor wired to fakes."""

from __future__ import annotations

import time
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

from app.executor.engine import ExecutorDeps
from app.services.runs import run_manager
from tests.fakes import FakeAnthropic, text_response


@pytest.fixture(autouse=True)
def _wire_manager(deps: ExecutorDeps) -> Generator[None, None, None]:
    run_manager._deps = deps
    yield
    run_manager._deps = None


def _wait_final(client: TestClient, run_id: str, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = client.get(f"/runs/{run_id}").json()
        if body["status"] not in ("queued", "running"):
            return body
        time.sleep(0.02)
    raise AssertionError("run did not finish")


def test_start_and_inspect_run(
    client: TestClient, sample_flow: dict, fake_client: FakeAnthropic
) -> None:
    fake_client.responses = [text_response(t) for t in ("plan", "code", "PASS", "APPROVE")]
    flow_id = client.post("/flows", json=sample_flow).json()["id"]

    resp = client.post(
        f"/flows/{flow_id}/run",
        json={"repo_url": "https://github.com/acme/demo", "branch": "main", "task": "fix"},
    )
    assert resp.status_code == 202, resp.text
    run = resp.json()
    assert run["status"] in ("queued", "running")
    assert run["flow_id"] == flow_id and run["task_input"] == "fix"

    final = _wait_final(client, run["id"])
    assert final["status"] == "success"
    assert final["final_diff"].startswith("diff --git")
    assert final["cost_tokens"] == 80

    steps = client.get(f"/runs/{run['id']}/steps").json()
    assert [s["step_id"] for s in steps] == ["plan", "code", "test", "check", "review"]
    assert steps[0]["messages"][1]["content"][0]["text"] == "plan"

    rows = client.get("/runs").json()
    assert [r["id"] for r in rows] == [run["id"]]
    assert "final_diff" not in rows[0]
    assert client.get("/runs", params={"status": "failed"}).json() == []
    assert (
        client.get("/runs", params={"flow_id": flow_id, "status": "success"}).json()[0]["id"]
        == run["id"]
    )
    assert client.get("/runs", params={"flow_id": "other"}).json() == []


def test_start_run_validation(client: TestClient, sample_flow: dict) -> None:
    assert client.post("/flows/nope/run", json={"repo_url": "https://x/y"}).status_code == 404
    empty = client.post("/flows", json={"name": "empty"}).json()["id"]
    assert client.post(f"/flows/{empty}/run", json={"repo_url": "https://x/y"}).status_code == 422
    flow_id = client.post("/flows", json=sample_flow).json()["id"]
    bad = client.post(
        f"/flows/{flow_id}/run", json={"repo_url": "--upload-pack=evil", "branch": "main"}
    )
    assert bad.status_code == 422 and "repo_url" in bad.text
    bad = client.post(
        f"/flows/{flow_id}/run", json={"repo_url": "https://github.com/a/b", "branch": "-x"}
    )
    assert bad.status_code == 422
    assert client.get("/runs").json() == []


def test_cancel_run(client: TestClient, sample_flow: dict, fake_client: FakeAnthropic) -> None:
    import asyncio

    async def _hang(**kwargs):
        await asyncio.sleep(30)
        return text_response("never")

    fake_client.beta.messages.create = _hang  # type: ignore[method-assign]
    flow_id = client.post("/flows", json=sample_flow).json()["id"]
    run_id = client.post(
        f"/flows/{flow_id}/run", json={"repo_url": "https://github.com/a/b"}
    ).json()["id"]
    time.sleep(0.1)
    resp = client.post(f"/runs/{run_id}/cancel")
    assert resp.status_code == 200
    final = _wait_final(client, run_id)
    assert final["status"] == "cancelled"
    assert client.post(f"/runs/{run_id}/cancel").status_code == 409
    assert client.post("/runs/missing/cancel").status_code == 404
