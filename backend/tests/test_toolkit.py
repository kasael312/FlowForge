"""Tool plugin registry, schema generation for two providers, every tool, MCP."""

from __future__ import annotations

import json
import smtplib
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from mcp.client._memory import InMemoryTransport
from mcp.server.mcpserver import MCPServer
from sqlalchemy.orm import Session

from app import crypto
from app.executor.engine import ExecutorDeps, FlowExecutor
from app.executor.tools import ToolExecutor
from app.llm.base import ToolSpec
from app.llm.openai_compat import to_openai_tools
from app.models import ApiKey, KeyProvider, McpServer, Run, RunStatus, StepRun, User
from app.services import mcp as mcp_service
from app.tools import BUILTIN_TOOLS, ToolCategory, ToolContext, ToolError, registry
from app.tools.files import build_document, extract_text
from app.tools.registry import ToolRegistry
from tests.conftest import login, make_step
from tests.fakes import FakeAnthropic, FakeSandbox, text_response, tool_response

# ---------------------------------------------------------------- registry & schemas


def test_registry_lists_every_category_and_generates_schemas() -> None:
    names = registry.names()
    assert {
        "read_file",
        "fetch_url",
        "read_document",
        "query_sql",
        "send_email",
        "notion",
        "generate_image",
    } <= set(names)
    cats = {c: [t.name for t in ts] for c, ts in registry.by_category().items()}
    assert cats[ToolCategory.code] == [
        "read_file",
        "write_file",
        "list_files",
        "run_shell",
        "git_diff",
    ]
    assert set(cats[ToolCategory.web]) == {"fetch_url", "web_search", "scrape"}
    assert set(cats[ToolCategory.communication]) == {"send_email", "slack_post", "telegram_send"}
    assert len(BUILTIN_TOOLS) == len(names)

    # canonical definition -> Anthropic passes through, OpenAI wraps as function
    defs = registry.definitions(["read_file", "web_search", "nope"])
    assert [d["name"] for d in defs] == ["read_file", "web_search"]
    for d in defs:
        assert (
            d["input_schema"]["type"] == "object"
            and d["input_schema"]["additionalProperties"] is False
        )
        assert "required" in d["input_schema"]
    oa = to_openai_tools([ToolSpec.from_dict(d) for d in defs])
    assert oa[0]["type"] == "function" and oa[0]["function"]["name"] == "read_file"
    assert oa[1]["function"]["parameters"] == defs[1]["input_schema"]

    r = ToolRegistry()
    r.register(BUILTIN_TOOLS[0])
    with pytest.raises(ValueError):
        r.register(BUILTIN_TOOLS[0])
    assert r.is_known("mcp:srv:anything") and not r.is_known("bogus")
    with pytest.raises(ToolError):
        r.get("bogus")


def test_describe_reports_missing_credentials() -> None:
    ctx = ToolContext(credential=lambda p: "x" if p == "slack" else None)
    rows = {r["name"]: r for r in registry.describe(ctx)}
    assert rows["slack_post"]["status"] == "ready"
    assert rows["telegram_send"]["status"] == "missing_credentials"
    assert rows["telegram_send"]["missing_credentials"] == ["telegram"]
    assert rows["read_file"]["needs_sandbox"] is True and rows["read_file"]["status"] == "ready"


def test_step_schema_accepts_registry_tools_and_rejects_unknown(
    client: TestClient, sample_flow: dict
) -> None:
    node = make_step(
        "w", "custom", system_prompt="x", tools_allowed=["fetch_url", "web_search", "mcp:abc:do_it"]
    )
    r = client.post("/flows", json={"name": "t", "nodes": [node], "edges": []})
    assert r.status_code == 201, r.text
    assert r.json()["nodes"][0]["data"]["tools_allowed"] == [
        "fetch_url",
        "web_search",
        "mcp:abc:do_it",
    ]
    node["data"]["tools_allowed"] = ["fetch_url", "rm_rf"]
    r = client.post("/flows", json={"name": "t", "nodes": [node], "edges": []})
    assert r.status_code == 422 and "rm_rf" in r.text


# ---------------------------------------------------------------- helpers


def mock_ctx(
    handler, creds: dict[str, str] | None = None, sandbox: FakeSandbox | None = None
) -> ToolContext:
    return ToolContext(
        credential=lambda p: (creds or {}).get(p),
        http=httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True),
        sandbox=sandbox,
        shell_whitelist=frozenset({"ls"}),
    )


