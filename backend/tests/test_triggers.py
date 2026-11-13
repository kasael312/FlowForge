"""Input schema + run inputs, uploads, variables, and every trigger type."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import get_settings
from app.executor.engine import ExecutorDeps, FlowExecutor
from app.models import ApiKey, KeyProvider, Run, RunStatus, StepRun, Trigger, TriggerType, User
from app.schemas import InputField, coerce_inputs
from app.services import triggers as svc
from app.services.uploads import store_upload
from tests.conftest import login, make_step
from tests.fakes import FakeAnthropic, FakeSandbox, text_response

# ---------------------------------------------------------------- input schema


def test_input_schema_validation(client: TestClient) -> None:
    schema = [
        {"name": "topic", "label": "Topic", "type": "text", "required": True},
        {"name": "depth", "type": "select", "options": ["quick", "deep"], "default": "quick"},
        {"name": "count", "type": "number", "default": 3},
        {"name": "source", "type": "url"},
        {"name": "doc", "type": "file"},
        {"name": "verbose", "type": "boolean"},
    ]
    r = client.post("/flows", json={"name": "f", "input_schema": schema})
    assert r.status_code == 201, r.text
    assert [f["name"] for f in r.json()["input_schema"]] == [
        "topic",
        "depth",
        "count",
        "source",
        "doc",
        "verbose",
    ]
    for bad in (
        [{"name": "bad name", "type": "text"}],
        [{"name": "task", "type": "text"}],
        [{"name": "x", "type": "select"}],
        [{"name": "x", "type": "text"}, {"name": "x", "type": "text"}],
        [{"name": "x", "type": "pdf"}],
    ):
        assert client.post("/flows", json={"name": "f", "input_schema": bad}).status_code == 422, (
            bad
        )
    flow_id = r.json()["id"]
    r = client.put(
        f"/flows/{flow_id}", json={"input_schema": [{"name": "only", "type": "textarea"}]}
    )
    assert [f["name"] for f in r.json()["input_schema"]] == ["only"]


def test_coerce_inputs() -> None:
    fields = [
        InputField(name="topic", type="text", required=True),
        InputField(name="depth", type="select", options=["quick", "deep"], default="quick"),
        InputField(name="count", type="number"),
        InputField(name="site", type="url"),
        InputField(name="flag", type="boolean"),
        InputField(name="doc", type="file"),
    ]
    out = coerce_inputs(
        fields,
        {
            "topic": "AI",
            "count": "4",
            "flag": "yes",
            "doc": {"upload_id": "u1", "name": "a.pdf"},
            "extra": 1,
        },
    )
    assert out == {
        "topic": "AI",
        "depth": "quick",
        "count": 4,
        "site": None,
        "flag": True,
        "doc": {"upload_id": "u1", "name": "a.pdf"},
        "extra": 1,
    }
    with pytest.raises(ValueError, match="required"):
        coerce_inputs(fields, {})
    with pytest.raises(ValueError, match="number"):
        coerce_inputs(fields, {"topic": "x", "count": "many"})
    with pytest.raises(ValueError, match="one of"):
        coerce_inputs(fields, {"topic": "x", "depth": "medium"})
    with pytest.raises(ValueError, match="URL"):
        coerce_inputs(fields, {"topic": "x", "site": "example.com"})
    with pytest.raises(ValueError, match="upload"):
        coerce_inputs(fields, {"topic": "x", "doc": "a.pdf"})


def test_run_with_inputs_and_no_repo(
    client: TestClient, deps: ExecutorDeps, fake_client: FakeAnthropic
) -> None:
    from app.services.runs import run_manager

    run_manager._deps = deps
    try:
        flow = client.post(
            "/flows",
            json={
                "name": "f",
                "input_schema": [{"name": "topic", "type": "text", "required": True}],
                "nodes": [
                    make_step("a", "custom", system_prompt="Research {{input.topic}} for {{task}}")
                ],
            },
        ).json()
        r = client.post(f"/flows/{flow['id']}/run", json={"task": "a report"})
        assert r.status_code == 422 and "required" in r.text
        fake_client.responses = [text_response("done")]
        r = client.post(
            f"/flows/{flow['id']}/run", json={"task": "a report", "inputs": {"topic": "AI agents"}}
        )
        assert r.status_code == 202, r.text
        run = r.json()
        assert run["repo_url"] == "" and run["inputs"] == {"topic": "AI agents"}
        import time

        for _ in range(100):
            body = client.get(f"/runs/{run['id']}").json()
            if body["status"] not in ("queued", "running"):
                break
            time.sleep(0.02)
        assert body["status"] == "success", body
        assert fake_client.requests[0]["system"].endswith("Research AI agents for a report")
        assert "created empty workspace" in [
            e.get("message") for e in deps.bus.history(run["id"]) if e["event"] == "log"
        ]
    finally:
        run_manager._deps = None


async def test_inputs_section_and_file_staging(
    deps: ExecutorDeps,
    db_session: Session,
    fake_client: FakeAnthropic,
    fake_sandbox: FakeSandbox,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.models import Flow

    settings = get_settings()
    monkeypatch.setattr(settings, "uploads_dir", str(tmp_path / "uploads"))
    user = User(email="in@example.com", password_hash="x")
    db_session.add(user)
    db_session.flush()
    up = store_upload(
        db_session, settings, user.id, "../../report v1.pdf", b"%PDF-fake", "application/pdf"
    )
    assert up.name == "report_v1.pdf"
    flow = Flow(
        user_id=user.id,
        name="f",
        nodes=[make_step("a", "custom", system_prompt="Summarise the file.")],
        edges=[],
    )
    db_session.add(flow)
    db_session.flush()
    run = Run(
        flow_id=flow.id,
        user_id=user.id,
        repo_url="",
        task_input="go",
        inputs={"doc": {"upload_id": up.id, "name": up.name}, "lang": "vi", "n": 2},
    )
    db_session.add(run)
    db_session.commit()

    from app.services.uploads import read_upload

    deps.upload_reader = lambda uid, upload_id: read_upload(db_session, settings, uid, upload_id)
    fake_client.responses = [text_response("ok")]
    assert await FlowExecutor(deps, run.id).execute() is RunStatus.success
    assert fake_sandbox.binaries["input/report_v1.pdf"] == b"%PDF-fake"
    user_msg = fake_client.requests[0]["messages"][0]["content"]
    assert (
        "## Inputs" in user_msg
        and "- doc: file at input/report_v1.pdf" in user_msg
        and "- lang: vi" in user_msg
        and "- n: 2" in user_msg
    )


def test_upload_endpoint(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "uploads_dir", str(tmp_path / "up"))
    r = client.post("/uploads", files={"file": ("notes.txt", b"hello", "text/plain")})
    assert r.status_code == 201, r.text
    assert r.json()["name"] == "notes.txt" and r.json()["size"] == 5
    assert (tmp_path / "up").exists()
    assert (
        client.post("/uploads", files={"file": ("empty.txt", b"", "text/plain")}).status_code == 422
    )


# ---------------------------------------------------------------- triggers API + webhook


def _flow(client: TestClient) -> str:
    return client.post("/flows", json={"name": "f", "nodes": [make_step("a", "plan")]}).json()["id"]


def test_trigger_crud_and_validation(client: TestClient) -> None:
    flow_id = _flow(client)
    assert client.post(f"/flows/{flow_id}/triggers", json={"type": "manual"}).status_code == 422
    assert (
        client.post(
            f"/flows/{flow_id}/triggers", json={"type": "schedule", "config": {"cron": "every day"}}
        ).status_code
        == 422
    )
    r = client.post(
        f"/flows/{flow_id}/triggers",
        json={
            "type": "schedule",
            "name": "nightly",
            "config": {"cron": "0 2 * * *", "task": "nightly report", "inputs": {"x": 1}},
        },
    )
    assert r.status_code == 201, r.text
    sched = r.json()
    assert sched["config"]["cron"] == "0 2 * * *" and sched["webhook"] is None and sched["enabled"]

    r = client.post(f"/flows/{flow_id}/triggers", json={"type": "webhook", "name": "hook"})
    hook = r.json()
    assert (
        hook["webhook"]["url"].endswith(f"/hooks/{hook['id']}")
        and len(hook["webhook"]["secret"]) > 20
    )
    assert "curl" in hook["webhook"]["example"]

    r = client.post(
        f"/flows/{flow_id}/triggers", json={"type": "file_watch", "config": {"pattern": "*.pdf"}}
    )
    assert (
        r.json()["watch_folder"].endswith(r.json()["id"])
        and Path(r.json()["watch_folder"]).exists()
    )

    r = client.post(
        f"/flows/{flow_id}/triggers", json={"type": "email", "config": {"poll_seconds": 5}}
    )
    assert r.json()["config"] == {"folder": "INBOX", "poll_seconds": 30, "unseen_only": True}

    rows = client.get(f"/flows/{flow_id}/triggers").json()
    assert [t["type"] for t in rows] == ["schedule", "webhook", "file_watch", "email"]

    old_secret = hook["webhook"]["secret"]
    r = client.put(
        f"/triggers/{hook['id']}", json={"enabled": False, "rotate_secret": True, "name": "renamed"}
    )
    assert (
        r.json()["enabled"] is False
        and r.json()["name"] == "renamed"
        and r.json()["webhook"]["secret"] != old_secret
    )
    assert (
        client.put(f"/triggers/{sched['id']}", json={"config": {"cron": "bad"}}).status_code == 422
    )
    assert client.delete(f"/triggers/{sched['id']}").status_code == 204
    assert client.delete(f"/triggers/{sched['id']}").status_code == 404


def test_webhook_signatures(anon_client: TestClient, deps: ExecutorDeps) -> None:
    from app.services.runs import run_manager

    run_manager._deps = deps
    deps.provider_resolver = lambda u, m: (_ for _ in ()).throw(
        AssertionError("no model needed")
    )  # fail loudly if a run executes
    try:
        login(anon_client, "w@example.com")
        flow_id = anon_client.post(
            "/flows",
            json={
                "name": "f",
                "nodes": [make_step("s", "shell", command="ls")],
                "input_schema": [{"name": "topic", "type": "text"}],
            },
        ).json()["id"]
        hook = anon_client.post(
            f"/flows/{flow_id}/triggers",
            json={"type": "webhook", "config": {"task": "default task"}},
        ).json()
        secret = hook["webhook"]["secret"]
        anon_client.cookies.clear()  # the endpoint is public

        body = json.dumps({"topic": "bees", "task": "from hook", "extra": [1]}).encode()
        sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        r = anon_client.post(
            f"/hooks/{hook['id']}",
            content=body,
            headers={"content-type": "application/json", "X-FlowForge-Signature": sig},
        )
        assert r.status_code == 202, r.text
        run_id = r.json()["run_id"]

        # wrong signature / no signature / wrong secret bearer / unknown id
        assert (
            anon_client.post(
                f"/hooks/{hook['id']}",
                content=body,
                headers={"X-FlowForge-Signature": "sha256=deadbeef"},
            ).status_code
            == 401
        )
        assert anon_client.post(f"/hooks/{hook['id']}", content=body).status_code == 401
        assert (
            anon_client.post(
                f"/hooks/{hook['id']}", content=body, headers={"Authorization": "Bearer nope"}
            ).status_code
            == 401
        )
        assert (
            anon_client.post(
                "/hooks/does-not-exist", content=body, headers={"X-FlowForge-Signature": sig}
            ).status_code
            == 404
        )
        # bearer with the real secret works; github-style header too
        assert (
            anon_client.post(
                f"/hooks/{hook['id']}", content=b"{}", headers={"Authorization": f"Bearer {secret}"}
            ).status_code
            == 202
        )
        assert (
            anon_client.post(
                f"/hooks/{hook['id']}", content=body, headers={"X-Hub-Signature-256": sig}
            ).status_code
            == 202
        )

        login(anon_client, "w@example.com")
        run = anon_client.get(f"/runs/{run_id}").json()
        assert run["trigger_id"] == hook["id"] and run["task_input"] == "from hook"
        assert (
            run["inputs"]["topic"] == "bees"
            and run["inputs"]["extra"] == [1]
            and run["inputs"]["payload"]["topic"] == "bees"
        )
        assert anon_client.get(f"/flows/{flow_id}/triggers").json()[0]["last_fired"] is not None

        # disabled trigger -> 404
        anon_client.put(f"/triggers/{hook['id']}", json={"enabled": False})
        anon_client.cookies.clear()
        assert (
            anon_client.post(
                f"/hooks/{hook['id']}", content=body, headers={"X-FlowForge-Signature": sig}
            ).status_code
            == 404
        )
    finally:
        run_manager._deps = None
        run_manager._tasks.clear()
        run_manager._executors.clear()


def test_github_trigger_filters_events(anon_client: TestClient, deps: ExecutorDeps) -> None:
    from app.services.runs import run_manager

    run_manager._deps = deps
    try:
        login(anon_client, "g@example.com")
        flow_id = _flow(anon_client)
        hook = anon_client.post(
            f"/flows/{flow_id}/triggers",
            json={"type": "github", "config": {"events": ["pull_request"]}},
        ).json()
        secret = hook["webhook"]["secret"]
        body = json.dumps({"action": "opened", "number": 7}).encode()
        sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        anon_client.cookies.clear()
        r = anon_client.post(
            f"/hooks/{hook['id']}",
            content=body,
            headers={"X-Hub-Signature-256": sig, "X-GitHub-Event": "push"},
        )
        assert r.status_code == 422 and "not enabled" in r.text
        r = anon_client.post(
            f"/hooks/{hook['id']}",
            content=body,
            headers={"X-Hub-Signature-256": sig, "X-GitHub-Event": "pull_request"},
        )
        assert r.status_code == 202
        login(anon_client, "g@example.com")
        run = anon_client.get(f"/runs/{r.json()['run_id']}").json()
        assert run["inputs"]["event"] == "pull_request" and run["inputs"]["number"] == 7
    finally:
        run_manager._deps = None
        run_manager._tasks.clear()
        run_manager._executors.clear()


# ---------------------------------------------------------------- scheduler ticks


def _user_flow_trigger(
    db: Session, kind: TriggerType, config: dict[str, Any], **extra: Any
) -> Trigger:
    from app.models import Flow

    user = User(email=f"{kind.value}@example.com", password_hash="x")
    db.add(user)
    db.flush()
    flow = Flow(user_id=user.id, name="f", nodes=[make_step("a", "plan")], edges=[])
    db.add(flow)
    db.flush()
    trig = Trigger(
        flow_id=flow.id,
        user_id=user.id,
        type=kind,
        config=svc.validate_config(kind, config),
        **extra,
    )
    db.add(trig)
    db.commit()
    return trig


async def test_schedule_tick(session_factory, db_session: Session, tmp_path: Path) -> None:
    started: list[str] = []
    t0 = datetime(2026, 11, 9, 1, 0, tzinfo=UTC)
    trig = _user_flow_trigger(
        db_session,
        TriggerType.schedule,
        {"cron": "0 2 * * *", "task": "nightly", "repo_url": ""},
        created_at=t0,
    )
    sched = svc.TriggerScheduler(
        session_factory,
        get_settings(),
        started.append,
        clock=lambda: datetime(2026, 11, 9, 1, 30, tzinfo=UTC),
    )
    assert await sched.tick() == [] and started == []
    sched.clock = lambda: datetime(2026, 11, 9, 2, 0, 5, tzinfo=UTC)
    ids = await sched.tick()
    assert len(ids) == 1 and started == ids
    assert await sched.tick() == []  # not again until tomorrow
    sched.clock = lambda: datetime(2026, 11, 10, 2, 1, tzinfo=UTC)
    assert len(await sched.tick()) == 1
    with session_factory() as db:
        runs = db.query(Run).filter_by(trigger_id=trig.id).all()
        assert [r.task_input for r in runs] == ["nightly", "nightly"] and all(
            r.status is RunStatus.queued for r in runs
        )
        assert db.get(Trigger, trig.id).last_fired is not None


class FakeIMAP:
    """imaplib stand-in with two unseen messages."""

    instances: ClassVar[list[FakeIMAP]] = []

    def __init__(self, host: str, port: int) -> None:
        self.host, self.port = host, port
        self.flagged: list[bytes] = []
        self.logged_in: tuple[str, str] | None = None
        FakeIMAP.instances.append(self)

    def login(self, u: str, p: str) -> None:
        self.logged_in = (u, p)

    def select(self, folder: str) -> None:
        self.folder = folder

    def search(self, _charset: None, _crit: str) -> tuple[str, list[bytes]]:
        return "OK", [b"1 2"]

    def fetch(self, uid: bytes, _spec: str) -> tuple[str, list[Any]]:
        raw = (
            f"From: Alice <alice@example.com>\r\nTo: me@example.com\r\nSubject: =?utf-8?q?Hello_{uid.decode()}?=\r\n"
            f"Message-ID: <m{uid.decode()}@x>\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nBody of {uid.decode()}\r\n"
        ).encode()
        return "OK", [(b"1 (RFC822 {123}", raw), b")"]

    def store(self, uid: bytes, _op: str, _flag: str) -> None:
        self.flagged.append(uid)

    def logout(self) -> None:
        pass


async def test_email_tick(session_factory, db_session: Session) -> None:
    from app import crypto

    trig = _user_flow_trigger(
        db_session, TriggerType.email, {"folder": "INBOX", "poll_seconds": 60}
    )
    started: list[str] = []
    sched = svc.TriggerScheduler(
        session_factory,
        get_settings(),
        started.append,
        imap_factory=FakeIMAP,
        clock=lambda: datetime(2026, 11, 9, 1, 0, tzinfo=UTC),
    )
    await sched.tick()  # no imap credential -> last_error, no runs
    with session_factory() as db:
        assert "imap" in (db.get(Trigger, trig.id).last_error or "")
    db_session.add(
        ApiKey(
            user_id=trig.user_id,
            provider=KeyProvider.imap,
            encrypted_value=crypto.encrypt("imap://me%40example.com:pw@imap.example.com:993"),
            last4="993",
        )
    )
    db_session.commit()
    FakeIMAP.instances.clear()
    ids = await sched.tick()
    assert len(ids) == 2 and started == ids
    imap = FakeIMAP.instances[0]
    assert (
        imap.host == "imap.example.com"
        and imap.port == 993
        and imap.logged_in == ("me@example.com", "pw")
        and imap.flagged == [b"1", b"2"]
    )
    with session_factory() as db:
        runs = db.query(Run).filter_by(trigger_id=trig.id).order_by(Run.created_at).all()
        assert (
            runs[0].task_input == "New email: Hello 1"
            and runs[0].inputs["email"]["from"] == "Alice <alice@example.com>"
        )
        assert runs[1].inputs["email"]["body"].strip() == "Body of 2"
    # within poll interval -> no new poll
    assert await sched.tick() == []
    sched.clock = lambda: datetime(2026, 11, 9, 1, 2, tzinfo=UTC)
    assert len(await sched.tick()) == 2


async def test_file_watch_tick(
    session_factory, db_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "watch_dir", str(tmp_path / "watch"))
    monkeypatch.setattr(settings, "uploads_dir", str(tmp_path / "uploads"))
    trig = _user_flow_trigger(
        db_session, TriggerType.file_watch, {"pattern": "*.pdf", "task": "ignored"}
    )
    started: list[str] = []
    sched = svc.TriggerScheduler(session_factory, settings, started.append)
    assert await sched.tick() == []  # folder created, empty
    folder = svc.watch_folder(settings, trig)
    assert folder.exists() and (folder / "processed").exists()
    (folder / "invoice.pdf").write_bytes(b"%PDF-1")
    (folder / "notes.txt").write_bytes(b"skip me")
    (folder / ".hidden.pdf").write_bytes(b"skip")
    ids = await sched.tick()
    assert len(ids) == 1
    assert (
        not (folder / "invoice.pdf").exists()
        and len(list((folder / "processed").glob("*_invoice.pdf"))) == 1
    )
    with session_factory() as db:
        run = db.get(Run, ids[0])
        assert run is not None and run.task_input == "Process file invoice.pdf"
        assert run.inputs["file"]["name"] == "invoice.pdf" and run.inputs["file"]["size"] == 6
    assert await sched.tick() == []


def test_fire_endpoint(client: TestClient, deps: ExecutorDeps, fake_client: FakeAnthropic) -> None:
    from app.services.runs import run_manager

    run_manager._deps = deps
    try:
        flow_id = _flow(client)
        trig = client.post(
            f"/flows/{flow_id}/triggers",
            json={"type": "schedule", "config": {"cron": "0 0 1 1 *", "task": "manual fire"}},
        ).json()
        fake_client.responses = [text_response("ran")]
        r = client.post(f"/triggers/{trig['id']}/fire")
        assert (
            r.status_code == 202
            and r.json()["task_input"] == "manual fire"
            and r.json()["trigger_id"] == trig["id"]
        )
        import time

        for _ in range(100):
            body = client.get(f"/runs/{r.json()['id']}").json()
            if body["status"] not in ("queued", "running"):
                break
            time.sleep(0.02)
        assert body["status"] == "success"
    finally:
        run_manager._deps = None


def test_variables_in_steps_see_inputs(
    deps: ExecutorDeps, db_session: Session, fake_client: FakeAnthropic
) -> None:
    import asyncio

    from app.models import Flow

    flow = Flow(
        name="f",
        nodes=[
            make_step(
                "a",
                "custom",
                system_prompt="Topic={{input.topic}} N={{input.n}} Missing={{input.nope}}",
            )
        ],
        edges=[],
    )
    db_session.add(flow)
    db_session.flush()
    run = Run(flow_id=flow.id, repo_url="", inputs={"topic": "X", "n": 5})
    db_session.add(run)
    db_session.commit()
    fake_client.responses = [text_response("ok")]
    assert asyncio.run(FlowExecutor(deps, run.id).execute()) is RunStatus.success
    assert fake_client.requests[0]["system"].endswith("Topic=X N=5 Missing={{input.nope}}")
    with deps.session_factory() as db:
        assert db.query(StepRun).filter_by(run_id=run.id).one().status.value == "success"
