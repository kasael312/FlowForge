"""Safe evaluation of ``condition`` step expressions.

Expressions are a tiny Python subset evaluated over the previous step's result.
Two spellings are accepted::

    prev.success                     # attribute style
    success and exit_code == 0       # bare names
    contains("passed")               # substring of the previous output
    not icontains("error") and exit_code == 0
    matches(r"\\d+ passed")           # regex search on the output

Available names: ``success``, ``output``, ``exit_code``, ``type``, ``step_id``,
``prev`` (object with the same fields) and the helpers ``contains``,
``icontains``, ``startswith``, ``endswith``, ``matches``, ``len``, ``str``.
"""

from __future__ import annotations

import ast
import re
import warnings
from collections.abc import Callable
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
    ast.USub,
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
_FIELDS = {"step_id", "type", "success", "output", "exit_code"}
_STR_METHODS = {"lower", "upper", "startswith", "endswith", "strip"}
_HELPERS = {"contains", "icontains", "startswith", "endswith", "matches", "len", "str"}
_ALLOWED_NAMES = _FIELDS | _HELPERS | {"prev", "True", "False", "None"}


def _helpers(prev: StepResultView) -> dict[str, Callable[..., Any]]:
    out = prev.output or ""
    return {
        "contains": lambda s: str(s) in out,
        "icontains": lambda s: str(s).lower() in out.lower(),
        "startswith": lambda s: out.startswith(str(s)),
        "endswith": lambda s: out.endswith(str(s)),
        "matches": lambda pattern: re.search(str(pattern), out) is not None,
        "len": len,
        "str": str,
    }


def _check(tree: ast.AST) -> None:
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            raise ConditionError(f"disallowed syntax: {type(node).__name__}")
        if isinstance(node, ast.Name) and node.id not in _ALLOWED_NAMES:
            raise ConditionError(f"unknown name {node.id!r}")
        if isinstance(node, ast.Attribute) and node.attr not in _FIELDS | _STR_METHODS:
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
            if name not in _HELPERS | _STR_METHODS:
                raise ConditionError(f"call to {name!r} is not allowed")
            if node.keywords:
                raise ConditionError("keyword arguments are not allowed")


def _parse(expression: str) -> ast.Expression:
    expr = (expression or "").strip() or "success"
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SyntaxWarning)  # e.g. "\d" inside a regex literal
            tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise ConditionError(f"invalid expression: {exc.msg}") from exc
    _check(tree)
    return tree


def validate_expression(expression: str) -> None:
    """Raise :class:`ConditionError` if the expression is not acceptable."""
    _parse(expression)


def evaluate(expression: str, prev: StepResultView) -> bool:
    """Evaluate ``expression`` against ``prev``; empty expression means ``success``."""
    tree = _parse(expression)
    env: dict[str, Any] = {
        "__builtins__": {},
        "prev": prev,
        "step_id": prev.step_id,
        "type": prev.type,
        "success": prev.success,
        "output": prev.output,
        "exit_code": prev.exit_code,
        **_helpers(prev),
    }
    try:
        return bool(eval(compile(tree, "<condition>", "eval"), env))
    except re.error as exc:
        raise ConditionError(f"invalid regex: {exc}") from exc
    except Exception as exc:
        raise ConditionError(f"evaluation failed: {exc}") from exc
