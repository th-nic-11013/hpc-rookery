"""Code-pack tools: the MCP surface for what codes/ skills do with data.

Served by the hpc server (hpc_mcp/hpc_server.py), which registers them
inside a guard: a fault in a code tool is logged and skips the code tools,
but never stops the scheduler tools from starting. server.py defines them
(it has no entry point of its own); each module next to it backs one group of tools (records.py: the records adapter).
"""
