"""Knowledge base + AI settings API (``/biz/kb``, ``/biz/ai``)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.kb import service as kb
from app.kb.models import AiSettings, DocKind, KbDocument
from app.schemas import UtcDateTime
from app.workspace import OwnerWorkspace

router = APIRouter(prefix="/biz", tags=["kb"])
DbSession = Annotated[Session, Depends(get_db)]


class DocumentOut(BaseModel):
    id: str
    title: str
    kind: DocKind
    kind_label: str
    filename: str
    size: int
    status: str
    error: str | None
    chunk_count: int
    embedder: str
    preview: str
    created_at: UtcDateTime


class TextDocIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    kind: DocKind = DocKind.other
    text: str = Field(min_length=1, max_length=kb.MAX_TEXT_CHARS)


class AiSettingsIn(BaseModel):
    forbidden: list[str] | None = Field(default=None, max_length=50)
    address_self: str | None = Field(default=None, min_length=1, max_length=40)
    address_customer: str | None = Field(default=None, min_length=1, max_length=40)
    friendliness: int | None = Field(default=None, ge=1, le=3)
    style_examples: list[str] | None = Field(default=None, max_length=20)
    medical_guard: bool | None = None
    handoff_keywords: list[str] | None = Field(default=None, max_length=50)
    handoff_message: str | None = Field(default=None, max_length=1000)


class AiSettingsOut(BaseModel):
    forbidden: list[str]
    address_self: str
    address_customer: str
    friendliness: int
    style_examples: list[str]
    medical_guard: bool
    handoff_keywords: list[str]
    handoff_message: str
    handoff_preview: str
    guard_categories: list[str]
    updated_at: UtcDateTime


class AskIn(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    k: int = Field(default=4, ge=1, le=10)


def _doc_out(d: KbDocument) -> DocumentOut:
    return DocumentOut(
        id=d.id,
        title=d.title,
        kind=d.kind,
        kind_label=kb.KIND_LABEL.get(d.kind, d.kind.value),
        filename=d.filename,
        size=d.size,
        status=d.status.value,
        error=d.error,
        chunk_count=d.chunk_count,
        embedder=d.embedder,
        preview=(d.text or "")[:240],
        created_at=d.created_at,
    )


def _settings_out(s: AiSettings) -> AiSettingsOut:
    from app.kb.guard import CATEGORIES

    return AiSettingsOut(
        forbidden=list(s.forbidden or []),
        address_self=s.address_self,
        address_customer=s.address_customer,
        friendliness=s.friendliness,
        style_examples=list(s.style_examples or []),
        medical_guard=bool(s.medical_guard),
        handoff_keywords=list(s.handoff_keywords or []),
        handoff_message=s.handoff_message,
        handoff_preview=kb.handoff_text(s),
        guard_categories=list(CATEGORIES),
        updated_at=s.updated_at,
    )


# ---------------------------------------------------------------- documents


@router.get("/kb/documents", response_model=list[DocumentOut])
def list_documents(ctx: OwnerWorkspace, db: DbSession) -> list[DocumentOut]:
    rows = db.scalars(
        select(KbDocument)
        .where(KbDocument.workspace_id == ctx.id)
        .order_by(KbDocument.created_at.desc())
    ).all()
    return [_doc_out(d) for d in rows]


@router.post("/kb/documents", response_model=DocumentOut, status_code=status.HTTP_201_CREATED)
async def upload_document(
    file: UploadFile,
    ctx: OwnerWorkspace,
    db: DbSession,
    kind: DocKind = DocKind.other,
    title: str = "",
) -> DocumentOut:
    """Upload PDF / DOCX / XLSX / CSV / TXT; text is extracted, chunked and embedded."""
    data = await file.read()
    if not data:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "file is empty")
    try:
        doc = await kb.ingest(
            db,
            ctx.id,
            title=title or (file.filename or "Tài liệu"),
            kind=kind,
            filename=file.filename or "",
            data=data,
            content_type=file.content_type or "",
        )
    except kb.KbError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    return _doc_out(doc)


@router.post("/kb/documents/text", response_model=DocumentOut, status_code=status.HTTP_201_CREATED)
async def add_text_document(body: TextDocIn, ctx: OwnerWorkspace, db: DbSession) -> DocumentOut:
    """Paste text directly (FAQ, rules) without a file."""
    doc = await kb.ingest(
        db,
        ctx.id,
        title=body.title,
        kind=body.kind,
        filename=f"{body.title[:40]}.txt",
        data=body.text.encode(),
        content_type="text/plain",
    )
    return _doc_out(doc)


@router.delete("/kb/documents/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(doc_id: str, ctx: OwnerWorkspace, db: DbSession) -> None:
    d = db.get(KbDocument, doc_id)
    if d is None or d.workspace_id != ctx.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "document not found")
    db.delete(d)
    db.commit()


@router.get("/kb/documents/{doc_id}/text")
def document_text(doc_id: str, ctx: OwnerWorkspace, db: DbSession) -> dict[str, Any]:
    d = db.get(KbDocument, doc_id)
    if d is None or d.workspace_id != ctx.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "document not found")
    return {"id": d.id, "title": d.title, "text": d.text, "chunks": [c.text for c in d.chunks]}


@router.post("/kb/search")
async def search_kb(body: AskIn, ctx: OwnerWorkspace, db: DbSession) -> dict[str, Any]:
    hits = await kb.search(db, ctx.id, body.question, k=body.k)
    return {"hits": [h.__dict__ for h in hits]}


@router.post("/kb/preview")
async def preview_answer_context(body: AskIn, ctx: OwnerWorkspace, db: DbSession) -> dict[str, Any]:
    """What the AI would see for a question: guard decision, retrieved chunks, system prompt."""
    return await kb.preview(db, ctx.workspace, body.question, k=body.k)


# ---------------------------------------------------------------- AI settings


@router.get("/ai/settings", response_model=AiSettingsOut)
def get_ai_settings(ctx: OwnerWorkspace, db: DbSession) -> AiSettingsOut:
    return _settings_out(kb.get_settings_row(db, ctx.id))


@router.put("/ai/settings", response_model=AiSettingsOut)
def update_ai_settings(body: AiSettingsIn, ctx: OwnerWorkspace, db: DbSession) -> AiSettingsOut:
    s = kb.get_settings_row(db, ctx.id)
    data = body.model_dump(exclude_none=True)
    for key in ("forbidden", "style_examples", "handoff_keywords"):
        if key in data:
            data[key] = [str(x).strip()[:300] for x in data[key] if str(x).strip()]
    for k, v in data.items():
        setattr(s, k, v)
    db.commit()
    db.refresh(s)
    return _settings_out(s)
