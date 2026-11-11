"""Entry point for the hyogo MCP server (hyogo-mcp).

Tools take local paths and the facts other servers returned, never a
facility: hyogo does not reach a cluster. The agent moves files with
rookery's fs_upload / fs_download.

The GROMACS tools (build_system ... stop_job, from classical-md) are the one
exception to "writes inputs only": start_equilibration runs gmx on this
machine, detached. Their analysis half (analyze_density, stage_energies,
export_frames) is in yagura.
"""
import argparse
from hyogo.mcp_tools import load, mcp


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
