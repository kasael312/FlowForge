"""Data tools: read-only SQL against a configured connection, and Python in the sandbox."""

from __future__ import annotations

import re
import secrets
from typing import Any

from app.tools.base import Tool, ToolCategory, ToolContext, ToolError, schema, truncate

_NETWORK_DIALECTS = {
    "postgresql",
    "postgres",
    "mysql",
    "mariadb",
    "mssql",
    "oracle",
    "clickhouse",
    "snowflake",
    "cockroachdb",
    "redshift",
    "trino",
    "presto",
}
_READ_ONLY = re.compile(r"^\s*(select|with|show|describe|explain|pragma)\b", re.IGNORECASE)
_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|grant|attach)\b", re.IGNORECASE
)


class QuerySql(Tool):
    name = "query_sql"
    description = (
        "Run a read-only SQL query (SELECT/WITH) against the configured database connection "
        "(credential 'database' = SQLAlchemy URL). Returns up to max_rows rows as CSV."
    )
    category = ToolCategory.data
    required_credentials = ("database",)
    input_schema = schema(
        {"sql": {"type": "string"}, "max_rows": {"type": "integer", "description": "Default 200"}},
        ["sql"],
    )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        import asyncio

        from sqlalchemy import create_engine, text

        url = ctx.need("database", "SQLAlchemy URL such as postgresql+psycopg://user:pw@host/db")
        dialect = url.split(":", 1)[0].split("+", 1)[0].lower()
        if (
            dialect not in _NETWORK_DIALECTS
            or "://" not in url
            or not url.split("://", 1)[1].split("/", 1)[0]
        ):
            raise ToolError(
                "query_sql only accepts network databases (postgresql, mysql, mariadb, mssql, oracle, "
                "clickhouse, snowflake); file-backed URLs such as sqlite are not allowed"
            )
        sql = args["sql"].strip().rstrip(";")
        if not _READ_ONLY.match(sql) or _FORBIDDEN.search(sql):
            raise ToolError("only read-only SELECT/WITH queries are allowed")
        max_rows = max(1, min(int(args.get("max_rows") or 200), 5000))

        def _query() -> str:
            engine = create_engine(url, pool_pre_ping=True)
            try:
                with engine.connect() as conn:
                    result = conn.execute(text(sql))
                    cols = list(result.keys())
                    rows = result.fetchmany(max_rows + 1)
            finally:
                engine.dispose()
            more = len(rows) > max_rows
            lines = [",".join(cols)]
            lines += [",".join("" if v is None else str(v) for v in r) for r in rows[:max_rows]]
            if more:
                lines.append(f"... (more than {max_rows} rows)")
            return "\n".join(lines)

        try:
            return truncate(await asyncio.to_thread(_query))
        except Exception as exc:
            raise ToolError(f"query failed: {exc.__class__.__name__}: {str(exc)[:300]}") from exc

    async def self_test(self, ctx: ToolContext) -> str:
        return await self.run({"sql": "SELECT 1 AS ok", "max_rows": 1}, ctx)


class RunPython(Tool):
    name = "run_python"
    description = (
        "Run a Python script inside the sandbox (python3; pandas/matplotlib when the image provides them). "
        "Files written under /repo/output are kept as run artifacts. Returns stdout/stderr."
    )
    category = ToolCategory.data
    needs_sandbox = True
    input_schema = schema(
        {
            "code": {"type": "string"},
            "timeout": {"type": "integer", "description": "Seconds, default 300"},
        },
        ["code"],
    )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        sb = ctx.sandbox_required()
        code = args["code"]
        if not code.strip():
            raise ToolError("code must not be empty")
        name = f"/tmp/ff_{secrets.token_hex(4)}.py"
        res = await sb.exec(["tee", name], stdin=code, timeout=30)
        if not res.ok:
            raise ToolError("could not write script")
        await sb.exec(["mkdir", "-p", "/repo/output"], timeout=10)
        timeout = max(1, min(int(args.get("timeout") or 300), 1800))
        res = await sb.exec(["python3", name], timeout=timeout)
        return f"exit_code: {res.exit_code}\n{res.text()}"


DATA_TOOLS: list[Tool] = [QuerySql(), RunPython()]
