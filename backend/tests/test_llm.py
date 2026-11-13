"""Providers (mock HTTP), tool-schema normalisation, fallback, models API, pricing."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import crypto
from app.config import get_settings
from app.executor.engine import ExecutorDeps, FlowExecutor
from app.llm import (
    GenericOpenAICompatibleProvider,
    OllamaProvider,
    OpenAIProvider,
    ProviderAuthError,
    ProviderError,
    RateLimitError,
    ToolSpec,
)
from app.llm.base import Completion, LLMProvider, ModelParams, ToolCall, Usage
from app.llm.openai_compat import from_openai_message, to_openai_messages, to_openai_tools
from app.llm.pricing import estimate_cost, lookup, save_pricing
from app.models import (
    ApiKey,
    Flow,
    KeyProvider,
    Model,
    ModelProvider,
    Run,
    RunStatus,
    StepRun,
    User,
)
from app.services.models import ModelConfigError, resolve_provider
from tests.conftest import login, make_step
from tests.fakes import FakeAnthropic, text_response

# ---------------------------------------------------------------- OpenAI-compatible wire format

TOOLS = [
    ToolSpec(
        "read_file",
        "Read a file",
        {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        },
    ),
]


def test_tool_schema_normalisation() -> None:
    out = to_openai_tools(TOOLS)
    assert out == [
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read a file",
                "parameters": TOOLS[0].input_schema,
            },
        }
    ]


def test_message_conversion_round_trip() -> None:
    canonical = [
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "looking"},
                {
                    "type": "tool_use",
                    "id": "call_1",
                    "name": "read_file",
                    "input": {"path": "a.py"},
                },
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "call_1",
                    "content": "x = 1",
                    "is_error": False,
                }
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "call_2",
                    "content": "boom",
                    "is_error": True,
                },
                {"type": "text", "text": "continue"},
            ],
        },
    ]
    msgs = to_openai_messages("SYS", canonical)
    assert msgs[0] == {"role": "system", "content": "SYS"}
    assert msgs[1] == {"role": "user", "content": "hi"}
    assert msgs[2]["role"] == "assistant" and msgs[2]["content"] == "looking"
    assert msgs[2]["tool_calls"][0]["function"] == {
        "name": "read_file",
        "arguments": '{"path": "a.py"}',
    }
    assert msgs[3] == {"role": "tool", "tool_call_id": "call_1", "content": "x = 1"}
    assert msgs[4] == {"role": "tool", "tool_call_id": "call_2", "content": "ERROR: boom"}
    assert msgs[5] == {"role": "user", "content": "continue"}

    text, calls, blocks = from_openai_message(
        {
            "content": "ok",
            "tool_calls": [
                {
                    "id": "c9",
                    "type": "function",
                    "function": {"name": "run_shell", "arguments": '{"command": "ls"}'},
                }
            ],
        }
    )
    assert text == "ok" and calls == [ToolCall("c9", "run_shell", {"command": "ls"})]
    assert blocks[1] == {
        "type": "tool_use",
        "id": "c9",
        "name": "run_shell",
        "input": {"command": "ls"},
    }
    # malformed arguments don't crash
    _, calls, _ = from_openai_message(
        {"tool_calls": [{"function": {"name": "x", "arguments": "{bad"}}]}
    )
    assert calls[0].arguments == {"_raw": "{bad"} and calls[0].id.startswith("call_")


def _transport(handler: Any) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


async def test_openai_provider_complete_with_tools() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        body = json.loads(req.content)
        assert req.url == "https://api.openai.com/v1/chat/completions"
        assert req.headers["authorization"] == "Bearer sk-test"
        assert (
            body["model"] == "gpt-4o-mini"
            and body["temperature"] == 0.2
            and body["max_tokens"] == 512
        )
        assert body["tools"][0]["function"]["name"] == "read_file" and body["tool_choice"] == "auto"
        return httpx.Response(
            200,
            json={
                "model": "gpt-4o-mini-2026",
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_a",
                                    "type": "function",
                                    "function": {"name": "read_file", "arguments": '{"path":"x"}'},
                                }
                            ],
                        },
                    }
                ],
                "usage": {"prompt_tokens": 11, "completion_tokens": 7},
            },
        )

    p = OpenAIProvider(
        "gpt-4o-mini",
        api_key="sk-test",
        params=ModelParams(temperature=0.2, max_tokens=512),
        transport=_transport(handler),
    )
    c = await p.complete([{"role": "user", "content": "go"}], tools=TOOLS, system="S")
    assert c.stop_reason == "tool_use" and c.tool_calls[0].name == "read_file"
    assert c.usage.input_tokens == 11 and c.usage.output_tokens == 7
    assert c.model == "gpt-4o-mini-2026" and c.content[0]["type"] == "tool_use"
    assert p.label == "openai/gpt-4o-mini"


async def test_openai_compatible_errors_and_finish_reasons() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        if body["model"] == "auth":
            return httpx.Response(401, json={"error": "bad key"})
        if body["model"] == "limit":
            return httpx.Response(429, json={"error": "slow down"})
        if body["model"] == "down":
            return httpx.Response(503, text="unavailable")
        if body["model"] == "long":
            return httpx.Response(
                200,
                json={
                    "choices": [{"finish_reason": "length", "message": {"content": "partial"}}],
                    "usage": {},
                },
            )
        return httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": "stop", "message": {"content": "done"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )

    def mk(model: str) -> GenericOpenAICompatibleProvider:
        return GenericOpenAICompatibleProvider(
            model,
            base_url="https://api.groq.com/openai/v1/",
            api_key="k",
            transport=_transport(handler),
        )

    with pytest.raises(ProviderAuthError):
        await mk("auth").complete([{"role": "user", "content": "x"}])
    with pytest.raises(RateLimitError):
        await mk("limit").complete([{"role": "user", "content": "x"}])
    with pytest.raises(ProviderError) as exc:
        await mk("down").complete([{"role": "user", "content": "x"}])
    assert exc.value.status == 503 and exc.value.retryable
    c = await mk("long").complete([{"role": "user", "content": "x"}])
    assert c.stop_reason == "max_tokens" and c.text == "partial"
    c = await mk("fine").complete([{"role": "user", "content": "x"}])
    assert (
        c.stop_reason == "end_turn"
        and c.text == "done"
        and c.content == [{"type": "text", "text": "done"}]
    )
    assert mk("fine").base_url == "https://api.groq.com/openai/v1"


async def test_ollama_provider_defaults_and_ping() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "llama3.2"}, {"id": "qwen2.5-coder"}]})
        assert "authorization" not in req.headers
        return httpx.Response(
            200, json={"choices": [{"finish_reason": "stop", "message": {"content": "hola"}}]}
        )

    p = OllamaProvider("llama3.2", transport=_transport(handler))
    assert p.base_url == "http://localhost:11434/v1" and p.label == "ollama/llama3.2"
    assert (await p.complete([{"role": "user", "content": "hi"}])).text == "hola"
    assert (await p.ping()).startswith("OK (2 models")
    p2 = OllamaProvider("missing", base_url="http://gpu-box:11434/", transport=_transport(handler))
    assert p2.base_url == "http://gpu-box:11434/v1"
    assert "not listed" in await p2.ping()


async def test_connection_error_is_provider_error() -> None:
    def handler(_req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    p = OllamaProvider("x", transport=_transport(handler))
    with pytest.raises(ProviderError) as exc:
        await p.complete([{"role": "user", "content": "hi"}])
    assert "connection error" in str(exc.value) and exc.value.retryable


# ---------------------------------------------------------------- pricing


def test_pricing_lookup_and_estimate(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.llm import pricing

    monkeypatch.setattr(pricing, "pricing_path", lambda: tmp_path / "pricing.json")
    pricing._cache = None
    assert lookup("anthropic/claude-opus-5-5") == {"input": 4.0, "output": 20.0}
    assert lookup("anthropic/claude-opus-5-5-20260101") == {"input": 4.0, "output": 20.0}  # prefix
    assert lookup("ollama/llama3.2") == {"input": 0.0, "output": 0.0}
    assert lookup("openai_compatible/mystery") is None
    assert estimate_cost("anthropic/claude-opus-5-5", 1_000_000, 100_000) == 6.0
    assert estimate_cost("openai_compatible/mystery", 10, 10) is None

    save_pricing({"openai_compatible/mystery": {"input": "1.5", "output": 3}})
    assert estimate_cost("openai_compatible/mystery", 1_000_000, 1_000_000) == 4.5
    assert lookup("anthropic/claude-opus-5-5") is None  # table replaced
    with pytest.raises(ValueError):
        save_pricing({"x": {"input": -1, "output": 0}})
    with pytest.raises(ValueError):
        save_pricing({"x": "nope"})


def test_pricing_api(client: TestClient, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.llm import pricing

    monkeypatch.setattr(pricing, "pricing_path", lambda: tmp_path / "p.json")
    pricing._cache = None
    table = client.get("/pricing").json()["prices"]
    assert "anthropic/claude-opus-5-5" in table
    r = client.put(
        "/pricing",
        json={
            "prices": {
                "ollama/": {"input": 0, "output": 0},
                "openai/gpt-4o": {"input": 2.5, "output": 10},
            }
        },
    )
    assert r.status_code == 200 and set(r.json()["prices"]) == {"ollama/", "openai/gpt-4o"}
    assert (
        client.put("/pricing", json={"prices": {"x": {"input": -5, "output": 0}}}).status_code
        == 422
    )


# ---------------------------------------------------------------- models API + resolver


def _add_key(client: TestClient, provider: str, value: str) -> str:
    return client.post("/keys", json={"provider": provider, "value": value}).json()["id"]


def test_models_crud_default_and_isolation(anon_client: TestClient) -> None:
    login(anon_client, "m@example.com")
    key_id = _add_key(anon_client, "anthropic", "sk-ant-aaaaaaaaaaaa1234")
    r = anon_client.post(
        "/models",
        json={
            "provider": "anthropic",
            "model_name": "claude-sonnet-5-5",
            "label": "Sonnet",
            "api_key_id": key_id,
            "params": {"effort": "low"},
        },
    )
    assert r.status_code == 201, r.text
    sonnet = r.json()
    assert sonnet["is_default"] is True  # first model becomes default
    assert sonnet["api_key_last4"] == "1234" and "sk-ant" not in r.text
    assert (
        sonnet["pricing_key"] == "anthropic/claude-sonnet-5-5" and sonnet["price"]["input"] == 2.0
    )

    r = anon_client.post(
        "/models",
        json={
            "provider": "ollama",
            "model_name": "llama3.2",
            "label": "Local",
            "base_url": "http://localhost:11434/",
        },
    )
    assert (
        r.status_code == 201
        and r.json()["is_default"] is False
        and r.json()["base_url"] == "http://localhost:11434"
    )
    local = r.json()

    # validation
    assert (
        anon_client.post(
            "/models", json={"provider": "openai_compatible", "model_name": "x", "label": "no url"}
        ).status_code
        == 422
    )
    assert (
        anon_client.post(
            "/models",
            json={"provider": "ollama", "model_name": "x", "label": "k", "api_key_id": key_id},
        ).status_code
        == 422
    )
    assert (
        anon_client.post(
            "/models",
            json={"provider": "openai", "model_name": "x", "label": "k", "base_url": "ftp://x"},
        ).status_code
        == 422
    )
    assert (
        anon_client.post(
            "/models",
            json={
                "provider": "openai",
                "model_name": "x",
                "label": "k",
                "params": {"temperature": 9},
            },
        ).status_code
        == 422
    )

    # default switching
    r = anon_client.post(f"/models/{local['id']}/default")
    assert r.json()["is_default"] is True
    rows = anon_client.get("/models").json()
    assert [m["label"] for m in rows] == ["Local", "Sonnet"] and sum(
        m["is_default"] for m in rows
    ) == 1
    r = anon_client.put(
        f"/models/{sonnet['id']}",
        json={"label": "Sonnet 5.5", "is_default": True, "params": {"max_tokens": 4096}},
    )
    assert (
        r.json()["label"] == "Sonnet 5.5"
        and r.json()["is_default"]
        and r.json()["params"] == {"max_tokens": 4096}
    )

    # another user sees nothing
    login(anon_client, "n@example.com")
    assert anon_client.get("/models").json() == []
    assert anon_client.put(f"/models/{sonnet['id']}", json={"label": "x"}).status_code == 404
    assert anon_client.delete(f"/models/{sonnet['id']}").status_code == 404

    # deleting the default promotes the oldest remaining
    login(anon_client, "m@example.com")
    assert anon_client.delete(f"/models/{sonnet['id']}").status_code == 204
    rows = anon_client.get("/models").json()
    assert len(rows) == 1 and rows[0]["is_default"] is True
    anon_client.cookies.clear()
    assert anon_client.get("/models").status_code == 401


def test_model_test_endpoint(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    import app.routers.models as router_mod

    class Fake(LLMProvider):
        kind = "fake"

        async def complete(self, messages, *, tools=None, system="", max_tokens=16000):
            raise AssertionError

        async def ping(self) -> str:
            if self.model == "bad":
                raise ProviderAuthError("nope")
            return "OK fake"

    monkeypatch.setattr(
        router_mod, "build_provider", lambda row, key, settings: Fake(row.model_name)
    )
    good = client.post(
        "/models", json={"provider": "ollama", "model_name": "fine", "label": "a"}
    ).json()["id"]
    bad = client.post(
        "/models", json={"provider": "ollama", "model_name": "bad", "label": "b"}
    ).json()["id"]
    assert client.post(f"/models/{good}/test").json() == {"ok": True, "message": "OK fake"}
    assert client.post(f"/models/{bad}/test").json()["ok"] is False


def test_resolve_provider(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    user = User(email="r@example.com", password_hash="x")
    db_session.add(user)
    db_session.flush()

    # nothing configured, no keys anywhere -> clear error
    monkeypatch.setattr(settings, "anthropic_api_key", "")
    with pytest.raises(ModelConfigError) as exc:
        resolve_provider(db_session, settings, user.id, None)
    assert "Anthropic API key" in str(exc.value)

    # stored anthropic key -> implicit default uses it
    db_session.add(
        ApiKey(
            user_id=user.id,
            provider=KeyProvider.anthropic,
            encrypted_value=crypto.encrypt("sk-ant-user"),
            last4="user",
        )
    )
    db_session.commit()
    p = resolve_provider(db_session, settings, user.id, None)
    assert (
        p.kind == "anthropic"
        and p.model == settings.anthropic_model
        and p.client.api_key == "sk-ant-user"
    )  # type: ignore[attr-defined]

    # explicit models
    groq_key = ApiKey(
        user_id=user.id,
        provider=KeyProvider.other,
        encrypted_value=crypto.encrypt("gsk-1"),
        last4="sk-1",
    )
    db_session.add(groq_key)
    db_session.flush()
    groq = Model(
        user_id=user.id,
        provider=ModelProvider.openai_compatible,
        model_name="llama-70b",
        label="Groq",
        base_url="https://api.groq.com/openai/v1",
        api_key_id=groq_key.id,
        params={"temperature": 0.1},
        is_default=True,
    )
    local = Model(
        user_id=user.id,
        provider=ModelProvider.ollama,
        model_name="qwen",
        label="Local",
        base_url=None,
        params={},
    )
    oai = Model(
        user_id=user.id, provider=ModelProvider.openai, model_name="gpt-4o", label="OAI", params={}
    )
    db_session.add_all([groq, local, oai])
    db_session.commit()

    p = resolve_provider(db_session, settings, user.id, None)  # default -> groq
    assert p.kind == "openai_compatible" and p.api_key == "gsk-1" and p.params.temperature == 0.1  # type: ignore[attr-defined]
    p = resolve_provider(db_session, settings, user.id, local.id)
    assert p.kind == "ollama" and p.base_url == "http://localhost:11434/v1"  # type: ignore[attr-defined]
    # openai model without a stored key falls back to env, else errors
    monkeypatch.setattr(settings, "openai_api_key", "")
    with pytest.raises(ModelConfigError):
        resolve_provider(db_session, settings, user.id, oai.id)
    monkeypatch.setattr(settings, "openai_api_key", "sk-env")
    assert resolve_provider(db_session, settings, user.id, oai.id).api_key == "sk-env"  # type: ignore[attr-defined]
    # foreign / unknown model id
    other = User(email="o@example.com", password_hash="x")
    db_session.add(other)
    db_session.commit()
    with pytest.raises(ModelConfigError):
        resolve_provider(db_session, settings, other.id, local.id)


# ---------------------------------------------------------------- executor: per-step model, fallback, cost


class ScriptedProvider(LLMProvider):
    """Returns canned completions or raises; records calls."""

    kind = "fake"

    def __init__(self, model: str, script: list[Completion | Exception]) -> None:
        super().__init__(model)
        self.script = list(script)
        self.calls: list[dict[str, Any]] = []

    async def complete(self, messages, *, tools=None, system="", max_tokens=16000):
        self.calls.append({"system": system, "messages": messages, "tools": tools})
        nxt = self.script.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt

    async def ping(self) -> str:
        return "ok"


def done(text: str, inp: int = 100, out: int = 50) -> Completion:
    return Completion(
        text=text,
        tool_calls=[],
        stop_reason="end_turn",
        usage=Usage(inp, out),
        content=[{"type": "text", "text": text}],
    )


def _flow_run(db: Session, nodes: list[dict], edges: list[dict], user_id: str | None = None) -> str:
    flow = Flow(user_id=user_id, name="f", nodes=nodes, edges=edges)
    db.add(flow)
    db.flush()
    run = Run(
        flow_id=flow.id,
        user_id=user_id,
        repo_url="https://github.com/a/b",
        branch="main",
        task_input="t",
    )
    db.add(run)
    db.commit()
    return run.id


async def test_per_step_models_fallback_and_cost(
    deps: ExecutorDeps, db_session: Session, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.llm import pricing

    monkeypatch.setattr(pricing, "pricing_path", lambda: tmp_path / "pricing.json")
    pricing._cache = None
    save_pricing(
        {
            "fake/cheap": {"input": 1.0, "output": 2.0},
            "fake/strong": {"input": 10.0, "output": 20.0},
        }
    )

    cheap = ScriptedProvider("cheap", [RateLimitError("429"), done("cheap did it")])
    strong = ScriptedProvider("strong", [done("strong did it", 1000, 500)])
    default = ScriptedProvider("default", [done("plan")])
    providers = {None: default, "m-cheap": cheap, "m-strong": strong}
    deps.provider_resolver = lambda user_id, model_id: providers[model_id]

    run_id = _flow_run(
        db_session,
        [
            make_step("plan", "plan"),  # default model
            make_step("code", "code", model_id="m-cheap", fallback_model_id="m-strong"),
            make_step("review", "review", model_id="m-cheap"),
        ],
        [
            {"id": "1", "source": "plan", "target": "code"},
            {"id": "2", "source": "code", "target": "review"},
        ],
    )
    assert await FlowExecutor(deps, run_id).execute() is RunStatus.success

    with deps.session_factory() as db:
        steps = (
            db.query(StepRun)
            .filter_by(run_id=run_id)
            .order_by(StepRun.started_at, StepRun.id)
            .all()
        )
        run = db.get(Run, run_id)
    assert [s.model for s in steps] == ["fake/default", "fake/strong", "fake/cheap"]
    assert (
        steps[1].output == "strong did it"
        and steps[1].input_tokens == 1000
        and steps[1].output_tokens == 500
    )
    assert steps[1].cost_usd == pytest.approx((1000 * 10 + 500 * 20) / 1_000_000)
    assert steps[2].cost_usd == pytest.approx((100 * 1 + 50 * 2) / 1_000_000)
    assert steps[0].cost_usd is None  # fake/default not priced
    assert run is not None and run.cost_usd == pytest.approx(steps[1].cost_usd + steps[2].cost_usd)
    assert run.cost_tokens == 150 + 1500 + 150
    msgs = [e.get("message", "") for e in deps.bus.history(run_id) if e["event"] == "log"]
    assert any("falling back to fake/strong" in m for m in msgs)
    assert len(cheap.calls) == 2 and len(strong.calls) == 1


async def test_provider_error_without_fallback_fails_step(
    deps: ExecutorDeps, db_session: Session
) -> None:
    broken = ScriptedProvider("broken", [ProviderError("boom", status=500)])
    deps.provider_resolver = lambda user_id, model_id: broken
    run_id = _flow_run(db_session, [make_step("a", "code", on_fail="stop")], [])
    assert await FlowExecutor(deps, run_id).execute() is RunStatus.failed
    with deps.session_factory() as db:
        step = db.query(StepRun).filter_by(run_id=run_id).one()
    assert step.status.value == "failed" and "provider error: boom" in step.output


async def test_misconfigured_model_fails_before_clone(
    deps: ExecutorDeps, db_session: Session
) -> None:
    def resolver(user_id: str | None, model_id: str | None) -> LLMProvider:
        raise ModelConfigError("model 'ghost' not found")

    deps.provider_resolver = resolver
    run_id = _flow_run(db_session, [make_step("a", "code", model_id="ghost")], [])
    assert await FlowExecutor(deps, run_id).execute() is RunStatus.failed
    with deps.session_factory() as db:
        run = db.get(Run, run_id)
    assert run is not None and run.error == "model 'ghost' not found"
    assert not deps.workspace.prepared  # type: ignore[attr-defined]


async def test_shell_only_flow_needs_no_model(
    deps: ExecutorDeps, db_session: Session, fake_sandbox
) -> None:
    from app.executor.sandbox import ExecResult

    def resolver(user_id: str | None, model_id: str | None) -> LLMProvider:
        raise AssertionError("must not be called")

    deps.provider_resolver = resolver
    fake_sandbox.scripted["ls"] = ExecResult(0, "README.md\n", "")
    run_id = _flow_run(db_session, [make_step("s", "shell", command="ls")], [])
    assert await FlowExecutor(deps, run_id).execute() is RunStatus.success


def test_try_step_reports_model_config_error(
    client: TestClient, deps: ExecutorDeps, sample_flow: dict
) -> None:
    from app.services.runs import run_manager

    def resolver(user_id: str | None, model_id: str | None) -> LLMProvider:
        raise ModelConfigError("no key for you")

    deps.provider_resolver = resolver
    run_manager._deps = deps
    try:
        flow_id = client.post("/flows", json=sample_flow).json()["id"]
        r = client.post(f"/flows/{flow_id}/steps/code/try", json={})
        assert r.json()["success"] is False and r.json()["error"] == "no key for you"
    finally:
        run_manager._deps = None


def test_anthropic_fake_still_drives_the_executor(
    deps: ExecutorDeps, db_session: Session, fake_client: FakeAnthropic
) -> None:
    """The default test wiring (AnthropicProvider over FakeAnthropic) records model + tokens."""
    import asyncio

    fake_client.responses = [text_response("hello", tokens=7)]
    run_id = _flow_run(db_session, [make_step("a", "plan")], [])
    assert asyncio.run(FlowExecutor(deps, run_id).execute()) is RunStatus.success
    with deps.session_factory() as db:
        step = db.query(StepRun).filter_by(run_id=run_id).one()
    assert (
        step.model == "anthropic/claude-test" and step.input_tokens == 7 and step.output_tokens == 7
    )
