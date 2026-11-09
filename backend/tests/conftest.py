"""Shared fixtures: in-memory SQLite database, TestClient and executor fakes."""

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import models  # noqa: F401  (register tables)
from app.db import Base, get_db
from app.executor.agent import AgentConfig
from app.executor.engine import ExecutorDeps
from app.executor.events import RunEventBus
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
def client(db_session: Session) -> Generator[TestClient, None, None]:
    """TestClient whose ``get_db`` dependency is overridden with the test session."""

    def _override() -> Generator[Session, None, None]:
        yield db_session

    app.dependency_overrides[get_db] = _override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


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
        client=fake_client,  # type: ignore[arg-type]
        agent_config=AgentConfig(model="claude-test", use_fallbacks=True, effort="medium"),
        sandbox_factory=lambda run_id, host_path: fake_sandbox,
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