async def run(name: str, args: dict[str, Any], ctx: ToolContext) -> str:
    return await registry.get(name).run(args, ctx)


# ---------------------------------------------------------------- web


async def test_fetch_url_and_scrape() -> None:
    html = "<html><head><title>Hello Page</title></head><body><script>x()</script><article><h1>Hi</h1><p>Body &amp; text</p></article></body></html>"

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.host == "api.example":
            return httpx.Response(200, json={"a": 1}, headers={"content-type": "application/json"})
        return httpx.Response(200, text=html, headers={"content-type": "text/html"})

    ctx = mock_ctx(handler)
    out = await run("fetch_url", {"url": "https://example.com/page"}, ctx)
    assert "Body & text" in out and "x()" not in out and "<p>" not in out
    assert await run("fetch_url", {"url": "https://api.example/x"}, ctx) == '{"a":1}'
    out = await run("scrape", {"url": "https://example.com/page"}, ctx)
    assert out.startswith("# Hello Page") and "Body & text" in out
    with pytest.raises(ToolError):
        await run("fetch_url", {"url": "http://localhost:8800/admin"}, ctx)
    with pytest.raises(ToolError):
        await run("fetch_url", {"url": "ftp://x"}, ctx)


async def test_web_search_tavily_and_duckduckgo() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.host == "api.tavily.com":
            assert json.loads(req.content)["api_key"] == "tv-key"
            return httpx.Response(
                200, json={"results": [{"title": "T1", "url": "https://a", "content": "c1"}]}
            )
        return httpx.Response(
            200,
            text='<a class="result__a" href="https://b">Result &amp; B</a><a class="result__snippet">snip</a>',
            headers={"content-type": "text/html"},
        )

    out = await run("web_search", {"query": "x"}, mock_ctx(handler, {"tavily": "tv-key"}))
    assert out.startswith("1. T1\nhttps://a\nc1")
    out = await run("web_search", {"query": "x"}, mock_ctx(handler))
    assert "Result & B" in out and "https://b" in out and "snip" in out
    with pytest.raises(ToolError):
        await run("web_search", {"query": "  "}, mock_ctx(handler))


# ---------------------------------------------------------------- files


async def test_read_and_write_documents_roundtrip() -> None:
    sb = FakeSandbox()
    ctx = mock_ctx(lambda r: httpx.Response(500), sandbox=sb)
    await run(
        "write_document",
        {"path": "out/report.docx", "content": "# Title\n\nHello\n- item", "title": "Doc"},
        ctx,
    )
    await run(
        "write_document",
        {"path": "out/data.xlsx", "content": '[["a","b"],["1","2"]]', "title": "Data"},
        ctx,
    )
    await run(
        "write_document",
        {"path": "out/report.pdf", "content": "Line one\nLine two", "title": "PDF"},
        ctx,
    )
    assert {"out/report.docx", "out/data.xlsx", "out/report.pdf"} <= set(sb.binaries)

    docx_text = await run("read_document", {"path": "out/report.docx"}, ctx)
    assert "Doc" in docx_text and "Hello" in docx_text and "item" in docx_text
    xlsx_text = await run("read_document", {"path": "out/data.xlsx"}, ctx)
    assert "## sheet Data" in xlsx_text and "a,b" in xlsx_text and "1,2" in xlsx_text
    pdf_text = await run("read_document", {"path": "out/report.pdf"}, ctx)
    assert "page 1" in pdf_text and "Line one" in pdf_text
    sb.files["x.csv"] = "h1,h2\n1,2\n"
    assert await run("read_document", {"path": "x.csv"}, ctx) == "h1,h2\n1,2"
    with pytest.raises(ToolError):
        await run("read_document", {"path": "../etc/passwd"}, ctx)
    with pytest.raises(ToolError):
        extract_text(b"x", "exe")
    with pytest.raises(ToolError):
        build_document("txt", "x")
    with pytest.raises(ToolError):
        await run("read_document", {"path": "out/report.docx"}, ToolContext())  # no sandbox


