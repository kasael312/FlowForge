"""Tests for the condition expression evaluator."""

import pytest

from app.executor.conditions import ConditionError, StepResultView, evaluate

OK = StepResultView(step_id="test", type="test", success=True, output="12 passed", exit_code=0)
BAD = StepResultView(step_id="test", type="test", success=False, output="1 FAILED", exit_code=1)


@pytest.mark.parametrize(
    ("expr", "prev", "expected"),
    [
        ("", OK, True),
        ("prev.success", BAD, False),
        ("not prev.success", BAD, True),
        ("'FAILED' not in prev.output", OK, True),
        ("'FAILED' in prev.output and prev.exit_code == 1", BAD, True),
        ("prev.exit_code == 0 or prev.type == 'shell'", BAD, False),
        ("len(prev.output) > 3", OK, True),
        ("prev.output.lower().startswith('12')", OK, True),
    ],
)
def test_evaluate(expr: str, prev: StepResultView, expected: bool) -> None:
    assert evaluate(expr, prev) is expected


@pytest.mark.parametrize(
    "expr",
    [
        "__import__('os').system('id')",
        "prev.__class__",
        "open('/etc/passwd')",
        "[x for x in prev.output]",
        "prev.output.format()",
        "lambda: 1",
        "prev.success if True else",
    ],
)
def test_rejects_unsafe(expr: str) -> None:
    with pytest.raises(ConditionError):
        evaluate(expr, OK)
