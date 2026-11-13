"""The one place that imports the MCP SDK's server class.

Every machine repo imports MCPServer from here, never from `mcp` directly,
and none of them declare "mcp" as their own dependency — only hpc-agent-core
does. mcp 2.0.0 renamed mcp.server.fastmcp.FastMCP to
mcp.server.mcpserver.MCPServer with no deprecation window, which broke every
consumer overnight because each one imported it independently and pinned (or
didn't pin) the SDK on its own. If the SDK's API surface moves again, this
file is the only one that needs to change.

The class exported here is the SDK's, plus one rule: a tool call naming an
argument the tool does not take is refused instead of silently dropped.
The SDK builds each tool's argument model with pydantic's default
extra="ignore", so a misspelt name — or a new argument sent to a server
still running code from before it existed — vanished without an error and
the tool ran with the default. The check overrides the public
`MCPServer.call_tool` (every wire call goes through it) and reads the
accepted names from the public `list_tools()` input schema, so it touches
no SDK internals. If that ever stops working it logs to stderr once and
falls back to the SDK's behaviour; it can never stop the server starting.

yagura/mcp_server.py and hyogo/mcp_server.py, in their own repositories,
carry a deliberate copy of this check: the three import nothing from each
other. scripts/sibling_sync.py --check compares the copies when those
checkouts sit next to this one.
"""
import sys
from typing import Any

from mcp.server.mcpserver import MCPServer as _SDKServer

try:
    from mcp.server.mcpserver.exceptions import ToolError as _ArgumentError
except ImportError:  # the SDK turns any exception into an is_error result
    _ArgumentError = ValueError

__all__ = ["MCPServer"]

_warned = False


def _unknown_argument_message(tools: list[Any], name: str,
                              arguments: dict[str, Any]) -> str | None:
    """The refusal for a call naming arguments `name` does not take, or None."""
    tool = next((t for t in tools if t.name == name), None)
    if tool is None:  # unknown tool: the SDK's own error says so
        return None
    schema = tool.input_schema or {}
    props = schema.get("properties")
    if not isinstance(props, dict) or schema.get("additionalProperties"):
        return None  # the tool takes arbitrary arguments, or a shape we don't know
    unknown = [k for k in arguments if k not in props]
    if not unknown:
        return None
    takes = ", ".join(props) if props else "no arguments"
    return (f"{name} got unknown argument(s) {unknown}; it takes: {takes}. "
            "If the tool should have it, the server may be running old code: "
            "restart the session.")


class MCPServer(_SDKServer):
    """The SDK's MCPServer, refusing tool arguments a tool does not take."""

    async def call_tool(self, name: str, arguments: dict[str, Any], *args: Any, **kwargs: Any):
        global _warned
        try:
            message = _unknown_argument_message(await self.list_tools(), name, arguments or {})
        except Exception as e:  # never let the check itself break a call
            message = None
            if not _warned:
                _warned = True
                print(f"{type(self).__name__}: unknown-argument check disabled ({e!r}); "
                      "unknown tool arguments will be ignored", file=sys.stderr)
        if message:
            raise _ArgumentError(message)
        return await super().call_tool(name, arguments, *args, **kwargs)