async def test_ocr_reports_missing_tesseract() -> None:
    sb = FakeSandbox()
    with pytest.raises(ToolError) as exc:
        await run(
            "ocr_image", {"path": "scan.png"}, mock_ctx(lambda r: httpx.Response(500), sandbox=sb)
        )
    assert "tesseract" in str(exc.value)
    assert sb.calls[-1][:3] == ["tesseract", "/repo/scan.png", "-"]


# ---------------------------------------------------------------- data


async def test_query_sql_read_only(tmp_path) -> None:
    db = tmp_path / "t.db"
    import sqlite3

    con = sqlite3.connect(db)
    con.executescript(
        "create table t(a int, b text); insert into t values (1,'x'),(2,'y'),(3,'z');"
    )
    con.commit()
    con.close()
    # file-backed databases are refused (they could point at the app's own DB)
    with pytest.raises(ToolError, match="network databases"):
        await run(
            "query_sql",
            {"sql": "select 1"},
            mock_ctx(lambda r: httpx.Response(500), {"database": f"sqlite:///{db}"}),
        )
    ctx = mock_ctx(
        lambda r: httpx.Response(500), {"database": "postgresql+psycopg://u:p@db.example:5432/app"}
    )
    for bad in (
        "delete from t",
        "select 1; drop table t",
        "with x as (select 1) insert into t select 1,'q'",
    ):
        with pytest.raises(ToolError):
            await run("query_sql", {"sql": bad}, ctx)
    with pytest.raises(ToolError):
        await run("query_sql", {"sql": "select 1"}, mock_ctx(lambda r: httpx.Response(500)))


async def test_run_python_in_sandbox() -> None:
    sb = FakeSandbox()
    from app.executor.sandbox import ExecResult

    sb.scripted["python3"] = ExecResult(0, "42\n", "")
    out = await run(
        "run_python", {"code": "print(42)"}, mock_ctx(lambda r: httpx.Response(500), sandbox=sb)
    )
    assert out == "exit_code: 0\n42\n"
    assert any(c[:2] == ["tee", c[1]] and c[1].startswith("/tmp/ff_") for c in sb.calls)
    assert ["mkdir", "-p", "/repo/output"] in sb.calls


# ---------------------------------------------------------------- communication


async def test_send_email_smtp(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[Any] = []

    class FakeSMTP:
        def __init__(self, host, port, timeout=0):
            sent.append(("connect", host, port))

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self):
            sent.append(("starttls",))

        def login(self, u, p):
            sent.append(("login", u, p))

        def send_message(self, msg):
            sent.append(("send", msg["To"], msg["Subject"], msg.get_content().strip()))

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    ctx = mock_ctx(
        lambda r: httpx.Response(500), {"smtp": "smtp://me%40gmail.com:app-pass@smtp.gmail.com:587"}
    )
    out = await run("send_email", {"to": "a@x.io, b@x.io", "subject": "Hi", "body": "Hello"}, ctx)
    assert out == "sent to a@x.io, b@x.io"
    assert sent[0] == ("connect", "smtp.gmail.com", 587) and ("starttls",) in sent
    assert ("login", "me@gmail.com", "app-pass") in sent
    assert sent[-1] == ("send", "a@x.io, b@x.io", "Hi", "Hello")
    with pytest.raises(ToolError):
        await run(
            "send_email",
            {"to": "a@x.io", "subject": "s", "body": "b"},
            mock_ctx(lambda r: httpx.Response(500), {"smtp": "mailto:x"}),
        )


