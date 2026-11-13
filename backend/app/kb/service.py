"""Ingest documents, retrieve relevant chunks, compose the assistant's system prompt."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.biz.models import Service
from app.config import get_settings
from app.kb import guard
from app.kb.embed import Embedder, LexicalEmbedder, OpenAIEmbedder, cosine, tokens
from app.kb.models import AiSettings, DocKind, DocStatus, KbChunk, KbDocument
from app.models import KeyProvider, Workspace, WorkspaceMember
from app.services.keys import get_user_key
from app.tools.files import extract_text as _extract

CHUNK_CHARS = 900
CHUNK_OVERLAP = 120
MAX_DOC_BYTES = 10 * 1024 * 1024
MAX_TEXT_CHARS = 400_000
SUPPORTED_EXT = {"pdf", "docx", "xlsx", "csv", "txt", "md", "markdown"}

KIND_LABEL = {
    DocKind.price: "Bảng giá",
    DocKind.package: "Mô tả gói / dịch vụ",
    DocKind.rules: "Nội quy",
    DocKind.faq: "Câu hỏi thường gặp",
    DocKind.other: "Tài liệu khác",
}


class KbError(ValueError):
    pass


# ---------------------------------------------------------------- text + chunks


def extract_text(filename: str, data: bytes, content_type: str = "") -> str:
    name = (filename or "").lower()
    ext = name.rsplit(".", 1)[-1] if "." in name else ""
    if ext in ("txt", "md", "markdown") or content_type.startswith("text/plain") or not ext:
        return data.decode("utf-8", errors="replace")
    if ext in ("pdf", "docx", "csv", "xlsx"):
        try:
            return _extract(data, ext, max_rows=2000)
        except Exception as exc:
            raise KbError(f"không đọc được file {ext.upper()}: {exc}") from exc
    raise KbError("chỉ nhận PDF, DOCX, XLSX, CSV, TXT hoặc Markdown")


def chunk_text(text: str, size: int = CHUNK_CHARS, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Paragraph-aware windows of ~``size`` chars with ``overlap`` carried over."""
    text = re.sub(r"\r\n?", "\n", text).strip()
    if not text:
        return []
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    buf = ""
    for p in paras:
        while len(p) > size:  # very long paragraph → hard split on sentence boundaries if possible
            cut = p.rfind(". ", 0, size)
            cut = cut + 1 if cut > size // 2 else size
            piece, p = p[:cut].strip(), p[cut:].strip()
            if buf:
                chunks.append(buf)
                buf = ""
            chunks.append(piece)
        if len(buf) + len(p) + 1 <= size:
            buf = f"{buf}\n{p}".strip() if buf else p
        else:
            if buf:
                chunks.append(buf)
            buf = (chunks[-1][-overlap:].strip() + "\n" + p).strip() if chunks and overlap else p
    if buf:
        chunks.append(buf)
    return chunks


def pick_embedder(db: Session, workspace_id: str) -> Embedder:
    """OpenAI embeddings when allowed and a key exists (owner's key, else server key), else lexical."""
    settings = get_settings()
    if settings.kb_embeddings == "openai":
        owner = db.scalar(
            select(WorkspaceMember.user_id).where(
                WorkspaceMember.workspace_id == workspace_id, WorkspaceMember.role == "owner"
            )
        )
        key = (
            get_user_key(db, owner, KeyProvider.openai) if owner else None
        ) or settings.openai_api_key
        if key:
            return OpenAIEmbedder(key)
    return LexicalEmbedder()


