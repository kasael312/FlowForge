"""Knowledge base + AI settings tables (workspace scoped)."""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def _uuid() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.now(UTC)


class DocKind(str, enum.Enum):
    price = "price"  # bảng giá
    package = "package"  # mô tả gói
    rules = "rules"  # nội quy
    faq = "faq"
    other = "other"


class DocStatus(str, enum.Enum):
    ready = "ready"
    error = "error"


class KbDocument(Base):
    __tablename__ = "kb_documents"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[DocKind] = mapped_column(Enum(DocKind, native_enum=False), default=DocKind.other)
    filename: Mapped[str] = mapped_column(String(255), default="")
    content_type: Mapped[str] = mapped_column(String(100), default="text/plain")
    size: Mapped[int] = mapped_column(Integer, default=0)
    #: Extracted plain text (what the chunks were made from).
    text: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[DocStatus] = mapped_column(
        Enum(DocStatus, native_enum=False), default=DocStatus.ready
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    #: Name of the embedder used (``lexical`` or ``openai:<model>``).
    embedder: Mapped[str] = mapped_column(String(64), default="lexical")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    chunks: Mapped[list[KbChunk]] = relationship(
        back_populates="document", cascade="all, delete-orphan", order_by="KbChunk.ordinal"
    )


class KbChunk(Base):
    __tablename__ = "kb_chunks"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    document_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("kb_documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer, default=0)
    text: Mapped[str] = mapped_column(Text, default="")
    #: Dense vector (list of floats), produced by the document's embedder.
    embedding: Mapped[list | None] = mapped_column(JSON, nullable=True)

    document: Mapped[KbDocument] = relationship(back_populates="chunks")


class AiSettings(Base):
    """How the assistant talks and what it must never do (one row per workspace)."""

    __tablename__ = "kb_ai_settings"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    #: Owner-written prohibitions ("không giảm giá", "không hứa giữ chỗ"...).
    forbidden: Mapped[list] = mapped_column(JSON, default=list)
    #: How the assistant refers to itself: em | mình | phòng tập | spa | tên cơ sở.
    address_self: Mapped[str] = mapped_column(String(40), default="em")
    #: How it addresses the customer: anh/chị | bạn | quý khách.
    address_customer: Mapped[str] = mapped_column(String(40), default="anh/chị")
    #: 1 = formal, 2 = friendly (default), 3 = very casual with emoji.
    friendliness: Mapped[int] = mapped_column(Integer, default=2)
    #: Example replies written by the owner so the model copies the voice.
    style_examples: Mapped[list] = mapped_column(JSON, default=list)
    medical_guard: Mapped[bool] = mapped_column(Boolean, default=True)
    #: Extra owner-defined keywords that force a handoff to staff.
    handoff_keywords: Mapped[list] = mapped_column(JSON, default=list)
    handoff_message: Mapped[str] = mapped_column(
        Text,
        default=(
            "Dạ câu này {self} xin phép chuyển cho nhân viên chuyên môn để tư vấn kỹ hơn ạ. "
            "{customer} chờ {self} một chút nhé!"
        ),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
