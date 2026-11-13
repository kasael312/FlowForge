"""Part 4: templates, custom steps + variables, graph analysis, try-step, sharing."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.executor.conditions import ConditionError, StepResultView, evaluate
from app.executor.engine import ExecutorDeps, FlowExecutor
from app.executor.variables import render, used_variables
from app.models import Flow, FlowTemplate, Run, RunStatus
from app.schemas import Edge, Node, analyze_graph
from app.services.models import ModelConfigError
from app.services.runs import run_manager
from app.templates import all_builtin_templates, seed_builtin_templates
from tests.conftest import login, make_step
from tests.fakes import FakeAnthropic, text_response

# ---------------------------------------------------------------- conditions / variables


def test_condition_helpers() -> None:
    ok = StepResultView("t", "test", True, "12 passed, 0 failed\nRESULT: PASS", 0)
    bad = StepResultView("t", "test", False, "1 FAILED", 1)
    assert evaluate('contains("passed")', ok) is True
    assert evaluate('contains("passed")', bad) is False
    assert evaluate('icontains("failed")', ok) is True
    assert evaluate("exit_code == 0", ok) and not evaluate("exit_code == 0", bad)
    assert evaluate("success and not icontains('error')", ok)
    assert evaluate(r'matches("\d+ passed")', ok) and not evaluate(r'matches("\d+ passed")', bad)
    assert evaluate("prev.success", ok)  # old spelling still works
    assert evaluate("", bad) is False
    with pytest.raises(ConditionError):
        evaluate("contains(x=1)", ok)
    with pytest.raises(ConditionError):
        evaluate("__import__('os')", ok)
    with pytest.raises(ConditionError):
        evaluate('matches("(")', ok)


def test_variables() -> None:
    text = "Task: {{task}} on {{ repo }}@{{branch}}; prev={{prev_output}} unknown={{nope}}"
    out = render(
        text, {"task": "fix", "repo": "https://x/y", "branch": "main", "prev_output": "ok"}
    )
    assert out == "Task: fix on https://x/y@main; prev=ok unknown={{nope}}"
    assert used_variables(text) == {"task", "repo", "branch", "prev_output", "nope"}


async def test_custom_step_variables_reach_the_model(
    deps: ExecutorDeps, db_session: Session, fake_client: FakeAnthropic
) -> None:
    flow = Flow(
        name="custom",
        nodes=[
            make_step("a", "plan", system_prompt="first"),
            make_step(
                "b",
                "custom",
                system_prompt="Repo {{repo}} branch {{branch}}. Task: {{task}}. Before: {{prev_output}}. Step {{step_id}}.",
            ),
        ],
        edges=[{"id": "e", "source": "a", "target": "b"}],
    )
    db_session.add(flow)
    db_session.flush()
    run = Run(
        flow_id=flow.id,
        repo_url="https://github.com/acme/demo",
        branch="dev",
        task_input="add tests",
    )
    db_session.add(run)
    db_session.commit()
    fake_client.responses = [text_response("plan output"), text_response("done")]
    assert await FlowExecutor(deps, run.id).execute() is RunStatus.success
    req = fake_client.requests[1]
    assert req["system"].endswith(
        "Repo https://github.com/acme/demo branch dev. Task: add tests. Before: plan output. Step b."
    )
    # task/prev_output were injected via variables -> not repeated in the user message
    user = req["messages"][0]["content"]
    assert "## Task" not in user and "## Previous step" not in user


# ---------------------------------------------------------------- graph analysis


def _graph(nodes: list[dict], edges: list[dict]):
    return [Node.model_validate(n) for n in nodes], [Edge.model_validate(e) for e in edges]


def test_analyze_graph_errors_and_warnings(sample_flow: dict) -> None:
    rep = analyze_graph(*_graph(sample_flow["nodes"], sample_flow["edges"]))
    assert rep.errors == [] and rep.warnings == []  # guarded loop via condition is fine

    # unguarded cycle (a <-> b) with c as the only start, c unconnected
    rep = analyze_graph(
        *_graph(
            [make_step("a", "code"), make_step("b", "code"), make_step("c", "code")],
            [{"id": "1", "source": "a", "target": "b"}, {"id": "2", "source": "b", "target": "a"}],
        )
    )
    assert any("cycle without a condition" in e for e in rep.errors)
    assert not any("start node" in e for e in rep.errors)
    assert any("'c' is not connected" in w for w in rep.warnings)

    # two start nodes
    rep = analyze_graph(
        *_graph(
            [make_step("a", "code"), make_step("b", "code"), make_step("c", "code")],
            [{"id": "1", "source": "a", "target": "b"}, {"id": "2", "source": "c", "target": "b"}],
        )
    )
    assert any("exactly one start node" in e and "a, c" in e for e in rep.errors)

    # unreachable + lonely condition branch + bad expression + empty shell command
    rep = analyze_graph(
        *_graph(
            [
                make_step("s", "shell", command=""),
                make_step("c", "condition", expression="import os"),
                make_step("x", "code"),
                make_step("y", "code"),
            ],
            [
                {"id": "1", "source": "s", "target": "c"},
                {"id": "2", "source": "c", "target": "x", "sourceHandle": "true"},
            ],
        )
    )
    assert any("command is empty" in e for e in rep.errors)
    assert any("condition 'c': invalid expression" in e for e in rep.errors)
    assert any("only one branch" in w for w in rep.warnings)
    assert any("'y' is not connected" in w for w in rep.warnings)


def test_validate_endpoint_and_save_rejection(client: TestClient, sample_flow: dict) -> None:
    rep = client.post("/flows/validate", json=sample_flow).json()
    assert rep == {"errors": [], "warnings": []}
    bad = {
        **sample_flow,
        "edges": sample_flow["edges"] + [{"id": "loop", "source": "review", "target": "plan"}],
    }
    rep = client.post("/flows/validate", json=bad).json()
    assert rep["errors"] and "start node" in rep["errors"][0]
    assert client.post("/flows", json=bad).status_code == 422
    flow_id = client.post("/flows", json=sample_flow).json()["id"]
    assert client.put(f"/flows/{flow_id}", json={"edges": bad["edges"]}).status_code == 422
    # body validation errors still 422
    assert (
        client.post(
            "/flows/validate", json={"name": "x", "nodes": [make_step("a", "code", on_fail="boom")]}
        ).status_code
        == 422
    )


# ---------------------------------------------------------------- templates


def test_seed_is_idempotent(db_session: Session) -> None:
    assert seed_builtin_templates(db_session) == len(all_builtin_templates())
    assert seed_builtin_templates(db_session) == 0
    assert db_session.query(FlowTemplate).count() == len(all_builtin_templates())


def test_templates_api(anon_client: TestClient, db_session: Session) -> None:
    seed_builtin_templates(db_session)
    login(anon_client, "a@example.com")
    rows = anon_client.get("/templates").json()
    assert [t["key"] for t in rows][:3] == ["fix-issue", "add-feature", "refactor-review"]
    assert len(rows) == len(all_builtin_templates())
    assert all(t["builtin"] for t in rows) and rows[0]["node_count"] == 5

    used = anon_client.post(f"/templates/{rows[0]['id']}/use")
    assert used.status_code == 201
    flow = used.json()
    assert (
        flow["name"] == "Fix issue" and len(flow["nodes"]) == 5 and flow["visibility"] == "private"
    )

    # save my (edited) flow as a template
    anon_client.put(f"/flows/{flow['id']}", json={"name": "My fix"})
    saved = anon_client.post("/templates", json={"flow_id": flow["id"], "description": "mine"})
    assert saved.status_code == 201
    mine = saved.json()
    assert mine["name"] == "My fix" and mine["builtin"] is False and mine["description"] == "mine"
    assert [t["name"] for t in anon_client.get("/templates").json()][-1] == "My fix"

    assert anon_client.delete(f"/templates/{rows[0]['id']}").status_code == 403
    # another user doesn't see or touch my template
    login(anon_client, "b@example.com")
    assert all(t["builtin"] for t in anon_client.get("/templates").json())
    assert anon_client.post(f"/templates/{mine['id']}/use").status_code == 404
    assert anon_client.delete(f"/templates/{mine['id']}").status_code == 404
    login(anon_client, "a@example.com")
    assert anon_client.delete(f"/templates/{mine['id']}").status_code == 204


# ---------------------------------------------------------------- try a step


def test_try_step(
    client: TestClient, deps: ExecutorDeps, fake_client: FakeAnthropic, sample_flow: dict
) -> None:
    run_manager._deps = deps
    try:
        flow_id = client.post("/flows", json=sample_flow).json()["id"]
        fake_client.responses = [text_response("I would edit app.py")]
        r = client.post(
            f"/flows/{flow_id}/steps/code/try",
            json={"task": "fix bug", "prev_output": "plan says X", "repo": "https://r/x"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["success"] and body["output"] == "I would edit app.py" and body["tokens"] == 20
        assert "## Task\nfix bug" in body["user_message"] and "plan says X" in body["user_message"]
        req = fake_client.requests[0]
        assert "tools" not in req and "dry run" in req["system"]

        r = client.post(
            f"/flows/{flow_id}/steps/check/try", json={"prev_output": "ok", "prev_success": False}
        )
        assert r.json()["success"] is False and "-> False" in r.json()["output"]
        r = client.post(f"/flows/{flow_id}/steps/nope/try", json={})
        assert r.status_code == 404

        def no_model(user_id: str | None, model_id: str | None):
            raise ModelConfigError(
                "No Anthropic API key configured. Add one under Settings → API keys."
            )

        deps.provider_resolver = no_model
        r = client.post(f"/flows/{flow_id}/steps/code/try", json={})
        assert r.json()["success"] is False and "API key" in r.json()["error"]
    finally:
        run_manager._deps = None


# ---------------------------------------------------------------- sharing


def test_shared_link(anon_client: TestClient, sample_flow: dict) -> None:
    login(anon_client, "a@example.com")
    flow = anon_client.post("/flows", json=sample_flow).json()
    assert flow["visibility"] == "private" and flow["share_token"] is None

    shared = anon_client.put(f"/flows/{flow['id']}", json={"visibility": "shared_link"}).json()
    token = shared["share_token"]
    assert shared["visibility"] == "shared_link" and token and len(token) > 20

    # anonymous read works, no owner info leaks
    anon = TestClient(anon_client.app)
    anon.app = anon_client.app
    r = anon.get(f"/shared/{token}")
    assert r.status_code == 200
    assert r.json()["name"] == "Fix issue" and "user_id" not in r.json() and "id" not in r.json()
    assert anon.post(f"/shared/{token}/clone").status_code == 401
    assert anon.get("/shared/does-not-exist").status_code == 404

    # another user clones but cannot edit the original
    login(anon_client, "b@example.com")
    clone = anon_client.post(f"/shared/{token}/clone")
    assert clone.status_code == 201
    assert clone.json()["name"] == "Fix issue (clone)" and clone.json()["visibility"] == "private"
    assert clone.json()["id"] != flow["id"]
    assert anon_client.put(f"/flows/{flow['id']}", json={"name": "hack"}).status_code == 404

    # owner makes it private again -> link stops working, token kept for re-enable
    login(anon_client, "a@example.com")
    back = anon_client.put(f"/flows/{flow['id']}", json={"visibility": "private"}).json()
    assert back["share_token"] == token
    assert anon.get(f"/shared/{token}").status_code == 404
    again = anon_client.put(f"/flows/{flow['id']}", json={"visibility": "shared_link"}).json()
    assert again["share_token"] == token
