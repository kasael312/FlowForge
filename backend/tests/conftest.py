"""Shared fixtures: in-memory SQLite database and a TestClient bound to it."""

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import models  # noqa: F401  (register tables)
from app.db import Base, get_db
from app.main import app


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    """Fresh in-memory database per test."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


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
def sample_flow() -> dict:
    """A small valid flow: plan -> code -> test -> condition -> (review | code)."""

    def step(id_: str, type_: str, **extra: object) -> dict:
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

    return {
        "name": "Fix issue",
        "description": "sample",
        "nodes": [
            step("plan", "plan"),
            step("code", "code"),
            step("test", "test", on_fail="goto:code", retry_limit=2),
            step("check", "condition", expression="prev.success"),
            step("review", "review"),
        ],
        "edges": [
            {"id": "e1", "source": "plan", "target": "code"},
            {"id": "e2", "source": "code", "target": "test"},
            {"id": "e3", "source": "test", "target": "check"},
            {"id": "e4", "source": "check", "target": "review", "sourceHandle": "true"},
            {"id": "e5", "source": "check", "target": "code", "sourceHandle": "false"},
        ],
    }
