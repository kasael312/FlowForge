"""Integrations: Google Sheets, Notion, GitHub issues, generic webhooks."""

from __future__ import annotations

import json
import time
from typing import Any
from urllib.parse import urlparse

import httpx

from app.net import UnsafeUrl, check_public_url
from app.tools.base import Tool, ToolCategory, ToolContext, ToolError, schema, truncate


async def _json(resp: httpx.Response, what: str) -> dict[str, Any]:
    if resp.status_code >= 400:
        raise ToolError(f"{what} HTTP {resp.status_code}: {resp.text[:300]}")
    try:
        return resp.json()
    except ValueError as exc:
        raise ToolError(f"{what}: invalid JSON response") from exc


# ---------------------------------------------------------------- Google Sheets


async def google_access_token(ctx: ToolContext, service_account_json: str) -> str:
    """OAuth2 token for a service account (JWT bearer grant, Sheets scope)."""
    import jwt

    try:
        sa = json.loads(service_account_json)
        email, key = sa["client_email"], sa["private_key"]
    except (ValueError, KeyError) as exc:
        raise ToolError("google credential must be the service-account JSON") from exc
    now = int(time.time())
    assertion = jwt.encode(
        {
            "iss": email,
            "scope": "https://www.googleapis.com/auth/spreadsheets",
            "aud": "https://oauth2.googleapis.com/token",
            "iat": now,
            "exp": now + 3600,
        },
        key,
        algorithm="RS256",
    )
    r = await ctx.client().post(
        "https://oauth2.googleapis.com/token",
        data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion},
    )
    data = await _json(r, "google token")
    return str(data["access_token"])


class GoogleSheets(Tool):
    name = "google_sheets"
    description = (
        "Append rows to, or read a range from, a Google Sheet (credential 'google' = service-account JSON; "
        "share the sheet with the service account email)."
    )
    category = ToolCategory.integrations
    required_credentials = ("google",)
    input_schema = schema(
        {
            "spreadsheet_id": {"type": "string"},
            "action": {"type": "string", "enum": ["append", "read"]},
            "range": {"type": "string", "description": "A1 range, e.g. Sheet1!A:D"},
            "rows": {
                "type": "array",
                "items": {"type": "array", "items": {"type": "string"}},
                "description": "Rows to append",
            },
        },
        ["spreadsheet_id", "action", "range"],
    )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        token = await google_access_token(ctx, ctx.need("google"))
        base = f"https://sheets.googleapis.com/v4/spreadsheets/{args['spreadsheet_id']}/values/{args['range']}"
        headers = {"authorization": f"Bearer {token}"}
        try:
            if args["action"] == "append":
                rows = args.get("rows") or []
                if not rows:
                    raise ToolError("rows are required for append")
                r = await ctx.client().post(
                    f"{base}:append",
                    params={"valueInputOption": "USER_ENTERED"},
                    headers=headers,
                    json={"values": rows},
                )
                data = await _json(r, "sheets append")
                return (
                    f"appended {len(rows)} rows: {data.get('updates', {}).get('updatedRange', '')}"
                )
            r = await ctx.client().get(base, headers=headers)
            data = await _json(r, "sheets read")
            values = data.get("values") or []
            return truncate("\n".join(",".join(str(c) for c in row) for row in values) or "(empty)")
        except httpx.HTTPError as exc:
            raise ToolError(f"sheets request failed: {exc.__class__.__name__}") from exc


# ---------------------------------------------------------------- Notion


