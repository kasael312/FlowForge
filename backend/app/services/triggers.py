"""Triggers: webhook verification, cron schedules, IMAP polling, watched folders.

:class:`TriggerScheduler` runs inside the backend process and calls
:meth:`tick` every ``TRIGGER_POLL_SECONDS``; tests call ``tick`` directly with
fakes for IMAP and the clock.
"""

from __future__ import annotations

import asyncio
import email
import hashlib
import hmac
import imaplib
import json
import logging
import secrets
from collections.abc import Callable
from datetime import UTC, datetime
from email.header import decode_header, make_header
from pathlib import Path
from typing import Any

from croniter import croniter
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.crypto import DecryptError, decrypt, encrypt
from app.models import Flow, KeyProvider, Run, RunStatus, Trigger, TriggerType
from app.schemas import InputField, coerce_inputs
from app.services.keys import get_user_key
from app.services.uploads import store_upload

log = logging.getLogger(__name__)

SIGNATURE_HEADER = "x-flowforge-signature"
GITHUB_SIGNATURE_HEADER = "x-hub-signature-256"
RunStarter = Callable[[str], Any]
"""``run_id -> None``: hands a queued run to the executor."""


class TriggerError(Exception):
    """Configuration or verification problem."""


# ---------------------------------------------------------------- secrets


def new_secret() -> str:
    return secrets.token_urlsafe(32)


def get_secret(trigger: Trigger) -> str | None:
    if not trigger.secret_encrypted:
        return None
    try:
        return decrypt(trigger.secret_encrypted)
    except DecryptError:
        return None


def set_secret(trigger: Trigger, secret: str) -> None:
    trigger.secret_encrypted = encrypt(secret)


# ---------------------------------------------------------------- validation


def validate_config(kind: TriggerType, config: dict[str, Any]) -> dict[str, Any]:
    """Normalise and validate the type-specific settings."""
    cfg = dict(config or {})
    if kind is TriggerType.schedule:
        expr = str(cfg.get("cron", "")).strip()
        if not expr or not croniter.is_valid(expr):
            raise TriggerError("schedule needs a valid 5-field cron expression")
        cfg["cron"] = expr
    elif kind is TriggerType.email:
        cfg["folder"] = str(cfg.get("folder") or "INBOX")
        cfg["poll_seconds"] = max(30, int(cfg.get("poll_seconds") or 60))
        cfg["unseen_only"] = bool(cfg.get("unseen_only", True))
    elif kind is TriggerType.file_watch:
        cfg["pattern"] = safe_glob_pattern(str(cfg.get("pattern") or "*"))
    elif kind is TriggerType.github:
        events = cfg.get("events") or ["push", "pull_request", "issues"]
        if not isinstance(events, list):
            raise TriggerError("events must be a list")
        cfg["events"] = [str(e) for e in events]
    for key in ("repo_url", "branch", "task"):
        if key in cfg and cfg[key] is not None:
            cfg[key] = str(cfg[key])
    if "inputs" in cfg and not isinstance(cfg["inputs"], dict):
        raise TriggerError("inputs must be an object")
    return cfg


# ---------------------------------------------------------------- firing


def fire(
    db: Session,
    trigger: Trigger,
    *,
    starter: RunStarter | None,
    inputs: dict[str, Any] | None = None,
    task: str | None = None,
    now: datetime | None = None,
) -> Run:
    """Create a queued run from the trigger's defaults (+ event inputs) and start it."""
    flow = db.get(Flow, trigger.flow_id)
    if flow is None:
        raise TriggerError("flow no longer exists")
    cfg = trigger.config or {}
    merged: dict[str, Any] = dict(cfg.get("inputs") or {})
    merged.update(inputs or {})
    schema_fields = [InputField.model_validate(f) for f in (flow.input_schema or [])]
    if schema_fields:
        try:
            merged = coerce_inputs(schema_fields, merged)
        except ValueError as exc:
            raise TriggerError(f"inputs do not match the flow's input schema: {exc}") from exc
    run = Run(
        flow_id=flow.id,
        user_id=trigger.user_id,
        repo_url=str(cfg.get("repo_url") or "").strip(),
        branch=str(cfg.get("branch") or "main"),
        task_input=task if task is not None else str(cfg.get("task") or ""),
        inputs=merged,
        trigger_id=trigger.id,
        sandbox=dict(flow.sandbox or {}) or None,
        status=RunStatus.queued,
    )
    db.add(run)
    trigger.last_fired = now or datetime.now(UTC)
    trigger.last_error = None
    db.commit()
    db.refresh(run)
    if starter is not None:
        starter(run.id)
    return run


# ---------------------------------------------------------------- webhook


