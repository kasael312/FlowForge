"""Connector plumbing: secrets, HTTP client, errors, registry."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

from app.connectors.models import Connector
from app.crypto import DecryptError, decrypt, encrypt

#: Tests replace outbound HTTP with ``httpx.MockTransport``.
transport_override: httpx.AsyncBaseTransport | None = None


class ConnectorError(Exception):
    """Delivery / verification failed; the message is user-facing (Vietnamese)."""


@dataclass
class TestResult:
    ok: bool
    message: str
    details: dict[str, Any] | None = None


@dataclass
class SendResult:
    external_id: str | None
    #: ``message`` (free reply), ``zns`` (paid template) or ``tagged`` (Meta message tag).
    method: str
    cost_estimate: float = 0.0


def secrets_of(c: Connector) -> dict[str, str]:
    if not c.secrets_encrypted:
        return {}
    try:
        return json.loads(decrypt(c.secrets_encrypted))
    except (DecryptError, ValueError):
        return {}


def set_secrets(c: Connector, values: dict[str, str]) -> None:
    """Merge: empty strings keep the stored value; ``None`` removes it."""
    current = secrets_of(c)
    for k, v in values.items():
        if v is None:
            current.pop(k, None)
        elif v != "":
            current[k] = v
    c.secrets_encrypted = encrypt(json.dumps(current)) if current else None


def client(timeout: float = 20.0) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=timeout, transport=transport_override)


#: ``kind -> factory``; filled by the concrete modules on import.
registry: dict[str, Callable[[], Any]] = {}
