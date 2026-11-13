"""Keep files a run produced under ``/repo/output`` as downloadable artifacts."""

from __future__ import annotations

import logging
import mimetypes
import posixpath
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.executor.sandbox import REPO_ROOT, Sandbox
from app.models import Artifact

log = logging.getLogger(__name__)
OUTPUT_DIR = f"{REPO_ROOT}/output"


class ArtifactStore:
    """Copies files out of the sandbox and records them."""

    def __init__(self, session_factory: sessionmaker[Session], settings: Settings) -> None:
        self.session_factory = session_factory
        self.settings = settings

    async def collect(self, run_id: str, sandbox: Sandbox) -> list[dict[str, Any]]:
        """Copy every regular file under ``/repo/output`` (size/count capped)."""
        res = await sandbox.exec(
            ["find", OUTPUT_DIR, "-type", "f", "-not", "-path", "*/.*", "-printf", "%p\\0%s\\0"],
            timeout=60,
        )
        if not res.ok:
            return []
        rows: list[tuple[str, int]] = []
        fields = res.stdout.split("\0")
        for path, size in zip(fields[0::2], fields[1::2], strict=False):
            if path and size.isdigit() and "\n" not in path:
                rows.append((path, int(size)))
        rows.sort()
        kept: list[dict[str, Any]] = []
        skipped = 0
        root = Path(self.settings.artifacts_dir) / run_id
        with self.session_factory() as db:
            for path, size in rows[: self.settings.artifact_max_files]:
                if size > self.settings.artifact_max_bytes:
                    skipped += 1
                    continue
                name = posixpath.relpath(path, OUTPUT_DIR)
                rel = Path(run_id) / name
                full = Path(self.settings.artifacts_dir) / rel
                if name.startswith("..") or not full.resolve().is_relative_to(root.resolve()):
                    log.warning("artifact %s escapes the run directory; skipped", path)
                    skipped += 1
                    continue
                try:
                    data = await sandbox.read_bytes(path)
                except (FileNotFoundError, OSError) as exc:
                    log.warning("artifact %s unreadable: %s", path, exc)
                    continue
                if len(data) > self.settings.artifact_max_bytes:
                    skipped += 1
                    continue
                full.parent.mkdir(parents=True, exist_ok=True)
                full.write_bytes(data)
                ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
                row = Artifact(
                    run_id=run_id, name=name, size=len(data), content_type=ctype, path=str(rel)
                )
                db.add(row)
                db.flush()
                kept.append({"id": row.id, "name": name, "size": len(data), "content_type": ctype})
            db.commit()
        if len(rows) > self.settings.artifact_max_files:
            skipped += len(rows) - self.settings.artifact_max_files
        if skipped:
            log.info("run %s: %d artifact(s) skipped (size/count cap)", run_id, skipped)
        root.mkdir(parents=True, exist_ok=True)
        return kept


def list_artifacts(db: Session, run_id: str) -> list[Artifact]:
    return list(
        db.scalars(select(Artifact).where(Artifact.run_id == run_id).order_by(Artifact.name)).all()
    )


def artifact_path(settings: Settings, artifact: Artifact) -> Path:
    full = (Path(settings.artifacts_dir) / artifact.path).resolve()
    root = Path(settings.artifacts_dir).resolve()
    if root not in full.parents:
        raise FileNotFoundError("artifact outside store")
    return full