def verify_signature(secret: str, body: bytes, headers: dict[str, str]) -> bool:
    """Accept ``X-FlowForge-Signature: sha256=<hmac>``, GitHub's
    ``X-Hub-Signature-256`` or ``Authorization: Bearer <secret>``."""
    h = {k.lower(): v for k, v in headers.items()}
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    for name in (SIGNATURE_HEADER, GITHUB_SIGNATURE_HEADER):
        given = h.get(name, "")
        if given and hmac.compare_digest(given.strip(), expected):
            return True
    auth = h.get("authorization", "")
    return bool(
        auth.lower().startswith("bearer ") and hmac.compare_digest(auth[7:].strip(), secret)
    )


def webhook_inputs(
    trigger: Trigger, payload: Any, headers: dict[str, str]
) -> tuple[dict[str, Any], str | None]:
    """Map a request body onto run inputs. Returns ``(inputs, task)``."""
    inputs: dict[str, Any] = {"payload": payload}
    task: str | None = None
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key == "task" and isinstance(value, str):
                task = value
            elif key != "payload":
                inputs[key] = value
    if trigger.type is TriggerType.github:
        h = {k.lower(): v for k, v in headers.items()}
        inputs["event"] = h.get("x-github-event", "")
        wanted = (trigger.config or {}).get("events") or []
        if wanted and inputs["event"] not in wanted:
            raise TriggerError(f"event {inputs['event']!r} is not enabled for this trigger")
    return inputs, task


# ---------------------------------------------------------------- schedule


def schedule_due(trigger: Trigger, now: datetime) -> bool:
    """True when the cron expression has a fire time between the last fire and ``now``."""
    expr = (trigger.config or {}).get("cron")
    if not expr:
        return False
    base = trigger.last_fired or trigger.created_at or now
    if base.tzinfo is None:
        base = base.replace(tzinfo=UTC)
    nxt = croniter(expr, base).get_next(datetime)
    return nxt <= now


# ---------------------------------------------------------------- email (IMAP)


def _decode(value: Any) -> str:
    try:
        return str(make_header(decode_header(value or "")))
    except Exception:  # noqa: BLE001
        return str(value or "")


def _body_text(msg: email.message.Message) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and not part.get_filename():
                payload = part.get_payload(decode=True) or b""
                return payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        return ""
    payload = msg.get_payload(decode=True) or b""
    return payload.decode(msg.get_content_charset() or "utf-8", errors="replace")


def parse_imap_url(url: str) -> dict[str, Any]:
    """``imap://user:pass@host:993`` (imaps also accepted; TLS by default)."""
    from urllib.parse import unquote, urlparse

    u = urlparse(url)
    if u.scheme not in ("imap", "imaps") or not u.hostname:
        raise TriggerError("imap credential must look like imap://user:password@host:993")
    return {
        "host": u.hostname,
        "port": u.port or 993,
        "user": unquote(u.username or ""),
        "password": unquote(u.password or ""),
        "ssl": True,
    }


def fetch_new_messages(
    cred: dict[str, Any],
    folder: str,
    unseen_only: bool,
    imap_factory: Callable[..., Any] = imaplib.IMAP4_SSL,
) -> list[dict[str, Any]]:
    """Fetch (and mark seen) new messages. Blocking; run in a thread."""
    conn = imap_factory(cred["host"], cred["port"])
    try:
        conn.login(cred["user"], cred["password"])
        conn.select(folder)
        status, data = conn.search(None, "UNSEEN" if unseen_only else "ALL")
        if status != "OK":
            return []
        out = []
        for uid in (data[0] or b"").split()[:20]:
            status, parts = conn.fetch(uid, "(RFC822)")
            if status != "OK" or not parts:
                continue
            raw = next((p[1] for p in parts if isinstance(p, tuple)), b"")
            msg = email.message_from_bytes(raw)
            out.append(
                {
                    "uid": uid.decode() if isinstance(uid, bytes) else str(uid),
                    "subject": _decode(msg.get("Subject")),
                    "from": _decode(msg.get("From")),
                    "to": _decode(msg.get("To")),
                    "date": msg.get("Date", ""),
                    "message_id": msg.get("Message-ID", ""),
                    "body": _body_text(msg)[:20_000],
                }
            )
            conn.store(uid, "+FLAGS", "\\Seen")
        return out
    finally:
        try:
            conn.logout()
        except Exception as exc:  # noqa: BLE001
            log.debug("imap logout failed: %s", exc)


# ---------------------------------------------------------------- file watch


def watch_folder(settings: Settings, trigger: Trigger) -> Path:
    return Path(settings.watch_dir) / trigger.user_id / trigger.id


def safe_glob_pattern(pattern: str) -> str:
    """A file-name glob only: no directory parts, no ``..``, no ``**``."""
    pattern = pattern.strip() or "*"
    if (
        "/" in pattern
        or "\\" in pattern
        or ".." in pattern
        or "**" in pattern
        or len(pattern) > 100
    ):
        raise TriggerError("pattern must be a plain file-name glob such as *.pdf")
    return pattern


