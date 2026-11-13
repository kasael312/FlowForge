"""Tool plugins. Importing this package registers every built-in tool."""

from app.tools.base import Tool, ToolCategory, ToolContext, ToolError, schema, truncate
from app.tools.code import CODE_TOOLS, parse_whitelisted_command, safe_repo_path
from app.tools.communication import COMMUNICATION_TOOLS
from app.tools.data import DATA_TOOLS
from app.tools.files import FILE_TOOLS
from app.tools.industry import INDUSTRY_TOOLS
from app.tools.integrations import INTEGRATION_TOOLS
from app.tools.media import MEDIA_TOOLS
from app.tools.registry import MCP_PREFIX, ToolRegistry, registry
from app.tools.web import WEB_TOOLS

BUILTIN_TOOLS: list[Tool] = [
    *CODE_TOOLS,
    *WEB_TOOLS,
    *FILE_TOOLS,
    *DATA_TOOLS,
    *COMMUNICATION_TOOLS,
    *INTEGRATION_TOOLS,
    *MEDIA_TOOLS,
    *INDUSTRY_TOOLS,
]

for _tool in BUILTIN_TOOLS:
    if _tool.name not in registry.names():
        registry.register(_tool)

__all__ = [
    "BUILTIN_TOOLS",
    "MCP_PREFIX",
    "Tool",
    "ToolCategory",
    "ToolContext",
    "ToolError",
    "ToolRegistry",
    "parse_whitelisted_command",
    "registry",
    "safe_repo_path",
    "schema",
    "truncate",
]
