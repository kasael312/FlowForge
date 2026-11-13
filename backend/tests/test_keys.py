"""API keys: encryption at rest, no leakage, per-user isolation, executor usage."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import crypto
from app.models import ApiKey, KeyProvider, User
from app.services import keys as key_service
from app.services.keys import get_user_key
from tests.conftest import login

SECRET = "sk-ant-api03-ThisIsAFakeKeyForTests-0000abcd"


def test_create_returns_only_last4_and_encrypts(client: TestClient, db_session: Session) -> None:
    r = client.post("/keys", json={"provider": "anthropic", "label": "work", "value": SECRET})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["last4"] == "abcd" and body["label"] == "work" and body["provider"] == "anthropic"
    assert SECRET not in r.text and "value" not in body and "encrypted" not in r.text

    row = db_session.scalar(select(ApiKey))
    assert row is not None
    assert SECRET not in row.encrypted_value
    assert crypto.decrypt(row.encrypted_value) == SECRET

    listed = client.get("/keys").json()
    assert [k["last4"] for k in listed] == ["abcd"]
    assert SECRET not in client.get("/keys").text


def test_validation(client: TestClient) -> None:
    assert client.post("/keys", json={"provider": "hugging", "value": SECRET}).status_code == 422
    assert client.post("/keys", json={"provider": "openai", "value": "short"}).status_code == 422
    assert (
        client.post(
            "/keys", json={"provider": "openai", "value": "has space inside yes"}
        ).status_code
        == 422
    )


def test_delete_and_isolation(anon_client: TestClient) -> None:
    login(anon_client, "a@example.com")
    key_id = anon_client.post(
        "/keys", json={"provider": "github", "value": "ghp_" + "x" * 30}
    ).json()["id"]

    login(anon_client, "b@example.com")
    assert anon_client.get("/keys").json() == []
    assert anon_client.delete(f"/keys/{key_id}").status_code == 404
    assert anon_client.post(f"/keys/{key_id}/test").status_code == 404

    login(anon_client, "a@example.com")
    assert anon_client.delete(f"/keys/{key_id}").status_code == 204
    assert anon_client.get("/keys").json() == []
    assert anon_client.delete(f"/keys/{key_id}").status_code == 404


def test_requires_auth(anon_client: TestClient) -> None:
    assert anon_client.get("/keys").status_code == 401
    assert (
        anon_client.post("/keys/test", json={"provider": "openai", "value": SECRET}).status_code
        == 401
    )


def test_test_endpoints_use_provider_check(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[KeyProvider, str]] = []

    async def fake_test(provider: KeyProvider, value: str) -> tuple[bool, str]:
        calls.append((provider, value))
        return value.endswith("good"), "checked"

    monkeypatch.setattr(key_service, "test_key", fake_test)

    r = client.post("/keys/test", json={"provider": "openai", "value": "sk-something-good"})
    assert r.json() == {"ok": True, "message": "checked"}
    r = client.post("/keys/test", json={"provider": "openai", "value": "sk-something-bad!"})
    assert r.json()["ok"] is False

    key_id = client.post(
        "/keys", json={"provider": "anthropic", "value": "sk-ant-stored-good"}
    ).json()["id"]
    r = client.post(f"/keys/{key_id}/test")
    assert r.json()["ok"] is True
    assert calls[-1] == (KeyProvider.anthropic, "sk-ant-stored-good")


def test_get_user_key_picks_newest_and_handles_bad_secret(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = User(email="k@example.com", password_hash="x")
    db_session.add(user)
    db_session.flush()
    db_session.add(
        ApiKey(
            user_id=user.id,
            provider=KeyProvider.anthropic,
            encrypted_value=crypto.encrypt("old-key-1"),
            last4="ey-1",
        )
    )
    db_session.add(
        ApiKey(
            user_id=user.id,
            provider=KeyProvider.anthropic,
            encrypted_value=crypto.encrypt("new-key-2"),
            last4="ey-2",
        )
    )
    db_session.add(
        ApiKey(
            user_id=user.id,
            provider=KeyProvider.openai,
            encrypted_value=crypto.encrypt("oa-key"),
            last4="-key",
        )
    )
    db_session.commit()
    assert get_user_key(db_session, user.id, KeyProvider.anthropic) == "new-key-2"
    assert get_user_key(db_session, user.id, KeyProvider.openai) == "oa-key"
    assert get_user_key(db_session, user.id, KeyProvider.github) is None

    # a secret rotation makes the stored value unreadable -> None, not a crash
    monkeypatch.setattr(
        crypto,
        "_fernet",
        crypto.Fernet(crypto.Fernet.generate_key())
        and (lambda: crypto.Fernet(crypto.Fernet.generate_key())),
    )
    assert get_user_key(db_session, user.id, KeyProvider.anthropic) is None
