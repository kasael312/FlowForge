"""Rules: per-user built-ins, scope resolution, prompt order, resolved-prompt endpoint."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.executor.engine import ExecutorDeps, FlowExecutor
from app.models import Rule, RunStatus
from app.services import rules as svc
from tests.conftest import login, make_step
from tests.fakes import FakeAnthropic, text_response
from tests.test_nodes import _run, _steps


def test_builtin_rules_copied_per_user(client: TestClient) -> None:
    rows = client.get("/rules").json()
    assert [r["key"] for r in rows] == [
        "no-secrets",
        "always-write-tests",
        "follow-claude-md",
        "vietnamese-comments",
    ]
    assert [r["enabled"] for r in rows] == [True, True, True, False]
    assert all(r["scope"] == "global" for r in rows)
    assert client.get("/rules").json() == rows  # idempotent
    # another user gets their own copies
    login(client, "other@example.com")
    other = client.get("/rules").json()
    assert {r["id"] for r in other}.isdisjoint({r["id"] for r in rows})
    assert client.get(f"/rules/{rows[0]['id']}").status_code == 404


def test_rules_crud_and_scope_validation(client: TestClient, sample_flow: dict) -> None:
    flow = client.post("/flows", json=sample_flow).json()
    step_id = flow["nodes"][0]["id"]
    # global
    r = client.post(
        "/rules", json={"name": "Be terse", "content": "Answer in 3 lines.", "priority": 5}
    )
    assert r.status_code == 201 and r.json()["scope"] == "global"
    g = r.json()
    # flow scope needs an owned flow
    assert client.post("/rules", json={"name": "x", "scope": "flow"}).status_code == 422
    assert (
        client.post("/rules", json={"name": "x", "scope": "flow", "flow_id": "nope"}).status_code
        == 422
    )
    r = client.post(
        "/rules", json={"name": "Flow rule", "content": "F", "scope": "flow", "flow_id": flow["id"]}
    )
    assert r.status_code == 201
    f = r.json()
    # step scope needs a step of that flow
    assert (
        client.post(
            "/rules", json={"name": "x", "scope": "step", "flow_id": flow["id"], "step_id": "ghost"}
        ).status_code
        == 422
    )
    r = client.post(
        "/rules",
        json={
            "name": "Step rule",
            "content": "S",
            "scope": "step",
            "flow_id": flow["id"],
            "step_id": step_id,
        },
    )
    assert r.status_code == 201
    s = r.json()
    # filters
    assert [x["name"] for x in client.get("/rules", params={"scope": "flow"}).json()] == [
        "Flow rule"
    ]
    assert {x["name"] for x in client.get("/rules", params={"flow_id": flow["id"]}).json()} == {
        "Flow rule",
        "Step rule",
    }
    # update: disable, change priority, move scope
    r = client.put(f"/rules/{g['id']}", json={"enabled": False, "priority": 1})
    assert r.json()["enabled"] is False and r.json()["priority"] == 1
    r = client.put(f"/rules/{f['id']}", json={"scope": "global"})
    assert r.json()["scope"] == "global" and r.json()["flow_id"] is None
    assert client.put(f"/rules/{s['id']}", json={"step_id": "ghost"}).status_code == 422
    # delete
    assert client.delete(f"/rules/{s['id']}").status_code == 204
    assert client.get(f"/rules/{s['id']}").status_code == 404
    # deleting the flow removes its rules
    r = client.post("/rules", json={"name": "Tmp", "scope": "flow", "flow_id": flow["id"]}).json()
    client.delete(f"/flows/{flow['id']}")
    assert client.get(f"/rules/{r['id']}").status_code == 404


def test_resolve_rules_order(db_session: Session) -> None:
    uid = "u1"
    from app.models import User

    db_session.add(User(id=uid, email="u1@example.com", password_hash="x"))
    db_session.commit()
    svc.ensure_builtin_rules(db_session, uid)
    db_session.add_all(
        [
            Rule(
                user_id=uid,
                name="Step A",
                content="sa",
                scope="step",
                flow_id="f1",
                step_id="code",
                priority=1,
            ),
            Rule(user_id=uid, name="Flow A", content="fa", scope="flow", flow_id="f1", priority=1),
            Rule(
                user_id=uid, name="Flow other", content="fo", scope="flow", flow_id="f2", priority=1
            ),
            Rule(user_id=uid, name="Global late", content="gl", scope="global", priority=999),
            Rule(
                user_id=uid, name="Disabled", content="d", scope="global", priority=0, enabled=False
            ),
        ]
    )
    db_session.commit()
    names = [r.name for r in svc.resolve_rules(db_session, uid, "f1", "code")]
    assert names == [
        "No secrets",
        "Always write tests",
        "Follow CLAUDE.md",
        "Global late",
        "Flow A",
        "Step A",
    ]
    assert [r.name for r in svc.resolve_rules(db_session, uid, "f1", "plan")][-1] == "Flow A"
    assert "Flow A" not in [r.name for r in svc.resolve_rules(db_session, uid, "f2", "code")]
    assert svc.resolve_rules(db_session, None, "f1", "code") == []
    block = svc.rules_prompt_block(svc.resolve_rules(db_session, uid, "f1", "code"))
    assert block.startswith("# Rules") and block.index("## Rule: Flow A (flow)") < block.index(
        "## Rule: Step A (step)"
    )


def test_resolved_prompt_endpoint_order(client: TestClient, sample_flow: dict) -> None:
    persona = client.post(
        "/skills", json={"name": "writer", "kind": "persona", "content": "PERSONA-TEXT"}
    ).json()
    skill = client.post("/skills", json={"name": "fmt", "content": "SKILL-TEXT"}).json()
    sample_flow["nodes"][0]["data"]["skill_ids"] = [skill["id"], persona["id"]]
    sample_flow["nodes"][0]["data"]["system_prompt"] = "STEP-TEXT"
    flow = client.post("/flows", json=sample_flow).json()
    step_id = flow["nodes"][0]["id"]
    client.post(
        "/rules",
        json={"name": "Flow rule", "content": "FLOW-RULE", "scope": "flow", "flow_id": flow["id"]},
    )
    client.post(
        "/rules",
        json={
            "name": "Step rule",
            "content": "STEP-RULE",
            "scope": "step",
            "flow_id": flow["id"],
            "step_id": step_id,
        },
    )
    r = client.get(f"/flows/{flow['id']}/steps/{step_id}/resolved-prompt")
    assert r.status_code == 200, r.text
    body = r.json()
    sp = body["system_prompt"]
    order = [
        "You are an autonomous",
        "No secrets",
        "FLOW-RULE",
        "STEP-RULE",
        "PERSONA-TEXT",
        "SKILL-TEXT",
        "STEP-TEXT",
    ]
    positions = [sp.index(x) for x in order]
    assert positions == sorted(positions), sp
    assert [s["kind"] for s in body["sections"]] == [
        "base",
        "rule",
        "rule",
        "rule",
        "rule",
        "rule",
        "persona",
        "skill",
        "step",
    ]
    assert client.get(f"/flows/{flow['id']}/steps/ghost/resolved-prompt").status_code == 404
    # try step uses the same composition
    t = client.post(f"/flows/{flow['id']}/steps/{step_id}/try", json={"task": "t"}).json()
    assert t["system_prompt"].index("STEP-RULE") < t["system_prompt"].index("PERSONA-TEXT")


async def test_executor_applies_rules(
    deps: ExecutorDeps, db_session: Session, fake_client: FakeAnthropic
) -> None:
    deps.rule_loader = lambda uid, fid, sid: f"# Rules\n## Rule: scoped\nRULE-FOR-{sid}"
    fake_client.responses = [text_response("ok")]
    run_id = _run(db_session, [make_step("a", "custom", system_prompt="STEP")], [])
    assert await FlowExecutor(deps, run_id).execute() is RunStatus.success
    sys_a = fake_client.requests[0]["system"]
    assert "RULE-FOR-a" in sys_a and sys_a.index("RULE-FOR-a") < sys_a.index("STEP")
    assert [s.status.value for s in _steps(deps, run_id)] == ["success"]
