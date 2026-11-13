"""Skills: reusable instruction packages (SKILL.md + reference files) attached to LLM steps.

A skill lives in the ``skills`` table; its reference files are kept inline
(base64) so the DB stays the single source of truth. At run time the skill is
*materialized* to ``<skills_dir>/<id>/`` (a plain directory with ``SKILL.md``
and the files) and that directory is copied into the sandbox at
``/skills/<name>/`` so the agent can read it.
"""

from __future__ import annotations

import base64
import io
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import Skill

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")
MAX_FILE_BYTES = 1 * 1024 * 1024
MAX_TOTAL_BYTES = 5 * 1024 * 1024
MAX_FILES = 40
SKILLS_MOUNT = "/skills"


class SkillError(Exception):
    """Invalid skill content, file or archive."""


# ---------------------------------------------------------------- SKILL.md front matter


def parse_skill_md(text: str) -> tuple[dict[str, Any], str]:
    """Split ``SKILL.md`` into ``(frontmatter, body)``.

    The front matter is a tiny YAML subset: ``key: value`` lines plus
    ``tools_required: [a, b]`` or a ``- item`` list. Anything else is kept verbatim
    in the body. Missing front matter is fine.
    """
    text = text.replace("\r\n", "\n")
    if not text.startswith("---\n"):
        return {}, text.strip("\n")
    end = text.find("\n---", 4)
    if end < 0:
        return {}, text.strip("\n")
    head, body = text[4:end], text[end + 4 :]
    meta: dict[str, Any] = {}
    current_list: str | None = None
    for raw in head.split("\n"):
        line = raw.rstrip()
        if not line.strip():
            continue
        if current_list and line.lstrip().startswith("- "):
            meta.setdefault(current_list, []).append(line.lstrip()[2:].strip().strip("'\""))
            continue
        current_list = None
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key, value = key.strip(), value.strip()
        if value.startswith("[") and value.endswith("]"):
            meta[key] = [v.strip().strip("'\"") for v in value[1:-1].split(",") if v.strip()]
        elif value == "":
            current_list = key
            meta[key] = []
        else:
            meta[key] = value.strip("'\"")
    return meta, body.lstrip("\n").rstrip() + ("\n" if body.strip() else "")


KINDS = ("skill", "persona")


def kind_of(value: str | None) -> str:
    return value if value in KINDS else "skill"


def render_skill_md(
    name: str, description: str, tools_required: list[str], body: str, kind: str = "skill"
) -> str:
    """The canonical ``SKILL.md`` text for a skill."""
    tools = ", ".join(tools_required)
    kind_line = f"kind: {kind}\n" if kind_of(kind) != "skill" else ""
    return (
        f"---\nname: {name}\ndescription: {description.strip()}\n{kind_line}tools_required: [{tools}]\n---\n\n"
        + body.strip()
        + "\n"
    )