async def ingest(
    db: Session,
    workspace_id: str,
    *,
    title: str,
    kind: DocKind,
    filename: str,
    data: bytes,
    content_type: str = "",
) -> KbDocument:
    if len(data) > MAX_DOC_BYTES:
        raise KbError("file quá 10 MB")
    ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    if ext and ext not in SUPPORTED_EXT:
        raise KbError("chỉ nhận PDF, DOCX, XLSX, CSV, TXT hoặc Markdown")
    doc = KbDocument(
        workspace_id=workspace_id,
        title=title.strip() or filename or "Tài liệu",
        kind=kind,
        filename=filename,
        content_type=content_type or "text/plain",
        size=len(data),
    )
    db.add(doc)
    db.flush()
    try:
        text = extract_text(filename, data, content_type)[:MAX_TEXT_CHARS]
        if not text.strip():
            raise KbError("file không có nội dung chữ (ảnh scan cần OCR trước)")
        pieces = chunk_text(text)
        embedder = pick_embedder(db, workspace_id)
        vectors = await embedder.embed(pieces)
        doc.text = text
        doc.embedder = embedder.name
        doc.chunk_count = len(pieces)
        doc.status = DocStatus.ready
        for i, (piece, vec) in enumerate(zip(pieces, vectors, strict=True)):
            db.add(
                KbChunk(
                    workspace_id=workspace_id,
                    document_id=doc.id,
                    ordinal=i,
                    text=piece,
                    embedding=vec,
                )
            )
    except (KbError, RuntimeError) as exc:
        doc.status = DocStatus.error
        doc.error = str(exc)
    db.commit()
    db.refresh(doc)
    return doc


# ---------------------------------------------------------------- retrieval


@dataclass
class Hit:
    chunk_id: str
    document_id: str
    title: str
    kind: str
    ordinal: int
    text: str
    score: float


async def search(db: Session, workspace_id: str, query: str, k: int = 5) -> list[Hit]:
    """Hybrid: cosine on the stored vectors (per embedder) + lexical overlap bonus."""
    rows = db.execute(
        select(KbChunk, KbDocument)
        .join(KbDocument, KbDocument.id == KbChunk.document_id)
        .where(KbChunk.workspace_id == workspace_id, KbDocument.status == DocStatus.ready)
    ).all()
    if not rows or not query.strip():
        return []
    by_embedder: dict[str, list[tuple[KbChunk, KbDocument]]] = {}
    for chunk, doc in rows:
        by_embedder.setdefault(doc.embedder, []).append((chunk, doc))
    q_tokens = set(tokens(query))
    hits: list[Hit] = []
    for name, group in by_embedder.items():
        embedder: Embedder = (
            pick_embedder(db, workspace_id) if name.startswith("openai") else LexicalEmbedder()
        )
        if (
            embedder.name != name
        ):  # key disappeared → cannot embed the query the same way; lexical only
            embedder = LexicalEmbedder()
            qvec = None
        else:
            qvec = (await embedder.embed([query]))[0]
        for chunk, doc in group:
            dense = cosine(qvec, chunk.embedding or []) if qvec and chunk.embedding else 0.0
            c_tokens = set(tokens(chunk.text))
            lexical = len(q_tokens & c_tokens) / (len(q_tokens) or 1)
            score = 0.7 * dense + 0.3 * lexical if qvec else lexical
            if score > 0.02:
                hits.append(
                    Hit(
                        chunk.id,
                        doc.id,
                        doc.title,
                        doc.kind.value,
                        chunk.ordinal,
                        chunk.text,
                        round(score, 4),
                    )
                )
    hits.sort(key=lambda h: h.score, reverse=True)
    return hits[:k]


# ---------------------------------------------------------------- settings + prompt


def get_settings_row(db: Session, workspace_id: str) -> AiSettings:
    row = db.scalar(select(AiSettings).where(AiSettings.workspace_id == workspace_id))
    if row is None:
        row = AiSettings(workspace_id=workspace_id)
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


FRIENDLINESS = {
    1: "Giọng trang trọng, lịch sự, câu đầy đủ, không dùng emoji.",
    2: "Giọng thân thiện, gần gũi nhưng chuyên nghiệp; có thể dùng 1 emoji khi phù hợp.",
    3: "Giọng rất thoải mái, trẻ trung, dùng emoji tự nhiên, câu ngắn.",
}


def handoff_text(s: AiSettings) -> str:
    return (
        (s.handoff_message or "")
        .replace("{self}", s.address_self)
        .replace("{customer}", s.address_customer)
    )