async def test_slack_and_telegram() -> None:
    calls: list[tuple[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append((str(req.url), json.loads(req.content) if req.content else None))
        if "hooks.slack.com" in str(req.url):
            return httpx.Response(200, text="ok")
        if "chat.postMessage" in str(req.url):
            assert req.headers["authorization"] == "Bearer xoxb-1"
            return httpx.Response(200, json={"ok": True, "ts": "1.2"})
        if "auth.test" in str(req.url):
            return httpx.Response(200, json={"ok": True, "user": "bot", "team": "acme"})
        if "sendMessage" in str(req.url):
            return httpx.Response(
                200,
                json={"ok": True, "result": {"message_id": 7}},
                headers={"content-type": "application/json"},
            )
        if "getMe" in str(req.url):
            return httpx.Response(200, json={"ok": True, "result": {"username": "ffbot"}})
        return httpx.Response(404)

    assert (
        await run(
            "slack_post",
            {"text": "hi"},
            mock_ctx(handler, {"slack": "https://hooks.slack.com/services/x"}),
        )
        == "posted via webhook"
    )
    tok = mock_ctx(handler, {"slack": "xoxb-1"})
    assert "posted to #gen" in await run("slack_post", {"text": "hi", "channel": "#gen"}, tok)
    with pytest.raises(ToolError):
        await run("slack_post", {"text": "hi"}, tok)
    assert "bot" in await registry.get("slack_post").self_test(tok)
    tg = mock_ctx(handler, {"telegram": "123:abc"})
    assert "sent message 7" in await run("telegram_send", {"chat_id": "42", "text": "yo"}, tg)
    assert "ffbot" in await registry.get("telegram_send").self_test(tg)
    assert calls[-2][0] == "https://api.telegram.org/bot123:abc/sendMessage"


# ---------------------------------------------------------------- integrations


async def test_github_notion_webhook_sheets() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        url = str(req.url)
        if url.endswith("/repos/acme/app/issues"):
            assert json.loads(req.content)["labels"] == ["bug"]
            return httpx.Response(
                201, json={"number": 9, "html_url": "https://github.com/acme/app/issues/9"}
            )
        if url.endswith("/v1/pages"):
            body = json.loads(req.content)
            assert body["parent"] == {"database_id": "db1"} and "Name" in body["properties"]
            assert len(body["children"]) == 2
            return httpx.Response(200, json={"id": "pg1", "url": "https://notion.so/pg1"})
        if "/blocks/" in url:
            return httpx.Response(200, json={})
        if url == "https://hooks.example/x":
            return httpx.Response(202, text="queued")
        if url.endswith("/token"):
            return httpx.Response(200, json={"access_token": "ya29"})
        if ":append" in url:
            assert req.headers["authorization"] == "Bearer ya29"
            return httpx.Response(200, json={"updates": {"updatedRange": "Sheet1!A5:B6"}})
        if "sheets.googleapis.com" in url:
            return httpx.Response(200, json={"values": [["h1", "h2"], ["1", "2"]]})
        return httpx.Response(404)

    gh = mock_ctx(handler, {"github": "ghp_x"})
    assert (
        await run("github_issue", {"repo": "acme/app", "title": "Bug", "labels": ["bug"]}, gh)
        == "created issue #9: https://github.com/acme/app/issues/9"
    )
    with pytest.raises(ToolError):
        await run("github_issue", {"repo": "nope", "title": "x"}, gh)

    nt = mock_ctx(handler, {"notion": "secret_n"})
    out = await run(
        "notion",
        {"action": "create_page", "database_id": "db1", "title": "T", "text": "p1\n\np2"},
        nt,
    )
    assert out.startswith("created page pg1")
    assert (
        await run("notion", {"action": "append_text", "page_id": "pg1", "text": "more"}, nt)
        == "appended text to pg1"
    )

    wh = mock_ctx(handler)
    out = await run("webhook_call", {"url": "https://hooks.example/x", "json": {"a": 1}}, wh)
    assert out == "status: 202\nqueued"
    with pytest.raises(ToolError):
        await run("webhook_call", {"url": "http://127.0.0.1:9/x"}, wh)

    # Google: a real RSA key is needed for the JWT; generate one for the test
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    sa = json.dumps({"client_email": "bot@proj.iam.gserviceaccount.com", "private_key": pem})
    gs = mock_ctx(handler, {"google": sa})
    out = await run(
        "google_sheets",
        {"spreadsheet_id": "S1", "action": "append", "range": "Sheet1!A:B", "rows": [["x", "y"]]},
        gs,
    )
    assert out == "appended 1 rows: Sheet1!A5:B6"
    assert (
        await run(
            "google_sheets", {"spreadsheet_id": "S1", "action": "read", "range": "Sheet1!A:B"}, gs
        )
        == "h1,h2\n1,2"
    )
    with pytest.raises(ToolError):
        await run(
            "google_sheets",
            {"spreadsheet_id": "S1", "action": "append", "range": "A:B"},
            mock_ctx(handler, {"google": "not json"}),
        )


# ---------------------------------------------------------------- media


async def test_generate_image_and_transcribe() -> None:
    import base64

    png = b"\x89PNG fake"

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/images/generations"):
            assert json.loads(req.content)["prompt"] == "a cat"
            return httpx.Response(
                200, json={"data": [{"b64_json": base64.b64encode(png).decode()}]}
            )
        if req.url.path.endswith("/audio/transcriptions"):
            assert b'name="file"' in req.content and b"RIFF" in req.content
            return httpx.Response(200, json={"text": "hello world"})
        return httpx.Response(404)

    sb = FakeSandbox(binaries={"talk.wav": b"RIFFxxxx"})
    ctx = mock_ctx(handler, {"openai": "sk-o"}, sandbox=sb)
    assert "saved 9 bytes to output/image.png" == await run(
        "generate_image", {"prompt": "a cat"}, ctx
    )
    assert sb.binaries["output/image.png"] == png
    assert await run("transcribe_audio", {"path": "talk.wav"}, ctx) == "hello world"
    with pytest.raises(ToolError):
        await run("generate_image", {"prompt": "x"}, mock_ctx(handler, sandbox=sb))


# ---------------------------------------------------------------- MCP (in-memory server)


def _demo_server() -> MCPServer:
    srv = MCPServer("demo")

    @srv.tool()
    def add(a: int, b: int) -> int:
        """Add two numbers."""
        return a + b

    @srv.tool()
    def fail(msg: str) -> str:
        """Always errors."""
        raise ValueError(msg)

    return srv


@pytest.fixture
def mcp_in_memory(monkeypatch: pytest.MonkeyPatch) -> MCPServer:
    server = _demo_server()

    @asynccontextmanager
    async def override(_row: McpServer):
        async with InMemoryTransport(server) as (read, write):
            yield read, write

    monkeypatch.setattr(mcp_service, "transport_override", override)
    return server


async def test_mcp_list_call_and_wrapper(mcp_in_memory: MCPServer) -> None:
    row = McpServer(id="srv1", user_id="u", name="Demo", command="demo")
    tools = await mcp_service.list_tools(row)
    assert [t["name"] for t in tools] == ["add", "fail"]
    assert tools[0]["input_schema"]["properties"].keys() == {"a", "b"}
    assert await mcp_service.call_tool(row, "add", {"a": 2, "b": 3}) == "5"
    with pytest.raises(ToolError):
        await mcp_service.call_tool(row, "fail", {"msg": "boom"})

    tool = mcp_service.McpTool(row, tools[0])
    assert (
        tool.name == "mcp:srv1:add"
        and tool.category is ToolCategory.mcp
        and "[Demo]" in tool.description
    )
    assert await tool.run({"a": 1, "b": 1}, ToolContext()) == "2"
    assert mcp_service.split_tool_name("mcp:srv1:add") == ("srv1", "add")
    with pytest.raises(ToolError):
        mcp_service.split_tool_name("read_file")


async def test_mcp_tool_in_agent_loop(mcp_in_memory: MCPServer, fake_sandbox: FakeSandbox) -> None:
    from app.executor.agent import run_agent
    from app.llm import AnthropicProvider

    row = McpServer(id="srv1", user_id="u", name="Demo", command="demo")
    extra = {
        "mcp:srv1:add": mcp_service.McpTool(
            row,
            {
                "name": "add",
                "description": "Add",
                "input_schema": {
                    "type": "object",
                    "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
                    "required": ["a", "b"],
                },
            },
        )
    }
    client = FakeAnthropic(
        [tool_response([("mcp:srv1:add", {"a": 40, "b": 2})]), text_response("it is 42")]
    )
    res = await run_agent(
        AnthropicProvider("m", client=client),  # type: ignore[arg-type]
        system_prompt="s",
        user_message="add",
        tools_allowed=["mcp:srv1:add", "read_file"],
        max_turns=3,
        executor=ToolExecutor(fake_sandbox, {"ls"}, extra_tools=extra),
        log=lambda *a, **k: None,
    )
    assert res.success and res.tool_calls[0]["output"] == "42"
    assert [t["name"] for t in client.requests[0]["tools"]] == ["mcp:srv1:add", "read_file"]


def test_mcp_servers_api_and_tools_listing(
    anon_client: TestClient, mcp_in_memory: MCPServer
) -> None:
    login(anon_client, "mcp@example.com")
    r = anon_client.post(
        "/mcp-servers",
        json={"name": "Demo", "command": "demo-mcp", "args": ["--x"], "env": {"TOKEN": "s3cret"}},
    )
    assert r.status_code == 201, r.text
    srv = r.json()
    assert (
        srv["tool_count"] == 2
        and srv["env_keys"] == ["TOKEN"]
        and "s3cret" not in r.text
        and srv["last_error"] is None
    )

    rows = anon_client.get("/tools").json()
    mcp_rows = [t for t in rows if t["category"] == "mcp"]
    assert [t["name"] for t in mcp_rows] == [f"mcp:{srv['id']}:add", f"mcp:{srv['id']}:fail"]
    assert mcp_rows[0]["status"] == "ready" and mcp_rows[0]["server_id"] == srv["id"]
    builtin = {t["name"]: t for t in rows if t["category"] != "mcp"}
    assert builtin["telegram_send"]["status"] == "missing_credentials"
    assert builtin["fetch_url"]["status"] == "ready"

    assert anon_client.post(f"/tools/mcp:{srv['id']}:add/test").json()["ok"] is True
    assert anon_client.post("/tools/telegram_send/test").json() == {
        "ok": False,
        "message": "missing credential 'telegram': add it under Settings → API keys",
    }
    assert anon_client.post("/tools/nope/test").json()["ok"] is False

    assert anon_client.post("/mcp-servers", json={"name": "bad"}).status_code == 422
    assert (
        anon_client.post(
            "/mcp-servers", json={"name": "bad", "url": "x", "command": "y"}
        ).status_code
        == 422
    )
    r = anon_client.put(f"/mcp-servers/{srv['id']}", json={"enabled": False})
    assert r.json()["enabled"] is False
    assert (
        next(t for t in anon_client.get("/tools").json() if t["category"] == "mcp")["status"]
        == "offline"
    )

    login(anon_client, "other@example.com")
    assert anon_client.get("/mcp-servers").json() == []
    assert anon_client.delete(f"/mcp-servers/{srv['id']}").status_code == 404
    login(anon_client, "mcp@example.com")
    assert anon_client.delete(f"/mcp-servers/{srv['id']}").status_code == 204


# ---------------------------------------------------------------- executor wiring


async def test_executor_passes_credentials_and_mcp_tools(
    deps: ExecutorDeps, db_session: Session, fake_client: FakeAnthropic, mcp_in_memory: MCPServer
) -> None:
    user = User(email="t@example.com", password_hash="x")
    db_session.add(user)
    db_session.flush()
    db_session.add(
        ApiKey(
            user_id=user.id,
            provider=KeyProvider.slack,
            encrypted_value=crypto.encrypt("https://hooks.slack.com/services/T"),
            last4="s/T",
        )
    )
    srv = McpServer(
        user_id=user.id,
        name="Demo",
        command="demo",
        tools_cache=[
            {
                "name": "add",
                "description": "",
                "input_schema": {
                    "type": "object",
                    "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
                    "required": ["a", "b"],
                },
            }
        ],
    )
    db_session.add(srv)
    db_session.commit()

    posted: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        posted.append(json.loads(req.content)["text"])
        return httpx.Response(200, text="ok")

    from app.config import get_settings
    from app.services.toolctx import build_tool_context

    def ctx_factory(uid):
        ctx = build_tool_context(deps.session_factory, get_settings(), uid)
        ctx.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        return ctx

    deps.tool_context_factory = ctx_factory
    deps.mcp_tools_factory = mcp_service.mcp_tools_factory(deps.session_factory)

    from app.models import Flow

    flow = Flow(
        user_id=user.id,
        name="f",
        nodes=[
            make_step(
                "a", "custom", system_prompt="go", tools_allowed=["slack_post", f"mcp:{srv.id}:add"]
            )
        ],
        edges=[],
    )
    db_session.add(flow)
    db_session.flush()
    run_row = Run(
        flow_id=flow.id, user_id=user.id, repo_url="https://github.com/a/b", branch="main"
    )
    db_session.add(run_row)
    db_session.commit()

    fake_client.responses = [
        tool_response(
            [(f"mcp:{srv.id}:add", {"a": 1, "b": 2}), ("slack_post", {"text": "sum is 3"})]
        ),
        text_response("done"),
    ]
    assert await FlowExecutor(deps, run_row.id).execute() is RunStatus.success
    assert posted == ["sum is 3"]
    with deps.session_factory() as db:
        step = db.query(StepRun).filter_by(run_id=run_row.id).one()
    assert [c["output"] for c in step.tool_calls] == ["3", "posted via webhook"]
