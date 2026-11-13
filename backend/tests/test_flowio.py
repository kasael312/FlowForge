"""Flow export/import with models, skills and rules; templates with skills/rules; step skills; cost stats."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.executor.engine import ExecutorDeps, FlowExecutor
from app.models import RunStatus, StepRun
from app.services import skills as skillsvc
from app.skills_builtin import seed_builtin_skills
from app.templates import seed_builtin_templates
from tests.conftest import login, make_step
from tests.fakes import FakeAnthropic, text_response
from tests.test_nodes import _edge, _run


def _model(client: TestClient, label: str, default: bool = False) -> dict:
    r = client.post(
        "/models",
        json={
            "provider": "ollama",
            "model_name": "llama3",
            "label": label,
            "base_url": "http://localhost:11434",
            "params": {},
            "is_default": default,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_export_import_round_trip_other_account(
    client: TestClient, db_session: Session, sample_flow: dict
) -> None:
    seed_builtin_skills(db_session)
    fast = _model(client, "Fast")
    slow = _model(client, "Slow", default=True)
    own = client.post(
        "/skills", json={"name": "house-style", "description": "ours", "content": "# House\nShort."}
    ).json()
    builtin = next(s for s in client.get("/skills").json() if s["name"] == "python-testing")
    n0 = sample_flow["nodes"][0]
    n0["data"].update(
        {
            "model_id": fast["id"],
            "fallback_model_id": slow["id"],
            "skill_ids": [own["id"], builtin["id"]],
        }
    )
    flow = client.post("/flows", json=sample_flow).json()
    client.post(
        "/rules", json={"name": "Flow rule", "content": "F", "scope": "flow", "flow_id": flow["id"]}
    )
    client.post(
        "/rules",
        json={
            "name": "Step rule",
            "content": "S",
            "scope": "step",
            "flow_id": flow["id"],
            "step_id": n0["id"],
        },
    )

    doc = client.get(f"/flows/{flow['id']}/export").json()
    assert doc["flowforge"] == 2 and doc["name"] == sample_flow["name"]
    assert sorted(m["label"] for m in doc["models"]) == ["Fast", "Slow"]
    assert all("api_key" not in m for m in doc["models"])
    assert {s["name"]: s["builtin"] for s in doc["skills"]} == {
        "house-style": False,
        "python-testing": True,
    }
    assert [r["name"] for r in doc["rules"]] == ["Flow rule", "Step rule"]

    # same account: ids resolve to the same models/skills, rules re-created
    r = client.post("/flows/import", json=doc)
    assert r.status_code == 201, r.text
    assert r.json()["warnings"] == []
    imported = r.json()["flow"]
    assert imported["id"] != flow["id"]
    assert imported["nodes"][0]["data"]["model_id"] == fast["id"]
    assert imported["nodes"][0]["data"]["skill_ids"] == [own["id"], builtin["id"]]
    assert {x["name"] for x in client.get("/rules", params={"flow_id": imported["id"]}).json()} == {
        "Flow rule",
        "Step rule",
    }

    # another account: only "Slow" exists (as default); house-style is created from the document
    login(client, "other@example.com")
    other_default = _model(client, "Slow", default=True)
    r = client.post("/flows/import", json=doc)
    assert r.status_code == 201, r.text
    body = r.json()
    assert any("model 'Fast' is not configured" in w for w in body["warnings"])
    assert any("created skill 'house-style'" in w for w in body["warnings"])
    data = body["flow"]["nodes"][0]["data"]
    assert (
        data["model_id"] == other_default["id"] and data["fallback_model_id"] == other_default["id"]
    )
    skills = client.get("/skills").json()
    mine = next(s for s in skills if s["name"] == "house-style")
    assert not mine["builtin"] and mine["content"] == "# House\nShort."
    pt = next(s for s in skills if s["name"] == "python-testing")
    assert data["skill_ids"] == [mine["id"], pt["id"]]

    # import into an existing flow replaces graph and rules
    target = client.post("/flows", json={"name": "target", "nodes": [], "edges": []}).json()
    client.post("/rules", json={"name": "Old", "scope": "flow", "flow_id": target["id"]})
    r = client.post(f"/flows/{target['id']}/import", json=doc)
    assert r.status_code == 200, r.text
    assert r.json()["flow"]["id"] == target["id"] and len(r.json()["flow"]["nodes"]) == len(
        sample_flow["nodes"]
    )
    assert {x["name"] for x in client.get("/rules", params={"flow_id": target["id"]}).json()} == {
        "Flow rule",
        "Step rule",
    }
    # malformed
    assert client.post("/flows/import", json={"nodes": "x"}).status_code == 422
    assert (
        client.post(
            "/flows/import", json={"nodes": [], "edges": [{"source": "a", "target": "b"}]}
        ).status_code
        == 422
    )


def test_duplicate_copies_rules(client: TestClient, sample_flow: dict) -> None:
    flow = client.post("/flows", json=sample_flow).json()
    client.post("/rules", json={"name": "Flow rule", "scope": "flow", "flow_id": flow["id"]})
    clone = client.post(f"/flows/{flow['id']}/duplicate").json()
    assert [x["name"] for x in client.get("/rules", params={"flow_id": clone["id"]}).json()] == [
        "Flow rule"
    ]


def test_templates_carry_skills_and_rules(
    client: TestClient, db_session: Session, sample_flow: dict
) -> None:
    seed_builtin_skills(db_session)
    seed_builtin_templates(db_session)
    rows = client.get("/templates").json()
    rr = next(t for t in rows if t["key"] == "research-report")
    assert (
        rr["skills"]["synth"] == ["researcher", "citation-format"]
        and rr["rules"][0]["name"] == "Cite or drop"
    )
    flow = client.post(f"/templates/{rr['id']}/use").json()
    skills = {s["name"]: s["id"] for s in client.get("/skills").json()}
    synth = next(n for n in flow["nodes"] if n["id"] == "synth")
    assert synth["data"]["skill_ids"] == [skills["researcher"], skills["citation-format"]]
    assert [x["name"] for x in client.get("/rules", params={"flow_id": flow["id"]}).json()] == [
        "Cite or drop"
    ]
    inv = next(t for t in rows if t["key"] == "invoice-extractor")
    f2 = client.post(f"/templates/{inv['id']}/use").json()
    step_rule = client.get("/rules", params={"flow_id": f2["id"]}).json()[0]
    assert step_rule["scope"] == "step" and step_rule["step_id"] == "extract"

    # save-as-template captures names + rules, use resolves them back
    own = client.post("/skills", json={"name": "mine", "content": "x"}).json()
    sample_flow["nodes"][0]["data"]["skill_ids"] = [own["id"], skills["copywriter"]]
    src = client.post("/flows", json=sample_flow).json()
    client.post("/rules", json={"name": "Keep it short", "scope": "flow", "flow_id": src["id"]})
    t = client.post("/templates", json={"flow_id": src["id"], "name": "with skills"}).json()
    assert (
        t["skills"] == {sample_flow["nodes"][0]["id"]: ["mine", "copywriter"]}
        and t["rules"][0]["name"] == "Keep it short"
    )
    used = client.post(f"/templates/{t['id']}/use").json()
    assert used["nodes"][0]["data"]["skill_ids"] == [own["id"], skills["copywriter"]]
    assert [x["name"] for x in client.get("/rules", params={"flow_id": used["id"]}).json()] == [
        "Keep it short"
    ]


async def test_step_run_records_skills(
    deps: ExecutorDeps, db_session: Session, fake_client: FakeAnthropic
) -> None:
    from app.models import Skill

    p = Skill(
        user_id=None,
        name="writer",
        kind="persona",
        description="",
        content="warm",
        tools_required=[],
        files=[],
    )
    k = Skill(
        user_id=None,
        name="fmt",
        kind="skill",
        description="",
        content="bullets",
        tools_required=[],
        files=[],
    )
    db_session.add_all([p, k])
    db_session.commit()
    deps.skill_loader = lambda uid, ids: skillsvc.load_bundles(db_session, uid, ids)
    fake_client.responses = [text_response("ok")]
    run_id = _run(
        db_session, [make_step("a", "custom", system_prompt="S", skill_ids=[k.id, p.id])], []
    )
    assert await FlowExecutor(deps, run_id).execute() is RunStatus.success
    with deps.session_factory() as db:
        sr = db.query(StepRun).filter_by(run_id=run_id).one()
        assert sr.skills == [
            {"name": "fmt", "kind": "skill"},
            {"name": "writer", "kind": "persona"},
        ]


def test_stats_cost_by_model(client: TestClient, db_session: Session, sample_flow: dict) -> None:
    from app.models import Run

    flow = client.post("/flows", json=sample_flow).json()
    uid = client.get("/auth/me").json()["id"]
    run = Run(
        flow_id=flow["id"], user_id=uid, repo_url="", task_input="t", status=RunStatus.success
    )
    db_session.add(run)
    db_session.flush()
    db_session.add_all(
        [
            StepRun(
                run_id=run.id,
                step_id="a",
                step_type="custom",
                model="anthropic/claude-x",
                input_tokens=100,
                output_tokens=50,
                cost_usd=0.01,
            ),
            StepRun(
                run_id=run.id,
                step_id="b",
                step_type="custom",
                model="anthropic/claude-x",
                input_tokens=10,
                output_tokens=5,
                cost_usd=0.002,
            ),
            StepRun(
                run_id=run.id,
                step_id="c",
                step_type="custom",
                model="openai/gpt-x",
                input_tokens=1,
                output_tokens=1,
                cost_usd=0.05,
            ),
            StepRun(run_id=run.id, step_id="d", step_type="shell"),
        ]
    )
    db_session.commit()
    stats = client.get("/stats").json()
    assert [(c["model"], c["steps"], c["input_tokens"]) for c in stats["cost_by_model"]] == [
        ("openai/gpt-x", 1, 1),
        ("anthropic/claude-x", 2, 110),
    ]
    assert abs(stats["cost_by_model"][1]["cost_usd"] - 0.012) < 1e-9
    assert abs(stats["total_cost_usd"] - 0.062) < 1e-9
    _ = _edge  # keep helper import used
