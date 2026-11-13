"""Tests for the per-step agent loop with a scripted provider."""

from typing import Any

from app.executor.agent import run_agent
from app.executor.sandbox import ExecResult
from app.executor.tools import ToolExecutor
from app.llm import AnthropicProvider, ModelParams
from app.llm.anthropic_provider import FALLBACK_BETA
from tests.fakes import FakeAnthropic, FakeSandbox, text_response, tool_response

WL = {"pytest", "ls"}
ALL = ["read_file", "write_file", "run_shell", "list_files", "git_diff"]


def _events() -> tuple[list[tuple[str, dict[str, Any]]], Any]:
    events: list[tuple[str, dict[str, Any]]] = []

    def log(ev: str, **data: Any) -> None:
        events.append((ev, data))

    return events, log


def _provider(client: FakeAnthropic, **kw: Any) -> AnthropicProvider:
    return AnthropicProvider(
        "claude-test", client=client, params=ModelParams(effort="medium"), **kw
    )  # type: ignore[arg-type]


async def _run(client: FakeAnthropic, sandbox: FakeSandbox, **kw: Any):
    events, log = _events()
    res = await run_agent(
        _provider(client),
        system_prompt="sys",
        user_message="do it",
        tools_allowed=kw.pop("tools_allowed", ALL),
        max_turns=kw.pop("max_turns", 10),
        executor=ToolExecutor(sandbox, WL),
        log=log,
    )
    return res, events


async def test_tool_loop_records_everything(fake_sandbox: FakeSandbox) -> None:
    client = FakeAnthropic(
        [
            tool_response([("read_file", {"path": "README.md"})], text="Let me look."),
            tool_response(
                [
                    ("write_file", {"path": "src/x.py", "content": "y = 2\n"}),
                    ("run_shell", {"command": "pytest -q"}),
                ]
            ),
            text_response("Done: added x.py"),
        ]
    )
    fake_sandbox.scripted["pytest -q"] = ExecResult(0, "3 passed", "")
    res, events = await _run(client, fake_sandbox)

    assert res.success and res.error is None
    assert res.output == "Done: added x.py"
    assert res.tokens == 60 and res.input_tokens == 30 and res.output_tokens == 30
    assert res.model == "anthropic/claude-test"
    assert [c["tool"] for c in res.tool_calls] == ["read_file", "write_file", "run_shell"]
    assert res.tool_calls[0]["output"] == "# demo\n"
    assert res.tool_calls[2]["output"] == "exit_code: 0\n3 passed"
    assert fake_sandbox.files["src/x.py"] == "y = 2\n"

    roles = [m["role"] for m in res.messages]
    assert roles == ["user", "assistant", "user", "assistant", "user", "assistant"]
    assert len(res.messages[4]["content"]) == 2  # both results in ONE user message
    assert res.messages[4]["content"][0]["tool_use_id"] == "toolu_write_file_0"

    req = client.requests[0]
    assert req["model"] == "claude-test"
    assert req["system"] == "sys"
    assert req["betas"] == [FALLBACK_BETA] and req["fallbacks"] == "default"
    assert req["output_config"] == {"effort": "medium"}
    assert [t["name"] for t in req["tools"]] == ALL
    assert req["tools"][0]["strict"] is True

    kinds = [e for e, _ in events]
    assert kinds.count("tool_call") == 3 and kinds.count("tool_result") == 3 and "text" in kinds


async def test_tool_error_is_returned_as_is_error(fake_sandbox: FakeSandbox) -> None:
    client = FakeAnthropic(
        [tool_response([("run_shell", {"command": "rm -rf /"})]), text_response("ok, I won't")]
    )
    res, _ = await _run(client, fake_sandbox)
    assert res.success
    call = res.tool_calls[0]
    assert call["is_error"] and "not whitelisted" in call["output"]
    assert res.messages[2]["content"][0]["is_error"] is True


async def test_disallowed_tool_for_step(fake_sandbox: FakeSandbox) -> None:
    client = FakeAnthropic(
        [tool_response([("write_file", {"path": "a", "content": "b"})]), text_response("fine")]
    )
    res, _ = await _run(client, fake_sandbox, tools_allowed=["read_file"])
    assert res.tool_calls[0]["is_error"]
    assert "not allowed" in res.tool_calls[0]["output"]
    assert "a" not in fake_sandbox.files
    assert [t["name"] for t in client.requests[0]["tools"]] == ["read_file"]


async def test_max_turns(fake_sandbox: FakeSandbox) -> None:
    client = FakeAnthropic(
        [tool_response([("list_files", {})], text=f"turn {i}") for i in range(5)]
    )
    res, _ = await _run(client, fake_sandbox, max_turns=2)
    assert not res.success
    assert res.error == "max_turns (2) exceeded"
    assert len(client.requests) == 2
    assert res.output == "turn 1"


async def test_refusal_and_truncation(fake_sandbox: FakeSandbox) -> None:
    res, _ = await _run(FakeAnthropic([text_response("no", stop_reason="refusal")]), fake_sandbox)
    assert not res.success and "refused" in (res.error or "")
    res, _ = await _run(
        FakeAnthropic([text_response("partial", stop_reason="max_tokens")]), fake_sandbox
    )
    assert not res.success and "truncated" in (res.error or "")


async def test_no_fallbacks_when_disabled(fake_sandbox: FakeSandbox) -> None:
    client = FakeAnthropic([text_response("hi")])
    _unused, log = _events()
    provider = AnthropicProvider("m", client=client, use_fallbacks=False)  # type: ignore[arg-type]
    await run_agent(
        provider,
        system_prompt="s",
        user_message="u",
        tools_allowed=[],
        max_turns=1,
        executor=ToolExecutor(fake_sandbox, WL),
        log=log,
    )
    req = client.requests[0]
    assert "fallbacks" not in req and "betas" not in req and "tools" not in req
    assert "output_config" not in req
