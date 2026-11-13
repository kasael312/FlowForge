"""New node types: data_transform, http_request, loop, merge, template, output; variables."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.executor.engine import ExecutorDeps, FlowExecutor
from app.executor.nodes import NodeEnv, render_jinja, resolve_items, run_merge, run_template
from app.executor.sandbox import ExecResult
from app.models import Flow, Run, RunStatus, StepRun
from app.schemas import Edge, Node, analyze_graph
from app.tools import ToolContext
from tests.conftest import make_step
from tests.fakes import FakeAnthropic, FakeSandbox, text_response


def _run(
    db: Session,
    nodes: list[dict],
    edges: list[dict],
    inputs: dict[str, Any] | None = None,
    task: str = "t",
) -> str:
    flow = Flow(name="f", nodes=nodes, edges=edges)
    db.add(flow)
    db.flush()
    run = Run(flow_id=flow.id, repo_url="", task_input=task, inputs=inputs or {})
    db.add(run)
    db.commit()
    return run.id


def _steps(deps: ExecutorDeps, run_id: str) -> list[StepRun]:
    with deps.session_factory() as db:
        return (
            db.query(StepRun)
            .filter_by(run_id=run_id)
            .order_by(StepRun.started_at, StepRun.id)
            .all()
        )


def _edge(a: str, b: str, handle: str | None = None) -> dict:
    return {
        "id": f"{a}->{b}{':' + handle if handle else ''}",
        "source": a,
        "target": b,
        "sourceHandle": handle,
    }


# ---------------------------------------------------------------- helpers / pure functions


def test_resolve_items_forms() -> None:
    ctx = {
        "input": {"names": ["a", "b"], "csv": "x, y ,z", "map": {"k": 1}},
        "steps": {"s": {"data": {"rows": [1, 2, 3]}}},
    }
    assert resolve_items("input.names", ctx) == ["a", "b"]
    assert resolve_items("{{ steps.s.data.rows }}", ctx) == [1, 2, 3]
    assert resolve_items("input.csv", ctx) == ["x", "y", "z"]
    assert resolve_items("input.map", ctx) == [{"key": "k", "value": 1}]
    assert resolve_items('["p", "q"]', ctx) == ["p", "q"]
    assert resolve_items("one, two", ctx) == ["one", "two"]
    with pytest.raises(ValueError):
        resolve_items("", ctx)
    assert resolve_items("steps.s.data", ctx) == [{"key": "rows", "value": [1, 2, 3]}]
    with pytest.raises(TypeError):
        resolve_items("steps.s.data.rows.0", ctx)


def test_jinja_template_and_merge() -> None:
    ctx = {
        "task": "T",
        "input": {"topic": "AI"},
        "steps": {
            "a": {"output": "alpha", "status": "success", "data": None},
            "b": {"output": '{"n": 2}', "status": "success", "data": {"n": 2}},
        },
        "items": {},
    }
    out = render_jinja(
        "# {{ input.topic }}\n{% for sid, s in steps.items() %}- {{ sid }}: {{ s.output }}\n{% endfor %}",
        ctx,
    )
    assert out == '# AI\n- a: alpha\n- b: {"n": 2}\n'
    with pytest.raises(Exception):  # noqa: B017 - sandbox blocks attribute escapes
        render_jinja("{{ ''.__class__.__mro__ }}", ctx)
    node = Node.model_validate(make_step("m", "merge", merge_steps=["a", "b"], merge_mode="json"))
    r = run_merge(node, ctx)
    assert r.success and r.data == {"a": "alpha", "b": {"n": 2}}
    node = Node.model_validate(make_step("m", "merge"))
    r = run_merge(node, ctx)
    assert r.output == '## a\nalpha\n\n## b\n{"n": 2}'
    node = Node.model_validate(make_step("m", "merge", merge_steps=["zzz"]))
    assert not run_merge(node, ctx).success


async def test_template_node_writes_file() -> None:
    sb = FakeSandbox()
    env = NodeEnv(sandbox=sb, tool_ctx=ToolContext(), shell_whitelist=frozenset(), mcp_tools={})
    node = Node.model_validate(
        make_step("t", "template", template="Hello {{ input.name }}", output_path="output/hi.md")
    )
    r = await run_template(node, {"input": {"name": "Bob"}, "steps": {}, "items": {}}, env)
    assert r.success and r.output == "Hello Bob" and r.data == {"path": "output/hi.md", "chars": 9}
    assert sb.binaries["output/hi.md"] == b"Hello Bob"
    node = Node.model_validate(make_step("t", "template", template="{% if %}"))
    r = await run_template(node, {}, env)
    assert not r.success and "template error" in (r.error or "")


def test_graph_analysis_for_new_types() -> None:
    nodes = [
        make_step("fetch", "http_request", url="https://api.example/items"),
        make_step("each", "loop", items_expression="steps.fetch.data.body"),
        make_step("work", "data_transform", language="python", code="emit(items['current'])"),
        make_step("merge", "merge"),
        make_step("tpl", "template", template="x"),
        make_step("out", "output", output_kind="text", output_source="{{steps.tpl.output}}"),
    ]
    edges = [
        _edge("fetch", "each"),
        _edge("each", "work", "body"),
        _edge("work", "each"),
        _edge("each", "merge", "done"),
        _edge("merge", "tpl"),
        _edge("tpl", "out"),
    ]
    rep = analyze_graph(
        [Node.model_validate(n) for n in nodes], [Edge.model_validate(e) for e in edges]
    )
    assert rep.errors == [] and rep.warnings == [], (rep.errors, rep.warnings)

    bad = [
        make_step("l", "loop"),
        make_step("d", "data_transform"),
        make_step("h", "http_request"),
        make_step("t", "template"),
        make_step("o", "output", output_kind="xml"),
    ]
    rep = analyze_graph([Node.model_validate(n) for n in bad], [])
    msgs = " | ".join(rep.errors)
    for frag in (
        "items expression is empty",
        "code is empty",
        "url is empty",
        "template is empty",
        "kind must be",
    ):
        assert frag in msgs
    # loop edges must use body/done
    rep = analyze_graph(
        [Node.model_validate(n) for n in nodes[:3]], [Edge.model_validate(_edge("each", "work"))]
    )
    assert any("handle body/done" in e for e in rep.errors)


# ---------------------------------------------------------------- executor integration


async def test_loop_over_items_with_variables(
    deps: ExecutorDeps, db_session: Session, fake_client: FakeAnthropic, fake_sandbox: FakeSandbox
) -> None:
    # http_request -> loop(body: data_transform -> custom LLM) -> merge -> template -> output
    def handler(req: httpx.Request) -> httpx.Response:
        assert (
            req.url == "https://api.example/items?q=fruit" and req.headers["x-token"] == "tok-FRUIT"
        )
        return httpx.Response(200, json={"items": [{"name": "apple"}, {"name": "pear"}]})

    ctx = ToolContext(http=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    deps.tool_context_factory = lambda uid: ctx
    calls: list[str] = []

    def fake_python(argv: list[str]) -> ExecResult:
        # pretend to run the script: read ctx json written by tee and emit the item name upper-cased
        ctx_path = argv[2]
        ctx_json = json.loads(
            fake_sandbox.files[ctx_path.lstrip("/")]
            if ctx_path.lstrip("/") in fake_sandbox.files
            else fake_sandbox.files[ctx_path]
        )
        name = ctx_json["items"]["current"]["name"]
        calls.append(name)
        return ExecResult(
            0, json.dumps({"upper": name.upper(), "idx": ctx_json["items"]["index"]}), ""
        )

    fake_sandbox.scripted["python3"] = fake_python
    fake_client.responses = [text_response("APPLE is nice"), text_response("PEAR is nice")]

    nodes = [
        make_step(
            "fetch",
            "http_request",
            url="https://api.example/items?q={{input.q}}",
            headers={"x-token": "tok-{{input.token}}"},
        ),
        make_step(
            "each", "loop", items_expression="steps.fetch.data.body.items", label="For each item"
        ),
        make_step(
            "up",
            "data_transform",
            language="python",
            code="emit({'upper': items['current']['name'].upper()})",
        ),
        make_step(
            "say",
            "custom",
            system_prompt="Item {{items.index}}/{{items.total}}: {{steps.up.data.upper}} ({{items.current.name}})",
        ),
        make_step("all", "merge", merge_steps=["fetch", "each"], merge_mode="json"),
        make_step(
            "tpl", "template", template="{% for r in steps.each.data %}- {{ r }}\n{% endfor %}"
        ),
        make_step(
            "out",
            "output",
            output_kind="text",
            output_source="{{steps.tpl.output}}",
            label="Report",
        ),
    ]
    edges = [
        _edge("fetch", "each"),
        _edge("each", "up", "body"),
        _edge("up", "say"),
        _edge("say", "each"),
        _edge("each", "all", "done"),
        _edge("all", "tpl"),
        _edge("tpl", "out"),
    ]
    run_id = _run(db_session, nodes, edges, inputs={"q": "fruit", "token": "FRUIT"})
    assert await FlowExecutor(deps, run_id).execute() is RunStatus.success

    assert calls == ["apple", "pear"]
    systems = [r["system"] for r in fake_client.requests]
    assert systems[0].endswith("Item 0/2: APPLE (apple)") and systems[1].endswith(
        "Item 1/2: PEAR (pear)"
    )
    steps = _steps(deps, run_id)
    assert [s.step_id for s in steps] == [
        "fetch",
        "each",
        "up",
        "say",
        "up",
        "say",
        "all",
        "tpl",
        "out",
    ]
    loop = next(s for s in steps if s.step_id == "each")
    assert (
        json.loads(loop.output) == ["APPLE is nice", "PEAR is nice"]
        and loop.status.value == "success"
    )
    tpl = next(s for s in steps if s.step_id == "tpl")
    assert tpl.output == "- APPLE is nice\n- PEAR is nice\n"
    with deps.session_factory() as db:
        run = db.get(Run, run_id)
        assert run is not None and run.outputs == [
            {
                "name": "Report",
                "kind": "text",
                "step_id": "out",
                "value": "- APPLE is nice\n- PEAR is nice\n",
            }
        ]
    events = deps.bus.history(run_id)
    var_events = [e for e in events if e["event"] == "variables"]
    assert var_events and var_events[-1]["variables"]["steps"]["out"]["status"] == "success"
    assert var_events[-1]["variables"]["input"] == {"q": "fruit", "token": "FRUIT"}
    assert any(e["event"] == "output" and e["output"]["name"] == "Report" for e in events)
    assert any(e["event"] == "log" and "loop iteration 2/2" in e.get("message", "") for e in events)


async def test_loop_failure_policies(
    deps: ExecutorDeps, db_session: Session, fake_sandbox: FakeSandbox
) -> None:
    fake_sandbox.scripted["python3"] = lambda argv: ExecResult(1, "", "boom")
    nodes = [
        make_step("each", "loop", items_expression="1,2,3", on_fail="stop"),
        make_step("t", "data_transform", language="python", code="raise SystemExit(1)"),
    ]
    edges = [_edge("each", "t", "body"), _edge("t", "each")]
    run_id = _run(db_session, nodes, edges)
    assert await FlowExecutor(deps, run_id).execute() is RunStatus.failed
    assert [s.step_id for s in _steps(deps, run_id)] == [
        "each",
        "t",
    ]  # stopped after first failing iteration

    nodes[0] = make_step(
        "each", "loop", items_expression="1,2,3", on_fail="retry", retry_limit=0
    )  # continue on failure
    run_id = _run(db_session, nodes, edges)
    assert await FlowExecutor(deps, run_id).execute() is RunStatus.success
    loop = next(s for s in _steps(deps, run_id) if s.step_id == "each")
    assert "3 of 3 iterations failed" in loop.output and loop.status.value == "success"


async def test_http_request_errors_and_output_delivery(
    deps: ExecutorDeps, db_session: Session, fake_sandbox: FakeSandbox
) -> None:
    posted: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.host == "down.example":
            return httpx.Response(503, text="nope")
        if "hooks.slack.com" in str(req.url):
            posted.append(json.loads(req.content))
            return httpx.Response(200, text="ok")
        return httpx.Response(200, text="plain text body")

    ctx = ToolContext(
        http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        credential=lambda p: "https://hooks.slack.com/services/x" if p == "slack" else None,
    )
    deps.tool_context_factory = lambda uid: ctx

    run_id = _run(
        db_session,
        [
            make_step(
                "h", "http_request", url="https://down.example/x", method="POST", body='{"a": 1}'
            )
        ],
        [],
    )
    assert await FlowExecutor(deps, run_id).execute() is RunStatus.failed
    assert "HTTP 503" in _steps(deps, run_id)[0].output

    nodes = [
        make_step("h", "http_request", url="https://ok.example/x"),
        make_step(
            "o",
            "output",
            output_kind="text",
            output_source="Result: {{steps.h.output}}",
            deliver_tool="slack_post",
            deliver_args={"text": "Done: {{output}}"},
        ),
    ]
    run_id = _run(db_session, nodes, [_edge("h", "o")])
    assert await FlowExecutor(deps, run_id).execute() is RunStatus.success
    assert posted == [{"text": "Done: Result: plain text body"}]
    with deps.session_factory() as db:
        outs = db.get(Run, run_id).outputs  # type: ignore[union-attr]
    assert (
        outs[0]["delivered"]["tool"] == "slack_post"
        and outs[0]["value"] == "Result: plain text body"
    )

    # json output must be valid json; file output must exist
    run_id = _run(
        db_session, [make_step("o", "output", output_kind="json", output_source="not json")], []
    )
    assert await FlowExecutor(deps, run_id).execute() is RunStatus.failed
    fake_sandbox.scripted["test"] = lambda argv: ExecResult(
        0 if argv[-1] == "/repo/output/report.md" else 1, "", ""
    )
    run_id = _run(
        db_session,
        [make_step("o", "output", output_kind="file", output_source="output/report.md")],
        [],
    )
    assert await FlowExecutor(deps, run_id).execute() is RunStatus.success
    with deps.session_factory() as db:
        assert db.get(Run, run_id).outputs[0]["path"] == "output/report.md"  # type: ignore[union-attr]
    run_id = _run(
        db_session,
        [make_step("o", "output", output_kind="file", output_source="output/missing.md")],
        [],
    )
    assert await FlowExecutor(deps, run_id).execute() is RunStatus.failed


def test_run_api_exposes_outputs_and_new_types(client: TestClient) -> None:
    node = make_step("d", "data_transform", language="javascript", code="emit(1)")
    r = client.post(
        "/flows",
        json={
            "name": "f",
            "nodes": [node, make_step("o", "output", output_source="{{steps.d.output}}")],
            "edges": [_edge("d", "o")],
        },
    )
    assert r.status_code == 201, r.text
    assert r.json()["nodes"][0]["data"]["language"] == "javascript"
