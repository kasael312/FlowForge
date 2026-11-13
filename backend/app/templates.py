"""Built-in flow templates, seeded into the ``flow_templates`` table on startup."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import FlowTemplate
from app.schemas import FlowCreate
from app.templates_extra import EXTRA_TEMPLATES

COL, ROW = 260, 140
ALL_TOOLS = ["read_file", "write_file", "run_shell", "list_files", "git_diff"]
READ_TOOLS = ["read_file", "list_files", "run_shell", "git_diff"]


def _node(id_: str, type_: str, x: int, y: int, **data: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "label": id_,
        "type": type_,
        "system_prompt": "",
        "tools_allowed": READ_TOOLS if type_ in ("plan", "review") else ALL_TOOLS,
        "max_turns": 20,
        "on_fail": "stop",
        "retry_limit": 1,
        "command": "" if type_ == "shell" else None,
        "expression": "success" if type_ == "condition" else None,
    }
    base.update(data)
    return {"id": id_, "type": "step", "position": {"x": x, "y": y}, "data": base}


def _edge(src: str, dst: str, handle: str | None = None) -> dict[str, Any]:
    return {
        "id": f"{src}->{dst}{':' + handle if handle else ''}",
        "source": src,
        "target": dst,
        "sourceHandle": handle,
    }


BUILTIN_TEMPLATES: list[dict[str, Any]] = [
    {
        "key": "fix-issue",
        "name": "Fix issue",
        "description": "Plan → code → run tests → loop back on failure → review.",
        "nodes": [
            _node(
                "plan",
                "plan",
                0,
                0,
                label="Plan fix",
                system_prompt="Reproduce and diagnose the issue described in the task. Identify the root cause and list the exact files to change. Do not edit files.",
            ),
            _node(
                "code",
                "code",
                COL,
                0,
                label="Implement fix",
                system_prompt="Apply the fix from the plan with the smallest change that resolves the root cause. Add a regression test.",
                on_fail="retry",
            ),
            _node(
                "test",
                "test",
                COL * 2,
                0,
                label="Run tests",
                system_prompt='Run the test suite. If anything fails, fix it and re-run. End with the line "RESULT: PASS" or "RESULT: FAIL".',
                on_fail="goto:code",
                retry_limit=2,
            ),
            _node(
                "check",
                "condition",
                COL * 3,
                0,
                label="Tests pass?",
                expression='contains("RESULT: PASS")',
            ),
            _node("review", "review", COL * 4, 0, label="Review"),
        ],
        "edges": [
            _edge("plan", "code"),
            _edge("code", "test"),
            _edge("test", "check"),
            _edge("check", "review", "true"),
            _edge("check", "code", "false"),
        ],
    },
    {
        "key": "add-feature",
        "name": "Add feature with tests",
        "description": "Plan → write tests first → implement → run tests → review.",
        "nodes": [
            _node(
                "plan",
                "plan",
                0,
                0,
                label="Design",
                system_prompt="Design the feature: public API, files to touch, edge cases, and the tests that should exist.",
            ),
            _node(
                "tests",
                "code",
                COL,
                0,
                label="Write tests",
                system_prompt="Write failing tests that specify the feature from the design. Do not implement the feature yet.",
            ),
            _node(
                "impl",
                "code",
                COL * 2,
                0,
                label="Implement",
                system_prompt="Implement the feature so that the new tests pass without weakening them.",
                on_fail="retry",
            ),
            _node(
                "run",
                "shell",
                COL * 3,
                0,
                label="pytest",
                command="pytest -q",
                on_fail="goto:impl",
                retry_limit=2,
            ),
            _node("review", "review", COL * 4, 0, label="Review"),
        ],
        "edges": [
            _edge("plan", "tests"),
            _edge("tests", "impl"),
            _edge("impl", "run"),
            _edge("run", "review"),
        ],
    },
    {
        "key": "refactor-review",
        "name": "Refactor + review",
        "description": "Refactor → tests → review → fix if changes requested.",
        "nodes": [
            _node(
                "refactor",
                "code",
                0,
                0,
                label="Refactor",
                system_prompt="Refactor as described in the task without changing behaviour. Keep the public API stable.",
            ),
            _node(
                "test", "test", COL, 0, label="Run tests", on_fail="goto:refactor", retry_limit=2
            ),
            _node(
                "review",
                "review",
                COL * 2,
                0,
                label="Review",
                system_prompt='Review the diff for behaviour changes, readability and risk. End with "VERDICT: APPROVE" or "VERDICT: REQUEST_CHANGES".',
                tools_allowed=["read_file", "list_files", "git_diff"],
            ),
            _node(
                "gate",
                "condition",
                COL * 3,
                0,
                label="Approved?",
                expression='contains("VERDICT: APPROVE")',
            ),
            _node(
                "fix",
                "code",
                COL * 3,
                ROW,
                label="Address review",
                system_prompt="Address every point raised in the review output, then stop.",
            ),
            _node("done", "shell", COL * 4, 0, label="Final diff", command="git diff --stat"),
        ],
        "edges": [
            _edge("refactor", "test"),
            _edge("test", "review"),
            _edge("review", "gate"),
            _edge("gate", "done", "true"),
            _edge("gate", "fix", "false"),
            _edge("fix", "test"),
        ],
    },
]


def all_builtin_templates() -> list[dict[str, Any]]:
    """Coding templates followed by the general workflow templates."""
    return [*BUILTIN_TEMPLATES, *EXTRA_TEMPLATES]


def seed_builtin_templates(db: Session) -> int:
    """Insert built-in templates that are not present yet. Returns the number added."""
    existing = {
        t.key: t
        for t in db.scalars(select(FlowTemplate).where(FlowTemplate.key.is_not(None))).all()
    }
    added = 0
    for tpl in all_builtin_templates():
        if tpl["key"] in existing:
            # built-ins are read-only: keep the stored copy in sync with the code definition
            row = existing[tpl["key"]]
            if (row.skills or None) != (tpl.get("skills") or None) or (row.rules or None) != (
                tpl.get("rules") or None
            ):
                row.skills = tpl.get("skills") or None
                row.rules = tpl.get("rules") or None
                db.commit()
            continue
        body = FlowCreate(
            name=tpl["name"],
            description=tpl["description"],
            nodes=tpl["nodes"],
            edges=tpl["edges"],
            input_schema=tpl.get("input_schema", []),
            sandbox=tpl.get("sandbox"),
        )
        db.add(
            FlowTemplate(
                key=tpl["key"],
                user_id=None,
                name=body.name,
                description=body.description,
                nodes=[n.model_dump(mode="json") for n in body.nodes],
                edges=[e.model_dump(mode="json") for e in body.edges],
                input_schema=[f.model_dump(mode="json") for f in body.input_schema],
                sandbox=body.sandbox.model_dump(exclude_none=True) if body.sandbox else None,
                skills=tpl.get("skills") or None,
                rules=tpl.get("rules") or None,
            )
        )
        added += 1
    if added:
        db.commit()
    return added
