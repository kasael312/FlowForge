"""Storage of files used as run inputs (uploaded via the API or picked up by a watched folder)."""

from __future__ import annotations

import re
import secrets
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Upload

MAX_UPLOAD_BYTES = 100 * 1024 * 1024
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


class UploadError(Exception):
    """Invalid or missing upload."""


def safe_filename(name: str) -> str:
    """Strip directories and odd characters; never empty."""
    base = Path(name or "").name
    base = _SAFE_NAME.sub("_", base).strip("._") or "file"
    return base[:200]


def store_upload(
    db: Session,
    settings: Settings,
    user_id: str,
    name: str,
    data: bytes,
    content_type: str = "application/octet-stream",
) -> Upload:
    """Persist bytes under ``UPLOADS_DIR/<user>/<token>_<name>`` and record a row."""
    if len(data) > MAX_UPLOAD_BYTES:
        raise UploadError("file exceeds 100 MB")
    if not data:
        raise UploadError("file is empty")
    fname = safe_filename(name)
    rel = Path(user_id) / f"{secrets.token_hex(8)}_{fname}"
    full = Path(settings.uploads_dir) / rel
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_bytes(data)
    row = Upload(
        user_id=user_id, name=fname, size=len(data), content_type=content_type, path=str(rel)
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def read_upload(
    db: Session, settings: Settings, user_id: str | None, upload_id: str
) -> tuple[str, bytes]:
    """``(filename, bytes)`` of an upload owned by ``user_id``."""
    row = db.get(Upload, upload_id)
    if row is None or row.user_id != user_id:
        raise UploadError(f"upload {upload_id!r} not found")
    full = Path(settings.uploads_dir) / row.path
    try:
        return row.name, full.read_bytes()
    except OSError as exc:
        raise UploadError(f"upload {upload_id!r} is missing on disk") from exc
