"""Shared fixtures: in-memory SQLite database, TestClient and executor fakes."""

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import models  # noqa: F401  (register tables)
from app.db import Base, get_db
from app.executor.engine import ExecutorDeps
from app.executor.events import RunEventBus
from app.llm import AnthropicProvider, ModelParams
from app.main import app
from tests.fakes import FakeAnthropic, FakeSandbox, FakeWorkspace

WHITELIST = frozenset({"ls", "cat", "pytest", "npm", "git", "python", "make"})


@pytest.fixture
def session_factory() -> Generator[sessionmaker[Session], None, None]:
    """Session factory bound to a fresh in-memory database."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    try:
        yield factory
    finally:
        engine.dispose()


@pytest.fixture
def db_session(session_factory: sessionmaker[Session]) -> Generator[Session, None, None]:
    session = session_factory()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def anon_client(db_session: Session) -> Generator[TestClient, None, None]:
    """Unauthenticated TestClient bound to the test database."""

    def _override() -> Generator[Session, None, None]:
        yield db_session

    app.dependency_overrides[get_db] = _override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


DEFAULT_PASSWORD = "test-password-123"


def login(client: TestClient, email: str, password: str = DEFAULT_PASSWORD) -> str:
    """Register-or-login ``email`` on ``client`` (cookies persist). Returns user id."""
    client.cookies.clear()
    r = client.post("/auth/register", json={"email": email, "password": password})
    if r.status_code == 409:
        r = client.post("/auth/login", json={"email": email, "password": password})
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


@pytest.fixture
def client(anon_client: TestClient) -> TestClient:
    """TestClient logged in as ``user@example.com``."""
    login(anon_client, "user@example.com")
    return anon_client


@pytest.fixture
def fake_sandbox() -> FakeSandbox:
    return FakeSandbox(files={"README.md": "# demo\n", "src/app.py": "print('hi')\n"})


@pytest.fixture
def fake_client() -> FakeAnthropic:
    return FakeAnthropic()


@pytest.fixture
def deps(
    session_factory: sessionmaker[Session], fake_sandbox: FakeSandbox, fake_client: FakeAnthropic
) -> ExecutorDeps:
    """Executor dependencies wired to the fakes."""
    return ExecutorDeps(
        provider_resolver=lambda user_id, model_id: AnthropicProvider(
            "claude-test",
            client=fake_client,  # type: ignore[arg-type]
            params=ModelParams(effort="medium"),
            use_fallbacks=True,
        ),
        sandbox_factory=lambda run_id, host_path, options=None: fake_sandbox,
        workspace=FakeWorkspace(),
        session_factory=session_factory,
        bus=RunEventBus(),
        shell_whitelist=WHITELIST,
        max_steps_per_run=20,
    )


def make_step(id_: str, type_: str, **extra: object) -> dict:
    """Build a React Flow node dict for tests."""
    return {
        "id": id_,
        "type": "step",
        "position": {"x": 0, "y": 0},
        "data": {
            "type": type_,
            "label": id_,
            "system_prompt": f"You are the {type_} step.",
            **extra,
        },
    }


@pytest.fixture
def sample_flow() -> dict:
    """plan -> code -> test -> condition -> (review | code)."""
    return {
        "name": "Fix issue",
        "description": "sample",
        "nodes": [
            make_step("plan", "plan"),
            make_step("code", "code"),
            make_step("test", "test", on_fail="goto:code", retry_limit=2),
            make_step("check", "condition", expression="prev.success"),
            make_step("review", "review"),
        ],
        "edges": [
            {"id": "e1", "source": "plan", "target": "code"},
            {"id": "e2", "source": "code", "target": "test"},
            {"id": "e3", "source": "test", "target": "check"},
            {"id": "e4", "source": "check", "target": "review", "sourceHandle": "true"},
            {"id": "e5", "source": "check", "target": "code", "sourceHandle": "false"},
        ],
    }


@pytest.fixture(autouse=True)
def _fake_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests never touch DNS: every public-looking name resolves to a public address."""
    from app import net

    def resolve(host: str) -> list[str]:
        if host.endswith(".test-private"):
            return ["10.0.0.5"]
        return ["93.184.216.34"]

    monkeypatch.setattr(net, "resolver_override", resolve)


@pytest.fixture(autouse=True)
def industry_tools_db(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Industry tools open their own sessions: point them at the test database."""
    from app.tools import industry

    monkeypatch.setattr(industry, "session_factory", session_factory)