def services_block(db: Session, workspace_id: str) -> str:
    rows = db.scalars(
        select(Service)
        .where(Service.workspace_id == workspace_id, Service.active.is_(True))
        .order_by(Service.category, Service.name)
    ).all()
    if not rows:
        return ""
    lines = []
    for s in rows:
        bits = [f"{int(s.price):,}đ".replace(",", ".")]
        if s.sessions:
            bits.append(f"{s.sessions} buổi")
        if s.validity_days:
            bits.append(f"hiệu lực {s.validity_days} ngày")
        if s.duration_min:
            bits.append(f"{s.duration_min} phút/buổi")
        line = f"- {s.name}: {', '.join(bits)}"
        if s.description:
            line += f". {s.description.strip()}"
        if s.sale_notes:
            line += f" [Ghi chú nội bộ cho AI, không nói với khách: {s.sale_notes.strip()}]"
        lines.append(line)
    return "\n".join(lines)


def compose_system_prompt(
    ws: Workspace, s: AiSettings, *, services: str = "", context_chunks: list[Hit] | None = None
) -> str:
    """Rules → tone → business facts → retrieved knowledge; used by the industry flows."""
    branches = ", ".join(
        f"{b.get('name')}{' (' + b['address'] + ')' if b.get('address') else ''}"
        for b in (ws.branches or [])
    )
    parts = [
        f"Bạn là trợ lý chăm sóc khách hàng của {ws.name} ({'phòng gym' if ws.industry == 'gym' else 'spa' if ws.industry == 'spa' else 'gym & spa'}). "
        f'Trả lời bằng tiếng Việt, ngắn gọn, đúng trọng tâm, xưng "{s.address_self}" và gọi khách là "{s.address_customer}". '
        + FRIENDLINESS.get(s.friendliness, FRIENDLINESS[2]),
        (
            "# Quy tắc bắt buộc\n"
            "- Chỉ dùng thông tin trong phần Kiến thức và Dịch vụ bên dưới; không bịa giá, lịch, chính sách. Thiếu thông tin thì nói sẽ hỏi lại nhân viên.\n"
            "- Không tư vấn y tế, chấn thương, thuốc, thực phẩm chức năng hay dinh dưỡng chuyên sâu; những câu này trả lời bằng câu chuyển nhân viên và dừng lại.\n"
            "- Không tiết lộ ghi chú nội bộ. Không hứa điều không có trong tài liệu."
        ),
    ]
    if s.forbidden:
        parts.append("# Điều cấm (chủ cơ sở đặt)\n" + "\n".join(f"- {x}" for x in s.forbidden))
    parts.append(f"# Câu chuyển nhân viên\n{handoff_text(s)}")
    if s.style_examples:
        parts.append(
            "# Ví dụ văn phong (bắt chước giọng này)\n"
            + "\n".join(f"- {x}" for x in s.style_examples)
        )
    facts = f"Chi nhánh: {branches or 'chưa khai báo'}. Múi giờ: {ws.timezone}."
    parts.append("# Thông tin cơ sở\n" + facts)
    if services:
        parts.append("# Dịch vụ và giá\n" + services)
    if context_chunks:
        parts.append(
            "# Kiến thức liên quan (trích từ tài liệu của cơ sở)\n"
            + "\n\n".join(
                f"[{h.title} · {KIND_LABEL.get(DocKind(h.kind), h.kind)}]\n{h.text}"
                for h in context_chunks
            )
        )
    return "\n\n".join(parts)


async def preview(db: Session, ws: Workspace, question: str, k: int = 4) -> dict[str, Any]:
    """Everything the flow will use for one question: guard decision, hits and the prompt."""
    s = get_settings_row(db, ws.id)
    decision = guard.check(question, list(s.handoff_keywords or []), enabled=bool(s.medical_guard))
    hits = await search(db, ws.id, question, k=k) if question.strip() else []
    prompt = compose_system_prompt(ws, s, services=services_block(db, ws.id), context_chunks=hits)
    return {
        "guard": {
            "handoff": decision.handoff,
            "category": decision.category,
            "matched": decision.matched,
            "reply": handoff_text(s) if decision.handoff else None,
        },
        "hits": [h.__dict__ for h in hits],
        "system_prompt": prompt,
    }
