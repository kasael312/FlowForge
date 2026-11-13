"""Dashboard stats and password change."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import Flow, Run, RunStatus
from tests.conftest import DEFAULT_PASSWORD, login


def _seed(db: Session, user_id: str) -> None:
    flow = Flow(user_id=user_id, name="f", nodes=[], edges=[])
    db.add(flow)
    db.flush()
    now = datetime.now(UTC)
    rows = [
        (RunStatus.success, now),
        (RunStatus.failed, now - timedelta(minutes=5)),
        (RunStatus.success, now - timedelta(days=2)),
        (RunStatus.cancelled, now - timedelta(days=3)),
        (RunStatus.running, now - timedelta(days=4)),
        (RunStatus.success, now - timedelta(days=5)),
    ]
    for status, created in rows:
        db.add(
            Run(
                flow_id=flow.id,
                user_id=user_id,
                repo_url="https://x/y",
                status=status,
                created_at=created,
            )
        )
    db.commit()


def test_stats(anon_client: TestClient, db_session: Session) -> None:
    uid = login(anon_client, "s@example.com")
    assert anon_client.get("/stats").json() == {
        "flows": 0,
        "runs_total": 0,
        "runs_today": 0,
        "runs_active": 0,
        "success_rate": None,
        "recent_runs": [],
        "cost_by_model": [],
        "total_cost_usd": 0.0,
    }
    _seed(db_session, uid)
    body = anon_client.get("/stats").json()
    assert body["flows"] == 1 and body["runs_total"] == 6 and body["runs_active"] == 1
    assert body["runs_today"] == 2
    assert body["success_rate"] == 3 / 5  # 3 success out of 5 finished
    assert [r["status"] for r in body["recent_runs"]] == [
        "success",
        "failed",
        "success",
        "cancelled",
        "running",
    ]

    # another user sees their own (empty) numbers
    login(anon_client, "t@example.com")
    assert anon_client.get("/stats").json()["runs_total"] == 0
    anon_client.cookies.clear()
    assert anon_client.get("/stats").status_code == 401


def test_change_password(anon_client: TestClient) -> None:
    login(anon_client, "p@example.com")
    bad = anon_client.post(
        "/auth/password", json={"current_password": "nope-nope", "new_password": "new-password-1"}
    )
    assert bad.status_code == 400
    assert (
        anon_client.post(
            "/auth/password", json={"current_password": DEFAULT_PASSWORD, "new_password": "short"}
        ).status_code
        == 422
    )
    ok = anon_client.post(
        "/auth/password",
        json={"current_password": DEFAULT_PASSWORD, "new_password": "new-password-1"},
    )
    assert ok.status_code == 204
    anon_client.cookies.clear()
    assert (
        anon_client.post(
            "/auth/login", json={"email": "p@example.com", "password": DEFAULT_PASSWORD}
        ).status_code
        == 401
    )
    assert (
        anon_client.post(
            "/auth/login", json={"email": "p@example.com", "password": "new-password-1"}
        ).status_code
        == 200
    )
