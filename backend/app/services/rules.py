"""Rules: short constraints prepended to every LLM step's system prompt.

Scopes: ``global`` (all of a user's flows), ``flow`` (one flow), ``step`` (one step of
one flow). Built-in rules are copied into each user's account the first time their
rules are read, so each user can edit, disable or delete their copies.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Rule

SCOPES = ("global", "flow", "step")

BUILTIN_RULES: list[dict[str, Any]] = [
    {
        "key": "no-secrets",
        "name": "No secrets",
        "priority": 10,
        "enabled": True,
        "content": (
            "Never write API keys, passwords, tokens or other credentials into files, logs or "
            "your replies. Read them from environment variables or the configured credential "
            "store, and redact any secret you encounter."
        ),
    },
    {
        "key": "always-write-tests",
        "name": "Always write tests",
        "priority": 20,
        "enabled": True,
        "content": (
            "Every behaviour change ships with a test that fails without the change. Run the "
            "project's test suite before declaring the work done and report the result."
        ),
    },
    {
        "key": "follow-claude-md",
        "name": "Follow CLAUDE.md",
        "priority": 30,
        "enabled": True,
        "content": (
            "If the repository contains CLAUDE.md, AGENTS.md or CONTRIBUTING.md, read it first "
            "and follow its conventions (style, commands, structure) over your own defaults."
        ),
    },
    {
        "key": "vietnamese-comments",
        "name": "Vietnamese comments",
        "priority": 40,
        "enabled": False,
        "content": (
            "Viết comment, docstring và thông điệp commit bằng tiếng Việt; tên biến, hàm và "
            "API vẫn bằng tiếng Anh."
        ),
    },
]


def ensure_builtin_rules(db: Session, user_id: str) -> int:
    """Copy built-in rules the user does not have yet (matched by ``key``). Returns count added."""
    have = set(
        db.scalars(select(Rule.key).where(Rule.user_id == user_id, Rule.key.is_not(None))).all()
    )
    added = 0
    for spec in BUILTIN_RULES:
        if spec["key"] in have:
            continue
        db.add(
            Rule(
                user_id=user_id,
                key=spec["key"],
                name=spec["name"],
                content=spec["content"],
                scope="global",
                priority=spec["priority"],
                enabled=spec["enabled"],
            )
        )
        added += 1
    if added:
        db.commit()
    return added


def resolve_rules(
    db: Session, user_id: str | None, flow_id: str | None, step_id: str | None
) -> list[Rule]:
    """Enabled rules that apply to ``step_id`` of ``flow_id``: global → flow → step, by priority."""
    if user_id is None:
        return []
    ensure_builtin_rules(db, user_id)
    rows = db.scalars(select(Rule).where(Rule.user_id == user_id, Rule.enabled.is_(True))).all()

    def applies(r: Rule) -> bool:
        if r.scope == "global":
            return True
        if r.scope == "flow":
            return flow_id is not None and r.flow_id == flow_id
        return flow_id is not None and r.flow_id == flow_id and r.step_id == step_id

    order = {"global": 0, "flow": 1, "step": 2}
    return sorted(
        (r for r in rows if applies(r)),
        key=lambda r: (order.get(r.scope, 9), r.priority, r.name.lower()),
    )


def rules_prompt_block(rules: list[Rule]) -> str:
    """Text inserted into the system prompt (empty when no rules apply)."""
    if not rules:
        return ""
    parts = ["# Rules\nThese rules override everything below them."]
    for r in rules:
        parts.append(f"## Rule: {r.name} ({r.scope})\n{r.content.strip()}")
    return "\n\n".join(parts)


def load_rules_block(
    db: Session, user_id: str | None, flow_id: str | None, step_id: str | None
) -> str:
    return rules_prompt_block(resolve_rules(db, user_id, flow_id, step_id))
