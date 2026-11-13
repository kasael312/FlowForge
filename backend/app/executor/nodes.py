"""Non-LLM node types: data_transform, http_request, merge, template, output.

Each handler receives the node, the run-wide variable context and a small
:class:`NodeEnv` and returns a :class:`NodeResult`. ``data`` is the parsed
JSON value of the output (when it is JSON) so later steps can use
``{{steps.<id>.data.<field>}}``.
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx
from jinja2 import TemplateError
from jinja2.sandbox import SandboxedEnvironment

from app.executor.sandbox import REPO_ROOT, Sandbox
from app.executor.variables import lookup, render
from app.net import UnsafeUrl, check_public_url
from app.schemas import Node
from app.tools import ToolContext, ToolError, registry
from app.tools.code import safe_repo_path

MAX_OUTPUT = 200_000


@dataclass(slots=True)
class NodeResult:
    success: bool
    output: str
    data: Any = None
    error: str | None = None
    exit_code: int | None = None


@dataclass(slots=True)
class NodeEnv:
    """What non-LLM nodes may use."""

    sandbox: Sandbox | None
    tool_ctx: ToolContext
    shell_whitelist: frozenset[str]
    mcp_tools: dict[str, Any]


def parse_json(text: str) -> Any:
    """JSON value of ``text`` or ``None`` when it isn't JSON."""
    t = text.strip()
    if not t or t[0] not in '[{"0123456789-tfn':
        return None
    try:
        return json.loads(t)
    except ValueError:
        return None


def _truncate(text: str) -> str:
    return (
        text
        if len(text) <= MAX_OUTPUT
        else text[:MAX_OUTPUT] + f"\n... [truncated {len(text) - MAX_OUTPUT} chars]"
    )


# ---------------------------------------------------------------- data_transform

PY_PRELUDE = (
    "import json, sys\n"
    "ctx = json.load(open(sys.argv[1]))\n"
    "input, steps, items, prev, task = ctx['input'], ctx['steps'], ctx['items'], ctx['prev'], ctx['task']\n"
    "def emit(value):\n"
    "    print(json.dumps(value, ensure_ascii=False, default=str))\n"
    "# ---- user code ----\n"
)
JS_PRELUDE = (
    "const ctx = JSON.parse(require('fs').readFileSync(process.argv[2], 'utf8'));\n"
    "const { input, steps, items, prev, task } = ctx;\n"
    "const emit = (v) => console.log(JSON.stringify(v));\n"
    "// ---- user code ----\n"
)


async def run_data_transform(node: Node, context: dict[str, Any], env: NodeEnv) -> NodeResult:
    """Run a small Python/JavaScript script in the sandbox with the variable context."""
    if env.sandbox is None:
        return NodeResult(False, "", error="data_transform needs the sandbox")
    code = (node.data.code or "").strip()
    if not code:
        return NodeResult(False, "", error="code is empty")
    lang = (node.data.language or "python").lower()
    token = secrets.token_hex(4)
    ctx_path = f"/tmp/ff_ctx_{token}.json"
    ctx_json = json.dumps(_serialisable(context), ensure_ascii=False, default=str)
    res = await env.sandbox.exec(["tee", ctx_path], stdin=ctx_json, timeout=30)
    if not res.ok:
        return NodeResult(False, "", error="could not write context")
    if lang in ("python", "py"):
        script_path, argv = f"/tmp/ff_{token}.py", ["python3", f"/tmp/ff_{token}.py", ctx_path]
        body = PY_PRELUDE + code + "\n"
    elif lang in ("javascript", "js", "node"):
        script_path, argv = f"/tmp/ff_{token}.js", ["node", f"/tmp/ff_{token}.js", ctx_path]
        body = JS_PRELUDE + code + "\n"
    else:
        return NodeResult(False, "", error=f"unsupported language {lang!r}")
    res = await env.sandbox.exec(["tee", script_path], stdin=body, timeout=30)
    if not res.ok:
        return NodeResult(False, "", error="could not write script")
    timeout = max(1, min(int(node.data.max_turns or 20) * 30, 1800))
    res = await env.sandbox.exec(argv, timeout=timeout)
    out = _truncate(res.stdout.strip())
    if not res.ok:
        return NodeResult(
            False,
            out,
            error=f"exit code {res.exit_code}: {res.stderr.strip()[:500]}",
            exit_code=res.exit_code,
        )
    return NodeResult(True, out, data=parse_json(out), exit_code=0)


