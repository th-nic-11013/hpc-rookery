"""Code-pack tools: the MCP surface for what codes/ skills do with data.

Kept apart from hpc_mcp, the scheduler server every facility shares, so a
fault in a code tool can never stop the scheduler tools from starting.
server.py is the entry point (hpc-codes-mcp); each module next to it backs
one group of tools (records.py: the records adapter).
"""
