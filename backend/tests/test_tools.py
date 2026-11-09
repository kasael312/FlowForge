"""Tests for path confinement, the shell whitelist and tool dispatch."""

import pytest

from app.executor.tools import (
    ToolError,
    ToolExecutor,
    parse_whitelisted_command,
    safe_repo_path,
    tool_definitions,
)
from tests.fakes import FakeSandbox

WL = {"ls", "pytest", "git"}


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("src/app.py", "/repo/src/app.py"),
        ("./src/../README.md", "/repo/README.md"),
        ("/repo/src/app.py", "/repo/src/app.py"),
        (".", "/repo"),
        ("/repo", "/repo"),
        ("a/b/../../c", "/repo/c"),
    ],
)
def test_safe_repo_path_ok(given: str, expected: str) -> None:
    assert safe_repo_path(given) == expected


@pytest.mark.parametrize(
    "given", ["../etc/passwd", "/etc/passwd", "src/../../x", "", "   ", "/repo/../x"]
)
def test_safe_repo_path_rejects(given: str) -> None:
    with pytest.raises(ToolError):
        safe_repo_path(given)


def test_whitelist_accepts_plain_commands() -> None:
    assert parse_whitelisted_command("pytest -q tests/", WL) == ["pytest", "-q", "tests/"]
    assert parse_whitelisted_command("/usr/bin/ls -la", WL) == ["ls", "-la"]
    assert parse_whitelisted_command("git log --oneline 'a b'", WL) == [
        "git",
        "log",
        "--oneline",
        "a b",
    ]


@pytest.mark.parametrize(
    "cmd",
    [
        "rm -rf /",
        "ls; rm -rf /",
        "ls && cat /etc/passwd",
        "ls | sh",
        "cat < /etc/passwd",
        "ls > out.txt",
        "ls $(whoami)",
        "ls `id`",
        "ls\nrm x",
        "",
        "python -c 'import os'",  # python not in this whitelist
    ],
)
def test_whitelist_rejects(cmd: str) -> None:
    with pytest.raises(ToolError):
        parse_whitelisted_command(cmd, WL)


def test_tool_definitions_filters_and_orders() -> None:
    defs = tool_definitions(["git_diff", "read_file", "bogus"])
    assert [d["name"] for d in defs] == ["git_diff", "read_file"]


async def test_read_write_list(fake_sandbox: FakeSandbox) -> None:
    ex = ToolExecutor(fake_sandbox, WL)
    assert await ex.call("read_file", {"path": "README.md"}) == "# demo\n"
    with pytest.raises(ToolError):
        await ex.call("read_file", {"path": "nope.txt"})
    msg = await ex.call("write_file", {"path": "src/new.py", "content": "x = 1\n"})
    assert "src/new.py" in msg
    assert fake_sandbox.files["src/new.py"] == "x = 1\n"
    listing = await ex.call("list_files", {"path": "src"})
    assert listing.splitlines() == ["src/app.py", "src/new.py"]
    with pytest.raises(ToolError):
        await ex.call("write_file", {"path": "../escape.py", "content": ""})
    with pytest.raises(ToolError):
        await ex.call("write_file", {"path": ".", "content": ""})


async def test_run_shell_uses_argv_not_shell(fake_sandbox: FakeSandbox) -> None:
    ex = ToolExecutor(fake_sandbox, WL)
    out = await ex.call("run_shell", {"command": "pytest -q"})
    assert fake_sandbox.calls[-1] == ["pytest", "-q"]
    assert out.startswith("exit_code: 127")  # not scripted -> not found
    with pytest.raises(ToolError):
        await ex.call("run_shell", {"command": "ls; id"})


async def test_git_diff(fake_sandbox: FakeSandbox) -> None:
    ex = ToolExecutor(fake_sandbox, WL)
    out = await ex.call("git_diff", {})
    assert out.startswith("diff --git")
    assert ["git", "add", "-N", "."] in fake_sandbox.calls


async def test_unknown_tool(fake_sandbox: FakeSandbox) -> None:
    ex = ToolExecutor(fake_sandbox, WL)
    with pytest.raises(ToolError):
        await ex.call("delete_everything", {})