class Notion(Tool):
    name = "notion"
    description = "Create a Notion page in a database (properties as JSON) or append paragraphs to a page (credential 'notion' = integration token)."
    category = ToolCategory.integrations
    required_credentials = ("notion",)
    input_schema = schema(
        {
            "action": {"type": "string", "enum": ["create_page", "append_text"]},
            "database_id": {"type": "string", "description": "For create_page"},
            "page_id": {"type": "string", "description": "For append_text"},
            "title": {"type": "string", "description": "Title property for create_page"},
            "properties": {
                "type": "object",
                "description": "Extra Notion properties JSON",
                "additionalProperties": True,
            },
            "text": {
                "type": "string",
                "description": "Paragraph text (create_page body or append_text)",
            },
        },
        ["action"],
    )

    def _headers(self, ctx: ToolContext) -> dict[str, str]:
        return {
            "authorization": f"Bearer {ctx.need('notion')}",
            "notion-version": "2022-06-28",
            "content-type": "application/json",
        }

    @staticmethod
    def _blocks(text: str) -> list[dict[str, Any]]:
        return [
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {"rich_text": [{"type": "text", "text": {"content": p[:2000]}}]},
            }
            for p in text.split("\n\n")
            if p.strip()
        ]

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        try:
            if args["action"] == "create_page":
                if not args.get("database_id"):
                    raise ToolError("database_id is required")
                props: dict[str, Any] = dict(args.get("properties") or {})
                if args.get("title"):
                    props.setdefault("Name", {"title": [{"text": {"content": args["title"]}}]})
                body: dict[str, Any] = {
                    "parent": {"database_id": args["database_id"]},
                    "properties": props,
                }
                if args.get("text"):
                    body["children"] = self._blocks(args["text"])
                r = await ctx.client().post(
                    "https://api.notion.com/v1/pages", headers=self._headers(ctx), json=body
                )
                data = await _json(r, "notion")
                return f"created page {data.get('id')} {data.get('url', '')}"
            if not args.get("page_id") or not args.get("text"):
                raise ToolError("page_id and text are required")
            r = await ctx.client().patch(
                f"https://api.notion.com/v1/blocks/{args['page_id']}/children",
                headers=self._headers(ctx),
                json={"children": self._blocks(args["text"])},
            )
            await _json(r, "notion")
            return f"appended text to {args['page_id']}"
        except httpx.HTTPError as exc:
            raise ToolError(f"notion request failed: {exc.__class__.__name__}") from exc

    async def self_test(self, ctx: ToolContext) -> str:
        r = await ctx.client().get("https://api.notion.com/v1/users/me", headers=self._headers(ctx))
        data = await _json(r, "notion")
        return f"authenticated as {data.get('name') or data.get('id')}"


# ---------------------------------------------------------------- GitHub


class GithubIssue(Tool):
    name = "github_issue"
    description = "Create a GitHub issue (credential 'github' = personal access token)."
    category = ToolCategory.integrations
    required_credentials = ("github",)
    input_schema = schema(
        {
            "repo": {"type": "string", "description": "owner/name"},
            "title": {"type": "string"},
            "body": {"type": "string"},
            "labels": {"type": "array", "items": {"type": "string"}},
        },
        ["repo", "title"],
    )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        repo = args["repo"].strip()
        if repo.count("/") != 1:
            raise ToolError("repo must be owner/name")
        try:
            r = await ctx.client().post(
                f"https://api.github.com/repos/{repo}/issues",
                headers={
                    "authorization": f"Bearer {ctx.need('github')}",
                    "accept": "application/vnd.github+json",
                },
                json={
                    "title": args["title"],
                    "body": args.get("body") or "",
                    "labels": args.get("labels") or [],
                },
            )
        except httpx.HTTPError as exc:
            raise ToolError(f"github request failed: {exc.__class__.__name__}") from exc
        data = await _json(r, "github")
        return f"created issue #{data.get('number')}: {data.get('html_url')}"

    async def self_test(self, ctx: ToolContext) -> str:
        r = await ctx.client().get(
            "https://api.github.com/user", headers={"authorization": f"Bearer {ctx.need('github')}"}
        )
        data = await _json(r, "github")
        return f"authenticated as {data.get('login')}"


# ---------------------------------------------------------------- Webhook


class WebhookCall(Tool):
    name = "webhook_call"
    description = "Call an HTTP endpoint (POST JSON by default). Returns status and body."
    category = ToolCategory.integrations
    input_schema = schema(
        {
            "url": {"type": "string"},
            "method": {"type": "string", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"]},
            "json": {"type": "object", "additionalProperties": True},
            "headers": {"type": "object", "additionalProperties": {"type": "string"}},
        },
        ["url"],
    )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        u = urlparse(args["url"])
        if u.scheme not in ("http", "https") or not u.netloc:
            raise ToolError("url must be http(s)")
        try:
            await check_public_url(args["url"])
        except UnsafeUrl as exc:
            raise ToolError(str(exc)) from exc
        method = (args.get("method") or "POST").upper()
        try:
            r = await ctx.client().request(
                method, args["url"], json=args.get("json"), headers=args.get("headers") or {}
            )
        except httpx.HTTPError as exc:
            raise ToolError(f"request failed: {exc.__class__.__name__}") from exc
        return f"status: {r.status_code}\n{truncate(r.text, 10_000)}"


INTEGRATION_TOOLS: list[Tool] = [GoogleSheets(), Notion(), GithubIssue(), WebhookCall()]
