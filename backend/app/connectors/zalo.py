"""Zalo Official Account: webhook events, CS messages (24h) and ZNS templates.

Secrets: ``access_token``, ``refresh_token``, ``app_secret`` (OA secret key, used both to
verify ``X-ZEvent-Signature`` and to refresh tokens). Config: ``app_id``, ``oa_id``,
``oa_name``, ``zns_templates`` = ``[{id, name, price, params[]}]``.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

import httpx

from app.connectors.base import (
    ConnectorError,
    SendResult,
    TestResult,
    client,
    registry,
    secrets_of,
    set_secrets,
)
from app.connectors.models import Connector

OPENAPI = "https://openapi.zalo.me"
BUSINESS = "https://business.openapi.zalo.me"
OAUTH = "https://oauth.zaloapp.com/v4/oa/access_token"


def verify_signature(c: Connector, raw_body: bytes, signature: str | None, timestamp: str) -> bool:
    """``mac=sha256(app_id + body + timestamp + oa_secret_key)``; True when no secret is configured."""
    secret = secrets_of(c).get("app_secret")
    if not secret:
        return True
    if not signature:
        return False
    mac = signature.split("mac=", 1)[-1].strip()
    expected = hashlib.sha256(
        (
            str((c.config or {}).get("app_id", ""))
            + raw_body.decode("utf-8", "replace")
            + timestamp
            + secret
        ).encode()
    ).hexdigest()
    return mac == expected


def parse_event(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Normalise a webhook event into ``{thread_id, text, external_id, at}`` (None if not a text message)."""
    name = str(payload.get("event_name") or "")
    if name not in ("user_send_text", "user_send_image", "user_send_sticker", "user_send_file"):
        return None
    sender = (payload.get("sender") or {}).get("id")
    if not sender:
        return None
    msg = payload.get("message") or {}
    text = str(
        msg.get("text")
        or ("[hình ảnh]" if name == "user_send_image" else f"[{name.removeprefix('user_send_')}]")
    )
    ts = payload.get("timestamp")
    at = datetime.fromtimestamp(int(ts) / 1000, tz=UTC) if str(ts).isdigit() else datetime.now(UTC)
    return {
        "thread_id": str(sender),
        "text": text,
        "external_id": str(msg.get("msg_id") or ""),
        "at": at,
    }


async def _refresh_token(c: Connector) -> str:
    s = secrets_of(c)
    if not (s.get("refresh_token") and s.get("app_secret") and (c.config or {}).get("app_id")):
        raise ConnectorError(
            "Access token hết hạn và không có refresh token / secret key để làm mới."
        )
    async with client() as http:
        r = await http.post(
            OAUTH,
            headers={
                "secret_key": s["app_secret"],
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={
                "refresh_token": s["refresh_token"],
                "app_id": str(c.config["app_id"]),
                "grant_type": "refresh_token",
            },
        )
    data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if r.status_code != 200 or not data.get("access_token"):
        raise ConnectorError(
            f"Làm mới token Zalo thất bại: {data.get('error_description') or data.get('error_name') or r.status_code}"
        )
    set_secrets(
        c, {"access_token": data["access_token"], "refresh_token": data.get("refresh_token") or ""}
    )
    return data["access_token"]


async def _call(c: Connector, method: str, url: str, **kw: Any) -> dict[str, Any]:
    """Zalo responds 200 with ``error != 0`` on failures; -216 = expired token → refresh once."""
    token = secrets_of(c).get("access_token")
    if not token:
        raise ConnectorError("Chưa có access token Zalo OA.")
    for attempt in (1, 2):
        async with client() as http:
            r = await http.request(
                method,
                url,
                headers={"access_token": token, "Content-Type": "application/json"},
                **kw,
            )
        try:
            data = r.json()
        except ValueError as exc:
            raise ConnectorError(
                f"Zalo trả về dữ liệu không hợp lệ (HTTP {r.status_code})"
            ) from exc
        err = int(data.get("error", 0) or 0)
        if err == 0:
            return data.get("data") if isinstance(data.get("data"), dict) else data
        if err in (-216, -124) and attempt == 1:  # access token invalid / expired
            token = await _refresh_token(c)
            continue
        raise ConnectorError(f"Zalo lỗi {err}: {data.get('message') or 'không rõ'}")
    raise ConnectorError("Zalo: không gửi được")  # pragma: no cover


async def test_connection(c: Connector) -> TestResult:
    try:
        info = await _call(c, "GET", f"{OPENAPI}/v2.0/oa/getoa")
    except (ConnectorError, httpx.HTTPError) as exc:
        return TestResult(False, f"Không kết nối được Zalo OA: {exc}")
    name = str(info.get("name") or "")
    cfg = dict(c.config or {})
    cfg["oa_name"] = name
    cfg["oa_id"] = str(info.get("oa_id") or cfg.get("oa_id") or "")
    c.config = cfg
    return TestResult(
        True,
        f"Đã kết nối OA “{name}”.",
        {"oa_id": cfg["oa_id"], "followers": info.get("num_follower")},
    )


async def send_text(c: Connector, user_id: str, text: str) -> SendResult:
    """Customer-service message: only valid inside the 24h window."""
    data = await _call(
        c,
        "POST",
        f"{OPENAPI}/v3.0/oa/message/cs",
        json={"recipient": {"user_id": user_id}, "message": {"text": text}},
    )
    return SendResult(external_id=str(data.get("message_id") or ""), method="message")


def template_by_id(c: Connector, template_id: str) -> dict[str, Any] | None:
    for t in (c.config or {}).get("zns_templates") or []:
        if str(t.get("id")) == str(template_id):
            return t
    return None


async def send_zns(
    c: Connector, phone: str, template_id: str, params: dict[str, Any], tracking_id: str = ""
) -> SendResult:
    """Zalo Notification Service: paid template message to a phone number (works outside 24h)."""
    tpl = template_by_id(c, template_id)
    if tpl is None:
        raise ConnectorError(f"Mẫu ZNS {template_id} chưa được khai báo trong Kết nối.")
    intl = "84" + phone[1:] if phone.startswith("0") else phone
    data = await _call(
        c,
        "POST",
        f"{BUSINESS}/message/template",
        json={
            "phone": intl,
            "template_id": str(template_id),
            "template_data": params,
            "tracking_id": tracking_id or f"ff-{datetime.now(UTC).timestamp():.0f}",
        },
    )
    return SendResult(
        external_id=str(data.get("msg_id") or ""),
        method="zns",
        cost_estimate=float(tpl.get("price") or 0),
    )


registry["zalo"] = lambda: {"test": test_connection, "send_text": send_text, "send_zns": send_zns}
