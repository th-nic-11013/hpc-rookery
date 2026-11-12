"""The hpc_codes server's tools, one module per group: hpc_codes/mcp_tools/<group>.py.

hpc_codes/server.py imports every module here (load()) and register() hands
their tools to the hpc server;
a new tool goes in its group's module, or a new module, and nothing else
is edited. This file holds the server object and what several groups share.
"""
import importlib
import pkgutil
from hpc_agent_core.mcp_server import MCPServer

mcp = MCPServer("hpc-codes-mcp")


def load() -> list:
    """Import every tool module in this folder, in name order, and return them."""
    return [importlib.import_module(f"{__name__}.{m.name}")
            for m in sorted(pkgutil.iter_modules(__path__), key=lambda m: m.name)]


#: every tool below, in definition order, for register()
TOOLS = []


def _tool(fn):
    """@mcp.tool(), also recording fn so register() can serve it elsewhere.
    Decorate every new tool with @_tool, not @mcp.tool(): register() only
    sees TOOLS, and tests/conformance.py fails on a tool hpc does not serve."""
    TOOLS.append(fn)
    return mcp.tool()(fn)
