"""Web tools: fetch a URL, search, and extract readable article text."""

from __future__ import annotations

import html
import re
from typing import Any
from urllib.parse import urlparse

import httpx

from app.net import UnsafeUrl, check_public_url_sync
from app.tools.base import Tool, ToolCategory, ToolContext, ToolError, schema, truncate

MAX_BODY = 2_000_000
UA = "FlowForge/1.0 (+https://github.com/flowforge)"


def _check_url(url: str) -> str:
    u = urlparse(url.strip())
    if u.scheme not in ("http", "https") or not u.netloc:
        raise ToolError("url must start with http:// or https://")
    try:
        return check_public_url_sync(url)
    except UnsafeUrl as exc:
        raise ToolError(str(exc)) from exc


def _strip_tags(text: str) -> str:
    text = re.sub(r"(?is)<(script|style|noscript).*?</\1>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n\n", text)).strip()


async def _get(ctx: ToolContext, url: str, **kw: Any) -> httpx.Response:
    try:
        r = await ctx.client().get(url, headers={"user-agent": UA}, **kw)
    except httpx.HTTPError as exc:
        raise ToolError(f"request failed: {exc.__class__.__name__}") from exc
    if r.status_code >= 400:
        raise ToolError(f"HTTP {r.status_code} from {url}")
    return r


class FetchUrl(Tool):
    name = "fetch_url"
    description = "Fetch a web page or API URL and return its text (HTML is stripped to text unless raw=true)."
    category = ToolCategory.web
    input_schema = schema(
        {
            "url": {"type": "string"},
            "raw": {
                "type": "boolean",
                "description": "Return the raw body instead of stripped text",
            },
            "max_chars": {"type": "integer", "description": "Default 20000"},
        },
        ["url"],
    )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        url = _check_url(args["url"])
        r = await _get(ctx, url)
        body = r.text[:MAX_BODY]
        ctype = r.headers.get("content-type", "")
        text = body if args.get("raw") or "html" not in ctype else _strip_tags(body)
        limit = max(500, min(int(args.get("max_chars") or 20_000), 200_000))
        return truncate(text, limit)

    async def self_test(self, ctx: ToolContext) -> str:
        r = await _get(ctx, "https://example.com/")
        return f"fetched example.com ({len(r.text)} bytes)"


class WebSearch(Tool):
    name = "web_search"
    description = "Search the web. Uses Tavily when a 'tavily' key is stored, otherwise DuckDuckGo HTML results."
    category = ToolCategory.web
    input_schema = schema(
        {
            "query": {"type": "string"},
            "max_results": {"type": "integer", "description": "Default 5, max 10"},
        },
        ["query"],
    )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        query = args["query"].strip()
        if not query:
            raise ToolError("query must not be empty")
        n = max(1, min(int(args.get("max_results") or 5), 10))
        key = ctx.credential("tavily")
        if key:
            return await self._tavily(ctx, key, query, n)
        return await self._duckduckgo(ctx, query, n)

    async def _tavily(self, ctx: ToolContext, key: str, query: str, n: int) -> str:
        try:
            r = await ctx.client().post(
                "https://api.tavily.com/search",
                json={"api_key": key, "query": query, "max_results": n, "include_answer": False},
            )
        except httpx.HTTPError as exc:
            raise ToolError(f"tavily request failed: {exc.__class__.__name__}") from exc
        if r.status_code >= 400:
            raise ToolError(f"tavily HTTP {r.status_code}")
        rows = r.json().get("results") or []
        return (
            "\n\n".join(
                f"{i + 1}. {x.get('title', '')}\n{x.get('url', '')}\n{(x.get('content') or '')[:500]}"
                for i, x in enumerate(rows[:n])
            )
            or "(no results)"
        )

    async def _duckduckgo(self, ctx: ToolContext, query: str, n: int) -> str:
        r = await _get(ctx, "https://html.duckduckgo.com/html/", params={"q": query})
        items = re.findall(
            r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>.*?'
            r'(?:<a[^>]+class="result__snippet"[^>]*>(.*?)</a>)?',
            r.text,
            flags=re.DOTALL,
        )
        out = []
        for href, title, snippet in items[:n]:
            out.append(
                f"{len(out) + 1}. {_strip_tags(title)}\n{html.unescape(href)}\n{_strip_tags(snippet)[:300]}"
            )
        return "\n\n".join(out) or "(no results)"


class Scrape(Tool):
    name = "scrape"
    description = (
        "Extract the main readable article text (and title) from a web page using readability."
    )
    category = ToolCategory.web
    input_schema = schema({"url": {"type": "string"}, "max_chars": {"type": "integer"}}, ["url"])

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        from readability import Document

        url = _check_url(args["url"])
        r = await _get(ctx, url)
        try:
            doc = Document(r.text[:MAX_BODY])
            title, summary_html = doc.title(), doc.summary(html_partial=True)
        except Exception as exc:
            raise ToolError(f"could not parse page: {exc.__class__.__name__}") from exc
        text = _strip_tags(summary_html)
        limit = max(500, min(int(args.get("max_chars") or 20_000), 200_000))
        return truncate(f"# {title}\n\n{text}", limit)


WEB_TOOLS: list[Tool] = [FetchUrl(), WebSearch(), Scrape()]
