"""The one place in yagura that imports the MCP SDK's server class.

Same rule as hpc_agent_core/mcp_server.py, kept separate because yagura
must not import hpc_agent_core: if the SDK's API moves again, this file
is the only one in yagura that changes.
"""
from mcp.server.mcpserver import MCPServer

__all__ = ["MCPServer"]
