"""Entry point for the yagura MCP server (yagura-mcp).

Tools take paths on this machine, never a facility: yagura does not reach
a cluster. For a file on a cluster, fetch it first (rookery's fs_download).
The GROMACS analysis tools (analyze_density, stage_energies, export_frames,
from classical-md) run gmx on this machine over a run folder hyogo made.

The CIF tools need ASE, the optional [yagura] extra. It is imported inside
them, so the server and the numpy-only tools (the checks and the
trajectory analysis) work without it.
"""
import argparse
from yagura.mcp_tools import load, mcp


#: the tool modules, one per group (mcp_tools/<group>.py), loaded in name order.
#: Their tools stay attributes of this module too (server.<tool>), as before.
TOOL_MODULES = load()
for _mod in TOOL_MODULES:
    for _name, _obj in vars(_mod).items():
        if callable(_obj) and not _name.startswith("_") and getattr(_obj, "__module__", None) == _mod.__name__:
            globals()[_name] = _obj


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--transport", default="stdio", choices=["stdio", "streamable-http"])
    args, _ = parser.parse_known_args()
    mcp.run(transport=args.transport)


if __name__ == "__main__":
    main()