def slugify(name: str) -> str:
    """Best-effort skill name from free text (``My Zip`` → ``my-zip``)."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug[:63]


def validate_name(name: str) -> str:
    name = name.strip().lower()
    if not NAME_RE.match(name):
        raise SkillError("name must be 2-63 chars of a-z, 0-9 and '-' (e.g. python-testing)")
    return name


def safe_rel_path(path: str) -> str:
    """Normalise a file path inside a skill; reject escapes and SKILL.md."""
    p = PurePosixPath(path.replace("\\", "/").strip("/"))
    if not p.parts or any(part in ("..", "") for part in p.parts) or p.is_absolute():
        raise SkillError(f"invalid file path: {path!r}")
    if any(len(part) > 120 for part in p.parts) or len(p.parts) > 6:
        raise SkillError(f"file path too long: {path!r}")
    if str(p) == "SKILL.md":
        raise SkillError("SKILL.md is the skill body itself; edit `content` instead")
    return str(p)


# ---------------------------------------------------------------- files (inline base64)


SAFE_CONTENT_TYPES = {
    "md": "text/markdown",
    "txt": "text/plain",
    "py": "text/x-python",
    "ts": "text/plain",
    "tsx": "text/plain",
    "js": "text/javascript",
    "json": "application/json",
    "yaml": "text/yaml",
    "yml": "text/yaml",
    "sh": "text/x-shellscript",
    "csv": "text/csv",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "pdf": "application/pdf",
}


def guess_content_type(path: str) -> str:
    """Content type from the extension only (never trusted from documents/uploads)."""
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    return SAFE_CONTENT_TYPES.get(ext, "application/octet-stream")


def file_entries(skill: Skill) -> list[dict[str, Any]]:
    return list(skill.files or [])


def put_file(skill: Skill, path: str, data: bytes, content_type: str = "text/plain") -> None:
    """Add or replace a reference file."""
    rel = safe_rel_path(path)
    if len(data) > MAX_FILE_BYTES:
        raise SkillError("file exceeds 1 MB")
    files = [f for f in file_entries(skill) if f["path"] != rel]
    if len(files) >= MAX_FILES:
        raise SkillError(f"a skill can hold at most {MAX_FILES} files")
    total = sum(int(f.get("size", 0)) for f in files) + len(data)
    if total > MAX_TOTAL_BYTES:
        raise SkillError("skill files exceed 5 MB in total")
    files.append(
        {
            "path": rel,
            "size": len(data),
            "content_type": content_type or "application/octet-stream",
            "data": base64.b64encode(data).decode("ascii"),
        }
    )
    files.sort(key=lambda f: f["path"])
    skill.files = files


def remove_file(skill: Skill, path: str) -> bool:
    rel = safe_rel_path(path)
    before = file_entries(skill)
    after = [f for f in before if f["path"] != rel]
    skill.files = after
    return len(after) != len(before)


def file_bytes(skill: Skill, path: str) -> tuple[dict[str, Any], bytes]:
    rel = safe_rel_path(path)
    for f in file_entries(skill):
        if f["path"] == rel:
            return f, base64.b64decode(f.get("data", ""))
    raise SkillError(f"file not found: {rel}")


# ---------------------------------------------------------------- zip import / export


def export_zip(skill: Skill) -> bytes:
    """``<name>/SKILL.md`` + files, as a zip archive."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            f"{skill.name}/SKILL.md",
            render_skill_md(
                skill.name,
                skill.description,
                skill.tools_required or [],
                skill.content,
                kind_of(skill.kind),
            ),
        )
        for f in file_entries(skill):
            zf.writestr(f"{skill.name}/{f['path']}", base64.b64decode(f.get("data", "")))
    return buf.getvalue()


@dataclass
class ImportedSkill:
    name: str
    description: str
    tools_required: list[str]
    content: str
    kind: str = "skill"
    files: list[tuple[str, bytes]] = field(default_factory=list)


def import_zip(data: bytes, fallback_name: str = "") -> ImportedSkill:
    """Read a skill folder from a zip (``SKILL.md`` at the root or one level down)."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise SkillError("not a zip archive") from exc
    names = [n for n in zf.namelist() if not n.endswith("/") and "__MACOSX" not in n]
    md = [n for n in names if PurePosixPath(n).name == "SKILL.md"]
    if not md:
        raise SkillError("archive has no SKILL.md")
    md.sort(key=lambda n: len(PurePosixPath(n).parts))
    root = str(PurePosixPath(md[0]).parent)
    prefix = "" if root == "." else root + "/"
    meta, body = parse_skill_md(zf.read(md[0]).decode("utf-8", errors="replace"))
    name = str(meta.get("name") or (root if root != "." else "") or fallback_name or "")
    name = validate_name(slugify(name.rsplit("/", 1)[-1]))
    tools = meta.get("tools_required") or []
    if isinstance(tools, str):
        tools = [t.strip() for t in tools.split(",") if t.strip()]
    files: list[tuple[str, bytes]] = []
    total = 0
    for n in names:
        if n == md[0] or not n.startswith(prefix):
            continue
        rel = n[len(prefix) :]
        if not rel or rel.startswith("."):
            continue
        info = zf.getinfo(n)
        if info.file_size > MAX_FILE_BYTES:
            raise SkillError(f"{rel} exceeds 1 MB")
        total += info.file_size
        if total > MAX_TOTAL_BYTES or len(files) >= MAX_FILES:
            raise SkillError("archive too large (5 MB / 40 files)")
        files.append((safe_rel_path(rel), zf.read(n)))
    return ImportedSkill(
        name=name,
        description=str(meta.get("description") or ""),
        tools_required=[str(t) for t in tools],
        content=body,
        kind=kind_of(str(meta.get("kind") or "skill")),
        files=files,
    )


# ---------------------------------------------------------------- lookup + materialize


def visible_skills(db: Session, user_id: str | None) -> list[Skill]:
    """Built-in skills followed by the user's own, by name."""
    cond = (
        Skill.user_id.is_(None)
        if user_id is None
        else or_(Skill.user_id.is_(None), Skill.user_id == user_id)
    )
    return list(
        db.scalars(select(Skill).where(cond).order_by(Skill.user_id.is_not(None), Skill.name)).all()
    )


