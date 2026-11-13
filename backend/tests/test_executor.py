"""End-to-end tests of the FlowExecutor with fake client/sandbox/workspace."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.executor.engine import ExecutorDeps, FlowExecutor, FlowGraphError, find_start
from app.executor.sandbox import ExecResult
from app.llm import AnthropicProvider
from app.models import Flow, Run, RunStatus, StepRun, StepRunStatus
from app.schemas import Edge, FlowCreate, Node
from tests.conftest import make_step
from tests.fakes import FakeAnthropic, FakeSandbox, FakeWorkspace, text_response, tool_response


def _create(
    db_session: Session, flow_dict: dict, *, task: str = "fix the bug", validate: bool = True
) -> str:
    """Persist a flow + queued run. ``validate=False`` stores a graph the API
    would reject, to exercise the executor's own runtime guards."""
    if validate:
        body = FlowCreate.model_validate(flow_dict)
        nodes = [n.model_dump(mode="json") for n in body.nodes]
        edges = [e.model_dump(mode="json") for e in body.edges]
    else:
        nodes = [Node.model_validate(n).model_dump(mode="json") for n in flow_dict["nodes"]]
        edges = [Edge.model_validate(e).model_dump(mode="json") for e in flow_dict["edges"]]
    flow = Flow(name=flow_dict["name"], description="", nodes=nodes, edges=edges)
    db_session.add(flow)
    db_session.flush()
    run = Run(
        flow_id=flow.id, repo_url="https://github.com/acme/demo", branch="main", task_input=task
    )
    db_session.add(run)
    db_session.commit()
    return run.id


def _steps(factory: sessionmaker[Session], run_id: str) -> list[StepRun]:
    with factory() as db:
        return list(
            db.scalars(
                select(StepRun)
                .where(StepRun.run_id == run_id)
                .order_by(StepRun.started_at, StepRun.id)
            ).all()
        )


def _run(factory: sessionmaker[Session], run_id: str) -> Run:
    with factory() as db:
        run = db.get(Run, run_id)
        assert run is not None
        return run


async def test_happy_path(
    deps: ExecutorDeps,
    db_session: Session,
    sample_flow: dict,
    fake_client: FakeAnthropic,
    fake_sandbox: FakeSandbox,
) -> None:
    fake_client.responses = [
        text_response("1. edit src/app.py"),  # plan
        tool_response([("write_file", {"path": "src/app.py", "content": "fixed\n"})]),  # code
        text_response("edited app.py"),
        tool_response([("run_shell", {"command": "pytest -q"})]),  # test
        text_response("All tests PASS"),
        text_response("APPROVE"),  # review
    ]
    fake_sandbox.scripted["pytest -q"] = ExecResult(0, "2 passed", "")
    run_id = _create(db_session, sample_flow)

    status = await FlowExecutor(deps, run_id).execute()

    assert status is RunStatus.success
    run = _run(deps.session_factory, run_id)
    assert run.status is RunStatus.success and run.error is None
    assert run.started_at and run.finished_at
    assert run.work_branch == f"flowforge/{run_id[:8]}"
    assert run.final_diff.startswith("diff --git")
    assert run.cost_tokens == 120
    assert fake_sandbox.started and fake_sandbox.stopped

    steps = _steps(deps.session_factory, run_id)
    assert [s.step_id for s in steps] == ["plan", "code", "test", "check", "review"]
    assert all(s.status is StepRunStatus.success for s in steps)
    code = steps[1]
    assert len(code.messages) == 4 and code.tool_calls[0]["tool"] == "write_file"
    assert steps[3].output == "prev.success -> True"
    assert steps[4].output == "APPROVE"

    # previous step output is forwarded to the next LLM step
    code_req = fake_client.requests[1]
    assert "## Task\nfix the bug" in code_req["messages"][0]["content"]
    assert "1. edit src/app.py" in code_req["messages"][0]["content"]
    assert code_req["system"].endswith("You are the code step.")
    assert "/repo" in code_req["system"]

    events = deps.bus.history(run_id)
    kinds = [e["event"] for e in events]
    assert kinds[0] == "run_status" and kinds[-1] == "run_status"
    assert kinds.count("step_start") == 5 and kinds.count("step_end") == 5
    assert deps.bus.is_closed(run_id)


