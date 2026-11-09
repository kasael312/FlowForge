"""Safe evaluation of ``condition`` step expressions.

Expressions are a tiny Python subset evaluated over a ``prev`` object that
describes the previous step: ``prev.success``, ``prev.output``,
``prev.exit_code``, ``prev.type``, ``prev.step_id``. Examples::

    prev.success
    not prev.success
    'FAILED' not in prev.output and prev.exit_code == 0
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class StepResultView:
    """What a condition expression can see about the previous step."""

    step_id: str
    type: str
    success: bool
    output: str
    exit_code: int | None = None


class ConditionError(Exception):
    """Raised for syntax errors or disallowed constructs."""


_ALLOWED_NODES = (
    ast.Expression,
    ast.BoolOp,
    ast.And,
    ast.Or,
    ast.UnaryOp,
    ast.Not,
    ast.Compare,
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
    ast.In,
    ast.NotIn,
    ast.Is,
    ast.IsNot,
    ast.Constant,
    ast.Name,
    ast.Load,
    ast.Attribute,
    ast.Call,
)
_ALLOWED_CALLS = {"len", "str", "lower", "upper", "startswith", "endswith", "strip"}
_ALLOWED_ATTRS = {"step_id", "type", "success", "output", "exit_code"} | _ALLOWED_CALLS


def evaluate(expression: str, prev: StepResultView) -> bool:
    """Evaluate ``expression`` against ``prev``; empty expression means ``prev.success``."""
    expr = (expression or "").strip() or "prev.success"
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise ConditionError(f"invalid expression: {exc.msg}") from exc
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            raise ConditionError(f"disallowed syntax: {type(node).__name__}")
        if isinstance(node, ast.Name) and node.id not in (
            "prev",
            "True",
            "False",
            "None",
            "len",
            "str",
        ):
            raise ConditionError(f"unknown name {node.id!r}")
        if isinstance(node, ast.Attribute) and node.attr not in _ALLOWED_ATTRS:
            raise ConditionError(f"attribute {node.attr!r} is not allowed")
        if isinstance(node, ast.Call):
            fn = node.func
            name = (
                fn.id
                if isinstance(fn, ast.Name)
                else fn.attr
                if isinstance(fn, ast.Attribute)
                else None
            )
            if name not in _ALLOWED_CALLS:
                raise ConditionError(
                    "only len/str/lower/upper/startswith/endswith/strip calls allowed"
                )
    env: dict[str, Any] = {"prev": prev, "len": len, "str": str, "__builtins__": {}}
    try:
        return bool(eval(compile(tree, "<condition>", "eval"), env))
    except Exception as exc:  # pragma: no cover - defensive
        raise ConditionError(f"evaluation failed: {exc}") from exc
