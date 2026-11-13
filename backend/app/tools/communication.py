"""Communication tools: email (SMTP), Slack, Telegram."""

from __future__ import annotations

import asyncio
import smtplib
from email.message import EmailMessage
from typing import Any
from urllib.parse import unquote, urlparse

import httpx

from app.tools.base import Tool, ToolCategory, ToolContext, ToolError, schema


def parse_smtp_url(url: str) -> dict[str, Any]:
    """``smtp://user:pass@host:587`` or ``smtps://...@host:465`` (Gmail: app password)."""
    u = urlparse(url)
    if u.scheme not in ("smtp", "smtps") or not u.hostname:
        raise ToolError("smtp credential must look like smtp://user:password@host:587")
    return {
        "host": u.hostname,
        "port": u.port or (465 if u.scheme == "smtps" else 587),
        "user": unquote(u.username or ""),
        "password": unquote(u.password or ""),
        "ssl": u.scheme == "smtps",
    }


class SendEmail(Tool):
    name = "send_email"
    description = "Send an email through SMTP (credential 'smtp' = smtp://user:password@host:port; Gmail works with an app password)."
    category = ToolCategory.communication
    required_credentials = ("smtp",)
    input_schema = schema(
        {
            "to": {"type": "string", "description": "Comma-separated recipients"},
            "subject": {"type": "string"},
            "body": {"type": "string"},
            "html": {"type": "boolean", "description": "Body is HTML"},
        },
        ["to", "subject", "body"],
    )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        cfg = parse_smtp_url(ctx.need("smtp"))
        recipients = [a.strip() for a in args["to"].split(",") if a.strip()]
        if not recipients:
            raise ToolError("no recipients")
        msg = EmailMessage()
        msg["From"] = cfg["user"] or "flowforge@localhost"
        msg["To"] = ", ".join(recipients)
        msg["Subject"] = args["subject"]
        if args.get("html"):
            msg.set_content("This message requires an HTML capable client.")
            msg.add_alternative(args["body"], subtype="html")
        else:
            msg.set_content(args["body"])

        def _send() -> None:
            cls = smtplib.SMTP_SSL if cfg["ssl"] else smtplib.SMTP
            with cls(cfg["host"], cfg["port"], timeout=30) as s:
                if not cfg["ssl"]:
                    s.starttls()
                if cfg["user"]:
                    s.login(cfg["user"], cfg["password"])
                s.send_message(msg)

        try:
            await asyncio.to_thread(_send)
        except (smtplib.SMTPException, OSError) as exc:
            raise ToolError(f"smtp error: {exc.__class__.__name__}: {str(exc)[:200]}") from exc
        return f"sent to {', '.join(recipients)}"

    async def self_test(self, ctx: ToolContext) -> str:
        cfg = parse_smtp_url(ctx.need("smtp"))
        return f"smtp credential parsed for {cfg['host']}:{cfg['port']}"


class SlackPost(Tool):
    name = "slack_post"
    description = "Post a message to Slack (credential 'slack' = incoming webhook URL or bot token xoxb-…; with a token pass channel)."
    category = ToolCategory.communication
    required_credentials = ("slack",)
    input_schema = schema(
        {
            "text": {"type": "string"},
            "channel": {
                "type": "string",
                "description": "Required with a bot token, e.g. #general",
            },
        },
        ["text"],
    )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        cred = ctx.need("slack")
        try:
            if cred.startswith("https://hooks.slack.com/"):
                r = await ctx.client().post(cred, json={"text": args["text"]})
                if r.status_code >= 400:
                    raise ToolError(f"slack webhook HTTP {r.status_code}: {r.text[:200]}")
                return "posted via webhook"
            if not args.get("channel"):
                raise ToolError("channel is required when using a bot token")
            r = await ctx.client().post(
                "https://slack.com/api/chat.postMessage",
                headers={"authorization": f"Bearer {cred}"},
                json={"channel": args["channel"], "text": args["text"]},
            )
            data = r.json()
            if not data.get("ok"):
                raise ToolError(f"slack error: {data.get('error', r.status_code)}")
            return f"posted to {args['channel']} (ts {data.get('ts')})"
        except httpx.HTTPError as exc:
            raise ToolError(f"slack request failed: {exc.__class__.__name__}") from exc

    async def self_test(self, ctx: ToolContext) -> str:
        cred = ctx.need("slack")
        if cred.startswith("https://hooks.slack.com/"):
            return "webhook configured (not sent)"
        r = await ctx.client().post(
            "https://slack.com/api/auth.test", headers={"authorization": f"Bearer {cred}"}
        )
        data = r.json()
        if not data.get("ok"):
            raise ToolError(f"slack auth failed: {data.get('error')}")
        return f"authenticated as {data.get('user')} in {data.get('team')}"


class TelegramSend(Tool):
    name = "telegram_send"
    description = "Send a Telegram message via a bot (credential 'telegram' = bot token)."
    category = ToolCategory.communication
    required_credentials = ("telegram",)
    input_schema = schema(
        {"chat_id": {"type": "string"}, "text": {"type": "string"}}, ["chat_id", "text"]
    )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        token = ctx.need("telegram")
        try:
            r = await ctx.client().post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": args["chat_id"], "text": args["text"]},
            )
        except httpx.HTTPError as exc:
            raise ToolError(f"telegram request failed: {exc.__class__.__name__}") from exc
        data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        if r.status_code >= 400 or not data.get("ok"):
            raise ToolError(f"telegram error: {data.get('description', r.status_code)}")
        return f"sent message {data['result'].get('message_id')} to {args['chat_id']}"

    async def self_test(self, ctx: ToolContext) -> str:
        token = ctx.need("telegram")
        r = await ctx.client().get(f"https://api.telegram.org/bot{token}/getMe")
        data = r.json()
        if not data.get("ok"):
            raise ToolError(f"telegram auth failed: {data.get('description')}")
        return f"bot @{data['result'].get('username')}"


COMMUNICATION_TOOLS: list[Tool] = [SendEmail(), SlackPost(), TelegramSend()]
