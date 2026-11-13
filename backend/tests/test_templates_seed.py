"""Approval node + seeded general-purpose templates."""

from __future__ import annotations

import asyncio
import json
import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.executor.engine import ExecutorDeps, FlowExecutor
from app.executor.sandbox import ExecResult
from app.models import Run, RunStatus, StepRunStatus
from app.schemas import FlowCreate, analyze_graph
from app.templates import all_builtin_templates, seed_builtin_templates
from app.tools import registry
from tests.conftest import make_step
from tests.fakes import FakeSandbox
from tests.test_nodes import _edge, _run, _steps

# ---------------------------------------------------------------- approval node


async def _start(deps: ExecutorDeps, run_id: str) -> tuple[FlowExecutor, asyncio.Task[RunStatus]]:
    ex = FlowExecutor(deps, run_id)
    task = asyncio.create_task(ex.execute())
    for _ in range(200):
        await asyncio.sleep(0.01)
        if ex.pending_approval:
            break
    assert ex.pending_approval, "executor never reached the approval step"
    return ex, task


def _approval_flow(**extra: object) -> tuple[list[dict], list[dict]]:
    nodes = [
        make_step("d", "data_transform", language="python", code="emit({'draft': 'hi'})"),
        make_step("gate", "approval", system_prompt="Send {{steps.d.data.draft}}?", **extra),
        make_step("after", "data_transform", language="python", code="emit('sent')"),
    ]
    return nodes, [_edge("d", "gate"), _edge("gate", "after")]


async def test_approval_approve_continues(
    deps: ExecutorDeps, db_session: Session, fake_sandbox: FakeSandbox
) -> None:
    fake_sandbox.scripted["python3"] = ExecResult(0, json.dumps({"draft": "hi"}), "")
    nodes, edges = _approval_flow()
    run_id = _run(db_session, nodes, edges)
    ex, task = await _start(deps, run_id)
    with deps.session_factory() as db:
        run = db.get(Run, run_id)
        assert run is not None and run.status is RunStatus.waiting
        gate = next(s for s in _steps(deps, run_id) if s.step_id == "gate")
        assert gate.status is StepRunStatus.waiting
    assert ex.resolve_approval("approve", "looks good")
    assert not ex.resolve_approval("approve")  # already resolved
    assert await task is RunStatus.success
    steps = _steps(deps, run_id)
    gate = next(s for s in steps if s.step_id == "gate")
    assert gate.status is StepRunStatus.success and gate.output == "looks good"
    assert [s.step_id for s in steps] == ["d", "gate", "after"]
    events = [e["event"] for e in deps.bus.history(run_id)]
    assert "approval_requested" in events and "approval_resolved" in events
    req = next(e for e in deps.bus.history(run_id) if e["event"] == "approval_requested")
    assert req["question"] == "Send hi?"


async def test_approval_reject_fails_run(
    deps: ExecutorDeps, db_session: Session, fake_sandbox: FakeSandbox
) -> None:
    fake_sandbox.scripted["python3"] = ExecResult(0, json.dumps({"draft": "hi"}), "")
    nodes, edges = _approval_flow()
    run_id = _run(db_session, nodes, edges)
    ex, task = await _start(deps, run_id)
    assert ex.resolve_approval("reject", "not now")
    assert await task is RunStatus.failed
    steps = _steps(deps, run_id)
    assert [s.step_id for s in steps] == ["d", "gate"]
    assert "rejected by reviewer" in steps[-1].output


async def test_approval_timeout(
    deps: ExecutorDeps,
    db_session: Session,
    fake_sandbox: FakeSandbox,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.executor.engine as engine_mod

    fake_sandbox.scripted["python3"] = ExecResult(0, json.dumps({"draft": "hi"}), "")

    orig = engine_mod.asyncio.wait_for

    async def quick(fut, timeout):
        return await orig(fut, timeout=0.05 if timeout == 30 else timeout)  # approval wait only

    monkeypatch.setattr(engine_mod.asyncio, "wait_for", quick)
    nodes, edges = _approval_flow(approval_timeout_seconds=30)
    run_id = _run(db_session, nodes, edges)
    _, task = await _start(deps, run_id)
    assert await task is RunStatus.failed
    gate = next(s for s in _steps(deps, run_id) if s.step_id == "gate")
    assert "no decision within 30s" in gate.output


def test_approve_endpoint_requires_waiting_run(client: TestClient, sample_flow: dict) -> None:
    flow = client.post("/flows", json=sample_flow).json()
    run = client.post(f"/flows/{flow['id']}/run", json={}).json()
    r = client.post(f"/runs/{run['id']}/approve", json={"decision": "approve"})
    assert r.status_code == 409
    r = client.post(f"/runs/{run['id']}/approve", json={"decision": "maybe"})
    assert r.status_code == 422


# ---------------------------------------------------------------- seeded templates

EXPECTED_KEYS = {
    "fix-issue",
    "add-feature",
    "refactor-review",
    "research-report",
    "blog-pipeline",
    "inbox-triage",
    "invoice-extractor",
    "weekly-data-report",
    "meeting-notes",
}


@pytest.mark.parametrize("tpl", all_builtin_templates(), ids=lambda t: t["key"])
def test_builtin_template_is_valid(tpl: dict) -> None:
    body = FlowCreate(
        name=tpl["name"],
        description=tpl["description"],
        nodes=tpl["nodes"],
        edges=tpl["edges"],
        input_schema=tpl.get("input_schema", []),
        sandbox=tpl.get("sandbox"),
    )
    report = analyze_graph(body.nodes, body.edges)
    assert report.errors == [] and report.warnings == []
    known = set(registry.names())
    for n in body.nodes:
        assert set(n.data.tools_allowed) <= known, (tpl["key"], n.id)
        if n.data.deliver_tool:
            assert n.data.deliver_tool in known
    # inputs referenced in prompts are declared
    declared = {f.name for f in body.input_schema} | {"email", "payload", "file"}
    for n in body.nodes:
        blob = " ".join(str(v) for v in n.data.model_dump().values())
        for ref in re.findall(r"\{\{\s*input\.([a-zA-Z_][a-zA-Z0-9_]*)", blob):
            assert ref in declared, (tpl["key"], n.id, ref)


def test_templates_seeded_and_usable(client: TestClient, db_session: Session) -> None:
    assert seed_builtin_templates(db_session) == len(all_builtin_templates())
    rows = client.get("/templates").json()
    assert EXPECTED_KEYS <= {t["key"] for t in rows if t["builtin"]}
    inbox = next(t for t in rows if t["key"] == "inbox-triage")
    assert any(n["data"]["type"] == "approval" for n in inbox["nodes"])
    assert [f["name"] for f in inbox["input_schema"]] == [
        "email_from",
        "email_subject",
        "email_body",
    ]
    assert inbox["sandbox"]["network"] == "none"

    flow = client.post(f"/templates/{inbox['id']}/use").json()
    assert flow["input_schema"] == inbox["input_schema"]
    assert flow["sandbox"]["network"] == "none"
    report = client.post(
        "/flows/validate", json={"nodes": flow["nodes"], "edges": flow["edges"]}
    ).json()
    assert report["errors"] == [] and report["warnings"] == []

    # save-as-template keeps the schema/sandbox too
    saved = client.post("/templates", json={"flow_id": flow["id"], "name": "my inbox"}).json()
    assert saved["input_schema"] == inbox["input_schema"]
    assert saved["sandbox"]["network"] == "none"


def test_seed_is_idempotent(db_session: Session) -> None:
    assert seed_builtin_templates(db_session) == len(all_builtin_templates())
    assert seed_builtin_templates(db_session) == 0
