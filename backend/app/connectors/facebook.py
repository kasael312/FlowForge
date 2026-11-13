"""Facebook Page / Messenger: webhook verification + events, Send API with message tags.

Secrets: ``page_access_token``, ``app_secret``. Config: ``page_id``, ``page_name``.
The Meta verify token is the connector's ``webhook_token``.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime
from typing import Any

import httpx

from app.connectors.base import ConnectorError, SendResult, TestResult, client, registry, secrets_of
from app.connectors.models import Connector
from app.connectors.policy import FB_TAGS

GRAPH = "https://graph.facebook.com/v19.0"


def verify_signature(c: Connector, raw_body: bytes, header: str | None) -> bool:
    """``X-Hub-Signature-256: sha256=<hmac(app_secret, body)>``; True when no secret is configured."""
    secret = secrets_of(c).get("app_secret")
    if not secret:
        return True
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(header[7:], expected)


def parse_events(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Text messages from a webhook payload → ``[{thread_id, text, external_id, at}]``."""
    out = []
    if payload.get("object") != "page":
        return out
    for entry in payload.get("entry") or []:
        for ev in entry.get("messaging") or []:
            msg = ev.get("message") or {}
            sender = (ev.get("sender") or {}).get("id")
            if not sender or not msg or msg.get("is_echo"):
                continue
            text = msg.get("text") or ("[tệp đính kèm]" if msg.get("attachments") else "")
            if not text:
                continue
            ts = ev.get("timestamp")
            at = (
                datetime.fromtimestamp(int(ts) / 1000, tz=UTC)
                if isinstance(ts, int | float)
                else datetime.now(UTC)
            )
            out.append(
                {
                    "thread_id": str(sender),
                    "text": str(text),
                    "external_id": str(msg.get("mid") or ""),
                    "at": at,
                }
            )
    return out


async def _graph(c: Connector, method: str, path: str, **kw: Any) -> dict[str, Any]:
    token = secrets_of(c).get("page_access_token")
    if not token:
        raise ConnectorError("Chưa có Page access token.")
    params = dict(kw.pop("params", {}) or {})
    params["access_token"] = token
    async with client() as http:
        r = await http.request(method, f"{GRAPH}{path}", params=params, **kw)
    try:
        data = r.json()
    except ValueError as exc:
        raise ConnectorError(
            f"Facebook trả về dữ liệu không hợp lệ (HTTP {r.status_code})"
        ) from exc
    if r.status_code >= 400 or "error" in data:
        err = data.get("error") or {}
        raise ConnectorError(
            f"Facebook lỗi {err.get('code', r.status_code)}: {err.get('message') or 'không rõ'}"
        )
    return data


async def test_connection(c: Connector) -> TestResult:
    try:
        me = await _graph(c, "GET", "/me", params={"fields": "id,name"})
    except (ConnectorError, httpx.HTTPError) as exc:
        return TestResult(False, f"Không kết nối được Facebook Page: {exc}")
    cfg = dict(c.config or {})
    cfg["page_id"] = str(me.get("id") or cfg.get("page_id") or "")
    cfg["page_name"] = str(me.get("name") or "")
    c.config = cfg
    return TestResult(True, f"Đã kết nối Page “{cfg['page_name']}”.", {"page_id": cfg["page_id"]})


async def send_text(c: Connector, psid: str, text: str, tag: str | None = None) -> SendResult:
    """``RESPONSE`` inside 24h; outside it a ``MESSAGE_TAG`` from Meta's allowed list is required."""
    body: dict[str, Any] = {"recipient": {"id": psid}, "message": {"text": text}}
    if tag:
        if tag not in FB_TAGS:
            raise ConnectorError(f"Tag {tag} không nằm trong danh sách Meta cho phép.")
        body["messaging_type"] = "MESSAGE_TAG"
        body["tag"] = tag
    else:
        body["messaging_type"] = "RESPONSE"
    data = await _graph(c, "POST", "/me/messages", json=body)
    return SendResult(
        external_id=str(data.get("message_id") or ""), method="tagged" if tag else "message"
    )


registry["facebook"] = lambda: {"test": test_connection, "send_text": send_text}