async def test_condition_false_loops_back_then_succeeds(
    deps: ExecutorDeps, db_session: Session, sample_flow: dict, fake_client: FakeAnthropic
) -> None:
    fake_client.responses = [
        text_response("plan"),
        text_response("code v1"),
        text_response(
            "tests FAILED", stop_reason="refusal"
        ),  # test step fails -> on_fail goto:code (attempt 1 <= 2)
        text_response("code v2"),
        text_response("PASS"),  # test ok -> condition true
        text_response("APPROVE"),
    ]
    run_id = _create(db_session, sample_flow)
    status = await FlowExecutor(deps, run_id).execute()
    assert status is RunStatus.success
    ids = [(s.step_id, s.attempt, s.status.value) for s in _steps(deps.session_factory, run_id)]
    assert ids == [
        ("plan", 1, "success"),
        ("code", 1, "success"),
        ("test", 1, "failed"),
        ("code", 2, "success"),
        ("test", 2, "success"),
        ("check", 1, "success"),
        ("review", 1, "success"),
    ]


async def test_condition_edge_false(
    deps: ExecutorDeps, db_session: Session, fake_client: FakeAnthropic, fake_sandbox: FakeSandbox
) -> None:
    flow = {
        "name": "cond",
        "nodes": [
            make_step("t", "shell", command="pytest -q"),
            make_step("c", "condition", expression="'passed' in prev.output"),
            make_step("good", "review"),
            make_step("bad", "code"),
        ],
        "edges": [
            {"id": "1", "source": "t", "target": "c"},
            {"id": "2", "source": "c", "target": "good", "sourceHandle": "true"},
            {"id": "3", "source": "c", "target": "bad", "sourceHandle": "false"},
        ],
    }
    # shell step exits 0 but the expression looks at output text
    fake_sandbox.scripted["pytest -q"] = ExecResult(0, "nothing ran", "")
    fake_client.responses = [text_response("fixing")]
    run_id = _create(db_session, flow)
    assert await FlowExecutor(deps, run_id).execute() is RunStatus.success
    steps = _steps(deps.session_factory, run_id)
    assert [s.step_id for s in steps] == ["t", "c", "bad"]
    assert steps[0].status is StepRunStatus.success and steps[0].output == "nothing ran"
    assert steps[1].status is StepRunStatus.failed  # condition evaluated to False


async def test_retry_then_stop(
    deps: ExecutorDeps, db_session: Session, fake_client: FakeAnthropic
) -> None:
    flow = {
        "name": "retry",
        "nodes": [make_step("a", "code", on_fail="retry", retry_limit=2), make_step("b", "review")],
        "edges": [{"id": "1", "source": "a", "target": "b"}],
    }
    fake_client.responses = [text_response("x", stop_reason="max_tokens")] * 3
    run_id = _create(db_session, flow)
    assert await FlowExecutor(deps, run_id).execute() is RunStatus.failed
    steps = _steps(deps.session_factory, run_id)
    assert [(s.step_id, s.attempt) for s in steps] == [("a", 1), ("a", 2), ("a", 3)]
    assert all(s.status is StepRunStatus.failed for s in steps)
    assert "[error] response truncated" in steps[0].output
    assert _run(deps.session_factory, run_id).error == "a step failed"


async def test_on_fail_stop(
    deps: ExecutorDeps, db_session: Session, fake_client: FakeAnthropic
) -> None:
    flow = {
        "name": "stop",
        "nodes": [make_step("a", "code", on_fail="stop"), make_step("b", "review")],
        "edges": [{"id": "1", "source": "a", "target": "b"}],
    }
    fake_client.responses = [text_response("nope", stop_reason="refusal"), text_response("unused")]
    run_id = _create(db_session, flow)
    assert await FlowExecutor(deps, run_id).execute() is RunStatus.failed
    assert len(_steps(deps.session_factory, run_id)) == 1
    assert len(fake_client.requests) == 1