def scan_folder(folder: Path, pattern: str) -> list[Path]:
    """New files directly in ``folder`` (not in ``processed/``), oldest first."""
    if not folder.exists():
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "processed").mkdir(exist_ok=True)
        return []
    pattern = safe_glob_pattern(pattern)
    root = folder.resolve()
    files = [
        p
        for p in folder.glob(pattern)
        if p.is_file()
        and not p.is_symlink()
        and not p.name.startswith(".")
        and p.resolve().parent == root
    ]
    return sorted(files, key=lambda p: p.stat().st_mtime)


# ---------------------------------------------------------------- scheduler


class TriggerScheduler:
    """Polls schedules, mailboxes and folders; fires triggers that are due."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        settings: Settings,
        starter: RunStarter | None,
        *,
        imap_factory: Callable[..., Any] = imaplib.IMAP4_SSL,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.starter = starter
        self.imap_factory = imap_factory
        self.clock = clock
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    # ---- lifecycle

    def start(self) -> None:
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name="trigger-scheduler")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception) as exc:  # noqa: BLE001
                log.debug("scheduler task ended: %r", exc)
            self._task = None

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self.tick()
            except Exception:
                log.exception("trigger tick failed")
            try:
                await asyncio.wait_for(
                    self._stop.wait(), timeout=self.settings.trigger_poll_seconds
                )
            except TimeoutError:
                pass

    # ---- one pass

    async def tick(self) -> list[str]:
        """Evaluate every enabled trigger once. Returns the ids of started runs."""
        started: list[str] = []
        now = self.clock()
        with self.session_factory() as db:
            rows = db.scalars(
                select(Trigger).where(
                    Trigger.enabled.is_(True),
                    Trigger.type.in_(
                        (TriggerType.schedule, TriggerType.email, TriggerType.file_watch)
                    ),
                )
            ).all()
            for trig in rows:
                try:
                    if trig.type is TriggerType.schedule:
                        started += self._tick_schedule(db, trig, now)
                    elif trig.type is TriggerType.email:
                        started += await self._tick_email(db, trig, now)
                    elif trig.type is TriggerType.file_watch:
                        started += self._tick_files(db, trig)
                except Exception as exc:  # noqa: BLE001
                    trig.last_error = f"{exc.__class__.__name__}: {str(exc)[:300]}"
                    db.commit()
                    log.warning("trigger %s failed: %s", trig.id, trig.last_error)
        return started

    def _tick_schedule(self, db: Session, trig: Trigger, now: datetime) -> list[str]:
        if not schedule_due(trig, now):
            return []
        run = fire(db, trig, starter=self.starter, now=now)
        return [run.id]

    async def _tick_email(self, db: Session, trig: Trigger, now: datetime) -> list[str]:
        cfg = trig.config or {}
        last = (trig.state or {}).get("last_poll")
        if last and (now - datetime.fromisoformat(last)).total_seconds() < cfg.get(
            "poll_seconds", 60
        ):
            return []
        url = get_user_key(db, trig.user_id, KeyProvider.imap)
        if not url:
            raise TriggerError("missing 'imap' credential (Settings → API keys)")
        cred = parse_imap_url(url)
        messages = await asyncio.to_thread(
            fetch_new_messages,
            cred,
            cfg.get("folder", "INBOX"),
            cfg.get("unseen_only", True),
            self.imap_factory,
        )
        trig.state = {**(trig.state or {}), "last_poll": now.isoformat()}
        db.commit()
        started = []
        for m in messages:
            run = fire(
                db,
                trig,
                starter=self.starter,
                inputs={"email": m},
                task=f"New email: {m['subject']}",
            )
            started.append(run.id)
        return started

    def _tick_files(self, db: Session, trig: Trigger) -> list[str]:
        folder = watch_folder(self.settings, trig)
        started = []
        for path in scan_folder(folder, (trig.config or {}).get("pattern", "*")):
            data = path.read_bytes()
            if not data:
                continue
            upload = store_upload(db, self.settings, trig.user_id, path.name, data)
            processed = folder / "processed"
            processed.mkdir(exist_ok=True)
            path.rename(processed / f"{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}_{path.name}")
            run = fire(
                db,
                trig,
                starter=self.starter,
                inputs={"file": {"upload_id": upload.id, "name": upload.name, "size": upload.size}},
                task=f"Process file {upload.name}",
            )
            started.append(run.id)
        return started


def describe_webhook(trigger: Trigger, base_url: str, secret: str | None) -> dict[str, Any]:
    """Payload for the UI: URL, header names and a curl example."""
    url = f"{base_url.rstrip('/')}/hooks/{trigger.id}"
    body = json.dumps({"task": "Describe what to do", "topic": "example"})
    example = (
        f"BODY='{body}'\n"
        f"SIG=sha256=$(printf '%s' \"$BODY\" | openssl dgst -sha256 -hmac '<secret>' | sed 's/^.* //')\n"
        f"curl -X POST {url} -H 'content-type: application/json' -H '{SIGNATURE_HEADER}: '$SIG -d \"$BODY\""
    )
    return {"url": url, "signature_header": SIGNATURE_HEADER, "secret": secret, "example": example}