def _serialisable(context: dict[str, Any]) -> dict[str, Any]:
    """Context subset handed to scripts (no secrets, plain JSON)."""
    return {
        "task": context.get("task", ""),
        "repo": context.get("repo", ""),
        "branch": context.get("branch", ""),
        "input": context.get("input") or {},
        "steps": context.get("steps") or {},
        "items": context.get("items") or {},
        "prev": context.get("prev") or {},
    }


# ---------------------------------------------------------------- http_request


async def run_http_request(node: Node, context: dict[str, Any], env: NodeEnv) -> NodeResult:
    """HTTP call from the backend; URL, headers and body are rendered with variables."""
    url = render(node.data.url or "", context).strip()
    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.netloc:
        return NodeResult(False, "", error="url must be http(s)")
    try:
        await check_public_url(url)
    except UnsafeUrl as exc:
        return NodeResult(False, "", error=str(exc))
    method = (node.data.method or "GET").upper()
    headers = {str(k): render(str(v), context) for k, v in (node.data.headers or {}).items()}
    body_text = render(node.data.body or "", context)
    kwargs: dict[str, Any] = {"headers": headers}
    if body_text and method in ("POST", "PUT", "PATCH", "DELETE"):
        parsed = parse_json(body_text)
        if parsed is not None:
            kwargs["json"] = parsed
        else:
            kwargs["content"] = body_text.encode()
    try:
        r = await env.tool_ctx.client().request(method, url, timeout=120.0, **kwargs)
    except httpx.HTTPError as exc:
        return NodeResult(False, "", error=f"request failed: {exc.__class__.__name__}")
    text = _truncate(r.text)
    data = parse_json(text)
    out = text
    ok = r.status_code < 400
    return NodeResult(
        ok,
        out,
        data={
            "status": r.status_code,
            "body": data if data is not None else text,
            "headers": dict(r.headers),
        }
        if True
        else None,
        error=None if ok else f"HTTP {r.status_code}",
        exit_code=r.status_code,
    )


# ---------------------------------------------------------------- merge


def run_merge(node: Node, context: dict[str, Any]) -> NodeResult:
    """Combine outputs of several earlier steps."""
    steps: dict[str, Any] = context.get("steps") or {}
    wanted = list(node.data.merge_steps or [])
    if not wanted:
        wanted = [sid for sid, s in steps.items() if s.get("status") == "success"]
    missing = [sid for sid in wanted if sid not in steps]
    if missing:
        return NodeResult(False, "", error=f"steps not run yet: {', '.join(missing)}")
    mode = (node.data.merge_mode or "concat").lower()
    if mode == "json":
        data = {
            sid: (
                steps[sid].get("data")
                if steps[sid].get("data") is not None
                else steps[sid].get("output")
            )
            for sid in wanted
        }
        out = json.dumps(data, ensure_ascii=False, indent=2)
        return NodeResult(True, _truncate(out), data=data)
    parts = [f"## {sid}\n{str(steps[sid].get('output') or '').strip()}" for sid in wanted]
    out = "\n\n".join(parts)
    return NodeResult(True, _truncate(out), data=None)


# ---------------------------------------------------------------- template

_jinja = SandboxedEnvironment(autoescape=False, trim_blocks=True, lstrip_blocks=True)
MAX_RANGE = 10_000


def _capped_range(*args: int) -> range:
    r = range(*args)
    if len(r) > MAX_RANGE:
        raise ValueError(f"range() in templates is limited to {MAX_RANGE} items")
    return r


_jinja.globals["range"] = _capped_range


def render_jinja(template: str, context: dict[str, Any]) -> str:
    """Render a Jinja2 template in the sandboxed environment."""
    tpl = _jinja.from_string(template)
    return tpl.render(**{k: v for k, v in context.items() if not k.startswith("_")})


