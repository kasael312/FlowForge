"""Portable flow documents: export/import with models (by label), skills (by name) and rules.

An export never contains API keys. Node data keeps its ``model_id`` / ``skill_ids``
references, and the document lists the referenced models and skills so an import
can map them onto the target account: models by label (falling back to the
default model with a warning), skills by name (built-in or own; created from the
document when missing), rules re-created for the imported flow.
"""

from __future__ import annotations

import base64
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Flow, Model, Rule, Skill, User
from app.schemas import FlowCreate, validate_graph
from app.services import skills as skillsvc

EXPORT_VERSION = 2


class FlowImportError(ValueError):
    """The document cannot be imported."""


# ---------------------------------------------------------------- export


def _node_refs(nodes: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    model_ids: list[str] = []
    skill_ids: list[str] = []
    for n in nodes:
        d = n.get("data") or {}
        for key in ("model_id", "fallback_model_id"):
            v = d.get(key)
            if v and v not in model_ids:
                model_ids.append(v)
        for sid in d.get("skill_ids") or []:
            if sid not in skill_ids:
                skill_ids.append(sid)
    return model_ids, skill_ids


def export_flow(db: Session, user: User, flow: Flow) -> dict[str, Any]:
    nodes = list(flow.nodes or [])
    model_ids, skill_ids = _node_refs(nodes)
    models = [
        m for m in (db.get(Model, i) for i in model_ids) if m is not None and m.user_id == user.id
    ]
    skills, _ = skillsvc.resolve_skills(db, user.id, skill_ids)
    rules = db.scalars(
        select(Rule)
        .where(Rule.user_id == user.id, Rule.flow_id == flow.id)
        .order_by(Rule.scope, Rule.priority)
    ).all()
    return {
        "flowforge": EXPORT_VERSION,
        "name": flow.name,
        "description": flow.description,
        "nodes": nodes,
        "edges": list(flow.edges or []),
        "input_schema": list(flow.input_schema or []),
        "sandbox": dict(flow.sandbox or {}) or None,
        "models": [
            {
                "id": m.id,
                "label": m.label,
                "provider": m.provider.value,
                "model_name": m.model_name,
                "base_url": m.base_url,
                "params": dict(m.params or {}),
            }
            for m in models
        ],
        "skills": [
            {
                "id": s.id,
                "name": s.name,
                "kind": skillsvc.kind_of(s.kind),
                "builtin": s.user_id is None,
                "description": s.description or "",
                "tools_required": list(s.tools_required or []),
                "content": s.content or "",
                "files": [
                    {
                        "path": f["path"],
                        "content_type": f.get("content_type", ""),
                        "data": f.get("data", ""),
                    }
                    for f in skillsvc.file_entries(s)
                ],
            }
            for s in skills
        ],
        "rules": [
            {
                "name": r.name,
                "content": r.content,
                "scope": r.scope,
                "step_id": r.step_id,
                "priority": r.priority,
                "enabled": r.enabled,
            }
            for r in rules
        ],
    }


# ---------------------------------------------------------------- import


def _default_model_id(db: Session, user_id: str) -> str | None:
    return db.scalar(select(Model.id).where(Model.user_id == user_id, Model.is_default.is_(True)))


def resolve_skill_names(db: Session, user_id: str, names: list[str]) -> tuple[list[str], list[str]]:
    """Visible skill ids for ``names`` (own skills win over built-ins); unknown names reported."""
    visible = skillsvc.visible_skills(db, user_id)
    by_name: dict[str, str] = {}
    for s in visible:  # built-ins first, own later → own overrides
        by_name[s.name] = s.id
    ids = [by_name[n] for n in names if n in by_name]
    missing = [n for n in names if n not in by_name]
    return ids, missing


def _import_skill(db: Session, user_id: str, spec: dict[str, Any]) -> Skill:
    s = Skill(
        user_id=user_id,
        name=skillsvc.validate_name(skillsvc.slugify(str(spec.get("name") or "skill"))),
        kind=skillsvc.kind_of(str(spec.get("kind") or "skill")),
        description=str(spec.get("description") or ""),
        content=str(spec.get("content") or ""),
        tools_required=[str(t) for t in spec.get("tools_required") or []],
        files=[],
        is_builtin=False,
    )
    for f in spec.get("files") or []:
        try:
            path = str(f.get("path"))
            skillsvc.put_file(
                s,
                path,
                base64.b64decode(f.get("data") or ""),
                skillsvc.guess_content_type(path),  # never trust the document's type
            )
        except (skillsvc.SkillError, ValueError):
            continue
    db.add(s)
    db.flush()
    return s


def apply_import(
    db: Session, user: User, doc: dict[str, Any], into: Flow | None = None
) -> tuple[Flow, list[str]]:
    """Create (or replace ``into`` with) the flow described by ``doc``. Returns ``(flow, warnings)``."""
    if (
        not isinstance(doc, dict)
        or not isinstance(doc.get("nodes"), list)
        or not isinstance(doc.get("edges"), list)
    ):
        raise FlowImportError('a flow document needs "nodes" and "edges" arrays')
    warnings: list[str] = []
    nodes = [dict(n) for n in doc["nodes"]]

    # ---- models: by label, else default
    exported_models = {m.get("id"): m for m in doc.get("models") or [] if isinstance(m, dict)}
    mine = db.scalars(select(Model).where(Model.user_id == user.id)).all()
    by_label = {m.label: m.id for m in mine}
    default_id = _default_model_id(db, user.id)
    model_map: dict[str, str | None] = {}

    def map_model(ref: str) -> str | None:
        if ref in model_map:
            return model_map[ref]
        spec = exported_models.get(ref)
        label = spec.get("label") if spec else None
        if label and label in by_label:
            model_map[ref] = by_label[label]
        else:
            model_map[ref] = default_id
            what = f"model '{label}'" if label else f"model {ref}"
            warnings.append(
                f"{what} is not configured here; using your default model"
                + ("" if default_id else " (none set — add one under Settings → Models)")
            )
        return model_map[ref]

    # ---- skills: by name, else created from the document
    exported_skills = {s.get("id"): s for s in doc.get("skills") or [] if isinstance(s, dict)}
    skill_map: dict[str, str | None] = {}

    def map_skill(ref: str) -> str | None:
        if ref in skill_map:
            return skill_map[ref]
        spec = exported_skills.get(ref)
        if spec is None:
            # maybe the id is already one of ours (same account re-import)
            ids, _ = skillsvc.resolve_skills(db, user.id, [ref])
            skill_map[ref] = ids[0].id if ids else None
            if not ids:
                warnings.append(f"skill {ref} not found; dropped")
            return skill_map[ref]
        ids, _missing = resolve_skill_names(db, user.id, [str(spec.get("name"))])
        if ids:
            skill_map[ref] = ids[0]
        elif spec.get("content") or spec.get("files"):
            try:
                created = _import_skill(db, user.id, spec)
            except skillsvc.SkillError as exc:
                warnings.append(f"skill '{spec.get('name')}' could not be created: {exc}")
                skill_map[ref] = None
                return None
            warnings.append(f"created skill '{created.name}' from the document")
            skill_map[ref] = created.id
        else:
            warnings.append(f"skill '{spec.get('name')}' not found; dropped")
            skill_map[ref] = None
        return skill_map[ref]

    for n in nodes:
        d = dict(n.get("data") or {})
        for key in ("model_id", "fallback_model_id"):
            if d.get(key):
                d[key] = map_model(str(d[key]))
        if d.get("skill_ids"):
            d["skill_ids"] = [sid for sid in (map_skill(str(s)) for s in d["skill_ids"]) if sid]
        n["data"] = d

    body = FlowCreate(
        name=str(doc.get("name") or "Imported flow"),
        description=str(doc.get("description") or ""),
        nodes=nodes,
        edges=doc["edges"],
        input_schema=doc.get("input_schema") or [],
        sandbox=doc.get("sandbox") or None,
    )
    try:
        validate_graph(body.nodes, body.edges)
    except ValueError as exc:
        raise FlowImportError(str(exc)) from exc

    dumped_nodes = [x.model_dump(mode="json") for x in body.nodes]
    dumped_edges = [x.model_dump(mode="json") for x in body.edges]
    dumped_schema = [x.model_dump(mode="json") for x in body.input_schema]
    sandbox = body.sandbox.model_dump(exclude_none=True) if body.sandbox else None
    if into is None:
        flow = Flow(
            user_id=user.id,
            name=body.name,
            description=body.description,
            nodes=dumped_nodes,
            edges=dumped_edges,
            input_schema=dumped_schema,
            sandbox=sandbox,
        )
        db.add(flow)
        db.flush()
    else:
        flow = into
        flow.name, flow.description = body.name, body.description
        flow.nodes, flow.edges, flow.input_schema, flow.sandbox = (
            dumped_nodes,
            dumped_edges,
            dumped_schema,
            sandbox,
        )
        for old in db.scalars(select(Rule).where(Rule.flow_id == flow.id)).all():
            db.delete(old)
        db.flush()

    # ---- rules
    step_ids = {n["id"] for n in dumped_nodes}
    for r in doc.get("rules") or []:
        if not isinstance(r, dict) or not r.get("name"):
            continue
        scope = r.get("scope") if r.get("scope") in ("flow", "step") else "flow"
        step_id = r.get("step_id") if scope == "step" else None
        if scope == "step" and step_id not in step_ids:
            warnings.append(f"rule '{r['name']}' targets unknown step '{step_id}'; dropped")
            continue
        db.add(
            Rule(
                user_id=user.id,
                name=str(r["name"])[:100],
                content=str(r.get("content") or ""),
                scope=scope,
                flow_id=flow.id,
                step_id=step_id,
                priority=int(r.get("priority") or 100),
                enabled=bool(r.get("enabled", True)),
            )
        )
    db.commit()
    db.refresh(flow)
    return flow, warnings


# ---------------------------------------------------------------- templates


def skills_by_name(
    db: Session, user_id: str | None, nodes: list[dict[str, Any]]
) -> dict[str, list[str]]:
    """``{node_id: [skill names]}`` for the skills referenced by ``nodes``."""
    out: dict[str, list[str]] = {}
    for n in nodes:
        ids = list((n.get("data") or {}).get("skill_ids") or [])
        if not ids:
            continue
        found, _ = skillsvc.resolve_skills(db, user_id, ids)
        if found:
            out[n["id"]] = [s.name for s in found]
    return out


def attach_template_skills(
    db: Session, user_id: str, nodes: list[dict[str, Any]], names: dict[str, list[str]]
) -> tuple[list[dict[str, Any]], list[str]]:
    """Replace ``skill_ids`` on ``nodes`` with ids resolved from ``names``."""
    warnings: list[str] = []
    out = []
    for n in nodes:
        n = dict(n)
        d = dict(n.get("data") or {})
        wanted = names.get(n["id"]) or []
        if wanted:
            ids, missing = resolve_skill_names(db, user_id, wanted)
            d["skill_ids"] = ids
            warnings += [f"skill '{m}' not found for step {n['id']}" for m in missing]
        else:
            d["skill_ids"] = [] if "skill_ids" in d else d.get("skill_ids", [])
        n["data"] = d
        out.append(n)
    return out, warnings


def create_flow_rules(db: Session, user_id: str, flow_id: str, rules: list[dict[str, Any]]) -> None:
    for r in rules:
        scope = r.get("scope") if r.get("scope") in ("flow", "step") else "flow"
        db.add(
            Rule(
                user_id=user_id,
                name=str(r.get("name") or "Rule")[:100],
                content=str(r.get("content") or ""),
                scope=scope,
                flow_id=flow_id,
                step_id=r.get("step_id") if scope == "step" else None,
                priority=int(r.get("priority") or 100),
                enabled=bool(r.get("enabled", True)),
            )
        )


def flow_rules_spec(db: Session, user_id: str, flow_id: str) -> list[dict[str, Any]]:
    rows = db.scalars(select(Rule).where(Rule.user_id == user_id, Rule.flow_id == flow_id)).all()
    return [
        {
            "name": r.name,
            "content": r.content,
            "scope": r.scope,
            "step_id": r.step_id,
            "priority": r.priority,
            "enabled": r.enabled,
        }
        for r in rows
    ]
