"""Test doubles: in-memory sandbox, scripted Anthropic client, no-op workspace."""

from __future__ import annotations

import posixpath
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from anthropic.types.beta import BetaMessage, BetaTextBlock, BetaToolUseBlock, BetaUsage

from app.executor.sandbox import REPO_ROOT, ExecResult

# ---------------------------------------------------------------- sandbox


@dataclass
class FakeSandbox:
    """Sandbox with an in-memory file system and scripted commands.

    ``scripted`` maps either a full command line (``"pytest -q"``) or an
    executable name (``"pytest"``) to an :class:`ExecResult` or a callable
    receiving the argv.
    """

    files: dict[str, str] = field(default_factory=dict)
    scripted: dict[str, ExecResult | Callable[[list[str]], ExecResult]] = field(
        default_factory=dict
    )
    calls: list[list[str]] = field(default_factory=list)
    started: bool = False
    stopped: bool = False

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    async def exec(
        self,
        argv: list[str],
        *,
        stdin: str | None = None,
        timeout: float = 300,
        cwd: str = REPO_ROOT,
    ) -> ExecResult:
        self.calls.append(list(argv))
        line = " ".join(argv)
        for key in (line, argv[0]):
            if key in self.scripted:
                val = self.scripted[key]
                return val(argv) if callable(val) else val
        match argv:
            case ["cat", "--", path]:
                return self._cat(path)
            case ["tee", "--", path]:
                self.files[_rel(path)] = stdin or ""
                return ExecResult(0, stdin or "", "")
            case ["mkdir", "-p", "--", _]:
                return ExecResult(0, "", "")
            case ["find", root, *_]:
                prefix = _rel(root)
                names = [
                    posixpath.join(REPO_ROOT, f)
                    for f in self.files
                    if prefix in (".", "") or f == prefix or f.startswith(prefix + "/")
                ]
                return ExecResult(0, "\n".join(names) + ("\n" if names else ""), "")
            case ["git", "diff", *_]:
                return ExecResult(0, "diff --git a/x b/x\n", "")
            case ["git", *_]:
                return ExecResult(0, "", "")
        return ExecResult(127, "", f"{argv[0]}: command not found")

    def _cat(self, path: str) -> ExecResult:
        rel = _rel(path)
        if rel in self.files:
            return ExecResult(0, self.files[rel], "")
        return ExecResult(1, "", f"cat: {path}: No such file or directory")


def _rel(path: str) -> str:
    if path == REPO_ROOT:
        return "."
    return path[len(REPO_ROOT) + 1 :] if path.startswith(REPO_ROOT + "/") else path


# ---------------------------------------------------------------- anthropic


def text_response(text: str, *, stop_reason: str = "end_turn", tokens: int = 10) -> BetaMessage:
    """A final assistant message with only text."""
    return _message([BetaTextBlock(type="text", text=text)], stop_reason, tokens)


def tool_response(
    calls: list[tuple[str, dict[str, Any]]], *, text: str = "", tokens: int = 10
) -> BetaMessage:
    """An assistant message requesting one or more tool calls."""
    blocks: list[Any] = []
    if text:
        blocks.append(BetaTextBlock(type="text", text=text))
    for i, (name, args) in enumerate(calls):
        blocks.append(
            BetaToolUseBlock(type="tool_use", id=f"toolu_{name}_{i}", name=name, input=args)
        )
    return _message(blocks, "tool_use", tokens)


def _message(blocks: list[Any], stop_reason: str, tokens: int) -> BetaMessage:
    return BetaMessage(
        id="msg_test",
        type="message",
        role="assistant",
        model="claude-test",
        content=blocks,
        stop_reason=stop_reason,  # type: ignore[arg-type]
        stop_sequence=None,
        usage=BetaUsage(input_tokens=tokens, output_tokens=tokens),
    )


class _FakeMessages:
    def __init__(self, parent: FakeAnthropic) -> None:
        self._parent = parent

    async def create(self, **kwargs: Any) -> BetaMessage:
        self._parent.requests.append(kwargs)
        if not self._parent.responses:
            raise AssertionError("FakeAnthropic ran out of scripted responses")
        nxt = self._parent.responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


class _FakeBeta:
    def __init__(self, parent: FakeAnthropic) -> None:
        self.messages = _FakeMessages(parent)


class FakeAnthropic:
    """Stands in for ``AsyncAnthropic``; pops scripted responses in order."""

    def __init__(self, responses: list[BetaMessage | Exception] | None = None) -> None:
        self.responses: list[BetaMessage | Exception] = list(responses or [])
        self.requests: list[dict[str, Any]] = []
        self.beta = _FakeBeta(self)


# ---------------------------------------------------------------- workspace


@dataclass
class FakeWorkspace:
    """Pretends to clone; records calls."""

    prepared: list[tuple[str, str, str]] = field(default_factory=list)
    cleaned: list[str] = field(default_factory=list)
    fail_with: str | None = None

    async def prepare(self, run_id: str, repo_url: str, branch: str) -> tuple[str, str]:
        from app.executor.workspace import WorkspaceError, validate_repo_ref

        validate_repo_ref(repo_url, branch)
        if self.fail_with:
            raise WorkspaceError(self.fail_with)
        self.prepared.append((run_id, repo_url, branch))
        return f"/tmp/ws/{run_id}", f"/host/ws/{run_id}"

    async def cleanup(self, run_id: str) -> None:
        self.cleaned.append(run_id)