async def run_template(node: Node, context: dict[str, Any], env: NodeEnv) -> NodeResult:
    """Render Jinja (with {{steps.x.output}}, loops, filters) to text, optionally into a file."""
    source = node.data.template or ""
    if not source.strip():
        return NodeResult(False, "", error="template is empty")
    try:
        text = render_jinja(source, context)
    except TemplateError as exc:
        return NodeResult(False, "", error=f"template error: {exc}")
    except Exception as exc:  # noqa: BLE001 - sandbox violations etc.
        return NodeResult(False, "", error=f"template error: {exc.__class__.__name__}: {exc}")
    path = (node.data.output_path or "").strip()
    if path:
        if env.sandbox is None:
            return NodeResult(False, text, error="writing a file needs the sandbox")
        try:
            full = safe_repo_path(path)
        except ToolError as exc:
            return NodeResult(False, text, error=str(exc))
        await env.sandbox.write_bytes(full, text.encode())
        rel = full[len(REPO_ROOT) + 1 :]
        return NodeResult(True, _truncate(text), data={"path": rel, "chars": len(text)})
    return NodeResult(True, _truncate(text), data=parse_json(text))


# ---------------------------------------------------------------- output


async def run_output(
    node: Node, context: dict[str, Any], env: NodeEnv
) -> tuple[NodeResult, dict[str, Any]]:
    """Define a final result of the run and optionally deliver it with a tool.

    Returns the node result and the output record stored on ``Run.outputs``.
    """
    kind = (node.data.output_kind or "text").lower()
    source = node.data.output_source or "{{prev_output}}"
    try:
        value = render_jinja(source, context) if "{%" in source else render(source, context)
    except Exception as exc:  # noqa: BLE001
        return NodeResult(False, "", error=f"output source error: {exc}"), {}
    record: dict[str, Any] = {"name": node.data.label or node.id, "kind": kind, "step_id": node.id}
    if kind == "json":
        data = parse_json(value)
        if data is None:
            return NodeResult(False, value, error="output is not valid JSON"), {}
        record["value"] = data
        text = json.dumps(data, ensure_ascii=False, indent=2)
    elif kind == "file":
        path = (node.data.output_path or value).strip()
        try:
            full = safe_repo_path(path)
        except ToolError as exc:
            return NodeResult(False, "", error=str(exc)), {}
        if env.sandbox is None:
            return NodeResult(False, "", error="file outputs need the sandbox"), {}
        if node.data.output_path and value and value != path:
            await env.sandbox.write_bytes(full, value.encode())
        res = await env.sandbox.exec(["test", "-f", full], timeout=10)
        if not res.ok:
            return NodeResult(False, "", error=f"file {path} does not exist"), {}
        record["path"] = full[len(REPO_ROOT) + 1 :]
        text = f"file: {record['path']}"
    else:
        record["value"] = value
        text = value
    deliver = (node.data.deliver_tool or "").strip()
    if deliver:
        try:
            tool = registry.get(deliver, env.mcp_tools)
            args = {
                k: render(str(v), {**context, "output": text}) if isinstance(v, str) else v
                for k, v in (node.data.deliver_args or {}).items()
            }
            ctx = env.tool_ctx
            ctx.sandbox = env.sandbox
            ctx.shell_whitelist = env.shell_whitelist
            delivered = await tool.run(args, ctx)
            record["delivered"] = {"tool": deliver, "result": delivered[:500]}
        except ToolError as exc:
            return NodeResult(False, text, error=f"delivery via {deliver} failed: {exc}"), record
    return NodeResult(True, _truncate(text), data=record.get("value")), record


# ---------------------------------------------------------------- loop helpers


def resolve_items(expression: str, context: dict[str, Any]) -> list[Any]:
    """Items for a loop: a variable path (``steps.x.data.rows``), JSON, or comma list."""
    expr = (expression or "").strip()
    if not expr:
        raise ValueError("items expression is empty")
    path = expr[2:-2].strip() if expr.startswith("{{") and expr.endswith("}}") else expr
    value = lookup(context, path)
    if value is lookup.__globals__["_MISSING"]:
        rendered = render(expr, context) if "{{" in expr else expr
        value = parse_json(rendered)
        if value is None:
            value = [p.strip() for p in rendered.split(",") if p.strip()]
    if isinstance(value, dict):
        value = [{"key": k, "value": v} for k, v in value.items()]
    if isinstance(value, str):
        parsed = parse_json(value)
        value = (
            parsed
            if isinstance(parsed, list)
            else [p.strip() for p in value.split(",") if p.strip()]
        )
    if not isinstance(value, list):
        raise TypeError("items expression did not resolve to a list")
    return value