def resolve_skills(
    db: Session, user_id: str | None, ids: list[str]
) -> tuple[list[Skill], list[str]]:
    """Skills visible to ``user_id`` in the order of ``ids``; unknown ids are reported."""
    if not ids:
        return [], []
    by_id = {s.id: s for s in visible_skills(db, user_id)}
    found = [by_id[i] for i in ids if i in by_id]
    missing = [i for i in ids if i not in by_id]
    return found, missing


@dataclass
class SkillBundle:
    """What the executor needs: prompt text plus files to mount."""

    id: str
    name: str
    description: str
    tools_required: list[str]
    content: str
    files: list[tuple[str, bytes]]
    kind: str = "skill"

    @property
    def mount_path(self) -> str:
        return f"{SKILLS_MOUNT}/{self.name}"


def bundle(skill: Skill) -> SkillBundle:
    return SkillBundle(
        id=skill.id,
        name=skill.name,
        description=skill.description or "",
        tools_required=list(skill.tools_required or []),
        content=skill.content or "",
        files=[(f["path"], base64.b64decode(f.get("data", ""))) for f in file_entries(skill)],
        kind=kind_of(skill.kind),
    )


def materialize(skill: Skill, skills_dir: str | Path) -> Path:
    """Write the skill to ``<skills_dir>/<id>/`` (SKILL.md + files) and return that path.

    Rewritten on every call so the cache never goes stale; cheap for the sizes allowed.
    """
    root = Path(skills_dir) / skill.id
    if root.exists():
        for p in sorted(root.rglob("*"), reverse=True):
            p.unlink() if p.is_file() else p.rmdir()
    root.mkdir(parents=True, exist_ok=True)
    (root / "SKILL.md").write_text(
        render_skill_md(
            skill.name,
            skill.description,
            skill.tools_required or [],
            skill.content,
            kind_of(skill.kind),
        ),
        encoding="utf-8",
    )
    for f in file_entries(skill):
        dest = root / f["path"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(base64.b64decode(f.get("data", "")))
    return root


def skills_prompt_block(bundles: list[SkillBundle]) -> str:
    """Prompt text for a step's personas and skills (personas first)."""
    personas = [b for b in bundles if b.kind == "persona"]
    skills = [b for b in bundles if b.kind != "persona"]
    parts: list[str] = []
    if personas:
        parts.append("# Persona\nAdopt this role, voice and standards for the whole step.")
        for b in personas:
            parts.append(
                f"## Persona: {b.name}\n{b.description.strip()}\n\n{b.content.strip()}".strip()
            )
    if skills:
        parts.append(
            "# Skills\nFollow these skills. Their reference files are mounted read-only under /skills/<name>/."
        )
        for b in skills:
            files = ", ".join(f"{b.mount_path}/{p}" for p, _ in b.files)
            header = f"## Skill: {b.name}"
            if b.description:
                header += f"\n{b.description.strip()}"
            if files:
                header += f"\nFiles: {files}"
            parts.append(f"{header}\n\n{b.content.strip()}")
    return "\n\n".join(parts)


def load_bundles(
    db: Session, user_id: str | None, ids: list[str]
) -> tuple[list[SkillBundle], list[str]]:
    skills, missing = resolve_skills(db, user_id, ids)
    return [bundle(s) for s in skills], missing
