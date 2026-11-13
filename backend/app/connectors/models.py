"""Connector table: one row per (workspace, kind) with encrypted secrets."""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, Enum, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def _uuid() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.now(UTC)


class ConnectorKind(str, enum.Enum):
    zalo = "zalo"
    facebook = "facebook"
    google_calendar = "google_calendar"


class ConnectorStatus(str, enum.Enum):
    disconnected = "disconnected"
    connected = "connected"
    error = "error"


class Connector(Base):
    __tablename__ = "biz_connectors"
    __table_args__ = (UniqueConstraint("workspace_id", "kind", name="uq_connector_kind"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[ConnectorKind] = mapped_column(Enum(ConnectorKind, native_enum=False))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    #: Non-secret settings: oa_id / page_id / calendar_id, zns_templates [...], display names.
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    #: Fernet-encrypted JSON of tokens/secrets (access_token, refresh_token, app_secret, ...).
    secrets_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Random token in the webhook URL / Meta verify token.
    webhook_token: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[ConnectorStatus] = mapped_column(
        Enum(ConnectorStatus, native_enum=False), default=ConnectorStatus.disconnected
    )
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Last inbound webhook event time (so the UI can show "đang nhận tin").
    last_event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
