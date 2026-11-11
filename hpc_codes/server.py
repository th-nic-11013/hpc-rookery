"""The code-pack tools, served by the hpc server (hpc-mcp) through register().

hpc_mcp/hpc_server.py calls register(its own MCPServer) inside a guard, so a
fault here is logged and skips these tools without stopping the scheduler
tools from starting. The standalone entry point (hpc-codes-mcp) still works
for a harness that registered it separately.

Registers every facility (via importing hpc_mcp), then exposes the tools
the code-pack skills under codes/ call. Every tool takes the facility slug
first, like the scheduler tools. The records_* tools work on local files
and never contact a cluster; the cp2k_* tools run one read-only command on
the facility's login node, after the same configuration check as the
scheduler tools (cp2k_bench_input then writes its inputs on this
machine); launch_plan reads records and pack values only.
"""
import hpc_mcp  # noqa: F401 -- import for its side effect: registers every facility
from hpc_agent_core.mcp_server import MCPServer
from hpc_agent_core.serving import serve
from hpc_codes.mcp_tools import TOOLS, load, mcp


#: the tool modules, one per group (mcp_tools/<group>.py), loaded in name order.
#: Their tools stay attributes of this module too (server.<tool>), as before.
TOOL_MODULES = load()
for _mod in TOOL_MODULES:
    for _name, _obj in vars(_mod).items():
        if callable(_obj) and not _name.startswith("_") and getattr(_obj, "__module__", None) == _mod.__name__:
            globals()[_name] = _obj


def register(target: MCPServer) -> MCPServer:
    """Add every code-pack tool to another server (the hpc server)."""
    for fn in TOOLS:
        target.add_tool(fn)
    return target


def main():
    serve(mcp)


if __name__ == "__main__":
    main()
