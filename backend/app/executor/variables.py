"""``{{variable}}`` substitution in prompts of ``custom`` (and other) steps."""

from __future__ import annotations

import json
import re
from typing import Any

_VAR_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z0-9_]+)*)\s*\}\}")

KNOWN_VARIABLES = ("task", "prev_output", "repo", "branch", "step_id", "input")

_MISSING = object()


def lookup(variables: dict[str, Any], dotted: str) -> Any:
    """Resolve ``a.b.c`` through nested dicts; returns ``_MISSING`` when absent."""
    cur: Any = variables
    for part in dotted.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return _MISSING
    return cur


def _to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(value, ensure_ascii=False)


def render(text: str, variables: dict[str, Any]) -> str:
    """Replace ``{{name}}`` / ``{{input.field}}`` placeholders. Unknown names stay."""
    if not text or "{{" not in text:
        return text

    def sub(m: re.Match[str]) -> str:
        value = lookup(variables, m.group(1))
        return m.group(0) if value is _MISSING else _to_text(value)

    return _VAR_RE.sub(sub, text)


def used_variables(text: str) -> set[str]:
    """Names referenced in ``text`` (dotted paths kept whole, e.g. ``input.topic``)."""
    return set(_VAR_RE.findall(text or ""))