async def test_shell_step_whitelist_and_failure(
    deps: ExecutorDeps, db_session: Session, fake_sandbox: FakeSandbox
) -> None:
    flow = {
        "name": "sh",
        "nodes": [make_step("s", "shell", command="curl http://evil")],
        "edges": [],
    }
    run_id = _create(db_session, flow)
    assert await FlowExecutor(deps, run_id).execute() is RunStatus.failed
    step = _steps(deps.session_factory, run_id)[0]
    assert "not whitelisted" in step.output
    assert ["curl", "http://evil"] not in fake_sandbox.calls

    flow = {"name": "sh2", "nodes": [make_step("s", "shell", command="make build")], "edges": []}
    fake_sandbox.scripted["make build"] = ExecResult(2, "", "error: no rule")
    run_id = _create(db_session, flow)
    assert await FlowExecutor(deps, run_id).execute() is RunStatus.failed
    step = _steps(deps.session_factory, run_id)[0]
    assert step.status is StepRunStatus.failed and "exit code 2" in step.output


async def test_infinite_loop_guard(
    deps: ExecutorDeps, db_session: Session, fake_client: FakeAnthropic
) -> None:
    flow = {
        "name": "loop",
        "nodes": [make_step("a", "code"), make_step("b", "code")],
        "edges": [
            {"id": "1", "source": "a", "target": "b"},
            {"id": "2", "source": "b", "target": "a"},
        ],
    }
    run_id = _create(db_session, flow, validate=False)
    # a has an incoming edge from b -> no start node
    with pytest.raises(FlowGraphError):
        find_start(
            [Node.model_validate(n) for n in flow["nodes"]],
            [Edge.model_validate(e) for e in flow["edges"]],
        )
    assert await FlowExecutor(deps, run_id).execute() is RunStatus.failed
    assert "start node" in (_run(deps.session_factory, run_id).error or "")

    # a -> b -> (condition always false) -> a : bounded by max_steps_per_run
    flow = {
        "name": "loop2",
        "nodes": [make_step("a", "code"), make_step("c", "condition", expression="False")],
        "edges": [
            {"id": "1", "source": "a", "target": "c"},
            {"id": "2", "source": "c", "target": "a", "sourceHandle": "false"},
        ],
    }
    # 'a' has incoming edge from c, so no start... use goto instead:
    flow = {
        "name": "loop3",
        "nodes": [
            make_step("a", "code", on_fail="goto:b", retry_limit=10),
            make_step("b", "code", on_fail="goto:a", retry_limit=10),
        ],
        "edges": [{"id": "1", "source": "a", "target": "b"}],
    }
    fake_client.responses = [text_response("x", stop_reason="refusal")] * 100
    deps.max_steps_per_run = 6
    run_id = _create(db_session, flow, validate=False)
    assert await FlowExecutor(deps, run_id).execute() is RunStatus.failed
    assert len(_steps(deps.session_factory, run_id)) == 6
    assert "exceeded 6" in (_run(deps.session_factory, run_id).error or "")


async def test_workspace_failure(
    deps: ExecutorDeps, db_session: Session, sample_flow: dict, fake_sandbox: FakeSandbox
) -> None:
    deps.workspace = FakeWorkspace(fail_with="git clone failed: not found")
    run_id = _create(db_session, sample_flow)
    assert await FlowExecutor(deps, run_id).execute() is RunStatus.failed
    assert "git clone failed" in (_run(deps.session_factory, run_id).error or "")
    assert not fake_sandbox.started


async def test_cancel_mid_run(
    deps: ExecutorDeps, db_session: Session, sample_flow: dict, fake_client: FakeAnthropic
) -> None:
    gate = asyncio.Event()

    class SlowClient(FakeAnthropic):
        async def _slow(self, **kwargs):
            gate.set()
            await asyncio.sleep(30)
            return text_response("never")

    slow = SlowClient()
    slow.beta.messages.create = slow._slow  # type: ignore[method-assign]
    deps.provider_resolver = lambda user_id, model_id: AnthropicProvider(
        "claude-test",
        client=slow,  # type: ignore[arg-type]
    )
    run_id = _create(db_session, sample_flow)
    executor = FlowExecutor(deps, run_id)
    task = asyncio.create_task(executor.execute())
    await gate.wait()
    executor.cancel.set()
    task.cancel()
    status = await task
    assert status is RunStatus.cancelled
    run = _run(deps.session_factory, run_id)
    assert run.status is RunStatus.cancelled and run.finished_at is not None
    steps = _steps(deps.session_factory, run_id)
    assert steps[0].status is StepRunStatus.failed and steps[0].output == "cancelled"
    assert deps.bus.history(run_id)[-1]["status"] == "cancelled"
