"""Skills: built-ins plus the user's own, with reference files and zip import/export."""

from __future__ import annotations

from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import CurrentUser
from app.db import get_db
from app.models import Skill
from app.schemas import SkillCreate, SkillOut, SkillUpdate
from app.services import skills as svc

router = APIRouter(prefix="/skills", tags=["skills"])
DbSession = Annotated[Session, Depends(get_db)]


def _out(s: Skill) -> SkillOut:
    return SkillOut(
        id=s.id,
        name=s.name,
        kind=svc.kind_of(s.kind),  # type: ignore[arg-type]
        description=s.description or "",
        content=s.content or "",
        tools_required=list(s.tools_required or []),
        files=[
            {
                "path": f["path"],
                "size": int(f.get("size", 0)),
                "content_type": f.get("content_type", ""),
            }
            for f in svc.file_entries(s)
        ],
        builtin=s.user_id is None,
        skill_md=svc.render_skill_md(
            s.name,
            s.description or "",
            s.tools_required or [],
            s.content or "",
            svc.kind_of(s.kind),
        ),
        created_at=s.created_at,
        updated_at=s.updated_at,
    )


def _visible(db: Session, user_id: str, skill_id: str) -> Skill:
    s = db.get(Skill, skill_id)
    if s is None or (s.user_id is not None and s.user_id != user_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "skill not found")
    return s


def _mine(db: Session, user_id: str, skill_id: str) -> Skill:
    s = _visible(db, user_id, skill_id)
    if s.user_id is None:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "built-in skills are read-only; duplicate it to edit"
        )
    return s


def _unique_name(db: Session, user_id: str, name: str, exclude_id: str | None = None) -> str:
    try:
        name = svc.validate_name(name)
    except svc.SkillError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    clash = db.scalar(
        select(Skill).where(
            Skill.user_id == user_id, Skill.name == name, Skill.id != (exclude_id or "")
        )
    )
    if clash is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"you already have a skill named {name}")
    return name


def _free_name(db: Session, user_id: str, base: str) -> str:
    """``base``, or ``base-2``, ``base-3``… until it does not clash with my skills."""
    taken = set(db.scalars(select(Skill.name).where(Skill.user_id == user_id)).all())
    if base not in taken:
        return base
    i = 2
    while f"{base[:60]}-{i}" in taken:
        i += 1
    return f"{base[:60]}-{i}"


@router.get("", response_model=list[SkillOut])
def list_skills(user: CurrentUser, db: DbSession) -> list[SkillOut]:
    """Built-in skills first, then mine."""
    return [_out(s) for s in svc.visible_skills(db, user.id)]


@router.post("", response_model=SkillOut, status_code=status.HTTP_201_CREATED)
def create_skill(body: SkillCreate, user: CurrentUser, db: DbSession) -> SkillOut:
    name = _unique_name(db, user.id, body.name)
    s = Skill(
        user_id=user.id,
        name=name,
        description=body.description,
        content=body.content,
        tools_required=body.tools_required,
        kind=body.kind,
        files=[],
        is_builtin=False,
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    return _out(s)


@router.post("/import", response_model=SkillOut, status_code=status.HTTP_201_CREATED)
async def import_skill(file: UploadFile, user: CurrentUser, db: DbSession) -> SkillOut:
    """Create a skill from a zip holding ``SKILL.md`` (+ reference files)."""
    data = await file.read()
    if len(data) > svc.MAX_TOTAL_BYTES * 2:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "archive too large")
    try:
        imported = svc.import_zip(data, fallback_name=(file.filename or "").rsplit(".", 1)[0])
        s = Skill(
            user_id=user.id,
            name=_free_name(db, user.id, imported.name),
            description=imported.description,
            content=imported.content,
            tools_required=imported.tools_required,
            kind=imported.kind,
            files=[],
            is_builtin=False,
        )
        for path, blob in imported.files:
            put_file_guess(s, path, blob)
    except svc.SkillError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    db.add(s)
    db.commit()
    db.refresh(s)
    return _out(s)


def put_file_guess(s: Skill, path: str, blob: bytes) -> None:
    svc.put_file(s, path, blob, svc.guess_content_type(path))


@router.get("/{skill_id}", response_model=SkillOut)
def get_skill(skill_id: str, user: CurrentUser, db: DbSession) -> SkillOut:
    return _out(_visible(db, user.id, skill_id))


@router.put("/{skill_id}", response_model=SkillOut)
def update_skill(skill_id: str, body: SkillUpdate, user: CurrentUser, db: DbSession) -> SkillOut:
    s = _mine(db, user.id, skill_id)
    if body.name is not None:
        s.name = _unique_name(db, user.id, body.name, exclude_id=s.id)
    if body.description is not None:
        s.description = body.description
    if body.content is not None:
        s.content = body.content
    if body.tools_required is not None:
        s.tools_required = body.tools_required
    if body.kind is not None:
        s.kind = body.kind
    db.commit()
    db.refresh(s)
    return _out(s)


@router.delete("/{skill_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_skill(skill_id: str, user: CurrentUser, db: DbSession) -> None:
    s = _mine(db, user.id, skill_id)
    db.delete(s)
    db.commit()


@router.post("/{skill_id}/duplicate", response_model=SkillOut, status_code=status.HTTP_201_CREATED)
def duplicate_skill(skill_id: str, user: CurrentUser, db: DbSession) -> SkillOut:
    """Copy a (built-in or own) skill into my editable skills."""
    src = _visible(db, user.id, skill_id)
    s = Skill(
        user_id=user.id,
        name=_free_name(db, user.id, src.name),
        description=src.description,
        content=src.content,
        tools_required=list(src.tools_required or []),
        kind=svc.kind_of(src.kind),
        files=[dict(f) for f in svc.file_entries(src)],
        is_builtin=False,
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    return _out(s)


@router.get("/{skill_id}/export")
def export_skill(skill_id: str, user: CurrentUser, db: DbSession) -> Response:
    s = _visible(db, user.id, skill_id)
    return Response(
        svc.export_zip(s),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{s.name}.zip"'},
    )


@router.post("/{skill_id}/files", response_model=SkillOut, status_code=status.HTTP_201_CREATED)
async def upload_skill_file(
    skill_id: str, file: UploadFile, user: CurrentUser, db: DbSession, path: str | None = None
) -> SkillOut:
    """Add or replace a reference file (``path`` defaults to the upload's filename)."""
    s = _mine(db, user.id, skill_id)
    data = await file.read()
    try:
        put_file_guess(s, path or file.filename or "file", data)
    except svc.SkillError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    db.commit()
    db.refresh(s)
    return _out(s)


@router.get("/{skill_id}/files/{path:path}")
def download_skill_file(skill_id: str, path: str, user: CurrentUser, db: DbSession) -> Response:
    s = _visible(db, user.id, skill_id)
    try:
        meta, blob = svc.file_bytes(s, path)
    except svc.SkillError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return Response(
        blob,
        media_type=meta.get("content_type") or "application/octet-stream",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(meta['path'].rsplit('/', 1)[-1])}",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.delete("/{skill_id}/files/{path:path}", response_model=SkillOut)
def delete_skill_file(skill_id: str, path: str, user: CurrentUser, db: DbSession) -> SkillOut:
    s = _mine(db, user.id, skill_id)
    try:
        if not svc.remove_file(s, path):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "file not found")
    except svc.SkillError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    db.commit()
    db.refresh(s)
    return _out(s)
